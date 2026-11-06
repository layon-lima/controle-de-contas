"""Envio OPCIONAL de cards de pagamento pela API oficial do WhatsApp.

Usa apenas a Cloud API da Meta e templates PAYMENT_REQUEST aprovados.
Nenhum envio automatico; requer numero, opt-in e clique explicito do usuario.
Credenciais apenas no volume persistente da VPS, nunca no GitHub.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CONFIG_NAME = "whatsapp-api.json"
VERSION = "v26.0"


class WhatsAppNotConfigured(RuntimeError):
    pass


def _read_config(app_dir: Path):
    path = Path(app_dir) / CONFIG_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    phone_id = str(raw.get("phone_number_id") or "").strip()
    token = str(raw.get("access_token") or "").strip()
    version = str(raw.get("api_version") or VERSION).strip()
    if not re.fullmatch(r"[0-9]{5,25}", phone_id) or not token:
        return None
    if not re.fullmatch(r"v[0-9]{1,2}[.][0-9]{1,2}", version):
        return None
    templates = {}
    for mode in ("boleto", "pix"):
        name = str(raw.get("template_" + mode) or "").strip()
        if re.fullmatch(r"[a-z0-9_]{1,128}", name):
            templates[mode] = name
    if not templates:
        return None
    return {"phone_id": phone_id, "token": token, "version": version,
            "templates": templates}


def status(app_dir: Path):
    cfg = _read_config(app_dir)
    return {"configured": cfg is not None,
            "modes": list(cfg["templates"]) if cfg else []}


def normalize_phone(number):
    digits = re.sub(r"\D", "", str(number or ""))
    if len(digits) in (10, 11):
        digits = "55" + digits
    if not digits.startswith("55") or len(digits) not in (12, 13):
        raise ValueError("Informe um WhatsApp do Brasil com DDD (ex.: 11999999999).")
    return digits


def payment_method(code):
    value = str(code or "").strip()
    digits = re.sub(r"[^0-9]", "", value)
    if value.startswith("000201") and len(value) >= 50 and len(value) <= 1024:
        return "pix", {"type": "pix_dynamic_code",
                       "pix_dynamic_code": {"code": value}}
    if digits.isdigit() and len(digits) in (47, 48) and re.fullmatch(r"[0-9. \-]+", value):
        return "boleto", {"type": "boleto", "boleto": {"digitable_line": digits}}
    raise ValueError(
        "O card oficial exige PIX copia e cola dinamico ou linha digitavel "
        "de boleto com 47/48 digitos. Use o envio de texto para outros codigos."
    )


def format_amount(value):
    amount = float(value or 0)
    if not 0 < amount < 1_000_000_000:
        raise ValueError("Valor de pagamento invalido.")
    return "R$ " + f"{amount:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def message_payload(account, recipient, config):
    mode, payment_setting = payment_method(account.get("codigo_pagamento"))
    template = config["templates"].get(mode)
    if not template:
        raise WhatsAppNotConfigured(
            "Falta aprovar/configurar o modelo WhatsApp para " + mode.upper() + "."
        )
    observation = re.sub(r"(?i)\.pdf\s*$", "", str(account.get("observacoes") or "")).strip()
    recipient_name = str(account.get("fornecedor") or "").strip()
    if not recipient_name:
        raise ValueError("Conta sem recebedor.")
    fields = [recipient_name, format_amount(account.get("valor")),
              observation or "Pagamento de boleto"]
    return {
        "messaging_product": "whatsapp",
        "to": normalize_phone(recipient),
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": "pt_BR"},
            "components": [
                {"type": "body", "parameters": [
                    {"type": "text", "text": x} for x in fields
                ]},
                {"type": "button", "sub_type": "payment_request", "index": "0",
                 "parameters": [{"type": "action", "action": {
                    "payment_request": {"payment_setting": payment_setting}
                }}]},
            ],
        },
    }


def send(app_dir: Path, account: dict, recipient: str, *, opt_in=False,
         opener=urlopen):
    if not opt_in:
        raise ValueError("Confirme que o destinatario autorizou mensagens de pagamento.")
    config = _read_config(app_dir)
    if not config:
        raise WhatsAppNotConfigured(
            "WhatsApp API ainda nao conectado. Ative a coexistencia e configure os "
            "modelos de pagamento aprovados pela Meta."
        )
    payload = message_payload(account, recipient, config)
    endpoint = (f"https://graph.facebook.com/{config['version']}/"
                f"{config['phone_id']}/messages")
    request = Request(endpoint, method="POST",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Authorization": "Bearer " + config["token"],
                               "Content-Type": "application/json",
                               "Accept": "application/json"})
    try:
        with opener(request, timeout=22) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Nunca devolver header Authorization, token ou dados do boleto.
        try:
            reason = json.loads(exc.read(8192)).get("error", {})
            message = str(reason.get("message") or "modelo nao aceito pela Meta")
            code = reason.get("code")
        except (ValueError, AttributeError, UnicodeError):
            message, code = "A Meta recusou a mensagem", exc.code
        raise RuntimeError(f"WhatsApp API: {message} (codigo {code}).") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("WhatsApp API indisponivel. Verifique a conexao e tente novamente.") from exc
    messages = result.get("messages") if isinstance(result, dict) else None
    msg_id = messages[0].get("id") if isinstance(messages, list) and messages else None
    if not msg_id:
        raise RuntimeError("A Meta nao confirmou o recebimento da mensagem.")
    return {"accepted": True, "message_id": msg_id, "mode": (
        "pix" if "pix_dynamic_code" in str(payload["template"]["components"][1]) else "boleto"
    )}
