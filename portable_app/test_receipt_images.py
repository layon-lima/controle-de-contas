"""Conversão segura de fotos de comprovante em PDF único, sem dados reais."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fitz
from PIL import Image, ImageDraw

from portable_app import pdf_workflows, receipt_images


def sample_image(fmt="JPEG", mode="RGB"):
    image = Image.new(mode, (680, 990), (255, 255, 255, 255)
                      if mode == "RGBA" else "white")
    painter = ImageDraw.Draw(image)
    painter.text((55, 95), "COMPROVANTE FOTO TESTE", fill="black")
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


class ReceiptImagesTests(unittest.TestCase):
    def test_jpeg_png_and_webp_become_real_single_page_pdf(self):
        for fmt, mime in (
            ("JPEG", "image/jpeg"), ("PNG", "image/png"),
            ("WEBP", "image/webp")
        ):
            with self.subTest(fmt=fmt):
                source = sample_image(fmt)
                pdf, converted = receipt_images.normalize_receipt(source, mime)
                self.assertTrue(converted)
                pdf_workflows.check_pdf(pdf)
                with fitz.open(stream=pdf, filetype="pdf") as result:
                    self.assertEqual(result.page_count, 1)
                    self.assertTrue(result[0].get_images())

    def test_png_transparency_becomes_white_background(self):
        image = Image.new("RGBA", (100, 120), (0, 0, 0, 0))
        image.putpixel((30, 30), (220, 0, 0, 255))
        raw = BytesIO()
        image.save(raw, "PNG")
        result, _ = receipt_images.normalize_receipt(raw.getvalue(), "image/png")
        with fitz.open(stream=result, filetype="pdf") as pdf:
            pixels = pdf[0].get_pixmap(matrix=fitz.Matrix(1, 1))
            self.assertGreater(len(pixels.samples), 1000)
            self.assertEqual(pdf.page_count, 1)

    def test_original_pdf_is_not_changed(self):
        with fitz.open() as doc:
            doc.new_page().insert_text((30, 30), "BOLETO ORIGINAL")
            original = doc.tobytes()
        result, image_origin = receipt_images.normalize_receipt(
            original, "application/pdf"
        )
        self.assertFalse(image_origin)
        self.assertIs(result, original)

    def test_same_photo_yields_identical_pdf_for_duplicate_detection(self):
        photo = sample_image("JPEG")
        first, _ = receipt_images.normalize_receipt(photo, "image/jpeg")
        second, _ = receipt_images.normalize_receipt(photo, "image/jpeg")
        self.assertEqual(first, second)

    def test_invalid_files_and_spoofed_mime_rejected(self):
        with self.assertRaisesRegex(ValueError, "Formato"):
            receipt_images.normalize_receipt(b"not-a-pdf", "application/heic")
        with self.assertRaises(ValueError):
            receipt_images.normalize_receipt(b"not a JPEG", "image/jpeg")
        with self.assertRaises(ValueError):
            receipt_images.normalize_receipt(sample_image("PNG"), "image/jpeg")
        with self.assertRaises(ValueError):
            receipt_images.normalize_receipt(b"not a PDF", "application/pdf")

    def test_multiframe_image_is_rejected_instead_of_losing_pages(self):
        first = Image.new("RGB", (50, 50), "white")
        second = Image.new("RGB", (50, 50), "black")
        raw = BytesIO()
        first.save(raw, format="GIF", save_all=True, append_images=[second])
        with self.assertRaisesRegex(ValueError, "uma imagem por vez"):
            receipt_images.normalize_receipt(raw.getvalue(), "image/gif")

    def test_image_merges_with_existing_invoice_as_one_pdf(self):
        receipt, _ = receipt_images.normalize_receipt(sample_image(), "image/jpeg")
        with TemporaryDirectory(prefix="receipt-image-") as tmp:
            invoice = Path(tmp) / "boleto.pdf"
            result = Path(tmp) / "mesclado.pdf"
            with fitz.open() as doc:
                doc.new_page().insert_text((30, 45), "BOLETO ORIGINAL")
                invoice.write_bytes(doc.tobytes())
            pages = pdf_workflows.merge_receipt(invoice, receipt, result)
            self.assertEqual(pages, 2)
            with fitz.open(str(result)) as pdf:
                self.assertEqual(pdf.page_count, 2)
                self.assertIn("BOLETO ORIGINAL", pdf[0].get_text())
                self.assertTrue(pdf[1].get_images())


if __name__ == "__main__":
    unittest.main()
