"""Regressao da politica R2: no PC todos os PDFs; na nuvem so nao pagos."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from source_current.controle_contas import cloud_sync


class R2PdfPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="contas_r2_")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.docs = self.home / "Documents" / "BOLETOS" / "BOLETOS 2027" / "01 - JANEIRO"
        self.docs.mkdir(parents=True)
        self.originals = {}
        for account_id in (1, 2):
            pdf = self.docs / ("boleto_%d.pdf" % account_id)
            pdf.write_bytes(b"%PDF-1.4\nPDF ORIGINAL DO PC\n%%EOF")
            self.originals[account_id] = pdf
        self.outside = self.home / "outside.pdf"
        self.outside.write_bytes(b"%PDF-1.4\nOUTRO ARQUIVO\n%%EOF")
        self.missing = self.docs / "nao_existe.pdf"
        self.db = self.home / "contas.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "CREATE TABLE contas("
                "id INTEGER PRIMARY KEY, fornecedor TEXT, documento TEXT, pagador TEXT,"
                "referencia TEXT, valor REAL, vencimento TEXT, codigo_pagamento TEXT,"
                "tipo_pagamento TEXT, observacoes TEXT, criado_em TEXT, atualizado_em TEXT,"
                "enviado_pagamento_em TEXT, pdf_conta TEXT)"
            )
            conn.execute(
                "CREATE TABLE pagamentos("
                "id INTEGER PRIMARY KEY, conta_id INTEGER, valor_pago REAL,"
                "data_pagamento TEXT, beneficiario TEXT, documento_beneficiario TEXT,"
                "resultado_conferencia TEXT, detalhes_conferencia TEXT)"
            )
            paths = {
                1: self.originals[1],
                2: self.originals[2],
                3: self.missing,
                4: self.outside,
            }
            for account_id, path in paths.items():
                conn.execute(
                    "INSERT INTO contas(id,fornecedor,valor,vencimento,pdf_conta)"
                    " VALUES(?,?,?,?,?)",
                    (account_id, "FORNECEDOR", 1.0, "2027-01-15", str(path))
                )
                if account_id != 1:
                    conn.execute(
                        "INSERT INTO pagamentos(id,conta_id,valor_pago,data_pagamento)"
                        " VALUES(?,?,?,?)",
                        (account_id, account_id, 1.0, "2026-10-20")
                    )

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def test_only_open_pdf_is_sent_and_paid_local_is_verified(self):
        before_db = self.db.read_bytes()
        original_bytes = {k: p.read_bytes() for k, p in self.originals.items()}
        with patch.object(cloud_sync, "_connect", side_effect=self._connect), \
             patch.object(Path, "home", return_value=self.home):
            accounts, pdf_paths = cloud_sync._snapshot()
        by_id = {int(item["id"]): item for item in accounts}
        self.assertEqual(set(pdf_paths), {1})
        self.assertEqual(pdf_paths[1], self.originals[1])
        self.assertEqual(by_id[1]["pdf_hash"],
                         hashlib.sha256(original_bytes[1]).hexdigest())
        self.assertFalse(by_id[1]["local_pdf_confirmed"])
        self.assertTrue(by_id[2]["local_pdf_confirmed"])
        self.assertEqual(by_id[2]["pdf_hash"], "")
        self.assertFalse(by_id[3]["local_pdf_confirmed"])
        self.assertFalse(by_id[4]["local_pdf_confirmed"])
        for item in accounts:
            self.assertNotIn("pdf_conta", item)
        self.assertEqual(self.db.read_bytes(), before_db)
        for key, pdf in self.originals.items():
            self.assertEqual(pdf.read_bytes(), original_bytes[key])

    def test_paid_file_invalid_header_must_not_be_deleted_from_r2(self):
        self.originals[2].write_bytes(b"NOT_A_PDF_BUT_HAS_PDF_EXTENSION")
        with patch.object(cloud_sync, "_connect", side_effect=self._connect), \
             patch.object(Path, "home", return_value=self.home):
            accounts, paths = cloud_sync._snapshot()
        paid = next(item for item in accounts if item["id"] == 2)
        self.assertFalse(paid["local_pdf_confirmed"])
        self.assertNotIn(2, paths)


if __name__ == "__main__":
    unittest.main()
