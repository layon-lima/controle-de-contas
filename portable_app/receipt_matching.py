"""Conferencia conservadora entre comprovantes e boletos; nao altera PDFs/DB.

Mesmo recebedor, valor e vencimento NAO sao identidade de uma cobranca.
Compara apenas identificadores de mesmo tipo presentes nos dois documentos.
EndToEndId identifica uma TRANSACAO, nao e o TXID da cobranca.
"""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from pathlib import Path

MAX_SCAN_PAGES = 3
MAX_SCAN_TEXT = 85000


def pdf_text(data: bytes | Path) -> str:
    """Texto extraido do PDF sem executar OCR pesado sobre PDFs inteiros."""
    import fitz
    with (fitz.open(str(data)) if isinstance(data, Path)
          else fitz.open(stream=data, filetype="pdf")) as pdf:
        pages = []
        for n in range(min(len(pdf), MAX_SCAN_PAGES)):
            pages.append(pdf[n].get_text("text", sort=True) or "")
        return "\n".join(pages)[:MAX_SCAN_TEXT]


def _clean_text(value: object) -> str:
    t = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).upper()


def _digits(text: object) -> str:
    return re.sub(r"\D", "", str(text or ""))


def _bank_code(value: object) -> str:
    digits = _digits(value)
    return digits if len(digits) in (44, 47, 48) else ""


def _strong_pix(value: object) -> str:
    raw = re.sub(r"\s+", "", str(value or "")).upper()
    # PIX estatico e uma chave reutilizavel; nunca afirmar equivalencia.
    if raw.startswith("000201010212") and len(raw) >= 60:
        return raw
    return ""


def _labels(text: str) -> dict[str, set[str]]:
    """Busca SOMENTE codigos rotulados, para evitar false positives."""
    norm = _clean_text(text)
    found = {"boleto": set(), "pix": set(), "nosso_numero": set(),
             "txid": set(), "e2e": set()}
    lines = norm.splitlines()
    for number, line in enumerate(lines):
        text_line = " ".join(line.split())
        following = " ".join(lines[number + 1].split()) if number + 1 < len(lines) else ""
        if re.search(r"LINHA DIGITAVEL|CODIGO DE BARRAS|CODIGO DO BOLETO", text_line):
            for m in re.finditer(r"[0-9.\s-]{44,108}", text_line + " " + following):
                code = _bank_code(m.group(0))
                if code:
                    found["boleto"].add(code)
        if "000201010212" in text_line and len(text_line) < 1500:
            for m in re.finditer(r"000201010212\S{48,1000}", text_line):
                code = _strong_pix(m.group(0))
                if code:
                    found["pix"].add(code)
        if re.search(r"NOSSO NUMERO", text_line):
            m = re.search(r"NOSSO NUMERO\s*[:#-]?\s*([A-Z0-9./-]{6,35})",
                          text_line + " " + following)
            if m:
                identifier = re.sub(r"[^A-Z0-9]", "", m.group(1))
                if len(_digits(identifier)) >= 5:
                    found["nosso_numero"].add(identifier)
        if re.search(r"\bTX\s*ID\b|\bTXID\b|ID DA COBRANCA", text_line):
            m = re.search(r"(?:TX\s*ID|TXID|ID DA COBRANCA)\s*[:#-]?\s*([A-Z0-9._-]{8,40})", text_line)
            if m:
                found["txid"].add(m.group(1).strip("._-"))
        # O identificador E2E da transacao e usado apenas para impedir
        # reaproveitar o MESMO pagamento. Nao se compara ao TXID do boleto.
        m = re.search(r"\bE[0-9]{8}[A-Z0-9]{20,30}\b", text_line)
        if m:
            found["e2e"].add(m.group(0))
    return found


def _account_codes(info: dict, *, include_pdf: bool = False) -> dict[str, set[str]]:
    codes = {"boleto": set(), "pix": set(), "nosso_numero": set(), "txid": set()}
    raw = str(info.get("codigo_pagamento") or "")
    bank = _bank_code(raw)
    pix = _strong_pix(raw)
    if bank:
        codes["boleto"].add(bank)
    if pix:
        codes["pix"].add(pix)
    if include_pdf:
        filename = str(info.get("pdf_conta") or "").strip()
        if filename:
            try:
                additional = _labels(pdf_text(Path(filename)))
                for key in codes:
                    codes[key].update(additional[key])
            except (OSError, ValueError, RuntimeError):
                pass
    return codes


def _same_details(a: dict, b: dict) -> bool:
    try:
        amount_equal = abs(float(a.get("valor") or 0) - float(b.get("valor") or 0)) < .005
    except (ValueError, TypeError):
        return False
    return bool(amount_equal and
                str(a.get("vencimento") or "")[:10] == str(b.get("vencimento") or "")[:10] and
                " ".join(_clean_text(a.get("fornecedor")).split()) ==
                " ".join(_clean_text(b.get("fornecedor")).split()))


