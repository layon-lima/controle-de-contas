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


def cleanup_staging(appdir: Path):
    """Apaga PDFs temporários órfãos após uma reinicialização do servidor."""
    directory = appdir / "portable_staging"
    if not directory.is_dir():
        return
    now = time.time()
    with PREVIEW_LOCK:
        live = {record[0] for record in PREVIEWS.values()}
        for file in directory.glob("*.pdf"):
            if file in live:
                continue
            try:
                if now - file.stat().st_mtime > PREVIEW_SECONDS:
                    file.unlink(missing_ok=True)
            except OSError:
                pass


def _purge_expired():
    now = time.monotonic()
    for ticket, (path, deadline, _filename, _fields) in list(PREVIEWS.items()):
        if deadline < now:
            PREVIEWS.pop(ticket, None)
            path.unlink(missing_ok=True)


def preview(data: bytes, filename: str, appdir: Path,
            analyzer: Callable[[Path], dict]) -> dict:
    check_pdf(data)
    # Nome de origem (sem diretórios enviados pelo navegador), nunca o nome
    # temporário do OCR. Usado somente como padrão no NOVO cadastro.
    original_filename = Path(str(filename or "").replace("\\", "/")).name.strip()
    if not original_filename.lower().endswith(".pdf"):
        original_filename = "BOLETO.pdf"
    temp_folder = appdir / "portable_staging"
    temp_folder.mkdir(parents=True, exist_ok=True)
    cleanup_staging(appdir)
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
        PREVIEWS[ticket] = (pdf, time.monotonic() + PREVIEW_SECONDS, original_filename, read["fields"])
    return {"ticket": ticket, **read, "original_filename": original_filename}


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


def _normalized_payment_code(value):
    # Separadores da linha digitavel nao mudam a identidade do pagamento.
    return "".join(ch for ch in str(value or "") if ch.isalnum())


def _is_unique_payment_code(code):
    # Linha/barras bancarios: forte identificador da propria cobranca.
    return code.isdigit() and len(code) in (44, 46, 47, 48)


def _same_dynamic_pix(code, original, incoming_amount, incoming_due):
    # A chave PIX estatica pode ser reutilizada; so bloquear o PIX EMV
    # repetido quando VALOR e VENCIMENTO tambem forem iguais.
    return (
        code.startswith("000201") and len(code) >= 50
        and round(float(original["valor"] or 0), 2) == incoming_amount
        and str(original["vencimento"] or "") == incoming_due
    )


def check_duplicate_fields(conn, fields: dict, exclude_id: int | None = None):
    """Bloqueia identificadores de pagamento fortes, inclusive cadastro manual.

    Mesmo fornecedor, valor e vencimento, SEM identificador unico, nao bastam
    para barrar duas contas legitimas (ex.: duas notas no mesmo dia).
    """
    incoming_code = _normalized_payment_code(fields.get("codigo_pagamento"))
    if not incoming_code:
        return
    if not _is_unique_payment_code(incoming_code) and not (
        incoming_code.startswith("000201") and len(incoming_code) >= 50
    ):
        return
    incoming_amount = round(float(fields.get("valor") or 0), 2)
    incoming_due = str(fields.get("vencimento") or "")
    for row in conn.execute(
        "SELECT id,valor,vencimento,codigo_pagamento FROM contas"
    ):
        if exclude_id is not None and int(row["id"]) == exclude_id:
            continue
        original_code = _normalized_payment_code(row["codigo_pagamento"])
        if incoming_code != original_code:
            continue
        if _is_unique_payment_code(incoming_code):
            raise ValueError(
                "Linha digitavel ja cadastrada na conta " + str(row["id"]) + "."
            )
        if _same_dynamic_pix(incoming_code, row, incoming_amount, incoming_due):
            raise ValueError("PIX EMV ja cadastrado na conta " + str(row["id"]) + ".")


def check_duplicate(conn, pdf: Path, fields: dict, exclude_id: int | None = None,
                    *, remote_metadata=None, remote_read=None):
    """Compara arquivo e identificadores tambem quando original esta no R2.

    Como PDFs pagos ficam somente no Windows, a deteccao destes pelo hash
    depende de copia local acessivel. Codigos fortes continuam sendo
    comparados contra TODAS as contas, pagas ou abertas.
    """
    check_duplicate_fields(conn, fields, exclude_id=exclude_id)
    incoming_hash = pdf_hash(pdf)
    incoming_size = pdf.stat().st_size
    incoming_amount = round(float(fields.get("valor") or 0), 2)
    incoming_due = str(fields.get("vencimento") or "")
    incoming_supplier = " ".join(str(fields.get("fornecedor") or "").casefold().split())
    for row in conn.execute(
        "SELECT id,pdf_conta,fornecedor,valor,vencimento FROM contas"
    ):
        aid = int(row["id"])
        if exclude_id is not None and aid == exclude_id:
            continue
        original = Path(str(row["pdf_conta"] or "")) if row["pdf_conta"] else None
        if original and original.is_file():
            try:
                if original.stat().st_size == incoming_size and pdf_hash(original) == incoming_hash:
                    raise ValueError("Este PDF ja esta cadastrado na conta " + str(aid) + ".")
            except OSError:
                pass

        # PDFs no Windows nao sao legiveis no filesystem Linux da VPS.
        # Revisar candidatos com valor+vencimento iguais OU mesmo fornecedor
        # e ao menos um dos dois campos igual. Assim um erro isolado no OCR
        # nao impede comparar o PDF remoto, sem varrer todo o R2.
        same_amount = round(float(row["valor"] or 0), 2) == incoming_amount
        same_due = str(row["vencimento"] or "") == incoming_due
        same_supplier = bool(
            incoming_supplier
            and incoming_supplier ==
            " ".join(str(row["fornecedor"] or "").casefold().split())
        )
        if not remote_metadata or not (
            (same_amount and same_due)
            or (same_supplier and (same_amount or same_due))
        ):
            continue
        meta = remote_metadata(aid)
        if not meta:
            continue
        size = int(meta.get("size") or 0)
        if size and size != incoming_size:
            continue
        digest = str(meta.get("hash") or "").lower()
        if digest:
            if digest == incoming_hash:
                raise ValueError("Este PDF ja esta cadastrado na conta " + str(aid) + " (R2).")
        elif remote_read:
            # PDFs antigos do Worker podem nao ter metadado sha256:
            # conferir os bytes originais, sem apagar ou modificar o R2.
            content = remote_read(aid)
            if len(content) == incoming_size and hashlib.sha256(content).hexdigest() == incoming_hash:
                raise ValueError("Este PDF ja esta cadastrado na conta " + str(aid) + " (R2).")


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
