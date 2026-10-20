#!/usr/bin/env python3
"""Configura R2 existente no Controle de Contas, sem salvar chaves no Git.

A conta Cloudflare/R2 deve fornecer Account ID, Access Key ID e Secret Access Key
de um R2 API token com acesso ao bucket controle-contas-pdfs.
"""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys

DEST = Path("/opt/apps/contas-runtime/r2-agent.json")
AGENT_TOKEN = Path("/opt/apps/contas-private/r2-agent-token")
BUCKET = "controle-contas-pdfs"


def write_secure(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        raise RuntimeError("Credenciais ja existem. Nao sobrescrevi nada.")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Execute como root na VPS.")
    if DEST.exists() or AGENT_TOKEN.exists():
        print("R2 ja configurado: credenciais preservadas, nao alterei.")
        print("Para habilitar no aplicativo, execute o script ATIVAR_R2_PDFS.sh.")
        return

    print("CONFIGURAR R2 (o banco e os PDFs do Windows nao serao alterados)")
    print("Bucket:", BUCKET)
    account = input("Cloudflare Account ID (32 caracteres hex): ").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", account):
        raise RuntimeError("Account ID invalido.")
    key = input("R2 Access Key ID: ").strip()
    secret = getpass.getpass("R2 Secret Access Key (nao aparece na tela): ").strip()
    if not key or len(secret) < 20:
        raise RuntimeError("Credenciais incompletas.")
    token = secrets.token_urlsafe(48)
    obj = {
        "account_id": account,
        "access_key_id": key,
        "secret_access_key": secret,
        "bucket": BUCKET,
        "agent_token": token,
    }
    write_secure(DEST, json.dumps(obj, ensure_ascii=False))
    write_secure(AGENT_TOKEN, token + "\n")
    print("R2 configurado em arquivo privado da VPS.")
    print("Token do agente Windows gerado; nenhum segredo foi mostrado.")
    print("PROXIMO PASSO: bash deploy/vps/ATIVAR_R2_PDFS.sh")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("CONFIGURACAO R2 NAO CONCLUIDA:", exc, file=sys.stderr)
        raise SystemExit(1)
