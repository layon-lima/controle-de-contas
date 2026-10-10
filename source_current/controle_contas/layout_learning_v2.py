from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# APRENDIZADO DE LAYOUT V2
# ---------------------------------------------------------------------------
# Este modulo e deliberadamente independente da interface, do banco e do mobile.
# Ele recebe um PDF + os valores confirmados pelo usuario e aprende a estrutura
# daquela pagina. Um layout novo nunca altera as regras de outro perfil.
#
# O arquivo V1 continua intacto. A V2 grava somente aprendizado_layouts_v2.json.
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2
FIELDS = (
    "recebedor",
    "pagador",
    "referencia",
    "vencimento",
    "valor",
    "codigo_pagamento",
)

# Rótulos/elementos estruturais. A assinatura usa estes elementos com posição,
# não nomes de pessoas, valores, datas ou números variáveis.
LABEL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("doc_arrecadacao", ("documento", "de", "arrecadacao")),
    ("receitas_federais", ("receitas", "federais")),
    ("guia_fgts", ("guia", "do", "fgts", "digital")),
    ("nome_beneficiario", ("nome", "do", "beneficiario")),
    ("beneficiario", ("beneficiario",)),
    ("favorecido", ("favorecido",)),
    ("recebedor", ("recebedor",)),
    ("cedente", ("cedente",)),
    ("fornecedor", ("fornecedor",)),
    ("emitente", ("emitente",)),
    ("razao_social", ("razao", "social")),
    ("nome_pagador", ("nome", "do", "pagador")),
    ("pagador", ("pagador",)),
    ("sacado", ("sacado",)),
    ("cliente", ("cliente",)),
    ("consumidor", ("consumidor",)),
    ("titular", ("titular",)),
    ("nome_empregador", ("nome", "razao", "social", "do", "empregador")),
    ("cpf_cnpj_empregador", ("cpf", "cnpj", "do", "empregador")),
    ("cpf_nome", ("cpf", "nome")),
    ("cpf_cnpj", ("cpf", "cnpj")),
    ("cpf", ("cpf",)),
    ("cnpj", ("cnpj",)),
    ("vencimento", ("vencimento",)),
    ("data_vencimento", ("data", "de", "vencimento")),
    ("pagar_ate", ("pagar", "este", "documento", "ate")),
    ("valor_total_documento", ("valor", "total", "do", "documento")),
    ("valor_documento", ("valor", "do", "documento")),
    ("valor_recolher", ("valor", "a", "recolher")),
    ("valor_cobrado", ("valor", "cobrado")),
    ("total_guia", ("total", "da", "guia")),
    ("referencia", ("referencia",)),
    ("exercicio", ("exercicio",)),
    ("competencia", ("competencia",)),
    ("periodo_apuracao", ("periodo", "de", "apuracao")),
    ("mes_referencia", ("mes", "referencia")),
    ("numero_referencia", ("numero", "de", "referencia")),
    ("numero_fatura", ("numero", "da", "fatura")),
    ("unidade_consumidora", ("unidade", "consumidora")),
    ("agencia_beneficiario", ("agencia", "codigo", "beneficiario")),
    ("nosso_numero", ("nosso", "numero")),
    ("linha_digitavel", ("linha", "digitavel")),
    ("pix_copia_cola", ("pix", "copia", "e", "cola")),
    ("composicao_documento", ("composicao", "do", "documento")),
    ("data_documento", ("data", "do", "documento")),
    ("local_pagamento", ("local", "de", "pagamento")),
    ("ficha_compensacao", ("ficha", "de", "compensacao")),
    ("recibo_pagador", ("recibo", "do", "pagador")),
    ("endereco_beneficiario", ("endereco", "do", "beneficiario")),
    ("observacoes", ("observacoes",)),
    ("instrucoes", ("instrucoes",)),
)

FIELD_ANCHORS: dict[str, tuple[str, ...]] = {
    "recebedor": (
        "nome_beneficiario", "beneficiario", "favorecido", "recebedor",
        "cedente", "fornecedor", "emitente", "razao_social",
    ),
    "pagador": (
        "nome_pagador", "pagador", "sacado", "cliente", "consumidor",
        "titular", "nome_empregador", "cpf_nome", "cpf_cnpj_empregador",
        "cpf_cnpj", "cpf",
    ),
    "referencia": (
        "referencia", "exercicio", "competencia", "periodo_apuracao", "mes_referencia",
        "numero_referencia", "numero_fatura", "unidade_consumidora",
    ),
    "vencimento": ("data_vencimento", "vencimento", "pagar_ate"),
    "valor": (
        "valor_total_documento", "valor_documento", "valor_recolher",
        "valor_cobrado", "total_guia",
    ),
    "codigo_pagamento": (
        "linha_digitavel", "pix_copia_cola", "ficha_compensacao",
        "recibo_pagador",
    ),
}

# Palavras cuja presença ajuda a caracterizar o formulário. A lista não é usada
# para extrair valor; apenas para assinatura estrutural secundária.
DOMAIN_MARKER_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("itr", ("itr",)),
    ("fgts", ("fgts",)),
    ("previdencia", ("previdenciaria",)),
    ("previdencia", ("contr", "prev")),
    ("energia", ("energia",)),
    ("honorario", ("honorario",)),
    ("fatura", ("fatura",)),
    ("boleto", ("boleto",)),
    ("nota_fiscal", ("nota", "fiscal")),
)

STRUCTURAL_WORDS = {
    token
    for _key, pattern in LABEL_PATTERNS
    for token in pattern
} | {
    "autenticacao", "mecanica", "carteira", "especie", "aceite", "banco",
    "agencia", "codigo", "principal", "multa", "juros", "total",
    "declaracao", "itr", "contribuicao", "previdenciaria", "fgts",
    "energia", "fatura", "nota", "fiscal", "processamento", "documento",
    "arrecadacao", "pagamento", "pix", "qr", "recibo", "guia",
}

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


# ---------------------------------------------------------------------------
# Normalização / validação
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return _norm(text).split()


def _strip_cpf_cnpj(text: str) -> str:
    text = re.sub(r"\b\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}\b", " ", text)
    text = re.sub(r"\b\d{2}[.\s]?\d{3}[.\s]?\d{3}[\s/]?\d{4}[-\s]?\d{2}\b", " ", text)
    text = re.sub(r"\b\*{0,3}\.?\d{3}\.?\d{2,3}\*?[-.]?\*{0,2}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -:;|\t")


