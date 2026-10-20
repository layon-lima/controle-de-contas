"""Agente Windows de documentos - primeira operacao: consultar PDF, somente leitura.

A VPS recebe apenas ID da conta + tres metadados; nunca bytes/caminhos do PDF.
Nao e servico de entrada: o Windows realiza conexoes HTTPS de saida.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def windows_paths():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("Agente deve ser executado na conta Windows do Contas.")
    return (Path(local) / "ControleContas" / "data" / "controle_contas.db",
            Path.home() / "Documents" / "BOLETOS")


def pdf_status(database: Path, documents_root: Path, account_id: int) -> dict:
    """Nunca abre o arquivo nem vaza seu caminho; compara apenas metadados."""
    unavailable = {"available": False, "within_official_root": False, "size_bytes": None}
    if type(account_id) is not int or account_id <= 0:
        return unavailable
    database = Path(database)
    if not database.is_file():
        return unavailable
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=10)) as conn:
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute("SELECT pdf_conta FROM contas WHERE id=?",
                           (account_id,)).fetchone()
    if not row or not row[0]:
        return unavailable
    file = Path(str(row[0]))
    try:
        canonical_root = Path(documents_root).resolve(strict=True)
        canonical_pdf = file.resolve(strict=True)
        is_child = (os.path.commonpath((os.path.normcase(str(canonical_root)),
                                        os.path.normcase(str(canonical_pdf)))) ==
                    os.path.normcase(str(canonical_root)))
        if not is_child or canonical_pdf.suffix.lower() != ".pdf":
            return unavailable
        if not canonical_pdf.is_file():
            return unavailable
        size = canonical_pdf.stat().st_size
        return {"available": True, "within_official_root": True,
                "size_bytes": size if size <= 10**12 else None}
    except (OSError, ValueError, RuntimeError):
        return unavailable


def validate_broker_url(base_url: str):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" and
        not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))):
        raise ValueError("O agente exige HTTPS; HTTP so e permitido em localhost de teste.")
    if parsed.username or parsed.password or parsed.fragment or parsed.query or not parsed.hostname:
        raise ValueError("URL do broker invalida.")


class Remote:
    def __init__(self, url: str, token: str):
        validate_broker_url(url)
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("Token invalido.")
        self.base = url.rstrip("/")
        self.token = token

    def request(self, method: str, endpoint: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.token,
                   "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + endpoint, data=data, headers=headers, method=method)
        with urlopen(request, timeout=15) as response:
            if int(response.headers.get("Content-Length", "0") or "0") > 16384:
                raise ValueError("Resposta de tamanho inesperado.")
            decoded = response.read(16385)
        if len(decoded) > 16384:
            raise ValueError("Resposta de tamanho inesperado.")
        result = json.loads(decoded)
        if type(result) is not dict:
            raise ValueError("Resposta JSON invalida.")
        return result

    def next_job(self):
        return self.request("GET", "/agent/jobs/next").get("job")

    def complete(self, job_id, lease, result):
        return self.request("POST", "/agent/jobs/" + job_id + "/result", {
            "lease_token": lease, "result": result
        })


def run_one(remote: Remote, database: Path, root: Path) -> bool:
    job = remote.next_job()
    if job is None:
        return False
    if (type(job) is not dict or job.get("operation") != "pdf_status" or
            type(job.get("account_id")) is not int or
            type(job.get("id")) is not str or
            type(job.get("lease_token")) is not str):
        raise ValueError("Broker enviou operacao nao permitida; recusada.")
    result = pdf_status(database, root, job["account_id"])
    completed = remote.complete(job["id"], job["lease_token"], result)
    if completed.get("ok") is not True:
        raise RuntimeError("Confirmacao rejeitada: lease invalida ou expirada.")
    return True


def load_config(file: Path) -> dict:
    cfg = json.loads(Path(file).read_text(encoding="utf-8"))
    if type(cfg) is not dict or set(cfg) != {
        "broker_url", "agent_token", "documents_root", "database"
    }:
        raise ValueError("Configuracao do agente invalida.")
    return cfg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    local = os.environ.get("LOCALAPPDATA")
    if not local and args.config is None:
        raise RuntimeError("LOCALAPPDATA ausente.")
    cfg_path = args.config or (Path(local) / "ControleContas" / "agent_vps.json")
    cfg = load_config(cfg_path)
    remote = Remote(cfg["broker_url"], cfg["agent_token"])
    database, root = Path(cfg["database"]), Path(cfg["documents_root"])
    delay = 3
    while True:
        try:
            found = run_one(remote, database, root)
            delay = 3
            if not found:
                time.sleep(15)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            # Jamais registrar credenciais ou caminhos absolutos na VPS.
            print("Agente offline/erro de comunicacao: " + type(exc).__name__,
                  flush=True)
            if args.once:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
        if args.once:
            break


if __name__ == "__main__":
    main()
