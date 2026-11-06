"""Compartilhamento WhatsApp por link privado, sem API Meta.

O código de pagamento fica visível somente a quem receber o endereço assinado.
Links expiram em 30 dias e deixam de funcionar quando a conta é paga/excluída.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import re
import secrets
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

TTL_SECONDS = 30 * 24 * 60 * 60
SECRET_NAME = ".payment-share-key"


def _key(app_dir: Path):
    directory = Path(app_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SECRET_NAME
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "wb") as target:
            target.write(secrets.token_bytes(32))
    data = path.read_bytes()
    if len(data) != 32:
        raise RuntimeError("Chave de compartilhamento indisponível.")
    return data


def create_token(app_dir: Path, account_id: int, now=None):
    account_id = int(account_id)
    if account_id <= 0:
        raise ValueError("Identificador da conta inválido.")
    expires = int(time.time() if now is None else now) + TTL_SECONDS
    payload = f"{account_id}.{expires}.{secrets.token_urlsafe(18)}"
    signature = hmac.new(_key(app_dir), payload.encode("ascii"), hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return payload + "." + signed


def read_token(app_dir: Path, token: str, now=None):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{65,150}", token or ""):
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None
    number, expiry, nonce, signature = parts
    if not re.fullmatch(r"[0-9]{1,15}", number) or not re.fullmatch(r"[0-9]{10,12}", expiry):
        return None
    if len(nonce) < 20 or len(signature) != 43:
        return None
    if int(expiry) < int(time.time() if now is None else now):
        return None
    payload = ".".join(parts[:3])
    expected = base64.urlsafe_b64encode(
        hmac.new(_key(app_dir), payload.encode("ascii"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    if not hmac.compare_digest(expected, signature):
        return None
    return int(number) if int(number) > 0 else None


def share_origin():
    configured = (os.environ.get("CONTAS_SHARE_ORIGIN")
                  or os.environ.get("CONTAS_PUBLIC_ORIGIN") or "").strip().rstrip("/")
    if not configured:
        configured = ("https://contas-cloud.fazendanovohorizonte.com"
                      if os.environ.get("CONTAS_PORTABLE_HOST") == "0.0.0.0"
                      else "http://127.0.0.1:8767")
    result = urlsplit(configured)
    if result.scheme not in ("http", "https") or not result.hostname or result.username:
        raise ValueError("Origem pública de compartilhamento inválida.")
    return configured


def share_url(token, origin=None):
    return (origin or share_origin()).rstrip("/") + "/p/" + token


def clean_observation(raw):
    return re.sub(r"(?i)\.pdf\s*$", "", str(raw or "")).strip()


def display_amount(value):
    amount = float(value or 0)
    return "R$ " + f"{amount:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def share_page(account, link):
    """O WhatsApp lê as meta-tags no HTML, sem executar JavaScript."""
    e = lambda value: html.escape(str(value), quote=True)
    receiver = str(account.get("fornecedor") or "Pagamento")
    description = clean_observation(account.get("observacoes")) or "Dados para pagamento"
    amount = display_amount(account.get("valor"))
    code = str(account.get("codigo_pagamento") or "").strip()
    image_url = share_origin() + "/payment-share-preview.png"
    title = receiver + " • " + amount
    meta = f"{description} — Novo Horizonte"
    return f"""<!doctype html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>{e(title)}</title>
<meta name="description" content="{e(meta)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Novo Horizonte">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(meta)}">
<meta property="og:url" content="{e(link)}">
<meta property="og:image" content="{e(image_url)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<link rel="stylesheet" href="/payment-share.css">
</head><body>
<main class="wrapper"><section class="card">
<div class="brand"><span class="brand-mark">NH</span> NOVO HORIZONTE</div>
<div class="eyebrow">DADOS PARA PAGAMENTO</div>
<h1>{e(receiver)}</h1>
<div class="amount">{e(amount)}</div>
<p class="description">{e(description)}</p>
<div class="code-title">PIX / LINHA DIGITÁVEL</div>
<pre id="payment-code">{e(code)}</pre>
<button id="copy-code" type="button">Copiar código de pagamento</button>
<p id="copy-status" role="status" aria-live="polite"></p>
</section>
<p class="footer">Confira o favorecido e o valor no aplicativo do seu banco antes de pagar.</p>
</main><script src="/payment-share.js" defer></script>
</body></html>""".encode("utf-8")


def preview_png():
    """Imagem genérica pública; nunca inclui código, titular ou documento."""
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1200, 630), "#101d32")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1200, 22), fill="#14a575")
    draw.rounded_rectangle((66, 85, 1134, 553), radius=36, fill="#192b45")
    draw.rounded_rectangle((113, 135, 242, 264), radius=30, fill="#14a575")
    def font(size):
        for name in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                     "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()
    draw.text((137, 170), "NH", fill="white", font=font(62))
    draw.text((275, 159), "NOVO HORIZONTE", fill="white", font=font(56))
    draw.text((115, 327), "DADOS PARA PAGAMENTO", fill="#eff8ff", font=font(52))
    draw.text((115, 420), "PIX  /  BOLETO", fill="#8de2c1", font=font(34))
    out = BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()
