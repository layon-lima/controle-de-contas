"""Conferencia conservadora entre comprovantes e boletos; nao altera PDFs/DB.

Mesmo recebedor, valor e vencimento NAO sao identidade de uma cobranca.
Compara apenas identificadores de mesmo tipo presentes nos dois documentos.
EndToEndId identifica uma TRANSACAO, nao e o TXID da cobranca.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime
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


def _pix_txid(payload: str) -> str:
    """Lê o TXID PIX do campo 62.05 apenas quando o EMV é bem formado."""
    raw = re.sub(r"\s+", "", str(payload or "")).upper()
    if not raw.startswith("000201"):
        return ""

    def decode_tlv(value: str) -> dict[str, list[str]]:
        fields: dict[str, list[str]] = {}
        pos = 0
        while pos + 4 <= len(value):
            tag, size = value[pos:pos+2], value[pos+2:pos+4]
            if not (tag.isdigit() and size.isdigit()):
                break
            pos += 4
            length = int(size)
            if pos + length > len(value):
                break
            fields.setdefault(tag, []).append(value[pos:pos+length])
            pos += length
        return fields

    fields = decode_tlv(raw)
    additional = fields.get("62", [""])[0]
    txid = decode_tlv(additional).get("05", [""])[0]
    clean = re.sub(r"[^A-Z0-9]", "", txid)
    return clean if 8 <= len(clean) <= 35 and clean not in ("NAOINFORMADO", "SEMID") else ""


def _receipt_facts(text: str) -> dict:
    """Extrai dados do comprovante em português, sem confundir data e vencimento."""
    normalized = _clean_text(text)

    def first(expression: str) -> str:
        found = re.search(expression, normalized, re.MULTILINE)
        return found.group(1) if found else ""

    def date_br(raw: str) -> str:
        if not raw:
            return ""
        try:
            return datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
        except ValueError:
            return ""

    numbers = re.findall(
        r"\bVALOR(?:\s+(?:PAGO|DA TRANSACAO))?\s*:\s*R\$\s*"
        r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})", normalized
    )
    amounts = {round(float(x.replace(".", "").replace(",", ".")), 2) for x in numbers}
    paid_value = next(iter(amounts)) if len(amounts) == 1 else None
    return {
        "receipt_value": paid_value,
        "receipt_due": date_br(first(
            r"\b(?:VENCIMENTO|DATA DE VENCIMENTO|DATA DO VENCIMENTO)"
            r"\s*:\s*(\d{2}/\d{2}/\d{4})")),
        "payment_date": date_br(first(r"\bDATA DO DEBITO\s*:\s*(\d{2}/\d{2}/\d{4})"))
                        or date_br(first(r"^\s*(\d{2}/\d{2}/\d{4})\s*-")),
        "recipient": (first(r"\bDADOS DE QUEM RECEBEU\s*\n\s*NOME\s*:\s*([^\n]+)")
                      or first(r"^\s*(?:RECEBEDOR|BENEFICIARIO|FAVORECIDO|"
                               r"DESTINATARIO|NOME DO RECEBEDOR|NOME DO FAVORECIDO)"
                               r"\s*:\s*([^\n]+)")),
        "payer": first(r"\bDADOS DE QUEM PAGOU\s*\n\s*NOME\s*:\s*([^\n]+)"),
    }


def _labels(text: str) -> dict[str, set[str]]:
    """Busca SOMENTE codigos rotulados, para evitar false positives."""
    norm = _clean_text(text)
    found = {"boleto": set(), "pix": set(), "nosso_numero": set(),
             "txid": set(), "identifier": set(), "e2e": set()}
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
        # Ex.: comprovante Pix Bradesco: "Identificador: 070126...".
        # É identificador da cobrança no comprovante, não o EndToEndId.
        source = text_line + (" " + following if text_line.endswith(":") else "")
        m = re.search(
            r"^(?:IDENTIFICADOR(?:\s+DA\s+COBRANCA)?|ID DA COBRANCA)"
            r"\s*[:#-]\s*([A-Z0-9._/-]{8,60})(?![A-Z0-9])", source
        )
        if m:
            code = re.sub(r"[^A-Z0-9]", "", m.group(1))
            if code and not re.fullmatch(r"E\d{8}[A-Z0-9]{20,30}", code):
                found["identifier"].add(code)
        # Campo Bradesco "Número de controle" é E2E de transação;
        # permite detecção de comprovante duplicado, não identifica boleto.
        m = re.search(
            r"(?:NUMERO DE CONTROLE|END\s*TO\s*END|E2E|ID DA TRANSACAO)"
            r"\s*[:#-]\s*(E\d{8}[A-Z0-9]{20,30})\b", text_line
        )
        if m:
            found["e2e"].add(m.group(1))
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
    codes = {"boleto": set(), "pix": set(), "nosso_numero": set(),
             "txid": set(), "identifier": set()}
    raw = str(info.get("codigo_pagamento") or "")
    bank = _bank_code(raw)
    pix = _strong_pix(raw)
    if bank:
        codes["boleto"].add(bank)
    if pix:
        codes["pix"].add(pix)
        txid = _pix_txid(pix)
        if txid:
            codes["txid"].add(txid)
            codes["identifier"].add(txid)
    # Códigos curtos exclusivos que aparecem no cadastro, como 26 dígitos.
    for value in re.findall(r"(?<![A-Z0-9])\d{20,35}(?![A-Z0-9])", raw.upper()):
        codes["identifier"].add(value)
    if include_pdf:
        filename = str(info.get("pdf_conta") or "").strip()
        if filename:
            try:
                raw_pdf = pdf_text(Path(filename))
                additional = _labels(raw_pdf)
                for key in codes:
                    codes[key].update(additional[key])
                # Encontrar IDs longos também sem rotulo, quando o texto
                # da guia traz o numero isolado no meio do documento.
                for number in re.findall(r"(?<!\d)\d{20,35}(?!\d)", raw_pdf):
                    codes["identifier"].add(number)
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
            (any(markers[k] for k in ("nosso_numero", "txid", "identifier")) and
             (_same_details(account, selected) or
              _clean_text(account.get("fornecedor")) == _clean_text(selected.get("fornecedor"))))
        ))
        common = [(k, code) for k in ("boleto", "pix", "nosso_numero", "txid", "identifier")
                  for code in (codes[k] & (markers[k] | (
                      markers["identifier"] if k == "txid" else set()
                  ))) if code]
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


def _darf_barcodes(text: str) -> set[str]:
    """Normaliza linha de arrecadação de 48 dígitos e código de barras de 44."""
    values: set[str] = set()
    for original in _clean_text(text).splitlines():
        line = original.strip()
        if not (line.startswith("8") or "CODIGO DE BARRAS" in line or
                "LINHA DIGITAVEL" in line):
            continue
        if ":" in line:
            line = line.split(":", 1)[1]
        pieces = re.findall(r"\d+", line)
        for start, fragment in enumerate(pieces):
            if not fragment.startswith("8") or len(fragment) < 10:
                continue
            digits = ""
            for part in pieces[start:]:
                digits += part
                if len(digits) == 44:
                    values.add(digits)
                if len(digits) == 48:
                    values.add("".join(digits[k:k+11] for k in (0, 12, 24, 36)))
                if len(digits) >= 48:
                    break
            break
    return values


def _darf_documents(text: str) -> set[str]:
    return {_digits(number) for number in re.findall(
        r"(?<!\d)\d{2}\.\d{2}\.\d{5}\.\d{7}-\d(?!\d)", _clean_text(text))}


def _darf_receipt_facts(text: str) -> dict:
    """Formato Bradesco: DARF numerado não contém recebedor nem vencimento."""
    norm = _clean_text(text)
    value = re.search(
        r"\bVALOR\s+TOTAL\s*:\s*(?:R\$\s*)?"
        r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})", norm)
    date_match = re.search(
        r"\bDATA DO PAGAMENTO\s*:\s*(\d{2}/\d{2}/\d{4})", norm)
    bank_id = re.search(
        r"(?m)^\s*NUMERO DE CONTROLE\s*:\s*(\d{8,25})\b", norm)
    payment_date = ""
    if date_match:
        try:
            payment_date = datetime.strptime(date_match.group(1), "%d/%m/%Y").date().isoformat()
        except ValueError:
            pass
    amount = (round(float(value.group(1).replace(".", "").replace(",", ".")), 2)
              if value else None)
    return {
        "receipt_type": "darf", "receipt_value": amount,
        "receipt_due": "", "payment_date": payment_date,
        "recipient": "", "payer": "",
        "payment_control": bank_id.group(1) if bank_id else "",
    }


def _eligible_darf(text: str, accounts: list[dict],
                   previous_proofs: list[str], digest: str) -> dict:
    """Seleciona todas as contas com o mesmo documento/código, sem quitar nenhuma."""
    facts = _darf_receipt_facts(text)
    base = {**facts, "matched_account_id": None, "candidate_ids": []}
    bank_id = facts["payment_control"]
    if any(("SHA256:" + digest in str(row or "") or
            (bank_id and "DARFCTRL:" + bank_id in str(row or "")))
           for row in previous_proofs):
        return {**base, "status": "conflict",
                "reason": "Este comprovante DARF já foi utilizado."}
    barcodes = _darf_barcodes(text)
    documents = _darf_documents(text)
    if facts["receipt_value"] is None or not (barcodes or documents):
        return {**base, "status": "unverified",
                "reason": "Não foi possível ler o valor e o código de barras "
                          "ou número de documento do DARF."}

    unpaid, paid = [], []
    for account in accounts:
        try:
            if abs(float(account.get("valor") or 0) - facts["receipt_value"]) >= .005:
                continue
        except (TypeError, ValueError):
            continue
        raw = str(account.get("codigo_pagamento") or "")
        account_barcodes = _darf_barcodes(raw)
        account_documents = _darf_documents(raw)
        if len(_digits(raw)) == 17 and not account_documents:
            account_documents.add(_digits(raw))
        pdf_name = str(account.get("pdf_conta") or "").strip()
        if pdf_name:
            try:
                account_text = pdf_text(Path(pdf_name))
                account_barcodes.update(_darf_barcodes(account_text))
                account_documents.update(_darf_documents(account_text))
            except (OSError, ValueError, RuntimeError):
                pass
        same_barcode = bool(barcodes & account_barcodes)
        same_document = bool(documents & account_documents)
        if ((barcodes and account_barcodes and not same_barcode) or
            (documents and account_documents and not same_document)):
            continue
        if same_barcode or same_document:
            (paid if account.get("is_paid") else unpaid).append(int(account["id"]))
    if unpaid:
        ids = sorted(set(unpaid))
        return {**base, "status": "eligible", "candidate_ids": ids,
                "reason": (str(len(ids)) + " conta(s) com número do DARF "
                           "ou código de barras correspondente. Confirme a escolhida.")}
    if paid:
        return {**base, "status": "conflict",
                "reason": "Somente contas já pagas correspondem a este DARF."}
    return {**base, "status": "unverified",
            "reason": "Nenhuma conta corresponde ao código de barras ou "
                      "número deste DARF. Nenhum PDF foi alterado."}


def _standard_name(value: object) -> str:
    """Normalize nomes do recebedor sem comparar só partes do nome."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", _clean_text(value)).split())


