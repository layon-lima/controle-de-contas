from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CLOUD = ROOT / "cloud_mobile"
LEGACY_CANDIDATES = [
    ROOT.parent / "CONTROLE_CONTAS" / "cloud_mobile",
    ROOT / ".." / "CONTROLE_CONTAS" / "cloud_mobile",
]

IGNORE_NAMES = {
    "node_modules", ".wrangler", ".git", ".dev.vars", ".env", ".env.local"
}


def copy_current_cloud_if_needed() -> None:
    if (CLOUD / "src" / "index.js").is_file() and (CLOUD / "public" / "app.js").is_file():
        return

    source = None
    for candidate in LEGACY_CANDIDATES:
        candidate = candidate.resolve()
        if (candidate / "src" / "index.js").is_file():
            source = candidate
            break

    if source is None:
        raise RuntimeError(
            "Nao encontrei a pasta cloud_mobile atual. "
            "Ela deve existir no projeto antigo CONTROLE_CONTAS."
        )

    CLOUD.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if any(part in IGNORE_NAMES for part in rel.parts):
            continue
        if path.is_dir():
            continue
        dest = CLOUD / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    print(f"Cloud atual importada de: {source}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Trecho esperado nao encontrado: {label}")
    return text.replace(old, new, 1)


def patch_worker() -> None:
    path = CLOUD / "src" / "index.js"
    text = path.read_text(encoding="utf-8")

    if "authorized_devices" in text and "mobileManifest" in text:
        print("Worker ja possui acesso permanente por dispositivo.")
        return

    start = text.index("function authorized(request, expected, header) {")
    end = text.index("\nfunction statusConta", start)

    auth_block = r'''function authorized(request, expected, header) {
  const received = request.headers.get(header) || "";
  return Boolean(expected) && secureEqual(received, expected);
}

function cookieValue(request, name) {
  const raw = request.headers.get("Cookie") || "";
  for (const part of raw.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      try { return decodeURIComponent(rest.join("=")); }
      catch { return rest.join("="); }
    }
  }
  return "";
}

function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function randomDeviceToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

async function sha256Hex(value) {
  const data = new TextEncoder().encode(String(value || ""));
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
}

function requestDeviceToken(request) {
  const header = request.headers.get("X-Device-Token") || "";
  if (header) return header;

  const cookie = cookieValue(request, "cc_device");
  if (cookie) return cookie;

  try {
    const url = new URL(request.url);
    return String(url.searchParams.get("device") || "");
  } catch {
    return "";
  }
}

async function validDeviceToken(env, token, touch = true) {
  token = String(token || "").trim();
  if (!token) return false;

  const hash = await sha256Hex(token);
  const row = await env.DB.prepare(`
    SELECT token_hash
      FROM authorized_devices
     WHERE token_hash = ?
       AND revoked_at IS NULL
  `).bind(hash).first();

  if (!row) return false;

  if (touch) {
    await env.DB.prepare(
      "UPDATE authorized_devices SET last_seen_at=? WHERE token_hash=?"
    ).bind(new Date().toISOString(), hash).run();
  }
  return true;
}

function bootstrapViewAuthorized(request, expected) {
  if (!expected) return false;

  const headerToken = request.headers.get("X-View-Token") || "";
  if (headerToken && secureEqual(headerToken, expected)) return true;

  const legacyCookie = cookieValue(request, "cc_view");
  return Boolean(legacyCookie) && secureEqual(legacyCookie, expected);
}

async function viewAuthorized(request, env) {
  if (bootstrapViewAuthorized(request, env.VIEW_TOKEN)) return true;
  return validDeviceToken(env, requestDeviceToken(request));
}

function deviceCookie(token) {
  return `cc_device=${encodeURIComponent(token)}; Path=/; Max-Age=315360000; Secure; HttpOnly; SameSite=Strict`;
}

async function startViewSession(request, env) {
  const existingDevice = requestDeviceToken(request);

  if (existingDevice && await validDeviceToken(env, existingDevice)) {
    return new Response(JSON.stringify({
      ok:true,
      persistent:true,
      device_token:existingDevice,
    }), {
      status:200,
      headers:{
        "content-type":"application/json; charset=utf-8",
        "cache-control":"no-store",
        "x-content-type-options":"nosniff",
        "referrer-policy":"no-referrer",
        "set-cookie":deviceCookie(existingDevice),
      },
    });
  }

  if (!bootstrapViewAuthorized(request, env.VIEW_TOKEN)) {
    return json({ok:false, erro:"Nao autorizado."}, 401);
  }

  const deviceToken = randomDeviceToken();
  const tokenHash = await sha256Hex(deviceToken);
  const now = new Date().toISOString();
  const userAgent = String(request.headers.get("User-Agent") || "").slice(0, 500);

  await env.DB.prepare(`
    INSERT INTO authorized_devices(token_hash,created_at,last_seen_at,user_agent,revoked_at)
    VALUES(?,?,?,?,NULL)
  `).bind(tokenHash, now, now, userAgent).run();

  return new Response(JSON.stringify({
    ok:true,
    persistent:true,
    device_token:deviceToken,
  }), {
    status:200,
    headers:{
      "content-type":"application/json; charset=utf-8",
      "cache-control":"no-store",
      "x-content-type-options":"nosniff",
      "referrer-policy":"no-referrer",
      "set-cookie":deviceCookie(deviceToken),
    },
  });
}

async function mobileManifest(request, env) {
  const token = requestDeviceToken(request);
  const valid = token && await validDeviceToken(env, token, false);
  const startUrl = valid ? `/?device=${encodeURIComponent(token)}` : "/";

  return new Response(JSON.stringify({
    name:"Controle de Contas",
    short_name:"Contas",
    id:"/",
    start_url:startUrl,
    scope:"/",
    display:"standalone",
    background_color:"#f3f6f4",
    theme_color:"#0b1f17",
    description:"Painel mobile de visualizacao do Controle de Contas",
    icons:[{
      src:"/icon.svg",
      sizes:"any",
      type:"image/svg+xml",
      purpose:"any maskable",
    }],
  }), {
    headers:{
      "content-type":"application/manifest+json; charset=utf-8",
      "cache-control":"no-store",
      "x-content-type-options":"nosniff",
      "referrer-policy":"no-referrer",
    },
  });
}
'''

    text = text[:start] + auth_block + text[end:]
    text = text.replace(
        "viewAuthorized(request, env.VIEW_TOKEN)",
        "await viewAuthorized(request, env)",
    )

    text = replace_once(
        text,
        'if (path === "/api/session" && method === "POST") return startViewSession(request, env);',
        'if (path === "/api/session" && method === "POST") return await startViewSession(request, env);\n'
        '      if (path === "/api/manifest" && method === "GET") return await mobileManifest(request, env);',
        "rotas session/manifest",
    )

    path.write_text(text, encoding="utf-8")


