"""OCR híbrido conservador para o Controle de Contas Web.

Camada ADITIVA sobre o leitor já aprovado do desktop:
- reaproveita texto digital antes de fazer OCR redundante;
- só faz uma segunda leitura (imagens tratadas) de PDFs sem texto suficiente;
- extrai campos com rótulos explícitos e valida data, moeda e linha digitável;
- NUNCA altera um campo válido já retornado pelo leitor original;
- registra divergências para conferência, sem gravar PDFs, perfis ou banco.
"""
from __future__ import annotations

import math
import re
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

FIELDS = ("fornecedor", "pagador", "vencimento", "valor", "codigo_pagamento")
VALUE_LABELS = (
    r"valor(?:\s+(?:total|do\s+documento|a\s+pagar|cobrado))?",
    r"total\s+a\s+pagar",
    r"valor\s+da\s+cobranca",
)
DUE_LABELS = (r"vencimento", r"data\s+de\s+vencimento", r"venc\.")
RECEIVER_LABELS = (r"beneficiario", r"recebedor", r"cedente", r"favorecido")
PAYER_LABELS = (r"pagador", r"sacado")
LINE_LABELS = (r"linha\s+digitavel", r"codigo\s+de\s+barras", r"codigo\s+para\s+pagamento")


def folded(text: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in raw if not unicodedata.combining(ch)).casefold()


