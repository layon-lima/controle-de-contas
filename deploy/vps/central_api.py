"""API privada do Controle de Contas na VPS.

ETAPA DE PREPARACAO: inicializa schema isolado e verifica o banco.
Nao importa, altera ou expõe contas enquanto a migração nao for conferida.
Porta publica proibida: Docker publica somente 127.0.0.1 da VPS.
"""
from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import psycopg


def connect():
    return psycopg.connect(
        host=os.environ.get("CONTAS_DB_HOST", "contas-db"),
        port=int(os.environ.get("CONTAS_DB_PORT", "5432")),
        dbname=os.environ.get("CONTAS_DB_NAME", "contas_central"),
        user=os.environ.get("CONTAS_DB_USER", "contas"),
        password=os.environ["CONTAS_DB_PASSWORD"],
        connect_timeout=5,
        options="-c statement_timeout=5000",
    )


def initialize_schema() -> None:
    sql = Path(__file__).with_name("central_schema.sql").read_text(encoding="utf-8")
    # O arquivo e controlado pelo GitHub; evitar executar SQL vindo de usuarios.
    commands = [command.strip() for command in sql.split(";") if command.strip()]
    with connect() as conn:
        with conn.cursor() as cur:
            for command in commands:
                cur.execute(command)


def db_health() -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() == (1,)


def db_summary() -> dict:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM contas")
            accounts = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM pagamentos")
            payments = int(cur.fetchone()[0])
            cur.execute("SELECT value FROM central_meta WHERE key = %s", ("migration_complete",))
            row = cur.fetchone()
            migrated = bool(row and row[0] is True)
    return {
        "ok": True,
        "database": "contas_central",
        "accounts": accounts,
        "payments": payments,
        "migration_complete": migrated,
        "api_publicada": False,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ContasCentral"

    def log_message(self, format, *args):
        # Nao registrar tokens, caminhos nem dados financeiros em logs HTTP.
        return

    def respond(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = os.environ.get("CONTAS_API_TOKEN", "")
        provided = self.headers.get("Authorization", "")
        return (
            len(expected) >= 32
            and provided.startswith("Bearer ")
            and hmac.compare_digest(provided.removeprefix("Bearer "), expected)
        )

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            try:
                if db_health():
                    self.respond(200, {"ok": True, "service": "contas-central-api", "database": "online"})
                else:
                    self.respond(503, {"ok": False, "database": "offline"})
            except Exception:
                self.respond(503, {"ok": False, "database": "offline"})
            return

        if not path.startswith("/api/"):
            self.respond(404, {"ok": False, "erro": "Rota inexistente."})
            return
        if not self.authorized():
            self.respond(401, {"ok": False, "erro": "Nao autorizado."})
            return
        if path == "/api/v1/status":
            try:
                self.respond(200, db_summary())
            except Exception:
                self.respond(503, {"ok": False, "erro": "Banco indisponivel."})
            return
        if path == "/api/v1/contas":
            # Nunca devolver 0 contas como se uma migracao ainda nao iniciada
            # significasse que o usuario nao tem contas.
            self.respond(503, {
                "ok": False,
                "erro": "Migracao pendente; consulta desativada por seguranca.",
            })
            return
        self.respond(404, {"ok": False, "erro": "Rota inexistente."})

    def do_POST(self):
        self.respond(405, {"ok": False, "erro": "Alteracoes nao habilitadas nesta etapa."})

    def do_PUT(self):
        self.respond(405, {"ok": False, "erro": "Alteracoes nao habilitadas nesta etapa."})

    def do_DELETE(self):
        self.respond(405, {"ok": False, "erro": "Alteracoes nao habilitadas nesta etapa."})


def main():
    if len(os.environ.get("CONTAS_API_TOKEN", "")) < 32:
        raise RuntimeError("CONTAS_API_TOKEN obrigatorio para iniciar a API central.")
    initialize_schema()
    server = ThreadingHTTPServer(("0.0.0.0", 8768), Handler)
    print("CONTAS CENTRAL: API privada ativa, migracao ainda pendente.", flush=True)
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
