"""Controle de Contas portátil: servidor HTTP estritamente local.

Funciona em paralelo com o desktop instalado, sobre o MESMO banco SQLite,
sem PySide6, sem instalação, sem importação do UI legado nem migração de dados.
"""
from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
from contextlib import contextmanager
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = Path(os.environ.get("LOCALAPPDATA") or
               str(Path.home() / "AppData" / "Local")) / "ControleContas"
DB_PATH = APP_DIR / "data" / "controle_contas.db"
WEB_DIR = Path(__file__).resolve().parent
ICONS_DIR = ROOT / "source_current" / "assets" / "ui_icons"
HOST = "127.0.0.1"
PORT = int(os.environ.get("CONTAS_PORTABLE_PORT", "8767"))
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
PENDING: dict[str, tuple[int, float]] = {}
BACKUP_DONE = False

FIELDS = ("fornecedor", "documento", "pagador", "referencia", "valor",
          "vencimento", "codigo_pagamento", "tipo_pagamento", "observacoes")
TEXT_FIELDS = tuple(x for x in FIELDS if x != "valor")


def stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connection(write: bool = False):
    if not DB_PATH.is_file():
        raise FileNotFoundError("Banco local não encontrado. Instale ou recupere os dados existentes primeiro.")
    if write:
        before_first_write_backup()
    uri = DB_PATH.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=15000")
        if not write:
            conn.execute("PRAGMA query_only=ON")
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()


def before_first_write_backup():
    global BACKUP_DONE
    with LOCK:
        if BACKUP_DONE:
            return
        directory = APP_DIR / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        name = ("antes_portatil_" + datetime.now().strftime("%Y%m%d_%H%M%S")
                + "_" + secrets.token_hex(3) + ".db")
        destination = directory / name
        with sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro",
                             uri=True, timeout=15) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
        BACKUP_DONE = True


def get_tags(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"cc_tags", "cc_account_tags"}.issubset(tables):
        return [], {}
    labels = [dict(r) for r in conn.execute(
        "SELECT id,name FROM cc_tags ORDER BY name COLLATE NOCASE")]
    tags: dict[int, list[dict]] = {}
    for row in conn.execute(
        "SELECT x.account_id,t.id,t.name FROM cc_account_tags x "
        "JOIN cc_tags t ON t.id=x.tag_id ORDER BY t.name COLLATE NOCASE"
    ):
        tags.setdefault(row["account_id"], []).append(
            {"id": row["id"], "name": row["name"]})
    return labels, tags


def activity_map():
    path = APP_DIR / "cloud_activity.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(r["account_id"]): r for r in raw.get("activities", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def status(due, paid):
    if paid and str(paid).strip():
        return "Paga"
    try:
        days = (date.fromisoformat(str(due)[:10]) - date.today()).days
        return "Vencida" if days < 0 else "Próxima" if days <= 5 else "Em dia"
    except (ValueError, TypeError):
        return "Em dia"


def list_accounts():
    with connection() as conn:
        rows = conn.execute(
            "SELECT c.id,c.fornecedor,c.documento,c.pagador,c.referencia,"
            "c.valor,c.vencimento,c.codigo_pagamento,c.tipo_pagamento,"
            "c.observacoes,c.enviado_pagamento_em,c.pdf_conta,"
            "p.data_pagamento,p.valor_pago,p.pdf_comprovante "
            "FROM contas c LEFT JOIN pagamentos p ON p.id=("
            "SELECT p2.id FROM pagamentos p2 WHERE p2.conta_id=c.id "
            "ORDER BY p2.id DESC LIMIT 1) "
            "ORDER BY CASE WHEN p.id IS NULL THEN 0 ELSE 1 END,"
            "c.vencimento ASC,c.id DESC"
        ).fetchall()
        tag_labels, tags = get_tags(conn)
    activities = activity_map()
    output = []
    for row in rows:
        item = dict(row)
        pdf = str(item.pop("pdf_conta") or "")
        receipt = str(item.pop("pdf_comprovante") or "")
        item["has_pdf"] = bool(pdf and Path(pdf).is_file())
        item["has_receipt"] = bool(receipt and Path(receipt).is_file())
        item["status"] = status(item["vencimento"], item["data_pagamento"])
        item["tags"] = tags.get(item["id"], [])
        item["suggested_at"] = activities.get(item["id"], {}).get("suggested_at")
        output.append(item)
    return {"accounts": output, "tags": tag_labels,
            "db_available": True, "count": len(output)}


