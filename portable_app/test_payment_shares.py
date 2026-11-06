"""Testes isolados de link público de cobrança, sem rede nem banco real."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable_app import payment_shares as ps


class PaymentShareTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="contas-share-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.account = {
            "fornecedor": "Bradesco Auto RE",
            "pagador": "NÃO DEVE APARECER",
            "valor": 1589.15,
            "observacoes": "SEGURO ULTIMA PARCELA CAR 3.pdf",
            "codigo_pagamento": "23790001085203407107756026534406316210000158915",
        }

    def test_link_validates_signature_and_expires(self):
        now = 1_783_000_000
        token = ps.create_token(self.root, 40, now=now)
        self.assertEqual(ps.read_token(self.root, token, now=now), 40)
        self.assertEqual(
            ps.read_token(self.root, token, now=now + ps.TTL_SECONDS + 1), None
        )
        self.assertIsNone(ps.read_token(self.root, token[:-2] + "aa", now=now))
        self.assertIsNone(ps.read_token(self.root, "100.123.bug"))
        self.assertIsNone(ps.read_token(self.root, token.replace("40.", "41.", 1), now=now))
        self.assertEqual(len((self.root / ps.SECRET_NAME).read_bytes()), 32)

    def test_tokens_are_unique_and_private(self):
        now = 1_783_000_000
        a = ps.create_token(self.root, 15, now)
        b = ps.create_token(self.root, 15, now)
        self.assertNotEqual(a, b)
        self.assertEqual(ps.read_token(self.root, a, now), 15)
        self.assertEqual(ps.read_token(self.root, b, now), 15)
        if os.name == "posix":
            self.assertEqual((self.root / ps.SECRET_NAME).stat().st_mode & 0o777, 0o600)

    def test_html_has_open_graph_and_copy_code_without_private_fields(self):
        with patch.dict(os.environ, {
            "CONTAS_SHARE_ORIGIN": "https://contas-cloud.fazendanovohorizonte.com"
        }):
            url = "https://contas-cloud.fazendanovohorizonte.com/p/exemplo"
            output = ps.share_page(self.account, url).decode("utf-8")
        self.assertIn('property="og:title"', output)
        self.assertIn('property="og:description"', output)
        self.assertIn('property="og:image"', output)
        self.assertIn('content="https://contas-cloud.fazendanovohorizonte.com/p/exemplo"', output)
        self.assertIn("SEGURO ULTIMA PARCELA CAR 3", output)
        self.assertNotIn("CAR 3.pdf", output)
        self.assertIn(self.account["codigo_pagamento"], output)
        self.assertNotIn("NÃO DEVE APARECER", output)
        self.assertIn("Copiar código de pagamento", output)

    def test_no_html_injection_and_safe_metadata(self):
        self.account["fornecedor"] = '<script>alert("x")</script>'
        self.account["observacoes"] = 'Boleto <img src=x onerror=alert(1)>.PDF'
        with patch.dict(os.environ, {"CONTAS_SHARE_ORIGIN": "https://example.com"}):
            result = ps.share_page(self.account, "https://example.com/p/test").decode()
        self.assertNotIn("<script>alert", result)
        self.assertNotIn("<img src=x", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertIn("Boleto &lt;img", result)

    def test_preview_image_is_valid_png(self):
        image = ps.preview_png()
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image), 1000)

    def test_share_url_uses_configured_origin(self):
        with patch.dict(os.environ, {
            "CONTAS_SHARE_ORIGIN": "https://contas-cloud.fazendanovohorizonte.com"
        }):
            self.assertEqual(
                ps.share_url("abc"),
                "https://contas-cloud.fazendanovohorizonte.com/p/abc",
            )


if __name__ == "__main__":
    unittest.main()
