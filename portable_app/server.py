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
import subprocess
import sys
import threading
import time
import webbrowser
from contextlib import closing, contextmanager
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
PENDING_BATCH: dict[str, tuple[list[int], float]] = {}
BACKUP_DONE = False
try:
    SOURCE_REVISION = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        encoding="utf-8", stderr=subprocess.DEVNULL, timeout=5
    ).strip()
except (OSError, subprocess.SubprocessError):
    SOURCE_REVISION = "indisponivel"

if __package__:
    from . import pdf_intelligence, pdf_workflows
else:
    import pdf_intelligence
    import pdf_workflows


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
        # sqlite3.Connection.__exit__ confirma/reverte transações, mas NÃO
        # fecha a conexão. No Windows, isso mantém o .db temporário bloqueado.
        try:
            with closing(sqlite3.connect(
                DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=15
            )) as source:
                with closing(sqlite3.connect(destination, timeout=15)) as target:
                    source.backup(target)
                    target.commit()
                    result = target.execute("PRAGMA integrity_check").fetchone()
                    if not result or result[0] != "ok":
                        raise RuntimeError("Backup do banco não passou na verificação.")
        except Exception:
            destination.unlink(missing_ok=True)
            raise
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


def get_document_folder():
    base = _pasta_documentos_configurada()
    return {"configured": bool(base), "folder": str(base) if base else ""}