def account(conn, account_id: int):
    row = conn.execute("SELECT * FROM contas WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise LookupError("Conta não encontrada.")
    return dict(row)


def normalize(data: dict):
    payload = {}
    for key in TEXT_FIELDS:
        value = str(data.get(key) or "").strip()
        if len(value) > (3000 if key == "observacoes" else 500):
            raise ValueError(f"Campo {key} excede o tamanho permitido.")
        payload[key] = value
    if not payload["fornecedor"]:
        raise ValueError("Informe o fornecedor.")
    try:
        amount = round(float(str(data.get("valor") or 0).replace(",", ".")), 2)
    except ValueError as exc:
        raise ValueError("Valor inválido.") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    payload["valor"] = amount
    due = payload["vencimento"]
    try:
        date.fromisoformat(due)
    except ValueError as exc:
        raise ValueError("Informe um vencimento válido.") from exc
    return payload


def create_account(data):
    values = normalize(data)
    with connection(write=True) as conn:
        columns = ",".join(FIELDS)
        placeholders = ",".join("?" for _ in FIELDS)
        record = conn.execute(
            f"INSERT INTO contas ({columns},criado_em,atualizado_em) "
            f"VALUES ({placeholders},?,?)",
            [values[c] for c in FIELDS] + [stamp(), stamp()],
        )
        return int(record.lastrowid)


def edit_account(account_id, data):
    values = normalize(data)
    with connection(write=True) as conn:
        account(conn, account_id)
        # Não toca em pagamentos, PDFs, atividade cloud ou datas de envio.
        pairs = ",".join(f"{key}=?" for key in FIELDS)
        conn.execute(
            f"UPDATE contas SET {pairs},atualizado_em=? WHERE id=?",
            [values[c] for c in FIELDS] + [stamp(), account_id])


def remove_account(account_id):
    with connection(write=True) as conn:
        account(conn, account_id)
        paid = conn.execute(
            "SELECT 1 FROM pagamentos WHERE conta_id=? LIMIT 1", (account_id,)
        ).fetchone()
        if paid:
            raise ValueError("Conta com pagamento registrado não pode ser excluída aqui.")
        # Não apagar PDF físico nem comprovantes ou arquivos sincronizados.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='cc_account_tags'").fetchone():
            conn.execute("DELETE FROM cc_account_tags WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM contas WHERE id=?", (account_id,))


def payment_text(data):
    # Mesma ordem e os mesmos campos do desktop, sem qualquer dependência de PDF.
    values = normalize(data)
    money = f"{values['valor']:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    entries = [
        ("FORNECEDOR", values["fornecedor"]),
        ("DOCUMENTO", values["documento"]),
        ("PAGADOR", values["pagador"]),
        ("REFERÊNCIA", values["referencia"]),
        ("VALOR", "R$ " + money),
        ("VENCIMENTO", values["vencimento"]),
        ("TIPO DE PAGAMENTO", values["tipo_pagamento"]),
        ("CÓDIGO PIX / LINHA DIGITÁVEL", values["codigo_pagamento"]),
        ("OBSERVAÇÕES", values["observacoes"]),
    ]
    return "\n".join(f"{label}: {value}" for label, value in entries if value)


def prepare_send(account_id):
    with connection() as conn:
        data = account(conn, account_id)
    text = payment_text(data)
    nonce = secrets.token_urlsafe(24)
    with LOCK:
        now = time.monotonic()
        for key, (_aid, expires) in list(PENDING.items()):
            if expires < now:
                del PENDING[key]
        PENDING[nonce] = (account_id, now + 120)
    return {"text": text, "nonce": nonce}