def patch_mobile_js() -> None:
    path = CLOUD / "public" / "app.js"
    text = path.read_text(encoding="utf-8")

    if "controle_contas_device_token" in text:
        print("App mobile ja possui persistencia de dispositivo.")
        return

    start = text.index("function getViewToken(){")
    end = text.index("\nasync function loadAccounts(){", start)

    auth_block = r'''const VIEW_TOKEN_KEY = "controle_contas_view_token";
const DEVICE_TOKEN_KEY = "controle_contas_device_token";

function getViewToken(){
  const fromHash = decodeURIComponent(location.hash.replace(/^#/, "").trim());
  if(fromHash){
    localStorage.setItem(VIEW_TOKEN_KEY, fromHash);
    return fromHash;
  }
  return localStorage.getItem(VIEW_TOKEN_KEY) || "";
}

function getDeviceToken(){
  const params = new URLSearchParams(location.search);
  const fromUrl = String(params.get("device") || "").trim();
  if(fromUrl){
    localStorage.setItem(DEVICE_TOKEN_KEY, fromUrl);
    return fromUrl;
  }
  return localStorage.getItem(DEVICE_TOKEN_KEY) || "";
}

function updatePersistentManifest(deviceToken){
  if(!deviceToken) return;
  let manifest = document.querySelector('link[rel="manifest"]');
  if(!manifest){
    manifest = document.createElement("link");
    manifest.rel = "manifest";
    document.head.appendChild(manifest);
  }
  manifest.href = "/api/manifest?device=" + encodeURIComponent(deviceToken);
}

function keepPersistentStartUrl(deviceToken){
  if(!deviceToken) return;
  const url = new URL(location.href);
  url.hash = "";
  if(!url.searchParams.get("device")){
    url.searchParams.set("device", deviceToken);
    history.replaceState(null, "", url.pathname + url.search);
  }
}

function viewHeaders(){
  const deviceToken = getDeviceToken();
  if(deviceToken) return {"X-Device-Token":deviceToken};

  const viewToken = getViewToken();
  return viewToken ? {"X-View-Token":viewToken} : {};
}

async function ensureViewSession(){
  const deviceToken = getDeviceToken();
  const viewToken = getViewToken();
  if(!deviceToken && !viewToken) return false;

  const headers = deviceToken
    ? {"X-Device-Token":deviceToken}
    : {"X-View-Token":viewToken};

  try{
    const response = await fetch("/api/session?t=" + Date.now(), {
      method:"POST",
      cache:"no-store",
      headers
    });
    if(!response.ok) return false;

    const payload = await response.json().catch(()=>({}));
    const persistentToken = String(payload.device_token || deviceToken || "").trim();

    if(persistentToken){
      localStorage.setItem(DEVICE_TOKEN_KEY, persistentToken);
      updatePersistentManifest(persistentToken);
      keepPersistentStartUrl(persistentToken);

      try{
        if(navigator.storage && navigator.storage.persist){
          navigator.storage.persist().catch(()=>{});
        }
      }catch{}
    }
    return true;
  }catch{
    return false;
  }
}
'''

    text = text[:start] + auth_block + text[end:]

    text = replace_once(
        text,
        '''  const viewToken = getViewToken();

  if(viewToken){
    // Cria um cookie seguro no mesmo domínio. O iPhone copia cookies para
    // o Web App instalado, enquanto localStorage pode ficar isolado.
    await ensureViewSession();
  }
''',
        '''  if(getDeviceToken() || getViewToken()){
    await ensureViewSession();
  }
''',
        "inicio de loadAccounts",
    )

    text = replace_once(
        text,
        '''    if(response.status === 401){
      localStorage.removeItem("controle_contas_view_token");
      throw new Error("Abra novamente o link privativo para autorizar este dispositivo.");
    }
''',
        '''    if(response.status === 401){
      throw new Error("A autorizacao deste aparelho precisa ser restaurada uma unica vez pelo link privativo.");
    }
''',
        "tratamento 401",
    )

    text = replace_once(
        text,
        "\n\nloadAccounts();\n",
        "\n\nconst rememberedDeviceToken = getDeviceToken();\n"
        "if(rememberedDeviceToken) updatePersistentManifest(rememberedDeviceToken);\n\n"
        "loadAccounts();\n",
        "bootstrap mobile",
    )

    text = text.replace(
        'navigator.serviceWorker.register("/sw.js?v=10")',
        'navigator.serviceWorker.register("/sw.js?v=13")',
    )

    path.write_text(text, encoding="utf-8")


