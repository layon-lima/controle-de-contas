"""Regressoes do inventario: banco e PDFs permanecem intactos."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.CONTAS_INVENTARIAR_PDFS import inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder / "contas.db"
        self.root = self.folder / "BOLETOS"
        self.pdf = self.root / "BOLETOS 2027" / "01 - JANEIRO" / "teste.pdf"
        self.pdf.parent.mkdir(parents=True)
        self.pdf.write_bytes(b"%PDF-1.4 fake-fixture")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE contas(id INTEGER, pdf_conta TEXT)")
            conn.execute("CREATE TABLE pagamentos(conta_id INTEGER, pdf_comprovante TEXT)")
            conn.execute("INSERT INTO contas VALUES(1,?)", (str(self.pdf),))
            conn.execute("INSERT INTO contas VALUES(2,?)", (str(self.root / 'missing.pdf'),))
            conn.execute("INSERT INTO pagamentos VALUES(1,?)", (str(self.pdf),))

    def test_scan_does_not_change_data_or_create_pdfs(self):
        before_db, before_pdf = self.db.read_bytes(), self.pdf.read_bytes()
        report = inventory(self.db, self.root)
        self.assertEqual(report["accounts"], 2)
        self.assertEqual(report["references"], 3)
        self.assertEqual(report["unique_files"], 2)
        self.assertEqual(report["missing"], 1)
        self.assertEqual(report["by_year"]["BOLETOS 2027"], 2)
        self.assertEqual(report["pdfs_copied_to_vps"], 0)
        self.assertEqual(self.db.read_bytes(), before_db)
        self.assertEqual(self.pdf.read_bytes(), before_pdf)

    def test_missing_database_fails_closed(self):
        absent = self.folder / "missing.db"
        with self.assertRaises(FileNotFoundError):
            inventory(absent, self.root)
        self.assertFalse(absent.exists())

    def test_optional_hash_only_reads_document(self):
        original = self.pdf.read_bytes()
        result = inventory(self.db, self.root, hash_pdfs=True)
        self.assertEqual(len(result["entries"][0]["sha256"]), 64)
        self.assertEqual(original, self.pdf.read_bytes())


if __name__ == "__main__":
    unittest.main()