def confirm_send(account_id, nonce):
    with LOCK:
        record = PENDING.pop(str(nonce), None)
    if record is None or record[0] != account_id or record[1] < time.monotonic():
        raise ValueError("Cópia não confirmada. Repita a ação.")
    with connection(write=True) as conn:
        account(conn, account_id)
        saved = conn.execute(
            "UPDATE contas SET enviado_pagamento_em="
            "COALESCE(NULLIF(TRIM(enviado_pagamento_em),''),?),"
            "atualizado_em=? WHERE id=?",
            (stamp(), stamp(), account_id)
        )
        if saved.rowcount != 1:
            raise RuntimeError("Envio não foi registrado.")
    with connection() as conn:
        row = account(conn, account_id)
        if not str(row.get("enviado_pagamento_em") or "").strip():
            raise RuntimeError("Registro de envio não confirmado.")
    return row["enviado_pagamento_em"]


def assign_tags(account_id, ids, new_name=""):
    with connection(write=True) as conn:
        account(conn, account_id)
        conn.execute("CREATE TABLE IF NOT EXISTS cc_tags ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     "name TEXT NOT NULL UNIQUE)")
        conn.execute("CREATE TABLE IF NOT EXISTS cc_account_tags ("
                     "account_id INTEGER NOT NULL,tag_id INTEGER NOT NULL,"
                     "PRIMARY KEY(account_id,tag_id))")
        if new_name:
            new_name = " ".join(str(new_name).split())
            if len(new_name) > 60:
                raise ValueError("TAG longa demais.")
            conn.execute("INSERT OR IGNORE INTO cc_tags(name) VALUES(?)", (new_name,))
            found = conn.execute(
                "SELECT id FROM cc_tags WHERE name=? COLLATE NOCASE", (new_name,)
            ).fetchone()
            if found is not None:
                ids = list(ids) + [found["id"]]
        valid = {r["id"] for r in conn.execute("SELECT id FROM cc_tags")}
        ids = {int(i) for i in ids}
        if not ids.issubset(valid):
            raise ValueError("TAG desconhecida.")
        conn.execute("DELETE FROM cc_account_tags WHERE account_id=?", (account_id,))
        for tag_id in ids:
            conn.execute("INSERT INTO cc_account_tags(account_id,tag_id) VALUES (?,?)",
                         (account_id, tag_id))


def register_payment(account_id, data):
    paid_on = str(data.get("data_pagamento") or "").strip()
    try:
        date.fromisoformat(paid_on)
    except ValueError as exc:
        raise ValueError("Data do pagamento inválida.") from exc
    with connection(write=True) as conn:
        info = account(conn, account_id)
        already = conn.execute(
            "SELECT 1 FROM pagamentos WHERE conta_id=? AND "
            "data_pagamento IS NOT NULL AND TRIM(data_pagamento)<>'' LIMIT 1",
            (account_id,)
        ).fetchone()
        if already:
            raise ValueError("Esta conta já possui um pagamento registrado.")
        table = {r["name"]: r for r in conn.execute("PRAGMA table_info(pagamentos)")}
        values = {
            "conta_id": account_id,
            "valor_pago": float(info["valor"]),
            "data_pagamento": paid_on,
            "beneficiario": str(info.get("fornecedor") or ""),
            "documento_beneficiario": str(info.get("documento") or ""),
            "pdf_comprovante": "",
            "resultado_conferencia": "Sem comprovante",
            "detalhes_conferencia": "Pagamento manual no app portátil; comprovante pendente.",
            "criado_em": stamp(),
            "atualizado_em": stamp(),
        }
        # Evita inventar o schema da versão compilada: usa somente colunas reais.
        unknown_required = [
            row["name"] for row in table.values()
            if row["notnull"] and row["dflt_value"] is None
            and not row["pk"] and row["name"] not in values
        ]
        if unknown_required:
            raise RuntimeError("Estrutura de pagamento não compatível: "
                               + ", ".join(unknown_required))
        cols = [name for name in values if name in table]
        for must in ("conta_id", "data_pagamento", "valor_pago"):
            if must not in cols:
                raise RuntimeError("Estrutura de pagamento incompleta.")
        conn.execute(
            "INSERT INTO pagamentos (" + ",".join(cols) + ") VALUES ("
            + ",".join("?" for _ in cols) + ")",
            [values[name] for name in cols],
        )
    with connection() as conn:
        found = conn.execute(
            "SELECT data_pagamento FROM pagamentos WHERE conta_id=? "
            "ORDER BY id DESC LIMIT 1", (account_id,)
        ).fetchone()
        if found is None or found["data_pagamento"] != paid_on:
            raise RuntimeError("Pagamento não foi confirmado.")


