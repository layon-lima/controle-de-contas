"""Testes de regressao da duplicidade no Controle de Contas VPS.

Usa banco e arquivos TEMPORARIOS; nunca consulta dados reais nem acessa o R2.
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable_app import pdf_workflows
from portable_app import server as s


CODE = "12345678901234567890123456789012345678901234567"


class DuplicateChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="contas-duplicado-")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.database = self.folder / "data" / "controle_contas.db"
        self.database.parent.mkdir()
        self.conn = sqlite3.connect(self.database)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE contas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fornecedor TEXT, documento TEXT, pagador TEXT, referencia TEXT,
                valor REAL, vencimento TEXT, codigo_pagamento TEXT,
                tipo_pagamento TEXT, observacoes TEXT,
                criado_em TEXT, atualizado_em TEXT, enviado_pagamento_em TEXT,
                pdf_conta TEXT
            );
            CREATE TABLE pagamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conta_id INTEGER, valor_pago REAL, data_pagamento TEXT,
                beneficiario TEXT, documento_beneficiario TEXT,
                pdf_comprovante TEXT, resultado_conferencia TEXT,
                detalhes_conferencia TEXT, criado_em TEXT, atualizado_em TEXT
            );
        """)
        self.addCleanup(self.conn.close)
        self.root_patch = patch.object(s, "APP_DIR", self.folder)
        self.db_patch = patch.object(s, "DB_PATH", self.database)
        self.backup_patch = patch.object(s, "BACKUP_DONE", False)
        for patcher in (self.root_patch, self.db_patch, self.backup_patch):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fields = {
            "fornecedor": "FORNECEDOR EXEMPLO", "valor": 150.25,
            "vencimento": "2026-11-30", "codigo_pagamento": CODE,
        }
        self.incoming = self.folder / "novo.pdf"
        self.incoming.write_bytes(b"%PDF- content example - second import")

    def insert(self, *, code="", path="", amount=150.25, due="2026-11-30"):
        cursor = self.conn.execute(
            "INSERT INTO contas (fornecedor,valor,vencimento,codigo_pagamento,pdf_conta) "
            "VALUES (?,?,?,?,?)",
            ("FORNECEDOR EXEMPLO", amount, due, code, path),
        )
        self.conn.commit()
        return cursor.lastrowid

    def test_manual_create_rejects_same_boleto_even_with_formatting(self):
        self.insert(code=CODE)
        with self.assertRaisesRegex(ValueError, "Linha digitavel ja cadastrada"):
            s.create_account(dict(self.fields, codigo_pagamento=" ".join(
                CODE[i:i+5] for i in range(0, len(CODE), 5)
            )))
        count = self.conn.execute("SELECT COUNT(*) FROM contas").fetchone()[0]
        self.assertEqual(count, 1)

    def test_editing_other_account_does_not_allow_duplicate_payment_code(self):
        self.insert(code=CODE)
        other = self.insert(code="PIX_KEY_NOT_UNIQUE")
        with self.assertRaisesRegex(ValueError, "Linha digitavel ja cadastrada"):
            s.edit_account(other, self.fields)
        self.assertEqual(
            self.conn.execute("SELECT codigo_pagamento FROM contas WHERE id=?", (other,)).fetchone()[0],
            "PIX_KEY_NOT_UNIQUE",
        )

    def test_same_supplier_amount_and_due_are_not_enough_to_block(self):
        self.insert(code="")
        with self.assertRaises(Exception) as invalid_data:
            s.create_account({"fornecedor": "", "valor": 0, "vencimento": ""})
        self.assertTrue(invalid_data.exception)
        new_id = s.create_account({
            "fornecedor": "FORNECEDOR EXEMPLO",
            "valor": 150.25,
            "vencimento": "2026-11-30",
        })
        self.assertGreater(new_id, 1)

    def test_exact_pdf_found_on_local_disk_without_payment_code(self):
        original = self.folder / "original.pdf"
        original.write_bytes(self.incoming.read_bytes())
        aid = self.insert(path=str(original),code="")
        with self.assertRaisesRegex(ValueError, str(aid)):
            pdf_workflows.check_duplicate(
                self.conn, self.incoming, dict(self.fields,codigo_pagamento="")
            )

    def test_same_pdf_found_in_r2_by_sha256_when_windows_path_unavailable(self):
        aid = self.insert(path=r"C:\\Users\\User\\Documents\\BOLETOS\\anterior.pdf",code="")
        digest = hashlib.sha256(self.incoming.read_bytes()).hexdigest()
        hits = []
        def remote_meta(id):
            hits.append(id)
            return {"hash": digest, "size": self.incoming.stat().st_size}
        with self.assertRaisesRegex(ValueError, "R2"):
            pdf_workflows.check_duplicate(
                self.conn, self.incoming, dict(self.fields,codigo_pagamento=""),
                remote_metadata=remote_meta,
            )
        self.assertEqual(hits, [aid])

    def test_legacy_r2_pdf_without_sha256_is_compared_by_bytes(self):
        aid = self.insert(path=r"C:\\Documentos\\boleto.pdf",code="")
        reads = []
        def read(id):
            reads.append(id)
            return self.incoming.read_bytes()
        with self.assertRaisesRegex(ValueError, "R2"):
            pdf_workflows.check_duplicate(
                self.conn, self.incoming, dict(self.fields,codigo_pagamento=""),
                remote_metadata=lambda _: {"size": self.incoming.stat().st_size, "hash": ""},
                remote_read=read,
            )
        self.assertEqual(reads, [aid])

    def test_nonmatching_r2_is_not_incorrectly_rejected(self):
        self.insert(code="", path=r"C:\\Documentos\\outro.pdf")
        pdf_workflows.check_duplicate(
            self.conn,self.incoming,dict(self.fields,codigo_pagamento=""),
            remote_metadata=lambda _: {"hash": "a"*64, "size": self.incoming.stat().st_size},
            remote_read=lambda _: self.fail("Nao baixar documento com hash conhecido diferente"),
        )

    def test_unrelated_values_and_due_dates_do_not_scan_r2(self):
        self.insert(code="",path=r"C:\\Documentos\\outro.pdf",amount=987.23,due="2026-12-15")
        pdf_workflows.check_duplicate(
            self.conn,self.incoming,dict(self.fields,codigo_pagamento=""),
            remote_metadata=lambda _: self.fail("Nao deveria consultar R2 para contas sem indicios"),
        )

    def test_same_supplier_amount_finds_r2_even_if_due_differs(self):
        aid = self.insert(code="",path=r"C:\\Documentos\\boleto.pdf",due="2026-12-15")
        digest=hashlib.sha256(self.incoming.read_bytes()).hexdigest()
        checked=[]
        def metadata(id):
            checked.append(id)
            return {"hash":digest,"size":self.incoming.stat().st_size}
        with self.assertRaisesRegex(ValueError, "R2"):
            pdf_workflows.check_duplicate(
                self.conn,self.incoming,dict(self.fields,codigo_pagamento=""),
                remote_metadata=metadata,
            )
        self.assertEqual(checked,[aid])

    def test_static_pix_key_does_not_block_distinct_invoices(self):
        same_key={"codigo_pagamento": "chave.pix@empresa.com",
                  "valor": 150.25, "vencimento": "2026-11-30"}
        self.insert(code="chave.pix@empresa.com")
        pdf_workflows.check_duplicate_fields(self.conn,same_key)

    def test_self_edit_does_not_block_existing_code(self):
        aid=self.insert(code=CODE)
        pdf_workflows.check_duplicate_fields(self.conn,self.fields,exclude_id=aid)


if __name__ == "__main__":
    unittest.main()
