"""Testes isolados da IA opcional; nenhuma rede, usuario ou PDF real."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable_app.ai_assist import ChatGPTAssist, merge_missing, verify_id_token


class AICompletionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = ChatGPTAssist(Path(temp.name), "http://127.0.0.1:8767/auth/callback")

    def test_initially_disabled_without_consent_or_credentials(self):
        self.assertFalse(self.app.status()["enabled"])
        self.assertFalse(self.app.status()["connected"])
        with self.assertRaises(ValueError):
            self.app.begin()
        with patch.object(self.app, "ask") as ask:
            with self.assertRaises(ValueError):
                self.app.correct(Path("unused.pdf"), {"fornecedor": ""})
            ask.assert_not_called()

    def test_consent_does_not_activate_without_valid_login(self):
        result = self.app.consent(True)
        self.assertTrue(result["consented"])
        self.assertFalse(result["enabled"])
        url = self.app.begin()["url"]
        self.assertIn("client_id=dynamic_agent_client", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("127.0.0.1%3A8767", url)
        self.assertNotIn("access_token", url)

    def test_manual_correction_requires_click_and_confirmed_consent(self):
        original = {
            "fornecedor": "BANCO ERRADO", "pagador": "ELMER MARLOW",
            "vencimento": "2026-04-30", "valor": 3500.0,
            "codigo_pagamento": "", "observacoes": "APOLICE ORIGINAL.pdf",
        }
        with patch.object(self.app, "ask") as ask:
            with self.assertRaises(ValueError):
                self.app.correct(Path("falso.pdf"), original)
            ask.assert_not_called()
        self.app.credentials = {
            "access_token": "fake", "scopes": ["chatgpt.tokens.use.direct"]
        }
        self.app.settings = {"enabled": True, "consented": True}
        with patch.object(self.app, "ask", return_value={
            "fornecedor": "BRADESCO AUTO/RE COMPANHIA DE SEGUROS",
            "pagador": "ELMER MARLOW",
            "vencimento": "2026-07-30",
            "valor": 3493.34,
            "codigo_pagamento": "12345",
        }) as ask:
            reviewed = self.app.correct(Path("falso.pdf"), original)
        ask.assert_called_once()
        self.assertEqual(reviewed["fields"]["fornecedor"],
                         "BRADESCO AUTO/RE COMPANHIA DE SEGUROS")
        self.assertEqual(reviewed["fields"]["valor"], 3493.34)
        self.assertEqual(reviewed["fields"]["vencimento"], "2026-07-30")
        self.assertEqual(reviewed["fields"]["codigo_pagamento"], "")
        self.assertNotIn("observacoes", reviewed["fields"])
        self.assertEqual(original["fornecedor"], "BANCO ERRADO")
        self.assertEqual(original["valor"], 3500.0)
        self.assertEqual(set(reviewed["changed"]), {"fornecedor", "valor", "vencimento"})
        self.assertFalse(hasattr(self.app, "save_account"))

    def test_manual_ai_cannot_clear_fields_or_invent_invalid_payments(self):
        from portable_app.ai_assist import merge_reviewed
        data = {"fornecedor": "EMPRESA ORIGINAL", "vencimento": "2026-07-30",
                "valor": 34.2, "codigo_pagamento": ""}
        out, changed = merge_reviewed(data, {
            "fornecedor": "", "vencimento": "2026-02-31",
            "valor": -300, "codigo_pagamento": "1234"
        })
        self.assertEqual(changed, [])
        self.assertEqual(out, data)

    def test_missing_field_reviewer_is_opt_in_even_when_login_enabled(self):
        self.app.credentials = {
            "access_token": "fake", "scopes": ["chatgpt.tokens.use.direct"]
        }
        self.app.settings = {"enabled": False, "consented": True}
        with patch.object(self.app, "ask") as ask:
            with self.assertRaises(ValueError):
                self.app.correct(Path("unused.pdf"), {"fornecedor": ""})
            ask.assert_not_called()

    def test_bad_dates_and_payment_codes_ignored(self):
        result, fields = merge_missing({
            "valor": None, "vencimento": "", "codigo_pagamento": "",
            "fornecedor": "",
        }, {
            "valor": "-200", "vencimento": "31/02/2026",
            "codigo_pagamento": "1234", "fornecedor": "Banco Teste",
        })
        self.assertEqual(fields, ["fornecedor"])
        self.assertEqual(result["codigo_pagamento"], "")

    def test_auth_state_is_one_time_and_cannot_be_forged(self):
        self.app.consent(True)
        self.app.begin()
        with self.assertRaises(ValueError):
            self.app.finish("state=wrong&code=attack")
        self.assertIsNone(self.app.pending)

    def test_unsigned_identity_token_is_rejected(self):
        with self.assertRaises(ValueError):
            verify_id_token("e30.e30.", "oaiapp_fake", "nonce")


if __name__ == "__main__":
    unittest.main()