def patch_assets() -> None:
    index_path = CLOUD / "public" / "index.html"
    text = index_path.read_text(encoding="utf-8")
    text = text.replace(
        '<link rel="manifest" href="/manifest.json?v=11">',
        '<link rel="manifest" href="/api/manifest">',
    )
    text = re.sub(r'/app\\.js\\?v=\\d+', '/app.js?v=13', text)
    index_path.write_text(text, encoding="utf-8")

    sw_path = CLOUD / "public" / "sw.js"
    sw = sw_path.read_text(encoding="utf-8")
    sw = re.sub(
        r'const CACHE = "controle-contas-cloud-v\d+";',
        'const CACHE = "controle-contas-cloud-v13";',
        sw,
    )
    sw_path.write_text(sw, encoding="utf-8")


def write_migration() -> None:
    path = CLOUD / "migrations" / "0003_permanent_device_access.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """CREATE TABLE IF NOT EXISTS authorized_devices (
  token_hash TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  user_agent TEXT NOT NULL DEFAULT '',
  revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_authorized_devices_last_seen
  ON authorized_devices(last_seen_at);

CREATE INDEX IF NOT EXISTS idx_authorized_devices_revoked
  ON authorized_devices(revoked_at);
""",
        encoding="utf-8",
    )


def validate() -> None:
    node = shutil.which("node")
    if node:
        subprocess.run([node, "--check", str(CLOUD / "src" / "index.js")], check=True)
        subprocess.run([node, "--check", str(CLOUD / "public" / "app.js")], check=True)

    worker = (CLOUD / "src" / "index.js").read_text(encoding="utf-8")
    app = (CLOUD / "public" / "app.js").read_text(encoding="utf-8")
    assert "authorized_devices" in worker
    assert "/api/manifest" in worker
    assert "controle_contas_device_token" in app
    assert "removeItem(\"controle_contas_view_token\")" not in app


def main() -> None:
    copy_current_cloud_if_needed()
    patch_worker()
    patch_mobile_js()
    patch_assets()
    write_migration()
    validate()
    print("Acesso mobile permanente aplicado e validado.")


if __name__ == "__main__":
    main()
