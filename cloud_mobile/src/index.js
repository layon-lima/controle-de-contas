const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status,
  headers: {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  },
});

function secureEqual(a, b) {
  a = String(a ?? "");
  b = String(b ?? "");
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function authorized(request, expected, header) {
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

function statusConta(vencimento, dataPagamento) {
  if (String(dataPagamento || "").trim()) return "Paga";
  const text = String(vencimento || "").slice(0, 10);
  const m = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return "Em dia";

  const due = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const days = Math.floor((due - today) / 86400000);
  if (days < 0) return "Vencida";
  if (days <= 5) return "Próxima";
  return "Em dia";
}

async function getMeta(env, key) {
  const row = await env.DB.prepare("SELECT value FROM app_meta WHERE key = ?").bind(key).first();
  return row?.value || null;
}

async function setMeta(env, key, value) {
  await env.DB.prepare(
    "INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
  ).bind(key, String(value ?? "")).run();
}

async function syncAccounts(request, env) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);

  let body;
  try { body = await request.json(); } catch { return json({ok:false, erro:"JSON inválido."}, 400); }
  const accounts = Array.isArray(body?.accounts) ? body.accounts : [];
  const syncedAt = String(body?.synced_at || new Date().toISOString());
  if (accounts.length > 10000) return json({ok:false, erro:"Quantidade de contas inválida."}, 400);

  const existingResult = await env.DB.prepare("SELECT id,pdf_hash,pdf_pages FROM accounts").all();
  const existing = new Map((existingResult.results || []).map(r => [Number(r.id), r]));
  const incomingIds = new Set();
  const needPdfs = [];
  const statements = [];

  for (const raw of accounts) {
    const id = Number(raw?.id);
    if (!Number.isInteger(id) || id <= 0) continue;
    incomingIds.add(id);

    const pdfHash = String(raw?.pdf_hash || "");
    const pdfName = String(raw?.pdf_name || "").slice(0, 300);
    const old = existing.get(id);
    const keepPages = old && String(old.pdf_hash || "") === pdfHash ? Number(old.pdf_pages || 0) : 0;
    if (pdfHash && (!old || String(old.pdf_hash || "") !== pdfHash || keepPages < 1)) needPdfs.push(id);

    const payload = {...raw};
    delete payload.pdf_hash;
    delete payload.pdf_name;

    statements.push(env.DB.prepare(`
      INSERT INTO accounts(id,payload,pdf_hash,pdf_name,pdf_pages,updated_at)
      VALUES(?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        payload=excluded.payload,
        pdf_hash=excluded.pdf_hash,
        pdf_name=excluded.pdf_name,
        pdf_pages=CASE WHEN accounts.pdf_hash=excluded.pdf_hash THEN accounts.pdf_pages ELSE 0 END,
        updated_at=excluded.updated_at
    `).bind(id, JSON.stringify(payload), pdfHash, pdfName, keepPages, syncedAt));
  }

  for (const [id, old] of existing) {
    if (!incomingIds.has(id)) {
      statements.push(env.DB.prepare("DELETE FROM payment_activity WHERE account_id = ?").bind(id));
      statements.push(env.DB.prepare("DELETE FROM accounts WHERE id = ?").bind(id));
      const pages = Number(old.pdf_pages || 0);
      const keys = [`accounts/${id}/original.pdf`];
      for (let page = 1; page <= pages; page++) keys.push(`accounts/${id}/pages/${page}.jpg`);
      if (keys.length) await env.PDFS.delete(keys);
    }
  }

  if (statements.length) {
    for (let i = 0; i < statements.length; i += 80) await env.DB.batch(statements.slice(i, i + 80));
  }
  await setMeta(env, "last_synced", syncedAt);
  return json({ok:true, quantidade:incomingIds.size, need_pdfs:needPdfs});
}

async function uploadPdf(request, env, id) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);
  const row = await env.DB.prepare("SELECT pdf_pages FROM accounts WHERE id = ?").bind(id).first();
  if (!row) return json({ok:false, erro:"Conta não encontrada."}, 404);

  const hash = String(request.headers.get("X-Pdf-Hash") || "");
  const name = String(request.headers.get("X-Pdf-Name") || `conta-${id}.pdf`).slice(0, 300);
  const pages = Math.max(0, Number(request.headers.get("X-Pdf-Pages") || 0));

  await env.PDFS.put(`accounts/${id}/original.pdf`, request.body, {
    httpMetadata: {contentType: "application/pdf"},
    customMetadata: {hash, name},
  });
  await env.DB.prepare("UPDATE accounts SET pdf_hash=?, pdf_name=? WHERE id=?").bind(hash, name, id).run();
  return json({ok:true, pages});
}

