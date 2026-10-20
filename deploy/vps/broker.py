"""Broker isolado para o agente Windows do Controle de Contas.

Somente metadados. Protocolo inicial permite exclusivamente pdf_status.
Nenhum PDF, caminho Windows, OCR ou comprovante atravessa esta API.
Nao conectar o broker ao dominio publico ate autenticar e testar o frontend.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_REQUEST = 4096
LEASE_SECONDS = 120
MAX_ATTEMPTS = 8
KEY = re.compile(r"^[A-Za-z0-9:_-]{12,100}$")
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{20,80}$")


def validate_secrets(admin: str, agent: str) -> None:
    if (not isinstance(admin, str) or not isinstance(agent, str) or
            len(admin) < 32 or len(agent) < 32 or
            admin.startswith("SUBSTITUA_") or agent.startswith("SUBSTITUA_") or
            hmac.compare_digest(admin, agent)):
        raise ValueError("Configure duas chaves distintas e seguras (minimo 32 caracteres).")


class Queue:
    """SQLite persistente somente para fila e respostas pequenas, nunca arquivos."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                request_key TEXT NOT NULL UNIQUE,
                operation TEXT NOT NULL CHECK(operation='pdf_status'),
                account_id INTEGER NOT NULL CHECK(account_id > 0),
                status TEXT NOT NULL CHECK(status IN ('queued','leased','done','failed')),
                lease_key TEXT,
                lease_until REAL,
                attempts INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_next
                ON jobs(status, lease_until, created_at);
            """)

    def _db(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return closing(conn)

    def enqueue(self, request_key: str, account_id: int) -> dict:
        if not isinstance(request_key, str) or not KEY.fullmatch(request_key):
            raise ValueError("request_key invalida.")
        if type(account_id) is not int or not 0 < account_id <= 2_147_483_647:
            raise ValueError("account_id invalido.")
        now = time.time()
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                existing = c.execute(
                    "SELECT id,account_id FROM jobs WHERE request_key=?",
                    (request_key,)
                ).fetchone()
                if existing:
                    if existing["account_id"] != account_id:
                        raise ValueError("request_key ja usada para outra conta.")
                    job_id = existing["id"]
                else:
                    job_id = secrets.token_urlsafe(24)
                    c.execute(
                        "INSERT INTO jobs(id,request_key,operation,account_id,status,"
                        "created_at,updated_at) VALUES (?,?, 'pdf_status',?, 'queued',?,?)",
                        (job_id, request_key, account_id, now, now)
                    )
                c.commit()
                return {"id": job_id, "status": "queued" if not existing else
                        c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]}
            except Exception:
                c.rollback()
                raise

    def inspect(self, job_id: str) -> dict | None:
        if not JOB_ID.fullmatch(job_id):
            return None
        with self._db() as c:
            row = c.execute(
                "SELECT id,operation,account_id,status,attempts,result_json "
                "FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"], "operation": row["operation"],
            "account_id": row["account_id"], "status": row["status"],
            "attempts": row["attempts"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def claim(self) -> dict | None:
        now = time.time()
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute(
                    "UPDATE jobs SET status='failed',lease_key=NULL,lease_until=NULL,"
                    "result_json=?,updated_at=? WHERE status='leased' "
                    "AND lease_until < ? AND attempts >= ?",
                    (json.dumps({"error": "maximum_attempts"}), now, now, MAX_ATTEMPTS)
                )
                row = c.execute(
                    "SELECT id,operation,account_id FROM jobs "
                    "WHERE status='queued' OR "
                    "(status='leased' AND lease_until < ? AND attempts < ?) "
                    "ORDER BY created_at ASC LIMIT 1", (now, MAX_ATTEMPTS)
                ).fetchone()
                if not row:
                    c.commit()
                    return None
                lease = secrets.token_urlsafe(24)
                c.execute(
                    "UPDATE jobs SET status='leased',lease_key=?,lease_until=?,"
                    "attempts=attempts+1,updated_at=? WHERE id=?",
                    (lease, now + LEASE_SECONDS, now, row["id"])
                )
                c.commit()
                return {"id": row["id"], "operation": row["operation"],
                        "account_id": row["account_id"], "lease_token": lease}
            except Exception:
                c.rollback()
                raise

    def complete(self, job_id: str, lease_token: str, result: dict) -> bool:
        if (not JOB_ID.fullmatch(job_id) or not isinstance(lease_token, str)
                or len(lease_token) < 24):
            return False
        if type(result) is not dict or set(result) != {
            "available", "within_official_root", "size_bytes"
        }:
            raise ValueError("Resposta fora do esquema de metadados.")
        if type(result["available"]) is not bool or type(result["within_official_root"]) is not bool:
            raise ValueError("Metadados invalidos.")
        size = result["size_bytes"]
        if size is not None and (type(size) is not int or not 0 <= size <= 10**12):
            raise ValueError("Tamanho invalido.")
        if not result["available"] and size is not None:
            raise ValueError("Arquivo indisponivel nao deve informar tamanho.")
        now = time.time()
        with self._db() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute(
                    "SELECT status,lease_key,lease_until FROM jobs WHERE id=?",
                    (job_id,)
                ).fetchone()
                if not row or row["status"] != "leased" or not row["lease_key"]:
                    c.rollback()
                    return False
                if not hmac.compare_digest(row["lease_key"], lease_token) or row["lease_until"] < now:
                    c.rollback()
                    return False
                c.execute(
                    "UPDATE jobs SET status='done',result_json=?,lease_key=NULL,"
                    "lease_until=NULL,updated_at=? WHERE id=?",
                    (json.dumps(result, separators=(",", ":")), now, job_id)
                )
                c.commit()
                return True
            except Exception:
                c.rollback()
                raise


class Handler(BaseHTTPRequestHandler):
    queue: Queue
    admin_token: str
    agent_token: str

    def log_message(self, format: str, *args):
        # Nenhum token, conteudo financeiro ou caminho local deve ir ao log HTTP.
        return

    def send(self, code: int, data: dict):
        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self, expected: str) -> bool:
        header = self.headers.get("Authorization", "")
        value = header[7:] if header.startswith("Bearer ") else ""
        return hmac.compare_digest(value, expected)

    def parse_json(self) -> dict:
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ValueError("Content-Type deve ser application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length invalido.") from exc
        if not 1 <= length <= MAX_REQUEST:
            raise ValueError("Tamanho de requisicao invalido.")
        obj = json.loads(self.rfile.read(length))
        if type(obj) is not dict:
            raise ValueError("JSON invalido.")
        return obj

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self.send(200, {"online": True, "service": "contas-broker",
                            "stores_pdf": False})
            return
        if self.headers.get("Origin"):
            self.send(403, {"error": "Browser Origin nao permitido."})
            return
        if path == "/agent/jobs/next":
            if not self.authorized(self.agent_token):
                self.send(401, {"error": "Unauthorized"})
                return
            self.send(200, {"job": self.queue.claim()})
            return
        match = re.fullmatch(r"/internal/jobs/([A-Za-z0-9_-]{20,80})", path)
        if match:
            if not self.authorized(self.admin_token):
                self.send(401, {"error": "Unauthorized"})
                return
            obj = self.queue.inspect(match.group(1))
            self.send(200 if obj else 404, {"job": obj} if obj else {"error": "Not found"})
            return
        self.send(404, {"error": "Not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if self.headers.get("Origin"):
            self.send(403, {"error": "Browser Origin nao permitido."})
            return
        internal = path == "/internal/jobs"
        match = re.fullmatch(r"/agent/jobs/([A-Za-z0-9_-]{20,80})/result", path)
        if not internal and not match:
            self.send(404, {"error": "Not found"})
            return
        if not self.authorized(self.admin_token if internal else self.agent_token):
            self.send(401, {"error": "Unauthorized"})
            return
        try:
            body = self.parse_json()
            if internal:
                if set(body) != {"request_key", "account_id", "operation"} or body["operation"] != "pdf_status":
                    raise ValueError("Operacao nao autorizada.")
                result = self.queue.enqueue(body["request_key"], body["account_id"])
                self.send(202, {"job": result})
            else:
                if set(body) != {"lease_token", "result"}:
                    raise ValueError("Esquema de resultado invalido.")
                success = self.queue.complete(match.group(1), body["lease_token"], body["result"])
                self.send(200 if success else 409, {"ok": success})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send(400, {"error": str(exc)})


def make_handler(queue: Queue, admin: str, agent: str):
    validate_secrets(admin, agent)
    return type("ContasBrokerHandler", (Handler,), {
        "queue": queue, "admin_token": admin, "agent_token": agent
    })


def main():
    admin = os.environ.get("CONTAS_BROKER_ADMIN_TOKEN", "")
    agent = os.environ.get("CONTAS_BROKER_AGENT_TOKEN", "")
    validate_secrets(admin, agent)
    data = Path(os.environ.get("CONTAS_BROKER_DB", "/var/lib/contas/jobs.sqlite"))
    host = os.environ.get("CONTAS_BROKER_HOST", "127.0.0.1")
    port = int(os.environ.get("CONTAS_BROKER_PORT", "8769"))
    queue = Queue(data)
    server = ThreadingHTTPServer((host, port), make_handler(queue, admin, agent))
    print("Broker Contas ativo, apenas metadados, sem PDFs.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