def eligible_receipt_accounts(receipt: bytes, accounts: list[dict],
                              past_proofs: list[str]) -> dict:
    """Retorna TODAS as contas em aberto com recebedor + valor + vencimento iguais.

    A elegibilidade não é prova de identidade. Não seleciona nem quita conta.
    """
    text = pdf_text(receipt)
    digest = hashlib.sha256(receipt).hexdigest()
    if re.search(r"COMPROVANTE\s+DE\s+PAGAMENTO\s+DARF\b",
                 _clean_text(text)):
        return _eligible_darf(text, accounts, past_proofs, digest)
    facts = _receipt_facts(text)
    markers = _labels(text)
    e2es = sorted(markers["e2e"])
    base = {**facts, "candidate_ids": [], "matched_account_id": None}

    for historical in past_proofs:
        old = str(historical or "")
        if "SHA256:" + digest in old or any("E2E:" + e2e in old for e2e in e2es):
            return {**base, "status": "conflict",
                    "reason": "Este comprovante ou transação já foi registrado."}

    recipient = _standard_name(facts.get("recipient"))
    value = facts.get("receipt_value")
    due = facts.get("receipt_due")
    if not recipient or value is None or not due:
        missing = []
        if not recipient:
            missing.append("recebedor")
        if value is None:
            missing.append("valor")
        if not due:
            missing.append("vencimento")
        return {**base, "status": "unverified",
                "reason": "Não foi possível ler: " + ", ".join(missing) + "."}

    valid = []
    paid = []
    for account in accounts:
        if _standard_name(account.get("fornecedor")) != recipient:
            continue
        if str(account.get("vencimento") or "")[:10] != due:
            continue
        try:
            account_value = round(float(account.get("valor") or 0), 2)
        except (TypeError, ValueError):
            continue
        if abs(account_value - value) > .005:
            continue
        (paid if account.get("is_paid") else valid).append(int(account["id"]))

    if valid:
        return {**base, "status": "eligible", "candidate_ids": sorted(valid),
                "reason": (str(len(valid)) +
                           " conta(s) em aberto com recebedor, valor e vencimento iguais. "
                           "A escolha e a confirmação são feitas pelo usuário.")}
    if paid:
        return {**base, "status": "conflict",
                "reason": "Somente contas já pagas correspondem a estes dados."}
    return {**base, "status": "unverified",
            "reason": "Nenhuma conta em aberto corresponde a recebedor, valor e vencimento."}