def pdf_path(account_id, receipt=False):
    with connection() as conn:
        account(conn, account_id)
        if receipt:
            row = conn.execute("SELECT pdf_comprovante FROM pagamentos "
                               "WHERE conta_id=? ORDER BY id DESC LIMIT 1",
                               (account_id,)).fetchone()
            value = row["pdf_comprovante"] if row else ""
        else:
            value = account(conn, account_id)["pdf_conta"]
    path = Path(str(value or "").strip())
    if not value or not path.is_file() or path.suffix.lower() != ".pdf":
        raise FileNotFoundError("Esta conta não possui PDF disponível.")
    return path


def upload_pdf(account_id, data: bytes, filename: str):
    if len(data) < 5 or not data.startswith(b"%PDF-"):
        raise ValueError("O arquivo selecionado não é um PDF válido.")
    if len(data) > 30 * 1024 * 1024:
        raise ValueError("PDF acima de 30 MB.")
    folder = APP_DIR / "documents" / "portatil"
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).name)[:75]
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    target = folder / (str(account_id) + "_" + secrets.token_hex(6) + "_" + safe)
    # O arquivo só será referenciado após gravação e commit.
    temp = target.with_suffix(".part")
    try:
        temp.write_bytes(data)
        os.replace(temp, target)
        with connection(write=True) as conn:
            account(conn, account_id)
            conn.execute("UPDATE contas SET pdf_conta=?,atualizado_em=? WHERE id=?",
                         (str(target), stamp(), account_id))
    except Exception:
        temp.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return target.name


def get_mobile_link():
    sys.path.insert(0, str(ROOT / "source_current"))
    from controle_contas.cloud_sync import get_cloud_private_link
    link = get_cloud_private_link()
    if not link:
        raise ValueError("Acesso mobile ainda não está configurado.")
    return link


