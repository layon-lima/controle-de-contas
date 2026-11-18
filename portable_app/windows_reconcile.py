"""Manifesto de PDFs para espelhamento Windows e recuperação retroativa.

Somente leitura: não expõe caminhos internos, arquivos ou credenciais.
Cada hash corresponde aos bytes reais disponíveis na VPS ou no R2.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
from pathlib import Path


@lru_cache(maxsize=4096)
def _hash_file(path: str, size: int, modified_ns: int) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def pdf_manifest(conn, r2, *, max_accounts=30000):
    rows = conn.execute(
        "SELECT id,vencimento,observacoes,pdf_conta FROM contas ORDER BY id"
    ).fetchall()
    if len(rows) > max_accounts:
        raise ValueError("O volume de contas excede o limite da conciliação.")

    # Uma listagem R2 por execução, não uma requisição por lançamento.
    remote_ids = set()
    r2_error = False
    if r2.enabled():
        try:
            remote_ids = r2.existing_ids()
        except r2.R2Unavailable:
            r2_error = True
    items, missing, pending_cloud = [], 0, 0
    for row in rows:
        aid = int(row["id"])
        local = Path(str(row["pdf_conta"] or "").strip())
        local_file = bool(row["pdf_conta"] and local.is_file() and
                          local.suffix.lower() == ".pdf")
        remote_file = aid in remote_ids
        if not local_file and not remote_file:
            missing += 1
            continue
        signature, size = "", 0
        if local_file:
            try:
                stat = local.stat()
                size = stat.st_size
                if size < 5 or size > 30 * 1024 * 1024:
                    raise ValueError("Tamanho invalido.")
                signature = _hash_file(str(local), size, stat.st_mtime_ns)
            except (OSError, ValueError):
                if not remote_file:
                    missing += 1
                    continue
        if remote_file and not signature:
            try:
                metadata = r2.metadata(aid) or {}
                signature = str(metadata.get("hash") or "")
                size = int(metadata.get("size") or 0)
            except (ValueError, r2.R2Unavailable):
                # O arquivo segue na lista para ser tentado posteriormente.
                signature, size = "", 0
        if local_file and not remote_file and r2.enabled() and not r2_error:
            pending_cloud += 1
        items.append({
            "id": aid,
            "vencimento": str(row["vencimento"] or ""),
            "observacoes": str(row["observacoes"] or ""),
            "sha256": signature,
            "size": size,
        })
    return {
        "items": items,
        "available": len(items),
        "missing_source": missing,
        "r2_missing": pending_cloud,
        "r2_unavailable": r2_error,
    }


def upload_missing_to_r2(conn, r2, *, max_uploads=50):
    """Idempotente: envia apenas PDFs locais cuja chave ainda não existe no R2.

    Nunca sobrescreve um PDF remoto existente com cópia local possivelmente
    antiga. Um conflito requer comparação e aprovação separadas.
    """
    if not r2.enabled():
        return {"uploaded": 0, "remaining": 0, "failures": 0}
    remote_ids = r2.existing_ids()
    rows = conn.execute(
        "SELECT id,pdf_conta FROM contas ORDER BY id"
    ).fetchall()
    uploaded = 0
    failures = 0
    remaining = 0
    for row in rows:
        aid = int(row["id"])
        if aid in remote_ids:
            continue
        local = Path(str(row["pdf_conta"] or "").strip())
        if not row["pdf_conta"] or local.suffix.lower() != ".pdf" or not local.is_file():
            continue
        if uploaded >= max_uploads:
            remaining += 1
            continue
        try:
            if local.stat().st_size > r2.MAX_PDF:
                raise ValueError("PDF muito grande para o R2.")
            data = local.read_bytes()
            if not data.startswith(b"%PDF-"):
                raise ValueError("PDF invalido.")
            # Mesmo sem preencher uma tabela auxiliar, o R2 já será o
            # backup de segurança para novas sessões do Windows.
            r2.write_pdf(aid, data)
            uploaded += 1
        except (OSError, ValueError, r2.R2Unavailable):
            failures += 1
    return {"uploaded": uploaded, "remaining": remaining, "failures": failures}
