"""Sincroniza apenas PDFs de contas abertas do Windows para a VPS/R2.

Banco da VPS e autoridade: este programa LE o SQLite do PC sem alterar nada.
R2 credenciais nunca ficam no Windows; usa token de agente separado.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.error
import urllib.request

APP = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ControleContas"
CONFIG = APP / "r2-pdf-agent.json"
DATABASE = APP / "data" / "controle_contas.db"
MAX_SIZE = 30 * 1024 * 1024


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Agente R2 nao configurado. Instale antes de sincronizar.") from exc
    base = str(cfg.get("base_url", "")).rstrip("/")
    token = str(cfg.get("agent_token", ""))
    if base != "https://contas-cloud.fazendanovohorizonte.com" or len(token) < 32:
        raise RuntimeError("Endereco ou token do agente invalido.")
    return {"base": base, "token": token}


def request(cfg, route: str, data=None, digest=""):
    headers = {
        "User-Agent": "ControleContas-PDF-Windows/1",
        "X-Agent-Token": cfg["token"],
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/pdf"
        headers["X-Pdf-Sha256"] = digest
    req = urllib.request.Request(cfg["base"] + route, headers=headers,
                                 data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            obj = json.loads(resp.read(200000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"VPS retornou HTTP {exc.code} para {route}.") from exc
    if obj.get("ok") is not True:
        raise RuntimeError("VPS rejeitou a sincronizacao.")
    return obj


def local_documents():
    if not DATABASE.is_file():
        raise RuntimeError("Banco de dados original do Windows nao encontrado.")
    uri = DATABASE.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=15)) as db:
        db.execute("PRAGMA query_only=ON")
        return {
            int(row[0]): str(row[1] or "")
            for row in db.execute("SELECT id,pdf_conta FROM contas")
        }


def sync_once():
    cfg = load_config()
    docs = local_documents()
    pending = request(cfg, "/api/agent/pending")["pending"]
    sent = unchanged = missing = errors = 0

    for row in pending:
        account_id = int(row["id"])
        source = docs.get(account_id, "")
        if not source:
            missing += 1
            continue
        path = Path(source)
        try:
            if not path.is_file() or path.suffix.lower() != ".pdf":
                missing += 1
                continue
            if not 5 <= path.stat().st_size <= MAX_SIZE:
                missing += 1
                continue
            # Evitar upload se a conta nao mudou. Nao salva copias no PC.
            with path.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            if digest == row.get("r2_hash") and digest:
                unchanged += 1
                continue
            with path.open("rb") as f:
                blob = f.read(MAX_SIZE + 1)
            if len(blob) > MAX_SIZE or not blob.startswith(b"%PDF-"):
                missing += 1
                continue
            if hashlib.sha256(blob).hexdigest() != digest:
                raise RuntimeError("PDF alterado durante leitura.")
            reply = request(cfg, f"/api/agent/pdf/{account_id}", blob, digest)
            if reply.get("hash") != digest:
                raise RuntimeError("R2 nao confirmou integridade do upload.")
            sent += 1
        except (OSError, RuntimeError, ValueError) as exc:
            errors += 1
            print(f"Conta {account_id}: nao sincronizou ({type(exc).__name__}).")

    print(f"PDFs no R2: enviados={sent} | ja atualizados={unchanged} | "
          f"sem arquivo local={missing} | erros={errors}")
    if errors:
        raise RuntimeError("Alguns arquivos falharam; a proxima execucao tentara novamente.")


if __name__ == "__main__":
    try:
        sync_once()
    except Exception as exc:
        print("SINCRONIZACAO NAO CONCLUIDA:", exc, file=sys.stderr)
        raise SystemExit(1)