def set_document_folder(data):
    raw = str(data.get("folder") or "").strip()
    if not raw or len(raw) > 700:
        raise ValueError("Informe um caminho de pasta válido.")
    folder = Path(raw).expanduser()
    if not folder.is_absolute():
        raise ValueError("A pasta precisa ser um caminho completo do Windows.")
    if re.fullmatch(r"BOLETOS\s+\d{4}", folder.name, flags=re.IGNORECASE):
        folder = folder.parent
    # Não criar arquivos de conta nesta operação, somente guardar o destino.
    if not folder.is_dir():
        raise FileNotFoundError("A pasta informada não existe no computador.")
    try:
        test = folder / ("._teste_escrita_" + secrets.token_hex(4))
        with test.open("x", encoding="utf-8") as stream:
            stream.write("ok")
        test.unlink()
    except OSError as exc:
        raise ValueError("Sem permissão para gravar na pasta escolhida.") from exc
    config = APP_DIR / "config_documentos.json"
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps({"pasta_base": str(folder)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, config)
    finally:
        tmp.unlink(missing_ok=True)
    return {"folder": str(folder), "configured": True}


def create_account_from_preview(data):
    """Confirma campos corrigidos pelo usuário, grava PDF e cadastro atomicamente."""
    ticket = str(data.get("ticket") or "")
    source, source_name, predicted = pdf_workflows.take_preview(ticket)
    fields = normalize(data)
    # Arquivo original + banco são confirmados como uma unidade.
    with LOCK:
        temp = None
        target = None
        created = False
        try:
            with connection(write=True) as conn:
                pdf_workflows.check_duplicate(conn, source, fields)
                target, organized = _pdf_destination(fields, source_name, None)
                temp = target.with_name("." + target.stem + "_" + secrets.token_hex(6) + ".part")
                shutil.copyfile(source, temp)
                if target.exists():
                    raise RuntimeError("Arquivo existente no destino: a importação foi cancelada.")
                os.replace(temp, target)
                created = True
                columns = ",".join(FIELDS)
                marks = ",".join("?" for _ in FIELDS)
                result = conn.execute(
                    f"INSERT INTO contas ({columns},criado_em,atualizado_em,pdf_conta) "
                    f"VALUES ({marks},?,?,?)",
                    [fields[key] for key in FIELDS] + [stamp(), stamp(), str(target)],
                )
                account_id = int(result.lastrowid)
        except Exception:
            if temp is not None:
                temp.unlink(missing_ok=True)
            if created and target is not None:
                target.unlink(missing_ok=True)
            raise
    pdf_workflows.consume_preview(ticket)
    # A mesma aprendizagem V2 do desktop, após os dados serem confirmados.
    # Um problema de aprendizado NUNCA perde ou duplica o boleto recém-gravado.
    learning = {}
    warning = ""
    try:
        learning = pdf_intelligence.learn_from_confirmed(target, fields, predicted)
    except Exception as exc:
        warning = "Cadastro salvo; aprendizado de layout pendente: " + str(exc)
    return {"id": account_id, "name": target.name,
            "folder": str(target.parent), "organized": organized,
            "learning": learning, "warning": warning}


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


def _batch_ids(data):
    raw = data.get("ids") or []
    if not isinstance(raw, list) or len(raw) > 200 or not raw:
        raise ValueError("Selecione de 1 a 200 contas.")
    try:
        ids = sorted({int(value) for value in raw})
    except (ValueError, TypeError) as exc:
        raise ValueError("Identificadores de contas inválidos.") from exc
    if not ids or ids[0] <= 0:
        raise ValueError("Conta inválida.")
    return ids


def prepare_batch_send(data):
    ids = _batch_ids(data)
    with connection() as conn:
        details = []
        for account_id in ids:
            info = account(conn, account_id)
            details.append(payment_text(info))
    nonce = secrets.token_urlsafe(24)
    with LOCK:
        for key, (_ids, expiration) in list(PENDING_BATCH.items()):
            if expiration < time.monotonic():
                PENDING_BATCH.pop(key, None)
        PENDING_BATCH[nonce] = (ids, time.monotonic() + 120)
    return {"nonce": nonce, "count": len(ids),
            "text": "\n\n" + ("-" * 40) + "\n\n".join(
                ["CONTA " + str(ids[i]) + "\n" + message for i, message in enumerate(details)]
            )}


def confirm_batch_send(data):
    nonce = str(data.get("nonce") or "")
    with LOCK:
        record = PENDING_BATCH.pop(nonce, None)
        if not record or record[1] < time.monotonic():
            raise ValueError("Cópia em lote não foi confirmada. Repita.")
        ids = record[0]
        with connection(write=True) as conn:
            now = stamp()
            for account_id in ids:
                account(conn, account_id)
                conn.execute(
                    "UPDATE contas SET enviado_pagamento_em="
                    "COALESCE(NULLIF(TRIM(enviado_pagamento_em),''),?),"
                    "atualizado_em=? WHERE id=?",
                    (now, now, account_id),
                )
        return {"count": len(ids), "sent": True}


def batch_pay(data):
    ids = _batch_ids(data)
    date_paid = str(data.get("data_pagamento") or "")
    try:
        date.fromisoformat(date_paid)
    except ValueError as exc:
        raise ValueError("Data de pagamento inválida.") from exc
    paid, errors = [], {}
    for account_id in ids:
        try:
            register_payment(account_id, {"data_pagamento": date_paid})
            paid.append(account_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            errors[str(account_id)] = str(exc)
    return {"paid": paid, "errors": errors, "count": len(paid)}


def batch_suggest(data):
    ids = _batch_ids(data)
    sys.path.insert(0, str(ROOT / "source_current"))
    from controle_contas.cloud_sync import suggest_payments
    result = suggest_payments(ids)
    if result.get("ok") is False or int(result.get("quantidade", -1)) != len(ids):
        raise RuntimeError("A nuvem não confirmou todas as contas selecionadas.")
    return {"count": len(ids), "suggested": True}


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


def register_payment_with_receipt(account_id: int, paid_on: str, data: bytes):
    """Mescla comprovante ao PDF original, valida páginas e só então registra.

    O banco e os arquivos originais são restaurados se alguma etapa falhar.
    A cópia de segurança antes da primeira escrita já existe em connection().
    """
    pdf_workflows.check_pdf(data)
    try:
        date.fromisoformat(str(paid_on))
    except ValueError as exc:
        raise ValueError("Data do pagamento inválida.") from exc
    with LOCK:
        with connection() as conn:
            info = account(conn, account_id)
            previously = conn.execute(
                "SELECT 1 FROM pagamentos WHERE conta_id=? AND "
                "data_pagamento IS NOT NULL AND TRIM(data_pagamento)<>'' LIMIT 1",
                (account_id,),
            ).fetchone()
            if previously:
                raise ValueError("Esta conta já está paga. Nenhum PDF foi alterado.")
        raw = str(info.get("pdf_conta") or "").strip()
        existing = Path(raw) if raw else None
        if existing and not existing.is_file():
            raise FileNotFoundError(
                "O PDF da conta não foi encontrado no caminho cadastrado. "
                "Restaure o documento antes de adicionar comprovante."
            )
        if existing is None:
            target, _ = _pdf_destination(info, "COMPROVANTE.pdf", account_id)
        else:
            target = existing
        temporary = target.with_name("." + target.stem + "_" + secrets.token_hex(6) + ".part.pdf")
        backup = target.with_name("." + target.stem + "_" + secrets.token_hex(6) + ".restore.pdf") if existing else None
        replaced = False
        try:
            pages = pdf_workflows.merge_receipt(existing, data, temporary)
            if backup:
                shutil.copy2(target, backup)
            os.replace(temporary, target)
            replaced = True
            # A função de registro preserva os campos originais e a conferência
            # automática fica desativada, como no último desktop.
            with connection(write=True) as conn:
                account(conn, account_id)
                if conn.execute(
                    "SELECT 1 FROM pagamentos WHERE conta_id=? AND "
                    "data_pagamento IS NOT NULL AND TRIM(data_pagamento)<>'' LIMIT 1",
                    (account_id,),
                ).fetchone():
                    raise ValueError("Pagamento já registrado. Restaurei o PDF original.")
                table = {r["name"]: r for r in conn.execute("PRAGMA table_info(pagamentos)")}
                values = {
                    "conta_id": account_id,
                    "valor_pago": float(info["valor"]),
                    "data_pagamento": str(paid_on),
                    "beneficiario": str(info.get("fornecedor") or ""),
                    "documento_beneficiario": str(info.get("documento") or ""),
                    "pdf_comprovante": str(target),
                    "resultado_conferencia": "Não conferido",
                    "detalhes_conferencia": "Comprovante mesclado e páginas confirmadas; conferência automática desativada.",
                    "criado_em": stamp(),
                    "atualizado_em": stamp(),
                }
                missing = [
                    row["name"] for row in table.values()
                    if row["notnull"] and row["dflt_value"] is None
                    and not row["pk"] and row["name"] not in values
                ]
                if missing:
                    raise RuntimeError("Colunas necessárias desconhecidas: " + ", ".join(missing))
                cols = [name for name in values if name in table]
                conn.execute(
                    "INSERT INTO pagamentos (" + ",".join(cols) + ") VALUES ("
                    + ",".join("?" for _ in cols) + ")",
                    [values[name] for name in cols],
                )
                if existing is None:
                    conn.execute(
                        "UPDATE contas SET pdf_conta=?,atualizado_em=? WHERE id=?",
                        (str(target), stamp(), account_id),
                    )
            return {"paid": True, "merged": True, "pages": pages, "pdf": str(target)}
        except Exception:
            if replaced:
                if backup and backup.is_file():
                    os.replace(backup, target)
                elif existing is None:
                    target.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            if backup:
                backup.unlink(missing_ok=True)


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


# Repete a organizacao documental configurada no app legado.
# A configuracao e compartilhada, mas nao precisamos carregar Qt nem patches.
MESES = (
    "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
)


def _pasta_documentos_configurada():
    path = APP_DIR / "config_documentos.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        raw = str(config.get("pasta_base") or "").strip()
        if not raw:
            return None
        base = Path(raw).expanduser()
        if base.name.upper().startswith("BOLETOS ") and re.fullmatch(
            r"BOLETOS\s+\d{4}", base.name, flags=re.IGNORECASE
        ):
            base = base.parent
        return base
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _nome_pdf_organizado(observacoes, fornecedor):
    # Mesmo criterio do legado: observacoes digitadas viram nome do boleto.
    text = str(observacoes or fornecedor or "BOLETO")
    text = "".join(" " if ch in '<>:"/|?*' or ch == chr(92) or ord(ch) < 32 else ch for ch in text)
    text = " ".join(text.split()).strip().rstrip(".")
    if text.casefold().endswith(".pdf"):
        text = text[:-4].rstrip()
    text = text[:120].strip() or "BOLETO"
    if text.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        text = "BOLETO " + text
    return text + ".pdf"


def _pdf_destination(account_info, filename, account_id):
    base = _pasta_documentos_configurada()
    if base:
        try:
            due = date.fromisoformat(str(account_info["vencimento"])[:10])
        except (ValueError, KeyError) as exc:
            raise ValueError("Vencimento invalido para organizar o PDF.") from exc
        destination = base / f"BOLETOS {due.year}" / f"{due.month:02d} - {MESES[due.month - 1]}"
        name = _nome_pdf_organizado(
            account_info.get("observacoes"), account_info.get("fornecedor")
        )
    else:
        # Sem configuracao antiga, preservar importacao numa pasta protegida
        # sem inventar nova pasta-base para BOLETOS.
        destination = APP_DIR / "documents" / "portatil"
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).name)[:75]
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        name = str(account_id) + "_" + safe
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / name
    index = 1
    while target.exists():
        target = destination / (Path(name).stem + f" ({index})" + ".pdf")
        index += 1
    return target, base is not None


