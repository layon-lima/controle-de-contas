"""Operações de PDFs do portátil: prévia, duplicidade e mesclagem segura.

Nenhum cadastro é realizado pela fase de OCR. O usuário confirma os campos
antes de anexar o documento a um registro do banco.
"""
from __future__ import annotations

import hashlib
import io
import os
import secrets
import shutil
import time
from datetime import date
from pathlib import Path
from threading import RLock
from typing import Callable

MAX_PDF = 30 * 1024 * 1024
PREVIEW_SECONDS = 30 * 60
PREVIEW_LOCK = RLock()
PREVIEWS: dict[str, tuple[Path, float, str, dict]] = {}


def check_pdf(data: bytes):
    if not isinstance(data, bytes) or len(data) < 5 or not data.startswith(b"%PDF-"):
        raise ValueError("Selecione um arquivo PDF válido.")
    if len(data) > MAX_PDF:
        raise ValueError("Documento maior que 30 MB.")
    # Não confiar somente na extensão .pdf/na assinatura superficial.
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as document:
            if document.page_count < 1:
                raise ValueError("O PDF não contém páginas.")
    except (ValueError, RuntimeError) as exc:
        raise ValueError("O PDF está danificado ou não pode ser aberto.") from exc


def _purge_expired():
    now = time.monotonic()
    for ticket, (path, deadline, _filename, _fields) in list(PREVIEWS.items()):
        if deadline < now:
            PREVIEWS.pop(ticket, None)
            path.unlink(missing_ok=True)


def preview(data: bytes, filename: str, appdir: Path,
            analyzer: Callable[[Path], dict]) -> dict:
    check_pdf(data)
    temp_folder = appdir / "portable_staging"
    temp_folder.mkdir(parents=True, exist_ok=True)
    ticket = secrets.token_urlsafe(24)
    pdf = temp_folder / (ticket + ".pdf")
    pdf.write_bytes(data)
    try:
        read = analyzer(pdf)
    except Exception:
        pdf.unlink(missing_ok=True)
        raise
    if not isinstance(read, dict) or not isinstance(read.get("fields"), dict):
        pdf.unlink(missing_ok=True)
        raise RuntimeError("Análise do PDF não retornou campos válidos.")
    with PREVIEW_LOCK:
        _purge_expired()
        PREVIEWS[ticket] = (pdf, time.monotonic() + PREVIEW_SECONDS, filename, read["fields"])
    return {"ticket": ticket, **read}


def take_preview(ticket: str) -> tuple[Path, str, dict]:
    # Ticket é consumido uma única vez, mas se o salvamento falhar
    # mantemos a prévia para permitir corrigir campos e tentar novamente.
    if not isinstance(ticket, str) or len(ticket) > 128:
        raise ValueError("Prévia inválida.")
    with PREVIEW_LOCK:
        _purge_expired()
        record = PREVIEWS.get(ticket)
        if record is None:
            raise ValueError("Prévia expirada. Envie o PDF novamente.")
        path, deadline, filename, fields = record
        if deadline < time.monotonic() or not path.is_file():
            PREVIEWS.pop(ticket, None)
            raise ValueError("Prévia expirada. Envie o PDF novamente.")
        return path, filename, fields


def consume_preview(ticket: str) -> None:
    with PREVIEW_LOCK:
        record = PREVIEWS.pop(ticket, None)
        if record:
            record[0].unlink(missing_ok=True)


def pdf_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_duplicate(conn, pdf: Path, fields: dict, exclude_id: int | None = None):
    incoming_hash = pdf_hash(pdf)
    incoming_amount = round(float(fields.get("valor") or 0), 2)
    incoming_due = str(fields.get("vencimento") or "")
    incoming_code = "".join(c for c in str(fields.get("codigo_pagamento") or "") if c.isalnum())
    for row in conn.execute(
        "SELECT id,pdf_conta,valor,vencimento,codigo_pagamento,fornecedor FROM contas"
    ):
        if exclude_id and int(row["id"]) == exclude_id:
            continue
        original = Path(str(row["pdf_conta"] or "")) if row["pdf_conta"] else None
        if original and original.is_file():
            try:
                if original.stat().st_size == pdf.stat().st_size and pdf_hash(original) == incoming_hash:
                    raise ValueError("Este PDF já está cadastrado na conta " + str(row["id"]) + ".")
            except OSError:
                pass
        original_code = "".join(
            c for c in str(row["codigo_pagamento"] or "") if c.isalnum()
        )
        if not incoming_code or incoming_code != original_code:
            continue
        # Mesmas regras de duplicata do aplicativo instalado:
        # linha bancária igual é prova forte independentemente do fornecedor.
        if incoming_code.isdigit() and len(incoming_code) in {44, 46, 47, 48}:
            raise ValueError("Linha digitável já cadastrada na conta " + str(row["id"]) + ".")
        if incoming_code.startswith("000201") and len(incoming_code) >= 50:
            if (round(float(row["valor"] or 0), 2) == incoming_amount
                    and str(row["vencimento"] or "") == incoming_due):
                raise ValueError("PIX EMV já cadastrado na conta " + str(row["id"]) + ".")
        # Chave PIX estática, referência e fornecedor parecidos não bloqueiam
        # contas legítimas, ainda que seus valores e vencimentos coincidam.


def merge_receipt(original_pdf: Path | None, receipt_bytes: bytes,
                  output_pdf: Path) -> int:
    check_pdf(receipt_bytes)
    import fitz
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    if original_pdf is not None and original_pdf.is_file():
        with fitz.open(str(original_pdf)) as bill:
            with fitz.open(stream=receipt_bytes, filetype="pdf") as receipt:
                result = fitz.open()
                try:
                    result.insert_pdf(bill)
                    result.insert_pdf(receipt)
                    expected_pages = bill.page_count + receipt.page_count
                    result.save(str(output_pdf), garbage=4, deflate=True)
                finally:
                    result.close()
    else:
        with fitz.open(stream=receipt_bytes, filetype="pdf") as receipt:
            expected_pages = receipt.page_count
            receipt.save(str(output_pdf), garbage=4, deflate=True)
    with fitz.open(str(output_pdf)) as verify:
        if verify.page_count != expected_pages:
            output_pdf.unlink(missing_ok=True)
            raise RuntimeError("Falha ao validar as páginas do PDF mesclado.")
    return expected_pages
