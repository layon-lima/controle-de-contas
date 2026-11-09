"""Normaliza comprovantes de imagem para PDF, somente em memória.

A rotina de pagamento original continua recebendo bytes PDF e mescla
boleto + comprovante num arquivo final. Não modifica a imagem enviada.
"""
from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from . import pdf_workflows
except ImportError:
    # server.py tambem é iniciado diretamente pelo container da VPS.
    import pdf_workflows

MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 32_000_000
IMAGE_TYPES = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/bmp": "BMP",
    "image/gif": "GIF",
    "image/tiff": "TIFF",
}
ALLOWED_TYPES = frozenset({"application/pdf", *IMAGE_TYPES})


def normalize_receipt(data: bytes, content_type: str) -> tuple[bytes, bool]:
    """Returns (PDF bytes, was_image); rejects invalid/oversized content.

    Image attachments are single-page: multi-frame GIF/TIFF files are rejected
    rather than silently dropping evidence. Alpha gets a white background and
    EXIF rotation is respected. The original file isn't written to disk.
    """
    content_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_TYPES:
        raise ValueError("Formato não aceito. Use PDF, JPG, PNG, WebP, BMP, GIF ou TIFF.")
    if not isinstance(data, bytes) or len(data) < 5 or len(data) > MAX_FILE_BYTES:
        raise ValueError("O comprovante deve ter até 30 MB e não pode estar vazio.")
    if content_type == "application/pdf":
        pdf_workflows.check_pdf(data)
        return data, False

    try:
        with Image.open(BytesIO(data)) as source:
            if source.format != IMAGE_TYPES[content_type]:
                raise ValueError("O formato real da imagem não corresponde ao arquivo selecionado.")
            if source.width <= 0 or source.height <= 0:
                raise ValueError("A imagem possui dimensões inválidas.")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Imagem muito grande. O limite é de 32 megapixels.")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Envie uma imagem por vez. Arquivos animados ou TIFFs de várias páginas não são aceitos.")
            rotated = ImageOps.exif_transpose(source)
            # Transp. do PNG/WebP precisa ser sobre branco, não sobre preto.
            if rotated.mode in ("RGBA", "LA", "P") or "transparency" in rotated.info:
                rgba = rotated.convert("RGBA")
                image = Image.new("RGB", rgba.size, "white")
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = rotated.convert("RGB")
            jpeg = BytesIO()
            image.save(jpeg, format="JPEG", quality=93, subsampling=0, optimize=True)
            width, height = image.size

        # Uma folha A4 em orientação adequada. A imagem fica incorporada
        # integralmente no PDF, mantendo a resolução para zoom e impressão.
        paper_width, paper_height = ((842, 595) if width > height else (595, 842))
        margin = 22
        scale = min((paper_width - 2 * margin) / width,
                    (paper_height - 2 * margin) / height)
        shown_width, shown_height = width * scale, height * scale
        left = (paper_width - shown_width) / 2
        top = (paper_height - shown_height) / 2
        with fitz.open() as document:
            page = document.new_page(width=paper_width, height=paper_height)
            page.insert_image(
                fitz.Rect(left, top, left + shown_width, top + shown_height),
                stream=jpeg.getvalue(), keep_proportion=True
            )
            # Sem IDs randômicos: a mesma imagem gera exatamente o mesmo PDF.
            # Preserva detecção de comprovante duplicado via SHA256.
            result = document.tobytes(garbage=4, deflate=True, no_new_id=True)
        if len(result) > MAX_FILE_BYTES:
            raise ValueError("O PDF gerado ultrapassou 30 MB. Use uma imagem menor.")
        pdf_workflows.check_pdf(result)
        return result, True
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Não foi possível abrir esta imagem. Envie JPG, PNG, WebP, BMP, GIF ou TIFF válido.") from exc