async function uploadPage(request, env, id, page) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);
  if (!Number.isInteger(page) || page < 1 || page > 5000) return json({ok:false, erro:"Página inválida."}, 400);
  const row = await env.DB.prepare("SELECT id FROM accounts WHERE id = ?").bind(id).first();
  if (!row) return json({ok:false, erro:"Conta não encontrada."}, 404);
  await env.PDFS.put(`accounts/${id}/pages/${page}.jpg`, request.body, {
    httpMetadata: {contentType: "image/jpeg"},
  });
  return json({ok:true});
}

async function completePdf(request, env, id) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);
  let body;
  try { body = await request.json(); } catch { return json({ok:false, erro:"JSON inválido."}, 400); }
  const pages = Math.max(0, Math.min(5000, Number(body?.pages || 0)));
  const hash = String(body?.hash || "");
  const name = String(body?.name || "").slice(0, 300);
  await env.DB.prepare("UPDATE accounts SET pdf_pages=?, pdf_hash=?, pdf_name=? WHERE id=?")
    .bind(pages, hash, name, id).run();
  return json({ok:true});
}

async function listAccounts(request, env) {
  if (!await viewAuthorized(request, env)) return json({ok:false, erro:"Não autorizado."}, 401);
  const result = await env.DB.prepare(`
    SELECT
      a.id,
      a.payload,
      a.pdf_pages,
      p.suggested_at,
      p.suggested_until,
      p.copied_at,
      p.copied_until,
      p.copy_count
    FROM accounts a
    LEFT JOIN payment_activity p ON p.account_id = a.id
  `).all();
  const contas = [];
  const now = Date.now();
  for (const row of result.results || []) {
    try {
      const item = JSON.parse(row.payload);
      const suggestedUntilMs = Date.parse(String(row.suggested_until || ""));
      const copiedAtMs = Date.parse(String(row.copied_at || ""));
      const copiedMobileUntilMs = Number.isFinite(copiedAtMs)
        ? copiedAtMs + 15 * 60 * 1000
        : NaN;
      item.tem_pdf = Number(row.pdf_pages || 0) > 0;
      item.status = statusConta(item.vencimento, item.data_pagamento);
      item.sugestao_pagamento = item.status !== "Paga" && Number.isFinite(suggestedUntilMs) && suggestedUntilMs > now;
      item.sugerido_em = row.suggested_at || null;
      item.sugestao_ate = item.sugestao_pagamento ? row.suggested_until : null;
      item.codigo_copiado_recente = item.status !== "Paga" && Number.isFinite(copiedMobileUntilMs) && copiedMobileUntilMs > now;
      item.codigo_copiado_em = row.copied_at || null;
      item.codigo_copiado_ate = item.codigo_copiado_recente
        ? new Date(copiedMobileUntilMs).toISOString()
        : null;
      contas.push(item);
    } catch {}
  }
  contas.sort((a,b) => {
    const sa = a.sugestao_pagamento ? 0 : 1;
    const sb = b.sugestao_pagamento ? 0 : 1;
    if (sa !== sb) return sa - sb;
    const pa = a.status === "Paga" ? 1 : 0;
    const pb = b.status === "Paga" ? 1 : 0;
    if (pa !== pb) return pa - pb;
    const da = String(a.vencimento || "9999-12-31");
    const db = String(b.vencimento || "9999-12-31");
    if (da !== db) return da.localeCompare(db);
    return Number(b.id || 0) - Number(a.id || 0);
  });
  return json({
    ok:true,
    quantidade:contas.length,
    contas,
    meta:{ultima_atualizacao: await getMeta(env, "last_synced")},
  });
}

async function accountRow(env, id) {
  return env.DB.prepare("SELECT id,payload FROM accounts WHERE id = ?").bind(id).first();
}

