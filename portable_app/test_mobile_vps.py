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
             criado_em TEXT, atualizado_em TEXT, enviado_pagamento_em TEXT,
             pdf_conta TEXT
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


    def test_davi_in_any_user_field_never_appears_on_mobile(self):
        cases = {
            11: ("fornecedor", "Davi Consultoria"),
            12: ("pagador", "Conta do davi"),
            13: ("observacoes", "Descricao: pagar DAVI"),
            14: ("referencia", "SERVICO DÁVI"),
            15: ("documento", "REF/DAVI/2026"),
            16: ("codigo_pagamento", "PIX-DAVI-999"),
            17: ("tipo_pagamento", "Davi no tipo"),
            18: ("pdf_conta", r"C:\\Contas\\FATURA DAVI.pdf"),
        }
        for account_id, (field, value) in cases.items():
            self.db.execute(
                "INSERT INTO contas (id, fornecedor, valor, vencimento) "
                "VALUES (?,?,?,?)",
                (account_id, "SEM RESTRICAO", 10, "2026-12-31"),
            )
            self.db.execute(
                "UPDATE contas SET " + field + "=? WHERE id=?",
                (value, account_id),
            )
        # Um pagamento antigo pode ter a mencao, mesmo que o ultimo nao tenha.
        self.db.execute(
            "INSERT INTO contas (id, fornecedor, vencimento) "
            "VALUES (25,'SEM RESTRICAO','2026-12-31')"
        )
        self.db.execute(
            "INSERT INTO pagamentos (id, conta_id, beneficiario) "
            "VALUES (50,25,'DAVI antigo')"
        )
        self.db.execute(
            "INSERT INTO pagamentos (id, conta_id, beneficiario) "
            "VALUES (51,25,'Outro beneficiario')"
        )
        self.db.commit()
        checked_pdfs = []
        result = mobile_vps.listing(
            self.db, lambda due, paid: "Em dia",
            lambda aid: checked_pdfs.append(aid) or True,
        )
        self.assertEqual([row["id"] for row in result["contas"]], [1])
        self.assertEqual(checked_pdfs, [1])
        for account_id in [*cases, 25]:
            self.assertFalse(mobile_vps.mobile_account_visible(self.db, account_id))
            self.assertIsNone(mobile_vps.copied(self.db, account_id))
        self.assertTrue(mobile_vps.mobile_account_visible(self.db, 1))

    def test_davi_inside_payment_notes_also_hidden(self):
        self.db.execute(
            "INSERT INTO contas(id,fornecedor,vencimento) "
            "VALUES (26,'OUTRO','2026-12-31')"
        )
        self.db.execute(
            "INSERT INTO pagamentos(id,conta_id,detalhes_conferencia) "
            "VALUES (60,26,'Recebedor: DAVI')"
        )
        self.db.commit()
        visible = mobile_vps.listing(self.db, lambda due,paid:"Em dia")
        self.assertNotIn(26, [r["id"] for r in visible["contas"]])
        self.assertFalse(mobile_vps.mobile_account_visible(self.db,26))

    def test_filename_not_parent_directory_activates_block(self):
        self.db.execute(
            "INSERT INTO contas (id,fornecedor,vencimento,pdf_conta) "
            "VALUES (27,'OUTRO','2026-12-31',?)",
            (r"C:\\Users\\Davi\\Documents\\boleto normal.pdf",),
        )
        self.db.commit()
        self.assertTrue(mobile_vps.mobile_account_visible(self.db,27))
        ids = [row["id"] for row in mobile_vps.listing(
            self.db, lambda due,paid:"Em dia")["contas"]]
        self.assertIn(27, ids)




if __name__ == "__main__":
    unittest.main()
