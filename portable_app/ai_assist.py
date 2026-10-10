"""Camada opcional SIWC: so completa campos vazios do OCR local."""
from __future__ import annotations
import base64
import ctypes
import hashlib
import hmac
import json
import math
import os
import secrets
import sys
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, parse_qs
from urllib.request import Request, urlopen

AUTH = "https://auth.openai.com"
API = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
FIELDS = ("fornecedor", "pagador", "valor", "vencimento", "codigo_pagamento",
          "documento", "referencia", "tipo_pagamento")
CRITICAL = ("fornecedor", "pagador", "valor", "vencimento", "codigo_pagamento")
LOCK = threading.RLock()

def b64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def get_json(url, form=None, token=None):
    if not url.startswith((AUTH + "/", API + "/")):
        raise ValueError("Destino não autorizado")
    payload = urlencode(form).encode() if form is not None else None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, data=payload, headers=headers), timeout=22) as resp:
        return json.load(resp)

def verify_id_token(jwt, client, nonce):
    chunks = jwt.split(".")
    if len(chunks) != 3:
        raise ValueError("Identidade inválida")
    hdr, claims = json.loads(b64(chunks[0])), json.loads(b64(chunks[1]))
    if hdr.get("alg") != "RS256" or not hdr.get("kid"):
        raise ValueError("Assinatura não suportada")
    uri = get_json(AUTH + "/.well-known/openid-configuration").get("jwks_uri", "")
    if not uri.startswith(AUTH + "/"):
        raise ValueError("Emissor de chave inválido")
    keys = get_json(uri).get("keys", [])
    key = next((k for k in keys if k.get("kid") == hdr["kid"] and
                k.get("kty") == "RSA" and k.get("n") and k.get("e")), None)
    if not key:
        raise ValueError("Chave pública indisponível")
    n, e = int.from_bytes(b64(key["n"]), "big"), int.from_bytes(b64(key["e"]), "big")
    size = (n.bit_length() + 7) // 8
    sig = int.from_bytes(b64(chunks[2]), "big")
    decoded = pow(sig, e, n).to_bytes(size, "big")
    digest = hashlib.sha256((chunks[0] + "." + chunks[1]).encode()).digest()
    der = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    expected = b"\x00\x01" + b"\xff" * (size - len(der) - 3) + b"\x00" + der
    aud = claims.get("aud")
    if not hmac.compare_digest(decoded, expected) or claims.get("iss") != AUTH or \
       not (client in aud if isinstance(aud, list) else aud == client) or \
       claims.get("nonce") != nonce or not claims.get("sub") or \
       float(claims.get("exp") or 0) <= time.time() or \
       float(claims.get("iat") or 0) > time.time() + 120:
        raise ValueError("Validação de identidade falhou")
    return claims