def verify_receipt(receipt: bytes, selected: dict,
                   accounts: list[dict], past_proofs: list[str]) -> dict:
    """Retorna diagnostico; quem chama decide se exige confirmacao humana."""
    text = pdf_text(receipt)
    markers = _labels(text)
    digest = hashlib.sha256(receipt).hexdigest()
    for record in past_proofs:
        if "SHA256:" + digest in str(record or ""):
            return {"status": "conflict", "reason": "Este comprovante já foi usado em outro pagamento.",
                    "matched_account_id": None, "candidate_ids": [],
                    "manual_required": False, "sha256": digest, "e2e": ""}
    for e2e in markers["e2e"]:
        for record in past_proofs:
            if "E2E:" + e2e in str(record or ""):
                return {"status": "conflict", "reason": "Este identificador E2E já foi registrado em outro pagamento.",
                        "matched_account_id": None, "candidate_ids": [],
                        "manual_required": False, "sha256": digest, "e2e": e2e}
    selected_id = int(selected["id"])
    related = [a for a in accounts if _same_details(a, selected)]
    by_id = {int(a["id"]): a for a in accounts}
    # Primeiro, comparar com os identificadores digitados das contas,
    # mesmo quando valor, vencimento ou recebedor forem diferentes.
    matches = []
    for aid, account in by_id.items():
        codes = _account_codes(account, include_pdf=(
            aid == selected_id or
            (any(markers[k] for k in ("nosso_numero", "txid")) and
             (_same_details(account, selected) or
              _clean_text(account.get("fornecedor")) == _clean_text(selected.get("fornecedor"))))
        ))
        common = [(k, code) for k in ("boleto", "pix", "nosso_numero", "txid")
                  for code in (codes[k] & markers[k]) if code]
        if common:
            matches.append((aid, common))
    ids = sorted(aid for aid, _ in matches)
    if len(ids) == 1:
        if ids[0] != selected_id:
            return {"status": "conflict",
                    "reason": "O identificador do comprovante corresponde à conta " +
                              str(ids[0]) + ", não à conta " + str(selected_id) + ".",
                    "matched_account_id": ids[0], "candidate_ids": ids,
                    "manual_required": False, "sha256": digest,
                    "e2e": sorted(markers["e2e"])[0] if markers["e2e"] else ""}
        return {"status": "verified", "reason": "Identificador de cobrança confirmado no boleto e no comprovante.",
                "matched_account_id": selected_id, "candidate_ids": ids,
                "manual_required": False, "sha256": digest,
                "e2e": sorted(markers["e2e"])[0] if markers["e2e"] else ""}
    if len(ids) > 1:
        warning = "O mesmo identificador apareceu em mais de uma conta. Selecione e confira manualmente."
        candidates = ids
    elif len(related) > 1:
        warning = ("Existem " + str(len(related)) +
                   " contas com mesmo recebedor, valor e vencimento; nenhum identificador exclusivo foi confirmado.")
        candidates = sorted(int(a["id"]) for a in related)
    else:
        warning = "O comprovante não apresenta identificador comparável ao boleto. Associação não verificada."
        candidates = [selected_id]
    return {"status": "ambiguous" if len(candidates) > 1 else "unverified",
            "reason": warning, "matched_account_id": None,
            "candidate_ids": candidates, "manual_required": len(candidates) > 1,
            "sha256": digest, "e2e": sorted(markers["e2e"])[0] if markers["e2e"] else ""}

def discover_receipt(receipt: bytes, accounts: list[dict],
                     past_proofs: list[str]) -> dict:
    """Descobre a conta SEM modificar nada e sem presumir identidade por valor."""
    text = pdf_text(receipt)
    identifiers = _labels(text)
    digest = hashlib.sha256(receipt).hexdigest()
    e2e_ids = sorted(identifiers["e2e"])
    for previous in past_proofs:
        if "SHA256:" + digest in str(previous or ""):
            return {"status": "conflict", "reason": "Este comprovante já foi utilizado em um pagamento.",
                    "matched_account_id": None, "candidate_ids": [], "receipt_value": None}
        if any("E2E:" + e2e in str(previous or "") for e2e in e2e_ids):
            return {"status": "conflict", "reason": "Esta transação PIX já foi registrada anteriormente.",
                    "matched_account_id": None, "candidate_ids": [], "receipt_value": None}

    unpaid = [a for a in accounts if not a.get("is_paid")]
    matched = []
    for a in accounts:
        codes = _account_codes(a, include_pdf=bool(
            identifiers["nosso_numero"] or identifiers["txid"]))
        if any(codes[key] & identifiers[key] for key in
               ("boleto", "pix", "nosso_numero", "txid")):
            matched.append(int(a["id"]))
    matched = sorted(set(matched))
    if len(matched) == 1:
        return {"status": "verified",
                "reason": "Identificador de cobrança encontrado em uma conta cadastrada.",
                "matched_account_id": matched[0],
                "candidate_ids": matched, "receipt_value": None}
    if len(matched) > 1:
        return {"status": "ambiguous",
                "reason": "O identificador apareceu em várias contas. Selecione a correta após conferir os PDFs.",
                "matched_account_id": None,
                "candidate_ids": matched, "receipt_value": None}

    # Somente SUGESTAO visual por valor, nunca identidade confirmada.
    amounts = set()
    for value in re.findall(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})", text,
                            flags=re.IGNORECASE):
        try:
            amounts.add(round(float(value.replace(".", "").replace(",", ".")), 2))
        except ValueError:
            continue
    receipt_value = next(iter(amounts)) if len(amounts) == 1 else None
    candidates = []
    if receipt_value is not None:
        candidates = [int(a["id"]) for a in unpaid
                      if abs(float(a.get("valor") or 0) - receipt_value) < .005]
    if candidates:
        reason = ("Nenhum identificador exclusivo encontrado. Estas contas têm o "
                  "mesmo valor exibido no comprovante; confira antes de escolher.")
    else:
        reason = ("Não foi possível identificar a cobrança com segurança. "
                  "Escolha a conta manualmente e confira os dados.")
    return {"status": "unverified", "reason": reason,
            "matched_account_id": None,
            "candidate_ids": sorted(candidates),
            "receipt_value": receipt_value}