async function suggestPayment(request, env, id) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);
  const row = await accountRow(env, id);
  if (!row) return json({ok:false, erro:"Conta não encontrada."}, 404);

  try {
    const item = JSON.parse(row.payload || "{}");
    if (statusConta(item.vencimento, item.data_pagamento) === "Paga") {
      return json({ok:false, erro:"Conta já está paga."}, 409);
    }
  } catch {}

  const suggestedAt = new Date().toISOString();
  const suggestedUntil = new Date(Date.now() + 3 * 60 * 60 * 1000).toISOString();
  await env.DB.prepare(`
    INSERT INTO payment_activity(account_id,suggested_at,suggested_until)
    VALUES(?,?,?)
    ON CONFLICT(account_id) DO UPDATE SET
      suggested_at=excluded.suggested_at,
      suggested_until=excluded.suggested_until
  `).bind(id, suggestedAt, suggestedUntil).run();

  return json({ok:true, account_id:id, suggested_at:suggestedAt, suggested_until:suggestedUntil});
}

async function suggestPaymentsBatch(request, env) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) {
    return json({ok:false, erro:"Não autorizado."}, 401);
  }

  const body = await request.json().catch(() => ({}));
  const ids = [...new Set(
    (Array.isArray(body?.account_ids) ? body.account_ids : [])
      .map(Number)
      .filter(id => Number.isInteger(id) && id > 0)
  )].slice(0, 500);

  if (!ids.length) {
    return json({ok:true, quantidade:0, account_ids:[]});
  }

  const suggestedAt = new Date().toISOString();
  const suggestedUntil = new Date(Date.now() + 3 * 60 * 60 * 1000).toISOString();

  const statements = ids.map(id => env.DB.prepare(`
    INSERT INTO payment_activity(account_id,suggested_at,suggested_until)
    SELECT ?,?,?
     WHERE EXISTS (SELECT 1 FROM accounts WHERE id=?)
    ON CONFLICT(account_id) DO UPDATE SET
      suggested_at=excluded.suggested_at,
      suggested_until=excluded.suggested_until
  `).bind(id, suggestedAt, suggestedUntil, id));

  if (statements.length) await env.DB.batch(statements);

  return json({
    ok:true,
    quantidade:ids.length,
    account_ids:ids,
    suggested_at:suggestedAt,
    suggested_until:suggestedUntil,
  });
}


async function cancelSuggestion(request, env, id) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);
  await env.DB.prepare(`
    UPDATE payment_activity
       SET suggested_at=NULL, suggested_until=NULL
     WHERE account_id=?
  `).bind(id).run();
  return json({ok:true, account_id:id});
}

async function markPaymentCodeCopied(request, env, id) {
  if (!await viewAuthorized(request, env)) return json({ok:false, erro:"Não autorizado."}, 401);
  const row = await accountRow(env, id);
  if (!row) return json({ok:false, erro:"Conta não encontrada."}, 404);

  try {
    const item = JSON.parse(row.payload || "{}");
    if (statusConta(item.vencimento, item.data_pagamento) === "Paga") {
      return json({ok:false, erro:"Conta já está paga."}, 409);
    }
  } catch {}

  const copiedAt = new Date().toISOString();
  const copiedUntil = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
  await env.DB.prepare(`
    INSERT INTO payment_activity(account_id,copied_at,copied_until,copy_count)
    VALUES(?,?,?,1)
    ON CONFLICT(account_id) DO UPDATE SET
      copied_at=excluded.copied_at,
      copied_until=excluded.copied_until,
      copy_count=COALESCE(payment_activity.copy_count,0)+1
  `).bind(id, copiedAt, copiedUntil).run();

  const mobileCopiedUntil = new Date(Date.now() + 15 * 60 * 1000).toISOString();
  return json({
    ok:true,
    account_id:id,
    copied_at:copiedAt,
    copied_until:copiedUntil,
    mobile_copied_until:mobileCopiedUntil,
  });
}

async function desktopActivity(request, env) {
  if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);
  const now = new Date().toISOString();
  const result = await env.DB.prepare(`
    SELECT account_id,suggested_at,suggested_until,copied_at,copied_until,copy_count
      FROM payment_activity
     WHERE suggested_at IS NOT NULL
        OR copied_at IS NOT NULL
     ORDER BY COALESCE(copied_at,suggested_at) DESC
  `).all();
  return json({ok:true, activities:result.results || [], server_time:now});
}