def _clean_name(text: str) -> str:
    text = str(text or "").replace("\n", " ").strip()
    if not text:
        return ""

    # Remove rótulos apenas quando estiverem no início.
    patterns = (
        r"^\s*nome\s+do\s+benefici[aá]rio\s*[:\-]?\s*",
        r"^\s*nome\s+do\s+pagador\s*[:\-]?\s*",
        r"^\s*nome\s*/?\s*raz[aã]o\s+social\s+do\s+empregador\s*[:\-]?\s*",
        r"^\s*(?:benefici[aá]rio|recebedor|favorecido|cedente|fornecedor|emitente|pagador|sacado|cliente|consumidor|titular)\s*[:\-]?\s*",
        r"^\s*cpf\s*/\s*cnpj\s*[:\-]?\s*",
        r"^\s*cpf\s+nome\s*[:\-]?\s*",
    )
    for pat in patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)

    text = _strip_cpf_cnpj(text)
    # Corta sufixos que claramente começam outro campo.
    text = re.split(
        r"\s+(?:CPF|CNPJ|CEP|ENDERE[CÇ]O|VENCIMENTO|AG[EÊ]NCIA|NOSSO\s+N[ÚU]MERO)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.sub(r"\s+", " ", text).strip(" -:;|\t")
    if not text:
        return ""

    n = _norm(text)
    if len(n) < 3 or not re.search(r"[a-z]", n):
        return ""
    # Não aceita o próprio rótulo como nome.
    bad = {
        "nome", "nome do pagador", "nome do beneficiario", "pagador",
        "beneficiario", "cpf", "cnpj", "cpf cnpj endereco", "cpf nome",
        "valor", "vencimento", "referencia", "cliente", "consumidor",
        # Palavras funcionais que o OCR/leitor pode capturar de frases como
        # "Pagar este documento até". Nunca são nomes de pagador/recebedor.
        "este", "esta", "isto", "isso", "ate", "para", "pagar",
        "documento", "pagamento", "observacoes", "total", "principal",
        "multa", "juros", "codigo", "denominacao", "endereco",
        "pagar este documento ate",
    }
    if n in bad:
        return ""
    return text


def _parse_reference(text: str) -> str:
    raw = str(text or "")
    m = re.search(r"\b(0?[1-9]|1[0-2])\s*[/.-]\s*((?:19|20)\d{2})\b", raw)
    if m:
        return f"{int(m.group(1)):02d}/{m.group(2)}"
    n = _norm(raw)
    for name, month in MONTHS.items():
        m = re.search(rf"\b{name}\s+((?:19|20)\d{{2}})\b", n)
        if m:
            return f"{month:02d}/{m.group(1)}"
    return ""


def _parse_date(text: str) -> str:
    raw = str(text or "")
    m = re.search(r"\b(0?[1-9]|[12]\d|3[01])[./-](0?[1-9]|1[0-2])[./-]((?:19|20)\d{2})\b", raw)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b((?:19|20)\d{2})-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])\b", raw)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _parse_money_candidates(text: str) -> list[float]:
    raw = str(text or "")
    vals: list[float] = []
    # BR: 1.234.567,89 ou 420,00. Evita datas/linhas digitáveis.
    for m in re.finditer(r"(?<!\d)(?:R\$\s*)?(-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2})(?!\d)", raw, flags=re.IGNORECASE):
        s = m.group(1).replace(".", "").replace(",", ".")
        try:
            vals.append(round(float(s), 2))
        except ValueError:
            pass
    return vals