def _labeled(lines: list[str], patterns: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    label = "(?:" + "|".join(patterns) + ")"
    regex = re.compile(r"^\s*" + label + r"\s*(?:[:\-–]\s*|\s{2,})(.+?)\s*$", re.I)
    label_only = re.compile(r"^\s*" + label + r"\s*:?\s*$", re.I)
    for index, line in enumerate(lines):
        clean = folded(line).strip()
        match = regex.match(clean)
        if match:
            values.append(match.group(1).strip())
        elif label_only.match(clean) and index + 1 < len(lines):
            # Alguns PDFs digitais separam rótulo e valor em linhas.
            # A validação do campo rejeitará candidatos sem formato válido.
            following = folded(lines[index + 1]).strip()
            if len(following) <= 120:
                values.append(following)
    return values


def amount(text: Any) -> float | None:
    # Somente reais brasileiros claramente formatados; nunca inferir decimais.
    match = re.search(r"(?:r\$\s*)?((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\b", str(text))
    if not match:
        return None
    # Nunca converter -R$ 2,00 ou R$ -2,00 em valor positivo.
    if str(text)[:match.start()].rstrip().endswith("-"):
        return None
    raw = match.group(1).replace(".", "").replace(",", ".")
    try:
        parsed = float(raw)
    except ValueError:
        return None
    return round(parsed, 2) if math.isfinite(parsed) and 0 < parsed < 1e12 else None


def due_date(text: Any) -> str | None:
    raw = str(text)
    match = re.search(r"\b(\d{2})[/-](\d{2})[/-](\d{4})\b", raw)
    if not match:
        return None
    d, m, y = (int(v) for v in match.groups())
    try:
        parsed = date(y, m, d)
    except ValueError:
        return None
    return parsed.isoformat() if 2000 <= y <= 2100 else None


def _mod10(digits: str) -> int:
    total = 0
    for index, char in enumerate(reversed(digits)):
        multiplier = 2 if index % 2 == 0 else 1
        product = int(char) * multiplier
        total += product // 10 + product % 10
    return (10 - total % 10) % 10


def valid_bank_slip(code: str) -> bool:
    """Confere os três dígitos verificadores de uma linha bancária de 47 dígitos."""
    if not re.fullmatch(r"\d{47}", code):
        return False
    return all((
        _mod10(code[0:9]) == int(code[9]),
        _mod10(code[10:20]) == int(code[20]),
        _mod10(code[21:31]) == int(code[31]),
    ))


def _mod11_collection(digits: str) -> int:
    total = 0
    weight = 2
    for ch in reversed(digits):
        total += int(ch) * weight
        weight = 2 if weight == 9 else weight + 1
    rest = total % 11
    calculated = 11 - rest
    return 0 if calculated in (0, 10, 11) else calculated


def valid_collection_slip(code: str) -> bool:
    """Linha de arrecadação de 48 dígitos (tributos/concessionárias)."""
    if not re.fullmatch(r"8\d{47}", code):
        return False
    reference = code[2]
    if reference not in "6789":
        return False
    validator = _mod10 if reference in "67" else _mod11_collection
    return all(validator(code[i:i + 11]) == int(code[i + 11])
               for i in range(0, 48, 12))


def valid_pix_emv(code: str) -> bool:
    """CRC-16/CCITT-FALSE do payload BR Code, sem supor validade da chave PIX."""
    payload = str(code or "").strip()
    if not payload.startswith("000201") or len(payload) > 1024:
        return False
    if not re.fullmatch(r"[A-Za-z0-9 .*/+:_@\-]+", payload):
        return False
    match = re.search(r"6304([0-9A-Fa-f]{4})$", payload)
    if match is None:
        return False
    crc = 0xFFFF
    for byte in payload[:-4].encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc == int(match.group(1), 16)


def payment_code(text: str) -> str | None:
    raw = str(text).strip()
    cleaned = re.sub(r"[^0-9]", "", raw)
    if valid_bank_slip(cleaned) or valid_collection_slip(cleaned):
        return cleaned
    if valid_pix_emv(raw):
        return raw
    return None


def _name(text: str) -> str | None:
    candidate = " ".join(str(text).strip().split()).strip(".,;:- ")
    if not 5 <= len(candidate) <= 95:
        return None
    if len(re.findall(r"\d", candidate)) > 5 or not re.search(r"[a-zA-ZÀ-ÿ]{3}", candidate):
        return None
    if any(word in folded(candidate) for word in ("vencimento", "valor total", "documento:", "codigo")):
        return None
    return candidate


def _unique(options: list, kind) -> Any | None:
    normalized = [kind(v) for v in options]
    good = [v for v in normalized if v is not None]
    if not good:
        return None
    # Mais de um valor plausível no mesmo campo => não escolher.
    return good[0] if all(v == good[0] for v in good) else None


def read_labeled_text(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    # Processar apenas rótulos explícitos; não procurar "primeiro valor" do PDF.
    result = {
        "fornecedor": _unique(_labeled(lines, RECEIVER_LABELS), _name),
        "pagador": _unique(_labeled(lines, PAYER_LABELS), _name),
        "vencimento": _unique(_labeled(lines, DUE_LABELS), due_date),
        "valor": _unique(_labeled(lines, VALUE_LABELS), amount),
        "codigo_pagamento": _unique(_labeled(lines, LINE_LABELS), payment_code),
    }
    return {key: value for key, value in result.items() if value is not None}


def _read_digital(path: Path) -> tuple[str, int]:
    import fitz
    with fitz.open(str(path)) as pdf:
        pages = [page.get_text("text", sort=True) for page in list(pdf)[:4]]
    text = "\n".join(pages)
    return text[:80_000], len(text.strip())


def _read_scanned(path: Path) -> str:
    """Nova tentativa limitada a 2 páginas; não substitui o OCR original."""
    try:
        import cv2
        import numpy as np
        import fitz
        import pytesseract
    except ImportError:
        return ""
    # O Windows pode ter Tesseract fora do PATH. Usar o mesmo executável
    # em português preservado pelo aplicativo desktop, se disponível.
    try:
        if __package__:
            from .pdf_intelligence import find_tesseract_binary
        else:
            from pdf_intelligence import find_tesseract_binary
        executable = find_tesseract_binary()
        if executable:
            pytesseract.pytesseract.tesseract_cmd = str(executable)
        languages = set(pytesseract.get_languages(config=""))
        language = "por+eng" if {"por", "eng"}.issubset(languages) else (
            "por" if "por" in languages else "eng"
        )
    except (OSError, RuntimeError):
        language = "por+eng"
    outputs: list[str] = []
    with fitz.open(str(path)) as pdf:
        for page in list(pdf)[:2]:
            width, height = page.rect.width, page.rect.height
            if width <= 0 or height <= 0:
                continue
            # Páginas gigantes/anômalas jamais geram bitmaps ilimitados.
            scale = min(2.0, math.sqrt(6_000_000 / (width * height)))
            if scale < 0.3:
                continue
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
            # Nitidez e contraste local para imagem escaneada com fundo irregular.
            processed = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(image)
            try:
                words = pytesseract.image_to_string(
                    processed, lang=language, config="--psm 6", timeout=12
                )
            except (RuntimeError, OSError):
                continue
            if words:
                outputs.append(words[:25_000])
    return "\n".join(outputs)


def _missing(value: Any) -> bool:
    return value is None or str(value).strip() in ("", "0", "0.0", "0,00")


def _comparable(key: str, value: Any) -> str:
    if key == "valor":
        try:
            return f"{float(str(value).replace(',', '.')):.2f}"
        except ValueError:
            return str(value)
    if key == "codigo_pagamento":
        return re.sub(r"[\s.\-]", "", str(value))
    return folded(value).strip()


def enhance_pdf(path: Path, baseline: dict) -> dict:
    """Retorna o resultado original + preenchimentos seguros, nunca substituições.

    Qualquer falha é sinalizada ao chamador, que conserva integralmente o
    resultado original; esta função não modifica PDF, banco ou aprendizado.
    """
    initial = dict(baseline)
    initial["fields"] = dict(baseline.get("fields") or {})
    initial["warnings"] = list(baseline.get("warnings") or [])
    started = time.monotonic()
    text, size = _read_digital(path)
    candidate = read_labeled_text(text)
    sources = {name: "texto_digital" for name in candidate}
    missing_critical = sum(_missing(initial["fields"].get(key))
                           for key in ("valor", "vencimento", "codigo_pagamento"))
    # Escaneados: melhorar só quando a leitura original deixou >=2 campos
    # críticos vazios; não repetir OCR caro em documentos que já estão bons.
    if size < 80 and missing_critical >= 2:
        scanned = read_labeled_text(_read_scanned(path))
        for key, value in scanned.items():
            if key not in candidate:
                candidate[key] = value
                sources[key] = "imagem_tratada"
    filled: list[str] = []
    conflicts: list[str] = []
    for key, value in candidate.items():
        existing = initial["fields"].get(key)
        if _missing(existing):
            initial["fields"][key] = value
            filled.append(key)
        elif _comparable(key, existing) != _comparable(key, value):
            conflicts.append(key)
    if filled:
        initial["warnings"].append(
            "Leitura complementar preencheu: " + ", ".join(filled)
            + ". Confirme esses campos antes de salvar."
        )
    if conflicts:
        initial["warnings"].append(
            "Valores diferentes encontrados na leitura complementar para: "
            + ", ".join(conflicts) + ". Mantive os valores do leitor original."
        )
    initial["ocr_comparison"] = {
        "mode": "conservador",
        "candidate_fields": len(candidate),
        "filled": filled,
        "conflicts": conflicts,
        "sources": {key: sources[key] for key in filled},
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "legacy_preserved": True,
    }
    return initial
