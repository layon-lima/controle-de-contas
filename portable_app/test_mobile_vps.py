"""Smoke tests: mobile VPS uses SAME SQLite, excludes every paid account."""
import sqlite3
import unittest

from portable_app import mobile_vps


class MobileVpsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE contas (
             id INTEGER PRIMARY KEY, fornecedor TEXT, documento TEXT,
             pagador TEXT, referencia TEXT, valor REAL, vencimento TEXT,
             codigo_pagamento TEXT, tipo_pagamento TEXT, observacoes TEXT,
             criado_em TEXT, atualizado_em TEXT, enviado_pagamento_em TEXT
          );
          CREATE TABLE pagamentos (
             id INTEGER PRIMARY KEY, conta_id INTEGER, data_pagamento TEXT,
             valor_pago REAL, beneficiario TEXT, documento_beneficiario TEXT,
             resultado_conferencia TEXT, detalhes_conferencia TEXT
          );
          INSERT INTO contas(id,fornecedor,valor,vencimento,codigo_pagamento)
              VALUES (1,'ABERTO',123.45,'2026-12-31','PIX-123'),
                     (2,'PAGO',333.0,'2026-12-31','PIX-222'),
                     (3,'PAGAMENTO MULTIPLO',222.0,'2026-12-31','PIX-333');
          INSERT INTO pagamentos(id,conta_id,data_pagamento,valor_pago)
              VALUES(7,2,'2026-10-20',333.0),
                    (8,3,NULL,NULL),
                    (9,3,'2026-10-19',222.0),
                    (10,3,NULL,NULL);
        """)

    def tearDown(self):
        self.db.close()
        with mobile_vps.LOCK:
            mobile_vps.COPIED.clear()

    def test_mobile_excludes_paid_and_keeps_fields(self):
        result = mobile_vps.listing(self.db, lambda due, paid: "Em dia")
        self.assertEqual(result["quantidade"], 1)
        item = result["contas"][0]
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["recebedor"], "ABERTO")
        self.assertEqual(item["codigo_pagamento"], "PIX-123")
        self.assertEqual(item["valor"], 123.45)
        self.assertFalse(item["tem_pdf"])

    def test_copy_never_marks_paid(self):
        result = mobile_vps.copied(self.db, 1)
        self.assertTrue(result["ok"])
        visible = mobile_vps.listing(self.db, lambda due, paid: "Em dia")["contas"][0]
        self.assertTrue(visible["codigo_copiado_recente"])
        self.assertIsNone(visible["data_pagamento"])
        self.assertIsNone(mobile_vps.copied(self.db, 2))
        self.assertIsNone(mobile_vps.copied(self.db, 3))


if __name__ == "__main__":
    unittest.main()