def upload_pdf(account_id, data: bytes, filename: str):
    # Anexar a conta existente também precisa da detecção reforçada de
    # duplicidade. Sem isso um mesmo boleto poderia aparecer em duas contas.
    pdf_workflows.check_pdf(data)
    with LOCK:
        stage_dir = APP_DIR / "portable_staging"
        stage_dir.mkdir(parents=True, exist_ok=True)
        stage = stage_dir / (secrets.token_hex(12) + ".pdf")
        stage.write_bytes(data)
        target = None
        temp = None
        created = False
        try:
            with connection() as conn:
                info = account(conn, account_id)
                pdf_workflows.check_duplicate(conn, stage, info, exclude_id=account_id)
            target, organized = _pdf_destination(info, filename, account_id)
            temp = target.with_name("." + target.stem + "_upload_" + secrets.token_hex(6) + ".part")
            shutil.copyfile(stage, temp)
            if target.exists():
                raise RuntimeError("O nome do PDF já está em uso. Tente novamente.")
            os.replace(temp, target)
            created = True
            with connection(write=True) as conn:
                account(conn, account_id)
                conn.execute("UPDATE contas SET pdf_conta=?,atualizado_em=? WHERE id=?",
                             (str(target), stamp(), account_id))
        except Exception:
            if temp is not None:
                temp.unlink(missing_ok=True)
            if created and target is not None:
                target.unlink(missing_ok=True)
            raise
        finally:
            stage.unlink(missing_ok=True)
    return {"name": target.name, "folder": str(target.parent),
            "organized": organized}


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