async function pdfMeta(request, env, id) {
  if (!await viewAuthorized(request, env)) return json({ok:false, erro:"Não autorizado."}, 401);
  const row = await env.DB.prepare("SELECT pdf_name,pdf_pages FROM accounts WHERE id=?").bind(id).first();
  if (!row || Number(row.pdf_pages || 0) < 1) return json({ok:false, erro:"PDF não encontrado para esta conta."}, 404);
  return json({ok:true, paginas:Number(row.pdf_pages), nome:row.pdf_name || `conta-${id}.pdf`});
}

async function pdfPage(request, env, id, page) {
  if (!await viewAuthorized(request, env)) return json({ok:false, erro:"Não autorizado."}, 401);
  const object = await env.PDFS.get(`accounts/${id}/pages/${page}.jpg`);
  if (!object) return json({ok:false, erro:"Página não encontrada."}, 404);
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("content-type", "image/jpeg");
  headers.set("cache-control", "private, max-age=300");
  headers.set("x-content-type-options", "nosniff");
  return new Response(object.body, {headers});
}

async function originalPdf(request, env, id) {
  if (!await viewAuthorized(request, env)) return json({ok:false, erro:"Não autorizado."}, 401);
  const object = await env.PDFS.get(`accounts/${id}/original.pdf`);
  if (!object) return json({ok:false, erro:"PDF não encontrado."}, 404);
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set("content-type", "application/pdf");
  headers.set("content-disposition", `inline; filename="conta-${id}.pdf"`);
  headers.set("cache-control", "private, max-age=300");
  return new Response(object.body, {headers});
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method.toUpperCase();

    try {
      if (path === "/api/health") return json({ok:true, cloud:true});
      if (path === "/api/session" && method === "POST") return await startViewSession(request, env);
      if (path === "/api/manifest" && method === "GET") return await mobileManifest(request, env);
      if (path === "/api/contas" && method === "GET") return listAccounts(request, env);
      if (path === "/api/desktop/activity" && method === "GET") return desktopActivity(request, env);
      if (path === "/api/sync/accounts" && method === "POST") return syncAccounts(request, env);
      if (path === "/api/sync/heartbeat" && method === "POST") {
        if (!authorized(request, env.SYNC_TOKEN, "X-Sync-Token")) return json({ok:false, erro:"Não autorizado."}, 401);
        const body = await request.json().catch(() => ({}));
        await setMeta(env, "last_synced", body.synced_at || new Date().toISOString());
        return json({ok:true});
      }

      if (path === "/api/desktop/suggest-batch" && method === "POST") {
        return suggestPaymentsBatch(request, env);
      }

      let m = path.match(/^\/api\/desktop\/suggest\/(\d+)$/);
      if (m && method === "POST") return suggestPayment(request, env, Number(m[1]));
      if (m && method === "DELETE") return cancelSuggestion(request, env, Number(m[1]));

      m = path.match(/^\/api\/mobile\/copied\/(\d+)$/);
      if (m && method === "POST") return markPaymentCodeCopied(request, env, Number(m[1]));

      m = path.match(/^\/api\/sync\/pdf\/(\d+)$/);
      if (m && method === "PUT") return uploadPdf(request, env, Number(m[1]));
      m = path.match(/^\/api\/sync\/pdf\/(\d+)\/page\/(\d+)$/);
      if (m && method === "PUT") return uploadPage(request, env, Number(m[1]), Number(m[2]));
      m = path.match(/^\/api\/sync\/pdf\/(\d+)\/complete$/);
      if (m && method === "POST") return completePdf(request, env, Number(m[1]));

      m = path.match(/^\/api\/pdf\/(\d+)\/meta$/);
      if (m && method === "GET") return pdfMeta(request, env, Number(m[1]));
      m = path.match(/^\/api\/pdf\/(\d+)\/page\/(\d+)$/);
      if (m && method === "GET") return pdfPage(request, env, Number(m[1]), Number(m[2]));
      m = path.match(/^\/api\/pdf\/(\d+)$/);
      if (m && method === "GET") return originalPdf(request, env, Number(m[1]));

      if (path.startsWith("/api/")) return json({ok:false, erro:"Rota não encontrada."}, 404);
      return env.ASSETS.fetch(request);
    } catch (error) {
      return json({ok:false, erro:"Erro interno na nuvem.", detalhe:String(error?.message || error)}, 500);
    }
  }
};