class ApiError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class Handler(BaseHTTPRequestHandler):
    server_version = "ControleContasPortable/1.0"

    def log_message(self, *_args):
        return

    def _send(self, data: bytes, status_code=200, mime="application/json; charset=utf-8"):
        self.send_response(status_code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), code)

    def _check(self, writing=False):
        origin = self.headers.get("Origin", "")
        expected = f"http://{HOST}:{PORT}"
        if origin and origin != expected:
            raise ApiError(403, "Origem não autorizada.")
        if self.headers.get("X-Local-Token", "") != TOKEN:
            raise ApiError(403, "Sessão inválida. Reabra pelo iniciador.")
        if writing and self.headers.get("Content-Type", "").split(";")[0].strip() not in ("application/json", "application/pdf"):
            raise ApiError(415, "Formato não aceito.")
    
    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ApiError(400, "Tamanho inválido.") from exc
        if length < 2 or length > 64_000:
            raise ApiError(413, "Dados muito grandes ou vazios.")
        try:
            obj = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as exc:
            raise ApiError(400, "JSON inválido.") from exc
        if not isinstance(obj, dict):
            raise ApiError(400, "Objeto esperado.")
        return obj

    def _handle(self):
        try:
            route = urlsplit(self.path).path
            if self.command == "GET" and route == "/":
                # HTML tem acesso exclusivo ao token; nenhuma API aceita chamadas
                # de outro site, formulários sem token ou fetch de outra origem.
                html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
                bootstrap = (f"<script>window.__CONTAS_TOKEN__="
                             f"{json.dumps(TOKEN)};</script>")
                # Script inline permitido apenas por nonce CSP temporário.
                nonce = secrets.token_urlsafe(16)
                bootstrap = bootstrap.replace("<script>", f'<script nonce="{nonce}">')
                body = html.replace("<!--LOCAL_AUTH-->", bootstrap).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy",
                                 f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
                                 "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                 "object-src 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.command == "GET" and route == "/app.js":
                self._send((WEB_DIR / "app.js").read_bytes(), mime="text/javascript; charset=utf-8")
                return
            if self.command == "GET" and route.startswith("/icons/"):
                name = route.removeprefix("/icons/")
                if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}\.svg", name):
                    raise ApiError(404, "Ícone não encontrado.")
                path = ICONS_DIR / name
                if not path.is_file():
                    raise ApiError(404, "Ícone não encontrado.")
                self._send(path.read_bytes(), mime="image/svg+xml")
                return
            if route.startswith("/api/"):
                self._check(writing=self.command == "POST")
                parts = route.strip("/").split("/")
                if self.command == "GET" and route == "/api/accounts":
                    self._json({"ok": True, **list_accounts()})
                    return
                if self.command == "GET" and route == "/api/health":
                    self._json({"ok": True, "db": DB_PATH.is_file()})
                    return
                if self.command == "GET" and route == "/api/mobile-link":
                    self._json({"ok": True, "url": get_mobile_link()})
                    return
                if len(parts) >= 3 and parts[:2] == ["api", "accounts"]:
                    try:
                        account_id = int(parts[2])
                        if account_id <= 0:
                            raise ValueError()
                    except ValueError as exc:
                        raise ApiError(400, "ID inválido.") from exc
                    op = parts[3] if len(parts) == 4 else ""
                    if self.command == "GET" and op == "pdf":
                        receipt = urlsplit(self.path).query == "receipt=1"
                        path = pdf_path(account_id, receipt=receipt)
                        content = path.read_bytes()
                        self._send(content, mime="application/pdf")
                        return
                    if self.command == "POST" and op == "upload-pdf":
                        try:
                            size = int(self.headers.get("Content-Length") or 0)
                        except ValueError as exc:
                            raise ApiError(400, "Tamanho inválido.") from exc
                        if size < 5 or size > 30 * 1024 * 1024:
                            raise ApiError(413, "O PDF deve ter até 30 MB.")
                        from urllib.parse import unquote
                        filename = unquote(self.headers.get("X-Pdf-Name") or "documento.pdf")
                        result = upload_pdf(account_id, self.rfile.read(size), filename)
                        self._json({"ok": True, "name": result})
                        return
                    if self.command == "POST":
                        data = self._body()
                        if op == "edit":
                            edit_account(account_id, data)
                            result = {"saved": True}
                        elif op == "delete":
                            remove_account(account_id)
                            result = {"deleted": True}
                        elif op == "prepare-send":
                            result = prepare_send(account_id)
                        elif op == "confirm-send":
                            result = {"sent_at": confirm_send(account_id, data.get("nonce"))}
                        elif op == "tags":
                            assign_tags(account_id, data.get("ids") or [], data.get("new_tag") or "")
                            result = {"saved": True}
                        elif op == "pay":
                            register_payment(account_id, data)
                            result = {"paid": True}
                        elif op == "suggest":
                            # Usa a mesma rotina de Cloudflare do app original.
                            sys.path.insert(0, str(ROOT / "source_current"))
                            from controle_contas.cloud_sync import suggest_payment
                            response = suggest_payment(account_id)
                            if response.get("ok") is False:
                                raise RuntimeError(str(response.get("erro") or "Nuvem rejeitou a sugestão."))
                            result = {"suggested": True}
                        else:
                            raise ApiError(404, "Operação desconhecida.")
                        self._json({"ok": True, **result})
                        return
                if self.command == "POST" and route == "/api/accounts":
                    self._json({"ok": True, "id": create_account(self._body())})
                    return
            raise ApiError(404, "Recurso não encontrado.")
        except ApiError as exc:
            self._json({"ok": False, "error": exc.message}, exc.code)
        except (ValueError, LookupError, FileNotFoundError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 500)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()


def main():
    if not DB_PATH.is_file():
        print("Banco original não encontrado:", DB_PATH)
        print("O modo portátil não cria nem substitui um banco vazio.")
        raise SystemExit(2)
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        # Se o iniciador for acionado novamente, o servidor existente permanece.
        webbrowser.open(f"http://{HOST}:{PORT}/")
        return
    url = f"http://{HOST}:{PORT}/"
    print("Controle de Contas portátil:", url)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
