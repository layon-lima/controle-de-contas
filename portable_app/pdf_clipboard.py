"""Transforma a primeira pagina de um PDF em PNG para colar no WhatsApp.

Leitura apenas, sem gravar/alterar o PDF original ou o banco de contas.
"""
from __future__ import annotations

import fitz

MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000


def first_page_png(data: bytes) -> bytes:
    if not isinstance(data, bytes) or not data.startswith(b"%PDF-"):
        raise ValueError("O boleto não é um PDF válido.")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("O boleto ultrapassa o limite de 30 MB.")
    try:
        with fitz.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise ValueError("Este PDF está protegido por senha.")
            if pdf.page_count < 1:
                raise ValueError("Este PDF não possui páginas.")
            page = pdf.load_page(0)
            bounds = page.rect
            if bounds.width <= 0 or bounds.height <= 0:
                raise ValueError("A primeira página do PDF tem dimensões inválidas.")
            # 2x = cerca de 144 dpi num PDF A4, legível no WhatsApp.
            # Limitar pixels evita imagens enormes de PDFs fora do padrão.
            scale = min(2.0, (MAX_IMAGE_PIXELS / (bounds.width * bounds.height)) ** 0.5)
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                colorspace=fitz.csRGB, alpha=False
            )
            return pixmap.tobytes("png")
    except ValueError:
        raise
    except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError) as exc:
        raise ValueError("Não foi possível transformar o PDF em imagem.") from exc