def dpapi(data, *, decode=False):
    """Tokens protegidos pela conta Windows local; nunca vão ao browser."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    original = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(original, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    fn = (ctypes.windll.crypt32.CryptUnprotectData if decode else
          ctypes.windll.crypt32.CryptProtectData)
    if decode:
        ok = fn(ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(output))
    else:
        ok = fn(ctypes.byref(incoming), "Controle de Contas", None, None, None, 0, ctypes.byref(output))
    if not ok:
        raise OSError("Não foi possível proteger credenciais no Windows")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))

def empty(value):
    return value is None or str(value).strip() in ("", "0", "0.0")

def merge_missing(original, suggestions):
    """Nunca sobrescrever leitura existente; validar sugestoes sensíveis."""
    result, filled = dict(original), []
    if not isinstance(suggestions, dict):
        return result, filled
    for field in FIELDS:
        if not empty(original.get(field)):
            continue
        raw = suggestions.get(field)
        if not isinstance(raw, (str, int, float)):
            continue
        value = str(raw).strip()
        if not value or len(value) > 500 or "\n" in value:
            continue
        if field == "valor":
            try:
                number = float(value.replace(",", "."))
                if not math.isfinite(number) or number <= 0:
                    continue
                value = round(number, 2)
            except (TypeError, ValueError):
                continue
        if field == "vencimento":
            try:
                date.fromisoformat(value)
            except ValueError:
                continue
        if field == "codigo_pagamento":
            try:
                from .ocr_hybrid import payment_code
            except ImportError:
                from ocr_hybrid import payment_code
            checked = payment_code(value)
            if not checked:
                continue
            value = checked
        if field in ("pagador", "fornecedor") and len(value) < 3:
            continue
        result[field] = value
        filled.append(field)
    return result, filled

def merge_reviewed(current, suggestions):
    """Revisao solicitada: validar toda sugestao e permitir corrigir OCR existente."""
    result, changed = dict(current), []
    if not isinstance(suggestions, dict):
        return result, changed
    for field in FIELDS:
        if field not in suggestions:
            continue
        sample, valid = merge_missing({field: ""}, {field: suggestions[field]})
        if field not in valid:
            continue
        value = sample[field]
        if str(value).strip() == str(current.get(field) or "").strip():
            continue
        result[field] = value
        changed.append(field)
    return result, changed


class ChatGPTAssist:
    def __init__(self, root: Path, callback: str):
        self.root = Path(root) / "chatgpt_assist"
        self.callback = callback
        self.pending = None
        self.settings = {}
        self.credentials = None
        try:
            self.settings = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
        try:
            raw = (self.root / "credentials.bin").read_bytes()
            if sys.platform == "win32":
                raw = dpapi(raw, decode=True)
            self.credentials = json.loads(raw)
        except (ValueError, OSError):
            pass

    def write(self, file, data):
        self.root.mkdir(parents=True, exist_ok=True)
        temp, dest = self.root / (file + ".tmp"), self.root / file
        try:
            temp.write_bytes(data)
            if os.name != "nt":
                temp.chmod(0o600)
            os.replace(temp, dest)
        finally:
            temp.unlink(missing_ok=True)

    def save_settings(self):
        self.write("settings.json", json.dumps(self.settings).encode())

    def save_tokens(self):
        raw = json.dumps(self.credentials).encode()
        self.write("credentials.bin", dpapi(raw) if sys.platform == "win32" else raw)

    def status(self):
        c = self.credentials or {}
        connected = bool(c.get("access_token") and "chatgpt.tokens.use.direct" in c.get("scopes", []))
        return {"connected": connected, "enabled": bool(connected and self.settings.get("enabled")),
                "consented": bool(self.settings.get("consented")),
                "email": c.get("email") if connected else None}

    def consent(self, enabled):
        with LOCK:
            self.settings["consented"] = bool(enabled)
            self.settings["enabled"] = bool(enabled and self.status()["connected"])
            self.save_settings()
            return self.status()

    def begin(self):
        with LOCK:
            if not self.settings.get("consented"):
                raise ValueError("Autorize o envio de boletos para análise IA.")
            host = self.settings.get("host_id")
            if not host:
                host = "urn:uuid:" + str(uuid.uuid4())
                self.settings["host_id"] = host
                self.save_settings()
            verifier = secrets.token_urlsafe(48)
            state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
            c = self.credentials or {}
            client = c.get("client_id") or "dynamic_agent_client"
            self.pending = dict(client=client, verifier=verifier, state=state,
                                nonce=nonce, expires=time.monotonic() + 300)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            params = dict(client_id=client, ext_agent_host_id=host, response_type="code",
                          redirect_uri=self.callback, resource=API, scope=SCOPES,
                          state=state, nonce=nonce, code_challenge_method="S256",
                          code_challenge=challenge)
            if client == "dynamic_agent_client":
                params["agent_name_hint"] = "Controle de Contas"
            elif c.get("id_token"):
                params["id_token_hint"] = c["id_token"]
            return {"url": AUTH + "/api/accounts/authorize?" + urlencode(params)}

    def finish(self, query):
        args = parse_qs(query)
        with LOCK:
            state = self.pending
            self.pending = None
            if not state or time.monotonic() > state["expires"] or \
               not hmac.compare_digest(args.get("state", [""])[0], state["state"]):
                raise ValueError("Tentativa de conexão expirada ou inválida.")
            if args.get("error"):
                raise ValueError("Autorização não concedida.")
            issued = args.get("client_id", [""])[0]
            client = state["client"]
            if client == "dynamic_agent_client":
                if not issued.startswith("oaiapp_"):
                    raise ValueError("ChatGPT não registrou o aplicativo.")
                client = issued
            elif issued and issued != client:
                raise ValueError("A identidade da conexão mudou.")
            code = args.get("code", [""])[0]
            if not code:
                raise ValueError("Autorização incompleta.")
            tokens = get_json(AUTH + "/api/accounts/oauth/token", form={
                "grant_type": "authorization_code", "client_id": client,
                "code": code, "code_verifier": state["verifier"],
                "redirect_uri": self.callback, "resource": API,
            })
            identity = verify_id_token(tokens["id_token"], client, state["nonce"])
            scopes = (tokens.get("scope") or "").split()
            if "chatgpt.tokens.use.direct" not in scopes:
                raise ValueError("Uso do plano ChatGPT não autorizado para este aplicativo.")
            if self.credentials and self.credentials.get("client_id") == client and \
               self.credentials.get("subject") != identity["sub"]:
                raise ValueError("Esta não é a conta anteriormente conectada.")
            self.credentials = dict(client_id=client, subject=identity["sub"],
                                    email=identity.get("email"), id_token=tokens["id_token"],
                                    access_token=tokens["access_token"],
                                    refresh_token=tokens.get("refresh_token"),
                                    scopes=scopes,
                                    expires=time.time() + int(tokens.get("expires_in") or 3600))
            self.save_tokens()
            self.settings["enabled"] = True
            self.save_settings()
            return self.status()

    def access_token(self):
        with LOCK:
            c = self.credentials or {}
            if not self.status()["enabled"]:
                return None
            if c.get("expires", 0) > time.time() + 90:
                return c["access_token"]
            if not c.get("refresh_token"):
                return None
            tokens = get_json(AUTH + "/api/accounts/oauth/token", form={
                "grant_type": "refresh_token", "client_id": c["client_id"],
                "refresh_token": c["refresh_token"], "resource": API,
            })
            scopes = (tokens.get("scope") or "").split()
            if "chatgpt.tokens.use.direct" not in scopes:
                return None
            c.update(access_token=tokens["access_token"],
                     refresh_token=tokens.get("refresh_token") or c["refresh_token"],
                     scopes=scopes, expires=time.time() + int(tokens.get("expires_in") or 3600))
            self.save_tokens()
            return c["access_token"]

    def disconnect(self):
        with LOCK:
            self.credentials = None
            (self.root / "credentials.bin").unlink(missing_ok=True)
            self.settings["enabled"] = False
            self.settings["consented"] = False
            self.save_settings()
            return self.status()

    def ask(self, pdf_path: Path, fields: dict, missing: list[str]):
        import fitz
        token = self.access_token()
        if not token:
            return {}
        models = get_json(API + "/models", token=token).get("models", [])
        names = [str(m.get("slug") or "") for m in models if isinstance(m, dict)]
        model = next((m for m in ("gpt-6.1-sol", "gpt-6", "gpt-5.6") if m in names), None)
        if model is None:
            model = next((m for m in names if m.startswith("gpt-")), None)
        if not model:
            return {}
        instructions = (
            "Analise todas as informações do boleto brasileiro em imagens. "
            "O usuário solicitou CORRIGIR COM GPT. Verifique todos os campos, " +
            "inclusive os já preenchidos pelo OCR, pois podem estar errados. " +
            "Campos que merecem atenção: " + ", ".join(missing) +
            ". Retorne APENAS JSON com objeto fields, escolhendo somente: " +
            ", ".join(FIELDS) + ". Se um campo não aparecer claramente, deixe vazio. "
            "Nunca invente datas/nomes/valores. Fornecedor é BENEFICIÁRIO; "
            "pagador é PAGADOR. Vencimento não é emissão. "
            "Valor em número decimal e data ISO AAAA-MM-DD. "
            "Transcreva código PIX/barras exatamente, sem adivinhar. "
            "Retorne apenas informações visualmente confirmadas, com alta certeza. " +
            "Nunca invente ou copie valores de exemplo. Formulário atual: " +
            json.dumps(fields, ensure_ascii=False)
        )
        content = [{"type": "input_text", "text": instructions}]
        with fitz.open(str(pdf_path)) as document:
            for page in list(document)[:2]:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
                image = pix.tobytes("jpeg", jpg_quality=68)
                content.append({"type": "input_image", "image_url":
                                "data:image/jpeg;base64," + base64.b64encode(image).decode()})
        payload = json.dumps(dict(model=model, store=False, stream=True,
                                  input=[{"role": "user", "content": content}]),
                             ensure_ascii=False).encode()
        req = Request(API + "/responses", data=payload, method="POST", headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json", "Accept": "text/event-stream",
        })
        chunks, completed = [], False
        with urlopen(req, timeout=85) as resp:
            for line in resp:
                if not line.startswith(b"data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except (ValueError, UnicodeError):
                    continue
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    chunks.append(str(event.get("delta") or ""))
                    if sum(map(len, chunks)) > 16000:
                        return {}
                if kind in ("response.failed", "response.incomplete"):
                    return {}
                if kind == "response.completed":
                    completed = True
                    break
        if not completed:
            return {}
        answer = "".join(chunks).strip()
        # Alguns modelos cercam o JSON com Markdown mesmo quando instruidos
        # a devolver apenas JSON. Nunca executar texto, apenas decodificar.
        begin, end = answer.find("{"), answer.rfind("}")
        if begin < 0 or end <= begin:
            return {}
        response = json.loads(answer[begin:end + 1])
        return response.get("fields", {}) if isinstance(response, dict) and isinstance(
            response.get("fields"), dict
        ) else {}

    def correct(self, path: Path, current: dict):
        """Somente mediante clique: não grava, não treina, não altera o PDF."""
        if not self.status()["enabled"] or not self.status()["consented"]:
            raise ValueError("Conecte e autorize o ChatGPT antes de corrigir um boleto.")
        values = {k: current.get(k, "") for k in FIELDS}
        requested = [k for k in FIELDS if empty(values[k])]
        if not requested:
            requested = list(FIELDS)
        proposed = self.ask(Path(path), values, requested)
        updated, changed = merge_reviewed(values, proposed)
        return {"fields": updated, "changed": changed,
                "message": ("Confira as sugestões e salve para ensinar o OCR."
                            if changed else
                            "O GPT não encontrou alterações seguras. Confira o PDF.")}

    def enhance(self, path: Path, baseline: dict):
        if not self.status()["enabled"] or not isinstance(baseline.get("fields"), dict):
            return baseline
        fields = baseline["fields"]
        missing = [k for k in CRITICAL if empty(fields.get(k))]
        if not missing:
            return baseline
        try:
            merged, filled = merge_missing(fields, self.ask(path, fields, missing))
        except Exception:
            return baseline  # offline, limite do plano, falha: OCR íntegro
        if not filled:
            return baseline
        result = dict(baseline)
        result["fields"] = merged
        result["ai_assistance"] = {"filled": filled, "used": True}
        unsafe = set(baseline.get("insecure_fields") or [])
        for key in filled:
            unsafe.discard("recebedor" if key == "fornecedor" else key)
        result["insecure_fields"] = sorted(unsafe)
        return result
