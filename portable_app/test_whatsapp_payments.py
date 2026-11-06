"""Testes offline da API oficial: nunca disparam mensagens reais."""
from __future__ import annotations

import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from portable_app import whatsapp_payments as wp


class _Reply:
    def __init__(self):
        self.content = BytesIO(json.dumps(
            {"messages": [{"id": "wamid.test-accepted"}]}
        ).encode())

    def __enter__(self):
        return self.content

    def __exit__(self, *args):
        self.content.close()


class WhatsAppPaymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="contas-whatsapp-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / wp.CONFIG_NAME).write_text(json.dumps({
            "enabled": True,
            "phone_number_id": "123456789012345",
            "access_token": "abc-secret-example-token-that-is-only-for-tests",
            "api_version": "v26.0",
            "template_boleto": "contas_pagar_boleto",
            "template_pix": "contas_pagar_pix",
        }), encoding="utf-8")
        self.account = {
            "fornecedor": "Bradesco Auto RE",
            "valor": 1589.15,
            "observacoes": "SEGURO ULTIMA PARCELA CAR 3.pdf",
            "codigo_pagamento": "23790001085203407107756026534406316210000158915"
        }

    def test_boleto_payload_and_observation_without_pdf(self):
        cfg = wp._read_config(self.root)
        payload = wp.message_payload(self.account, "(11) 99999-8888", cfg)
        self.assertEqual(payload["to"], "5511999998888")
        self.assertEqual(payload["template"]["name"], "contas_pagar_boleto")
        params = payload["template"]["components"][0]["parameters"]
        self.assertEqual([x["text"] for x in params], [
            "Bradesco Auto RE", "R$ 1.589,15", "SEGURO ULTIMA PARCELA CAR 3",
        ])
        action = payload["template"]["components"][1]["parameters"][0]
        self.assertEqual(
            action["action"]["payment_request"]["payment_setting"]["boleto"]["digitable_line"],
            self.account["codigo_pagamento"]
        )

    def test_pix_payload(self):
        self.account["codigo_pagamento"] = "000201" + "a" * 161
        cfg = wp._read_config(self.root)
        payload = wp.message_payload(self.account, "11999998888", cfg)
        self.assertEqual(payload["template"]["name"], "contas_pagar_pix")
        setting = payload["template"]["components"][1]["parameters"][0]["action"]["payment_request"]["payment_setting"]
        self.assertEqual(setting["pix_dynamic_code"]["code"], self.account["codigo_pagamento"])

    def test_short_or_unsupported_code_blocked(self):
        self.account["codigo_pagamento"] = "pix@banco.com"
        with self.assertRaises(ValueError):
            wp.message_payload(self.account, "11999998888", wp._read_config(self.root))
        self.account["codigo_pagamento"] = "12345"
        with self.assertRaises(ValueError):
            wp.message_payload(self.account, "11999998888", wp._read_config(self.root))

    def test_opt_in_and_configuration_required(self):
        with self.assertRaisesRegex(ValueError, "autorizou"):
            wp.send(self.root, self.account, "11999998888", opt_in=False)
        (self.root / wp.CONFIG_NAME).unlink()
        self.assertFalse(wp.status(self.root)["configured"])
        with self.assertRaises(wp.WhatsAppNotConfigured):
            wp.send(self.root, self.account, "11999998888", opt_in=True)

    def test_explicit_send_uses_only_meta_graph_url(self):
        calls = []
        def fake_open(request, timeout=0):
            calls.append((request.full_url, request.get_header("Authorization"),
                          json.loads(request.data)))
            return _Reply()
        result = wp.send(self.root, self.account, "11999998888",
                         opt_in=True, opener=fake_open)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["mode"], "boleto")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0],
                         "https://graph.facebook.com/v26.0/123456789012345/messages")
        self.assertEqual(calls[0][2]["messaging_product"], "whatsapp")

    def test_invalid_phone_and_template_name_blocked(self):
        with self.assertRaises(ValueError):
            wp.normalize_phone("123")
        cfg = wp._read_config(self.root)
        cfg["templates"]["boleto"] = None
        with self.assertRaises(wp.WhatsAppNotConfigured):
            wp.message_payload(self.account, "11999998888", cfg)


if __name__ == "__main__":
    unittest.main()