def _normalize_code(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    # PIX EMV pode conter letras; linha digitável/código de barras só dígitos.
    compact = re.sub(r"\s+", "", raw)
    if compact.startswith("000201") and len(compact) >= 50:
        return compact
    digits = re.sub(r"\D", "", raw)
    if len(digits) in {44, 46, 47, 48}:
        return digits
    if len(digits) >= 30 and len(digits) >= int(len(compact) * 0.75):
        return digits
    return compact if len(compact) >= 40 else ""


def _equivalent(field: str, a: Any, b: Any) -> bool:
    if field in {"recebedor", "pagador"}:
        aa, bb = _norm(_clean_name(str(a or ""))), _norm(_clean_name(str(b or "")))
        if not aa or not bb:
            return False
        if aa == bb or (min(len(aa), len(bb)) >= 8 and (aa in bb or bb in aa)):
            return True
        return SequenceMatcher(None, aa, bb).ratio() >= 0.91
    if field == "referencia":
        return bool(_parse_reference(str(a or "")) and _parse_reference(str(a or "")) == _parse_reference(str(b or "")))
    if field == "vencimento":
        da = _parse_date(str(a or "")) or str(a or "").strip()
        db = _parse_date(str(b or "")) or str(b or "").strip()
        return bool(da and db and da == db)
    if field == "valor":
        try:
            return abs(float(a) - float(b)) < 0.005
        except Exception:
            ca, cb = _parse_money_candidates(str(a or "")), _parse_money_candidates(str(b or ""))
            return bool(ca and cb and abs(ca[0] - cb[0]) < 0.005)
    if field == "codigo_pagamento":
        return bool(_normalize_code(str(a or "")) and _normalize_code(str(a or "")) == _normalize_code(str(b or "")))
    return str(a or "").strip() == str(b or "").strip()


# ---------------------------------------------------------------------------
# Leitura posicional
# ---------------------------------------------------------------------------

def _line_rect(words: list[dict[str, Any]]) -> list[float]:
    return [
        min(w["x0"] for w in words), min(w["y0"] for w in words),
        max(w["x1"] for w in words), max(w["y1"] for w in words),
    ]


def _norm_rect(rect: list[float], width: float, height: float) -> list[float]:
    return [rect[0] / width, rect[1] / height, rect[2] / width, rect[3] / height]


def _find_labels(line_text: str) -> list[str]:
    toks = _tokens(line_text)
    if not toks:
        return []
    joined = " ".join(toks)
    found: list[str] = []
    for key, pattern in LABEL_PATTERNS:
        needle = " ".join(pattern)
        if needle in joined:
            found.append(key)
    return found


def _native_document(caminho: str | Path) -> dict[str, Any]:
    import fitz

    doc = fitz.open(str(caminho))
    pages: list[dict[str, Any]] = []
    try:
        for page_index, page in enumerate(doc):
            width = max(float(page.rect.width), 1.0)
            height = max(float(page.rect.height), 1.0)
            raw_words = page.get_text("words", sort=True) or []
            groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
            all_words: list[dict[str, Any]] = []
            for item in raw_words:
                if len(item) < 8:
                    continue
                x0, y0, x1, y1, text, block, line_no, word_no = item[:8]
                text = str(text or "").strip()
                if not text:
                    continue
                w = {
                    "text": text,
                    "norm": _norm(text),
                    "x0": float(x0) / width,
                    "y0": float(y0) / height,
                    "x1": float(x1) / width,
                    "y1": float(y1) / height,
                    "block": int(block),
                    "line": int(line_no),
                    "word": int(word_no),
                }
                all_words.append(w)
                groups.setdefault((int(block), int(line_no)), []).append(w)

            lines: list[dict[str, Any]] = []
            for group in groups.values():
                group.sort(key=lambda w: (w["x0"], w["word"]))
                text = " ".join(w["text"] for w in group).strip()
                rect = [
                    min(w["x0"] for w in group), min(w["y0"] for w in group),
                    max(w["x1"] for w in group), max(w["y1"] for w in group),
                ]
                lines.append({
                    "page": page_index,
                    "text": text,
                    "norm": _norm(text),
                    "rect": rect,
                    "words": group,
                    "labels": _find_labels(text),
                    "source": "native",
                })
            lines.sort(key=lambda l: (l["rect"][1], l["rect"][0]))
            pages.append({
                "page": page_index,
                "width": width,
                "height": height,
                "aspect": round(width / height, 4),
                "lines": lines,
                "words": all_words,
            })
    finally:
        doc.close()
    return {"pages": pages, "source": "native"}


def _locate_tesseract_default() -> str | None:
    candidates: list[Path] = []
    meipass = Path(str(getattr(sys, "_MEIPASS", "") or "")) if getattr(sys, "_MEIPASS", None) else None
    if meipass:
        candidates.append(meipass / "tesseract" / "tesseract.exe")
    candidates.extend([
        Path(sys.executable).resolve().parent / "tesseract" / "tesseract.exe",
        Path(__file__).resolve().parents[1] / "tesseract" / "tesseract.exe",
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ])
    for p in candidates:
        if p and p.is_file():
            return str(p)
    return shutil.which("tesseract")


def _ocr_document(caminho: str | Path, tesseract_locator: Callable[[], str | None] | None = None) -> dict[str, Any]:
    import fitz
    import cv2
    import numpy as np
    import pytesseract
    from pytesseract import Output

    locator = tesseract_locator or _locate_tesseract_default
    exe = locator()
    if exe:
        pytesseract.pytesseract.tesseract_cmd = exe

    # Prefer pt_BR/por quando disponível; cai para padrão do ambiente.
    lang = None
    try:
        langs = set(pytesseract.get_languages(config=""))
        for candidate in ("por", "pt_BR", "eng"):
            if candidate in langs:
                lang = candidate
                break
    except Exception:
        pass

    doc = fitz.open(str(caminho))
    pages: list[dict[str, Any]] = []
    try:
        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
            else:
                img = cv2.cvtColor(arr, cv2.COLOR_RGB2RGB)
            kwargs: dict[str, Any] = {"output_type": Output.DICT, "config": "--oem 3 --psm 6"}
            if lang:
                kwargs["lang"] = lang
            data = pytesseract.image_to_data(img, **kwargs)
            total = len(data.get("text", []))
            groups: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
            all_words: list[dict[str, Any]] = []
            for i in range(total):
                text = str(data["text"][i] or "").strip()
                if not text:
                    continue
                try:
                    conf = float(data["conf"][i])
                except Exception:
                    conf = -1
                if conf < 20:
                    continue
                left = int(data["left"][i]); top = int(data["top"][i])
                ww = int(data["width"][i]); hh = int(data["height"][i])
                w = {
                    "text": text,
                    "norm": _norm(text),
                    "x0": left / max(pix.width, 1),
                    "y0": top / max(pix.height, 1),
                    "x1": (left + ww) / max(pix.width, 1),
                    "y1": (top + hh) / max(pix.height, 1),
                    "block": int(data.get("block_num", [0] * total)[i]),
                    "line": int(data.get("line_num", [0] * total)[i]),
                    "word": int(data.get("word_num", [0] * total)[i]),
                    "conf": conf,
                }
                all_words.append(w)
                key = (
                    int(data.get("block_num", [0] * total)[i]),
                    int(data.get("par_num", [0] * total)[i]),
                    int(data.get("line_num", [0] * total)[i]),
                )
                groups.setdefault(key, []).append(w)
            lines: list[dict[str, Any]] = []
            for group in groups.values():
                group.sort(key=lambda w: (w["x0"], w["word"]))
                text = " ".join(w["text"] for w in group).strip()
                rect = [
                    min(w["x0"] for w in group), min(w["y0"] for w in group),
                    max(w["x1"] for w in group), max(w["y1"] for w in group),
                ]
                lines.append({
                    "page": page_index,
                    "text": text,
                    "norm": _norm(text),
                    "rect": rect,
                    "words": group,
                    "labels": _find_labels(text),
                    "source": "ocr",
                })
            lines.sort(key=lambda l: (l["rect"][1], l["rect"][0]))
            pages.append({
                "page": page_index,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "aspect": round(float(page.rect.width) / max(float(page.rect.height), 1.0), 4),
                "lines": lines,
                "words": all_words,
            })
    finally:
        doc.close()
    return {"pages": pages, "source": "ocr"}


def _document_model(caminho: str | Path, tesseract_locator: Callable[[], str | None] | None = None) -> dict[str, Any]:
    native = _native_document(caminho)
    char_count = sum(len(line["text"]) for page in native["pages"] for line in page["lines"])
    # OCR somente quando realmente não há camada de texto suficiente.
    if char_count >= 80:
        return native
    try:
        return _ocr_document(caminho, tesseract_locator=tesseract_locator)
    except Exception:
        return native


# ---------------------------------------------------------------------------
# Assinatura estrutural
# ---------------------------------------------------------------------------

def _mask_variables(text: str) -> str:
    n = _norm(text)
    # E-mails/URLs e identificadores grandes.
    n = re.sub(r"\b(?:https?|www)\s+\S+", " url ", n)
    # Datas/períodos.
    n = re.sub(r"\b(?:0?[1-9]|[12]\d|3[01])\s+(?:0?[1-9]|1[0-2])\s+(?:19|20)\d{2}\b", " data ", n)
    n = re.sub(r"\b(?:0?[1-9]|1[0-2])\s+(?:19|20)\d{2}\b", " periodo ", n)
    # CPF/CNPJ e grandes números.
    n = re.sub(r"\b\d{11,14}\b", " docid ", n)
    n = re.sub(r"\b\d{5,}\b", " numero ", n)
    n = re.sub(r"\b\d+\b", " n ", n)
    return re.sub(r"\s+", " ", n).strip()


def _signature(model: dict[str, Any]) -> dict[str, Any]:
    anchors: list[str] = []
    skeleton: list[str] = []
    grid: Counter[str] = Counter()
    aspects: list[float] = []
    markers: set[str] = set()

    for page in model.get("pages", []):
        p = int(page["page"])
        aspects.append(float(page.get("aspect") or 0))
        for line in page.get("lines", []):
            rect = line["rect"]
            cx = (rect[0] + rect[2]) / 2
            cy = (rect[1] + rect[3]) / 2
            xb = min(11, max(0, int(cx * 12)))
            yb = min(15, max(0, int(cy * 16)))
            word_bucket = min(6, max(1, len(line.get("words") or [])))
            grid[f"p{p}:x{xb}:y{yb}:w{word_bucket}"] += 1

            for label in line.get("labels") or []:
                # Geometria grossa: tolera deslocamentos pequenos.
                lx = min(9, max(0, int(rect[0] * 10)))
                ly = min(13, max(0, int(rect[1] * 14)))
                anchors.append(f"p{p}:{label}:x{lx}:y{ly}")

            masked = _mask_variables(line.get("text") or "")
            joined_masked = " " + masked + " "
            for marker, pattern in DOMAIN_MARKER_PATTERNS:
                if " " + " ".join(pattern) + " " in joined_masked:
                    markers.add(marker)
            toks = [t for t in masked.split() if t in STRUCTURAL_WORDS]
            # Guarda somente texto estrutural, nunca nomes livres.
            if toks:
                key = " ".join(toks[:10])
                sx = min(7, max(0, int(rect[0] * 8)))
                sy = min(11, max(0, int(rect[1] * 12)))
                skeleton.append(f"p{p}:x{sx}:y{sy}:{key}")

    # Frequências pequenas no grid são úteis; limita tamanho no disco.
    grid_items = sorted(grid.items())[:420]
    return {
        "pages": len(model.get("pages", [])),
        "aspects": [round(a, 3) for a in aspects],
        "anchors": sorted(set(anchors))[:320],
        "skeleton": sorted(set(skeleton))[:420],
        "grid": [[k, int(v)] for k, v in grid_items],
        "markers": sorted(markers),
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _coverage(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _grid_similarity(a: list[list[Any]], b: list[list[Any]]) -> float:
    ca = {str(k): float(v) for k, v in a or []}
    cb = {str(k): float(v) for k, v in b or []}
    if not ca or not cb:
        return 0.0
    keys = set(ca) | set(cb)
    dot = sum(ca.get(k, 0.0) * cb.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb) if na and nb else 0.0


def signature_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    aa, ab = set(a.get("anchors") or []), set(b.get("anchors") or [])
    sa, sb = set(a.get("skeleton") or []), set(b.get("skeleton") or [])

    anchor_score = 0.55 * _coverage(aa, ab) + 0.45 * _jaccard(aa, ab) if aa and ab else 0.0
    skeleton_score = 0.55 * _coverage(sa, sb) + 0.45 * _jaccard(sa, sb) if sa and sb else 0.0
    grid_score = _grid_similarity(a.get("grid") or [], b.get("grid") or [])

    pages_a, pages_b = int(a.get("pages") or 1), int(b.get("pages") or 1)
    page_score = 1.0 if pages_a == pages_b else 0.0
    aspects_a, aspects_b = list(a.get("aspects") or []), list(b.get("aspects") or [])
    if aspects_a and aspects_b and len(aspects_a) == len(aspects_b):
        aspect_score = sum(max(0.0, 1.0 - abs(x - y) / 0.08) for x, y in zip(aspects_a, aspects_b)) / len(aspects_a)
    else:
        aspect_score = 0.0

    # Anchors são a parte mais importante. Skeleton diferencia formulários
    # semanticamente diferentes com geometria parecida. Grid é auxiliar.
    score = (
        0.46 * anchor_score
        + 0.30 * skeleton_score
        + 0.16 * grid_score
        + 0.05 * page_score
        + 0.03 * aspect_score
    )

    # Formulários com geometria parecida, mas finalidade documental diferente,
    # não devem compartilhar perfil. Ex.: DARF previdenciário x ITR.
    ma, mb = set(a.get("markers") or []), set(b.get("markers") or [])
    if ma and mb:
        shared = ma & mb
        if not shared:
            score -= 0.18
        else:
            score += min(0.03, 0.015 * len(shared))
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Localização precisa de campos
# ---------------------------------------------------------------------------

def _rect_union(words: list[dict[str, Any]]) -> list[float]:
    return [
        min(w["x0"] for w in words), min(w["y0"] for w in words),
        max(w["x1"] for w in words), max(w["y1"] for w in words),
    ]


def _best_word_window_for_name(line: dict[str, Any], target: str) -> list[float] | None:
    target_tokens = _tokens(_clean_name(target))
    words = line.get("words") or []
    if not target_tokens or not words:
        return None
    word_norms = [w.get("norm") or "" for w in words]
    best: tuple[float, int, int] | None = None
    # Janelas próximas ao número de tokens do alvo. Permite /, S/S etc.
    for length in range(max(1, len(target_tokens) - 1), min(len(words), len(target_tokens) + 3) + 1):
        for start in range(0, len(words) - length + 1):
            chunk = " ".join(x for x in word_norms[start:start + length] if x)
            if not chunk:
                continue
            score = SequenceMatcher(None, " ".join(target_tokens), chunk).ratio()
            tset, cset = set(target_tokens), set(chunk.split())
            cov = len(tset & cset) / len(tset) if tset else 0.0
            score = max(score, 0.78 * cov + 0.22 * score)
            if best is None or score > best[0]:
                best = (score, start, start + length)
    if best and best[0] >= 0.72:
        return _rect_union(words[best[1]:best[2]])
    return None


def _field_line_score(field: str, target: Any, line: dict[str, Any]) -> float:
    text = str(line.get("text") or "")
    if field in {"recebedor", "pagador"}:
        target_clean = _clean_name(str(target or ""))
        cand = _clean_name(text)
        if not target_clean or not cand:
            return 0.0
        a, b = _norm(target_clean), _norm(cand)
        if a == b:
            return 1.0
        if a in b:
            # Linha com CPF/endereço ainda pode conter o nome correto.
            return 0.97 if len(b) <= max(80, len(a) * 2.4) else 0.82
        ta, tb = set(a.split()), set(b.split())
        cov = len(ta & tb) / len(ta) if ta else 0.0
        seq = SequenceMatcher(None, a, b).ratio()
        return 0.76 * cov + 0.24 * seq
    if field == "referencia":
        target_ref = _parse_reference(str(target or ""))
        cand_ref = _parse_reference(text)
        if target_ref:
            return 1.0 if cand_ref == target_ref else 0.0
        # Referências também podem ser exercício, nº de fatura, safra etc.
        a, b = _norm(str(target or "")), _norm(text)
        if not a:
            return 0.0
        if re.fullmatch(r"(?:19|20)\d{2}", a):
            if re.search(rf"\b(?:exercicio|referencia|competencia|safra)\b.*\b{re.escape(a)}\b", b):
                return 1.0
            # Ano solto dentro de data não é referência segura.
            return 0.55 if a in b else 0.0
        if a == b:
            return 1.0
        if a in b and len(a) >= 3:
            return 0.94 if len(b) <= max(50, len(a) * 3) else 0.78
        return SequenceMatcher(None, a, b).ratio() * 0.75
    if field == "vencimento":
        target_date = _parse_date(str(target or "")) or str(target or "").strip()
        return 1.0 if target_date and target_date in {_parse_date(text), str(text).strip()} else 0.0
    if field == "valor":
        try:
            tv = round(float(target), 2)
        except Exception:
            vals = _parse_money_candidates(str(target or "")); tv = vals[0] if vals else None
        if tv is None:
            return 0.0
        vals = _parse_money_candidates(text)
        return 1.0 if any(abs(v - tv) < 0.005 for v in vals) else 0.0
    if field == "codigo_pagamento":
        tc = _normalize_code(str(target or ""))
        if not tc:
            return 0.0
        lc = _normalize_code(text)
        if lc == tc:
            return 1.0
        compact = re.sub(r"\s+", "", text)
        digits = re.sub(r"\D", "", text)
        if tc in compact or tc in digits:
            return 0.98
        return 0.0
    return 0.0


def _find_field_line(model: dict[str, Any], field: str, target: Any) -> tuple[dict[str, Any] | None, float]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for page in model.get("pages", []):
        for line in page.get("lines", []):
            score = _field_line_score(field, target, line)
            if score > 0:
                scored.append((score, line))
    if not scored:
        return None, 0.0
    scored.sort(key=lambda x: (x[0], -len(str(x[1].get("text") or ""))), reverse=True)
    return scored[0][1], float(scored[0][0])


def _refine_value_rect(field: str, target: Any, line: dict[str, Any]) -> list[float]:
    words = list(line.get("words") or [])
    if not words:
        return list(line["rect"])

    if field in {"recebedor", "pagador"}:
        precise = _best_word_window_for_name(line, str(target or ""))
        if precise:
            # margem curta; a leitura posterior trabalha por linha perto dessa área.
            return [
                max(0.0, precise[0] - 0.003), max(0.0, precise[1] - 0.003),
                min(1.0, precise[2] + 0.004), min(1.0, precise[3] + 0.003),
            ]

    if field == "codigo_pagamento":
        target_code = _normalize_code(str(target or ""))
        if target_code:
            best = None
            for start in range(len(words)):
                combined = ""
                for end in range(start, min(len(words), start + 12)):
                    piece = _normalize_code(str(words[end].get("text") or ""))
                    if not piece:
                        # Para grupos curtos formatados (ex. "9" no DV), usa dígitos.
                        piece = re.sub(r"\D", "", str(words[end].get("text") or ""))
                    combined += piece
                    if combined == target_code:
                        best = words[start:end + 1]
                        break
                    if len(combined) > len(target_code) + 4:
                        break
                if best:
                    break
            if best:
                r = _rect_union(best)
                return [max(0.0, r[0] - 0.003), max(0.0, r[1] - 0.003), min(1.0, r[2] + 0.003), min(1.0, r[3] + 0.003)]

    target_norm = ""
    if field == "referencia":
        target_norm = _norm(_parse_reference(str(target or "")) or str(target or ""))
    elif field == "vencimento":
        target_norm = _norm(_parse_date(str(target or "")) or str(target or ""))
    elif field == "codigo_pagamento":
        target_norm = _normalize_code(str(target or ""))

    if field == "valor":
        try:
            tv = round(float(target), 2)
        except Exception:
            vals = _parse_money_candidates(str(target or "")); tv = vals[0] if vals else None
        if tv is not None:
            for w in words:
                vals = _parse_money_candidates(w["text"])
                if any(abs(v - tv) < 0.005 for v in vals):
                    return [max(0.0, w["x0"] - 0.004), max(0.0, w["y0"] - 0.003), min(1.0, w["x1"] + 0.004), min(1.0, w["y1"] + 0.003)]

    if target_norm:
        for w in words:
            wn = _normalize_code(w["text"]) if field == "codigo_pagamento" else _norm(w["text"])
            if wn and (target_norm in wn or wn in target_norm):
                return [max(0.0, w["x0"] - 0.004), max(0.0, w["y0"] - 0.003), min(1.0, w["x1"] + 0.004), min(1.0, w["y1"] + 0.003)]

    return list(line["rect"])


def _rect_center(rect: list[float]) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def _find_anchor(model: dict[str, Any], field: str, value_line: dict[str, Any]) -> dict[str, Any] | None:
    allowed = set(FIELD_ANCHORS.get(field) or ())
    if not allowed:
        return None
    page_idx = int(value_line["page"])
    vx, vy = _rect_center(value_line["rect"])
    candidates: list[tuple[float, dict[str, Any], str]] = []
    page = next((p for p in model.get("pages", []) if int(p["page"]) == page_idx), None)
    if not page:
        return None
    for line in page.get("lines", []):
        labels = allowed & set(line.get("labels") or [])
        if not labels:
            continue
        lx, ly = _rect_center(line["rect"])
        dx, dy = abs(vx - lx), abs(vy - ly)
        # campos normalmente estão ao lado/abaixo; tolera tabela mais larga.
        distance = math.sqrt((dx * 0.75) ** 2 + (dy * 1.35) ** 2)
        if distance > 0.32:
            continue
        # Preferência por rótulo específico e proximidade.
        specificity = max(len(label) for label in labels) / 28.0
        score = 1.0 / (0.03 + distance) + specificity
        label = sorted(labels, key=len, reverse=True)[0]
        candidates.append((score, line, label))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _score, line, label = candidates[0]
    return {
        "label": label,
        "text": str(line.get("norm") or ""),
        "rect": [round(float(x), 6) for x in line["rect"]],
    }


def _build_rule(model: dict[str, Any], field: str, target: Any, corrected: bool) -> dict[str, Any] | None:
    line, score = _find_field_line(model, field, target)
    threshold = 0.72 if field in {"recebedor", "pagador"} else 0.95
    if not line or score < threshold:
        return None
    value_rect = _refine_value_rect(field, target, line)
    anchor = _find_anchor(model, field, line)
    rule: dict[str, Any] = {
        "page": int(line["page"]),
        "rect": [round(float(x), 6) for x in value_rect],
        "line_rect": [round(float(x), 6) for x in line["rect"]],
        "source": str(line.get("source") or model.get("source") or ""),
        "locate_score": round(score, 4),
        "confirmations": 0 if corrected else 1,
        "corrections": 1 if corrected else 0,
        "errors": 0,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if field == "referencia":
        rule["reference_kind"] = "period" if _parse_reference(str(target or "")) else "text"
    if field == "codigo_pagamento":
        code = _normalize_code(str(target or ""))
        if code:
            rule["code_length"] = len(code)

    if anchor:
        ar = anchor["rect"]
        rule["anchor"] = anchor
        rule["delta"] = [round(value_rect[i] - ar[i], 6) for i in range(4)]
        # Também guarda relação da linha inteira; útil para nomes variáveis.
        lr = line["rect"]
        rule["line_delta"] = [round(lr[i] - ar[i], 6) for i in range(4)]
    return rule


def _rule_strength(rule: dict[str, Any]) -> int:
    return int(rule.get("corrections", 0)) * 3 + int(rule.get("confirmations", 0)) - int(rule.get("errors", 0)) * 4


def _find_anchor_current(model: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any] | None:
    anchor = rule.get("anchor") or {}
    label = str(anchor.get("label") or "")
    if not label:
        return None
    page_idx = int(rule.get("page", 0))
    expected = anchor.get("rect") or [0, 0, 1, 1]
    ex, ey = _rect_center([float(x) for x in expected])
    best: tuple[float, dict[str, Any]] | None = None
    for page in model.get("pages", []):
        if int(page["page"]) != page_idx:
            continue
        for line in page.get("lines", []):
            if label not in set(line.get("labels") or []):
                continue
            x, y = _rect_center(line["rect"])
            geom = abs(x - ex) + 1.4 * abs(y - ey)
            text_ratio = SequenceMatcher(None, str(anchor.get("text") or ""), str(line.get("norm") or "")).ratio()
            score = text_ratio - geom * 1.2
            if best is None or score > best[0]:
                best = (score, line)
    return best[1] if best else None


def _predicted_rect(model: dict[str, Any], rule: dict[str, Any], use_line: bool = False) -> list[float]:
    anchor_line = _find_anchor_current(model, rule)
    delta_key = "line_delta" if use_line else "delta"
    fallback_key = "line_rect" if use_line else "rect"
    delta = rule.get(delta_key)
    if anchor_line is not None and isinstance(delta, list) and len(delta) == 4:
        ar = anchor_line["rect"]
        return [max(0.0, min(1.0, float(ar[i]) + float(delta[i]))) for i in range(4)]
    return [float(x) for x in (rule.get(fallback_key) or [0, 0, 1, 1])]


def _geometry_score(expected: list[float], actual: list[float]) -> float:
    ex, ey = _rect_center(expected); ax, ay = _rect_center(actual)
    dx = abs(ex - ax); dy = abs(ey - ay)
    dist = math.sqrt((dx * 0.8) ** 2 + (dy * 1.6) ** 2)
    return max(0.0, 1.0 - dist / 0.12)


def _text_from_region_words(page: dict[str, Any], rect: list[float]) -> str:
    expanded = [
        max(0.0, rect[0] - 0.006), max(0.0, rect[1] - 0.004),
        min(1.0, rect[2] + 0.006), min(1.0, rect[3] + 0.004),
    ]
    selected = []
    for w in page.get("words", []):
        cx = (w["x0"] + w["x1"]) / 2
        cy = (w["y0"] + w["y1"]) / 2
        if expanded[0] <= cx <= expanded[2] and expanded[1] <= cy <= expanded[3]:
            selected.append(w)
    selected.sort(key=lambda w: (w["y0"], w["x0"]))
    return " ".join(str(w["text"]) for w in selected).strip()


def _code_from_region_words(page: dict[str, Any], rect: list[float]) -> str:
    # Lê somente as palavras dentro da região aprendida. Isso evita incluir,
    # por exemplo, o pequeno "001-9" que fica antes da linha digitável.
    expanded = [
        max(0.0, rect[0] - 0.006), max(0.0, rect[1] - 0.004),
        min(1.0, rect[2] + 0.006), min(1.0, rect[3] + 0.004),
    ]
    selected = []
    for w in page.get("words", []):
        cx = (w["x0"] + w["x1"]) / 2
        cy = (w["y0"] + w["y1"]) / 2
        if expanded[0] <= cx <= expanded[2] and expanded[1] <= cy <= expanded[3]:
            selected.append(w)
    selected.sort(key=lambda w: (w["y0"], w["x0"]))
    if not selected:
        return ""
    return _normalize_code(" ".join(str(w["text"]) for w in selected))


def _clean_candidate(field: str, text: str) -> Any:
    if field in {"recebedor", "pagador"}:
        return _clean_name(text)
    if field == "referencia":
        return _parse_reference(text)
    if field == "vencimento":
        return _parse_date(text)
    if field == "valor":
        vals = _parse_money_candidates(text)
        return vals[0] if vals else None
    if field == "codigo_pagamento":
        return _normalize_code(text)
    return str(text or "").strip()


def _read_rule(model: dict[str, Any], rule: dict[str, Any], field: str) -> Any:
    page_idx = int(rule.get("page", 0))

    # Referência textual livre (ex.: exercício 2026) usa a região exata.
    if field == "referencia" and str(rule.get("reference_kind") or "") == "text":
        value_rect = _predicted_rect(model, rule, use_line=False)
        for page in model.get("pages", []):
            if int(page["page"]) != page_idx:
                continue
            text = _text_from_region_words(page, value_rect)
            if text:
                return text.strip()

    # Leia primeiro o retangulo exato que foi CORRIGIDO/CONFIRMADO
    # pelo usuario. Em boletos, uma mesma linha pode ter muitos
    # valores/datas; ler a linha inteira acaba escolhendo o primeiro.
    if field in {"codigo_pagamento", "valor", "vencimento", "referencia"}:
        value_rect = _predicted_rect(model, rule, use_line=False)
        for page in model.get("pages", []):
            if int(page["page"]) != page_idx:
                continue
            if field == "codigo_pagamento":
                value = _code_from_region_words(page, value_rect)
                if value and (not rule.get("code_length") or len(value) == int(rule["code_length"])):
                    return value
                continue
            fragment = _text_from_region_words(page, value_rect)
            value = _clean_candidate(field, fragment)
            if value not in (None, ""):
                return value

    expected_line = _predicted_rect(model, rule, use_line=True)
    candidates: list[tuple[float, Any, dict[str, Any]]] = []
    for page in model.get("pages", []):
        if int(page["page"]) != page_idx:
            continue
        for line in page.get("lines", []):
            geom = _geometry_score(expected_line, line["rect"])
            if geom < 0.25:
                continue
            value = _clean_candidate(field, str(line.get("text") or ""))
            if value in (None, ""):
                continue
            # Um campo de nome aprendido nunca deve devolver o próprio rótulo.
            # Os PDFs nativos frequentemente colocam rótulo e valor em linhas
            # separadas muito próximas; por isso linhas rotuladas são ignoradas.
            if field in {"recebedor", "pagador"} and set(line.get("labels") or []):
                continue
            candidates.append((geom, value, line))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Persistência / API
# ---------------------------------------------------------------------------

class LayoutLearningV2:
    def __init__(
        self,
        app_data_dir: str | Path,
        tesseract_locator: Callable[[], str | None] | None = None,
    ) -> None:
        self.app_data_dir = Path(app_data_dir)
        self.path = self.app_data_dir / "aprendizado_layouts_v2.json"
        self.v1_path = self.app_data_dir / "aprendizado_layouts.json"
        self.v1_backup = self.app_data_dir / "aprendizado_layouts_v1_backup.json"
        self.tesseract_locator = tesseract_locator
        self._data: dict[str, Any] | None = None
        self._model_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._signature_cache: dict[tuple[str, int, int], dict[str, Any]] = {}

    def _cache_key(self, caminho: str | Path) -> tuple[str, int, int]:
        p = Path(caminho).resolve()
        st = p.stat()
        return (str(p), int(st.st_mtime_ns), int(st.st_size))

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        base: dict[str, Any] = {"version": SCHEMA_VERSION, "profiles": []}
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("profiles"), list):
                    base = raw
        except Exception:
            pass
        self._data = base
        return base

    def _save(self) -> None:
        data = self._load()
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        # Backup do V1 uma única vez. O V1 original não é modificado pela V2.
        try:
            if self.v1_path.is_file() and not self.v1_backup.exists():
                shutil.copy2(self.v1_path, self.v1_backup)
        except Exception:
            pass
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _model(self, caminho: str | Path) -> dict[str, Any]:
        key = self._cache_key(caminho)
        cached = self._model_cache.get(key)
        if cached is not None:
            return cached
        model = _document_model(caminho, tesseract_locator=self.tesseract_locator)
        if len(self._model_cache) >= 32:
            self._model_cache.clear()
        self._model_cache[key] = model
        return model

    def _signature(self, caminho: str | Path) -> dict[str, Any]:
        key = self._cache_key(caminho)
        cached = self._signature_cache.get(key)
        if cached is not None:
            return cached
        sig = _signature(self._model(caminho))
        if len(self._signature_cache) >= 64:
            self._signature_cache.clear()
        self._signature_cache[key] = sig
        return sig

    def _profile_similarity(self, sig: dict[str, Any], profile: dict[str, Any]) -> float:
        signatures = profile.get("signatures") or []
        if not signatures and profile.get("signature"):
            signatures = [profile["signature"]]
        if not signatures:
            return 0.0
        return max(signature_similarity(sig, s) for s in signatures if isinstance(s, dict))

    def recognize(self, caminho: str | Path) -> tuple[dict[str, Any] | None, float, float]:
        sig = self._signature(caminho)
        scored: list[tuple[float, dict[str, Any]]] = []
        for profile in self._load().get("profiles", []):
            score = self._profile_similarity(sig, profile)
            scored.append((score, profile))
        if not scored:
            return None, 0.0, 0.0
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        # Limiar alto + margem. Perfis historicos duplicados de um
        # MESMO layout podem causar empate e bloquear TODAS as correcoes.
        # Resolver empate apenas quando as assinaturas sao praticamente
        # identicas e os marcadores do tipo de documento coincidem.
        if best_score < 0.76:
            return None, best_score, second
        if second >= 0.72 and (best_score - second) < 0.055:
            contenders = [(value, p) for value, p in scored
                          if value >= max(0.94, best_score - 0.055)]
            same_family = bool(contenders) and len(contenders) >= 2
            marker_set = set(sig.get("markers") or [])
            for _value, p in contenders:
                signatures = p.get("signatures") or ([p["signature"]] if p.get("signature") else [])
                if not signatures or set(max(
                    (signatures or [{}]),
                    key=lambda s: signature_similarity(sig, s),
                ).get("markers") or []) != marker_set:
                    same_family = False
                    break
            if same_family:
                for n, (_value, p) in enumerate(contenders):
                    for _other_value, q in contenders[n + 1:]:
                        left = p.get("signatures") or ([p["signature"]] if p.get("signature") else [])
                        right = q.get("signatures") or ([q["signature"]] if q.get("signature") else [])
                        if max((signature_similarity(a, b)
                                for a in left for b in right), default=0) < 0.975:
                            same_family = False
                            break
                    if not same_family:
                        break
            if not same_family:
                return None, best_score, second
            # Priorize o perfil explicitamente corrigido mais vezes;
            # confirmacoes simples nao vencem uma correcao manual.
            best = max(
                (p for _, p in contenders),
                key=lambda p: (
                    sum(int(rule.get("corrections", 0)) for rule in
                        (p.get("fields") or {}).values() if isinstance(rule, dict)),
                    sum(int(rule.get("confirmations", 0)) for rule in
                        (p.get("fields") or {}).values() if isinstance(rule, dict)),
                    int(p.get("saves", 0)),
                ),
            )
        return best, best_score, second

    def _new_profile(self, sig: dict[str, Any]) -> dict[str, Any]:
        seed = json.dumps(sig, ensure_ascii=False, sort_keys=True).encode("utf-8", errors="ignore")
        import hashlib
        profile = {
            "id": hashlib.sha1(seed).hexdigest()[:14],
            "signatures": [sig],
            "fields": {},
            "saves": 0,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._load().setdefault("profiles", []).append(profile)
        return profile

    def apply(self, caminho: str | Path, dados: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
        profile, score, _second = self.recognize(caminho)
        if profile is None:
            return dados, set()
        model = self._model(caminho)
        applied: set[str] = set()
        insecure = set(str(x) for x in dados.get("campos_inseguros", []) or [])
        fields = profile.get("fields") or {}

        for field in FIELDS:
            rule = fields.get(field)
            if not isinstance(rule, dict) or _rule_strength(rule) < 1:
                continue
            value = _read_rule(model, rule, field)
            if value in (None, ""):
                continue
            if field == "recebedor":
                dados["recebedor"] = str(value)
                dados["fornecedor"] = str(value)
            elif field == "valor":
                try:
                    dados[field] = float(value)
                except Exception:
                    continue
            else:
                dados[field] = value
            insecure.discard(field)
            applied.add(field)

        dados["campos_inseguros"] = sorted(insecure)
        dados["_layout_v2"] = str(profile.get("id") or "")
        dados["_layout_v2_score"] = round(float(score), 3)
        dados["_campos_layout_v2"] = sorted(applied)
        if applied:
            confidence = dados.get("confianca")
            if isinstance(confidence, dict):
                for field in applied:
                    confidence[field] = max(float(confidence.get(field, 0) or 0), 0.985)
            reasons = dados.get("motivos")
            if isinstance(reasons, dict):
                for field in applied:
                    reasons[field] = "campo lido por layout V2 aprendido e confirmado"
        return dados, applied

    def learn(
        self,
        caminho: str | Path,
        final_values: dict[str, Any],
        predicted_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        caminho = Path(caminho)
        if not caminho.is_file():
            return {"learned": False, "reason": "file_not_found"}
        predicted_values = predicted_values or {}
        sig = self._signature(caminho)
        profile, score, _second = self.recognize(caminho)
        # Para aprender, limiar um pouco menor é aceitável porque o usuário está
        # confirmando o documento atual. Ainda exigimos boa semelhança.
        if profile is None and score >= 0.72:
            # Não força merge quando recognize recusou por ambiguidade: cria perfil
            # isolado em vez de contaminar um existente.
            pass
        if profile is None:
            profile = self._new_profile(sig)
            score = 1.0
        else:
            signatures = profile.setdefault("signatures", [])
            if all(signature_similarity(sig, existing) < 0.985 for existing in signatures if isinstance(existing, dict)):
                signatures.append(sig)
                del signatures[:-6]

        model = self._model(caminho)
        fields = profile.setdefault("fields", {})
        learned_fields: list[str] = []
        corrected_fields: list[str] = []

        for field in FIELDS:
            final = final_values.get(field)
            if final in (None, ""):
                continue
            if field in {"recebedor", "pagador"}:
                # Nunca transforma lixo de leitura automática em regra aprendida.
                # Isto também impede que perfis antigos contaminados sejam
                # reforçados quando o usuário apenas salva o formulário.
                final = _clean_name(str(final))
                if not final:
                    continue
            if field == "valor":
                try:
                    if float(final) <= 0:
                        continue
                except Exception:
                    continue
            predicted = predicted_values.get(field)
            corrected = bool(predicted not in (None, "") and not _equivalent(field, final, predicted))
            rule = _build_rule(model, field, final, corrected=corrected)
            old = fields.get(field)
            if rule is None:
                # Se já existe regra e o valor previsto por ela foi aceito, confirma.
                if isinstance(old, dict) and predicted not in (None, "") and _equivalent(field, final, predicted):
                    old["confirmations"] = int(old.get("confirmations", 0)) + 1
                    old["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    learned_fields.append(field)
                continue

            if isinstance(old, dict):
                # Correção explícita atualiza apenas ESTE campo deste perfil.
                if corrected:
                    rule["confirmations"] += int(old.get("confirmations", 0))
                    rule["corrections"] += int(old.get("corrections", 0))
                    rule["errors"] = max(0, int(old.get("errors", 0)) - 1)
                else:
                    # Um novo exemplo confirmado refina a posição sem zerar histórico.
                    rule["confirmations"] += int(old.get("confirmations", 0))
                    rule["corrections"] += int(old.get("corrections", 0))
                    rule["errors"] = int(old.get("errors", 0))
            fields[field] = rule
            learned_fields.append(field)
            if corrected:
                corrected_fields.append(field)

        profile["saves"] = int(profile.get("saves", 0)) + 1
        profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save()
        return {
            "learned": True,
            "profile_id": profile.get("id"),
            "profile_score": round(float(score), 3),
            "fields": learned_fields,
            "corrected_fields": corrected_fields,
        }

    def debug_profiles(self) -> list[dict[str, Any]]:
        return list(self._load().get("profiles", []))


__all__ = ["LayoutLearningV2", "FIELDS", "signature_similarity"]