def discover_receipt(receipt: bytes, accounts: list[dict],
                     past_proofs: list[str]) -> dict:
    """Escolhe sozinho a conta SOMENTE com identificador único da cobrança."""
    text = pdf_text(receipt)
    markers = _labels(text)
    facts = _receipt_facts(text)
    digest = hashlib.sha256(receipt).hexdigest()
    e2e_ids = sorted(markers["e2e"])
    has_charge_id = any(markers[k] for k in
                        ("identifier", "txid", "boleto", "nosso_numero", "pix"))
    base = {**facts, "identifier_found": bool(has_charge_id),
            "matched_account_id": None, "candidate_ids": []}
    for record in past_proofs:
        if ("SHA256:" + digest in str(record or "")
                or any("E2E:" + e2e in str(record or "") for e2e in e2e_ids)):
            return {**base, "status": "conflict",
                    "reason": "Este comprovante ou transação já foi registrado."}

    matches = []
    for account in accounts:
        codes = _account_codes(account, include_pdf=bool(
            markers["nosso_numero"] or markers["identifier"] or markers["txid"]
        ))
        common = any(codes[k] & markers[k] for k in
                     ("boleto", "pix", "nosso_numero", "txid", "identifier"))
        # O recibo chama TXID de "Identificador"; o boleto pode usar "TXID".
        common = common or bool(codes["txid"] & markers["identifier"])
        if common:
            matches.append(account)
    ids = sorted({int(a["id"]) for a in matches})
    if len(ids) == 1:
        found = next(a for a in matches if int(a["id"]) == ids[0])
        if found.get("is_paid"):
            return {**base, "status": "conflict",
                    "reason": "O identificador aponta para uma conta já paga."}
        if facts["receipt_value"] is not None:
            try:
                amount = round(float(found.get("valor") or 0), 2)
            except (TypeError, ValueError):
                amount = None
            if amount != facts["receipt_value"]:
                return {**base, "status": "conflict",
                        "reason": "Identificador coincidente, mas o valor da cobrança é diferente."}
        if facts["receipt_due"] and str(found.get("vencimento") or "")[:10] != facts["receipt_due"]:
            return {**base, "status": "conflict",
                    "reason": "Identificador coincidente, mas o vencimento é diferente."}
        return {**base, "status": "verified",
                "reason": "Correspondência exclusiva pelo identificador da cobrança.",
                "matched_account_id": ids[0], "candidate_ids": ids}
    if len(ids) > 1:
        return {**base, "status": "ambiguous",
                "reason": "O identificador aparece em mais de uma conta.",
                "candidate_ids": ids}

    return {**base, "status": "unverified",
            "reason": ("O identificador do comprovante foi lido, mas não existe "
                       "correspondência nas cobranças cadastradas." if has_charge_id else
                       "Não foi possível ler um identificador de cobrança neste PDF.")}
