"""Testes locais da conversão PDF -> imagem PNG, sem contas reais ou rede."""
from __future__ import annotations

import struct
import unittest

import fitz

from portable_app.pdf_clipboard import first_page_png


def make_pdf(*, pages=1, encrypted=False) -> bytes:
    pdf = fitz.open()
    for number in range(pages):
        page = pdf.new_page(width=595, height=842)
        page.insert_text((40, 70), f"BOLETO DE TESTE - PAGINA {number+1}", fontsize=16)
    if encrypted:
        data = pdf.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="admin-test", user_pw="senha"
        )
    else:
        data = pdf.tobytes()
    pdf.close()
    return data


class PdfClipboardTests(unittest.TestCase):
    def test_one_page_generates_valid_png(self):
        result = first_page_png(make_pdf())
        self.assertTrue(result.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", result[16:24])
        self.assertEqual((width, height), (1190, 1684))
        self.assertGreater(len(result), 2000)

    def test_multiple_pages_render_only_first_page(self):
        a = first_page_png(make_pdf(pages=1))
        b = first_page_png(make_pdf(pages=2))
        self.assertEqual(a, b)

    def test_invalid_and_locked_pdfs_rejected(self):
        for value in (b"", b"not a PDF", b"%PDF-invalid"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    first_page_png(value)
        with self.assertRaisesRegex(ValueError, "senha"):
            first_page_png(make_pdf(encrypted=True))

    def test_large_pdf_rejected(self):
        with self.assertRaisesRegex(ValueError, "30 MB"):
            first_page_png(b"%PDF-" + b"x" * (30*1024*1024))


if __name__ == "__main__":
    unittest.main()