class LocalServer(ThreadingHTTPServer):
    # As threads de requisição devem terminar ANTES de fechar sockets/banco
    # nos testes e no reinício do portátil; não abandonar handlers em daemon.
    daemon_threads = False
    block_on_close = True


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
            if self.command == "GET" and route == "/health":
                # Apenas metadado de versao: nunca expor contas nem tokens.
                self._json({"online": True, "revision": SOURCE_REVISION})
                return
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
            if self.command == "GET" and route == "/search.svg":
                self._send((WEB_DIR / "search.svg").read_bytes(), mime="image/svg+xml")
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
                if self.command == "POST" and route == "/api/batch/prepare-send":
                    self._json({"ok": True, **prepare_batch_send(self._body())})
                    return
                if self.command == "POST" and route == "/api/batch/confirm-send":
                    self._json({"ok": True, **confirm_batch_send(self._body())})
                    return
                if self.command == "POST" and route == "/api/batch/pay":
                    self._json({"ok": True, **batch_pay(self._body())})
                    return
                if self.command == "POST" and route == "/api/batch/suggest":
                    self._json({"ok": True, **batch_suggest(self._body())})
                    return
                if self.command == "POST" and route == "/api/import/preview":
                    try:
                        size = int(self.headers.get("Content-Length") or 0)
                    except ValueError as exc:
                        raise ApiError(400, "Tamanho inválido.") from exc
                    if size < 5 or size > pdf_workflows.MAX_PDF:
                        raise ApiError(413, "O PDF deve ter até 30 MB.")
                    from urllib.parse import unquote
                    filename = unquote(self.headers.get("X-Pdf-Name") or "boleto.pdf")
                    parsed = pdf_workflows.preview(
                        self.rfile.read(size), filename, APP_DIR,
                        pdf_intelligence.analyze_pdf,
                    )
                    self._json({"ok": True, **parsed})
                    return
                if self.command == "POST" and route == "/api/import/commit":
                    self._json({"ok": True, **create_account_from_preview(self._body())})
                    return
                if self.command == "POST" and route == "/api/import/cancel":
                    ticket = str(self._body().get("ticket") or "")
                    pdf_workflows.consume_preview(ticket)
                    self._json({"ok": True, "cancelled": True})
                    return
                if self.command == "GET" and route == "/api/accounts":
                    self._json({"ok": True, **list_accounts()})
                    return
                if self.command == "GET" and route == "/api/health":
                    self._json({"ok": True, "db": DB_PATH.is_file()})
                    return
                if self.command == "GET" and route == "/api/settings/folder":
                    self._json({"ok": True, **get_document_folder()})
                    return
                if self.command == "POST" and route == "/api/settings/folder":
                    self._json({"ok": True, **set_document_folder(self._body())})
                    return
                if self.command == "GET" and route == "/api/mobile-link":
                    self._json({"ok": True, "url": get_mobile_link()})
                    return
                if self.command == "POST" and route == "/api/control/stop":
                    self._body()
                    self._json({"ok": True, "message": "Servidor portátil finalizando."})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if len(parts) >= 3 and parts[:2] == ["api", "accounts"]:
                    try:
                        account_id = int(parts[2])
                        if account_id <= 0:
                            raise ValueError()
                    except ValueError as exc:
                        raise ApiError(400, "ID inválido.") from exc
                    op = parts[3] if len(parts) == 4 else ""
                    if self.command == "GET" and op == "analyze":
                        original = pdf_path(account_id)
                        self._json({"ok": True,
                                    **pdf_intelligence.analyze_pdf(original)})
                        return
                    if self.command == "GET" and op == "pdf":
                        receipt = urlsplit(self.path).query == "receipt=1"
                        path = pdf_path(account_id, receipt=receipt)
                        content = path.read_bytes()
                        self._send(content, mime="application/pdf")
                        return
                    if self.command == "POST" and op == "pay-with-receipt":
                        try:
                            size = int(self.headers.get("Content-Length") or 0)
                        except ValueError as exc:
                            raise ApiError(400, "Tamanho inválido.") from exc
                        if size < 5 or size > pdf_workflows.MAX_PDF:
                            raise ApiError(413, "Comprovante deve ter até 30 MB.")
                        paid_on = self.headers.get("X-Paid-Date") or ""
                        self._json({"ok": True, **register_payment_with_receipt(
                            account_id, paid_on, self.rfile.read(size)
                        )})
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
                        self._json({"ok": True, **result})
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
        except (pdf_intelligence.ReaderUnavailable,) as exc:
            self._json({"ok": False, "error": str(exc)}, 503)
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 500)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()


def main():
    pdf_workflows.cleanup_staging(APP_DIR)
    if not DB_PATH.is_file():
        print("Banco original não encontrado:", DB_PATH)
        print("O modo portátil não cria nem substitui um banco vazio.")
        raise SystemExit(2)
    try:
        httpd = LocalServer((HOST, PORT), Handler)
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
