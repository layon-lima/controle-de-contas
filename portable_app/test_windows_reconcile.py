"""Regressões da recuperação retroativa de PDFs do CONTAS 4."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portable_app import windows_reconcile as sync


class FakeR2:
    MAX_PDF = 30 * 1024 * 1024

    class R2Unavailable(RuntimeError):
        pass

    def __init__(self, known=None, enabled=True):
        self.known = set(known or [])
        self.files = {}
        self.enabled_flag = enabled
        self.uploaded = []

    def enabled(self):
        return self.enabled_flag

    def existing_ids(self):
        return set(self.known)

    def metadata(self, account_id):
        return {
            "hash": hashlib.sha256(self.files[account_id]).hexdigest(),
            "size": len(self.files[account_id])
        }

    def write_pdf(self, account_id, content):
        self.uploaded.append((account_id, content))
        self.files[account_id] = content
        self.known.add(account_id)


class WindowsRetroTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE contas(id INTEGER PRIMARY KEY, vencimento TEXT,"
            " observacoes TEXT, pdf_conta TEXT)"
        )
        path = Path(self.root.name, "boleto.pdf")
        self.pdf = b"%PDF-1.7\narquivo de teste"
        path.write_bytes(self.pdf)
        self.conn.executemany(
            "INSERT INTO contas VALUES(?,?,?,?)",
            [
                (1, "2026-11-18", "Conta nova", str(path)),
                (2, "2026-10-20", "Holerite pago", "/Windows/antigo.pdf"),
                (3, "2026-09-20", "Fonte ausente", "/Windows/sumiu.pdf"),
            ]
        )

    def test_manifest_inclui_antigos_e_pagos_sem_abrir_caminho_local(self):
        fake = FakeR2(known=[2])
        fake.files[2] = b"%PDF-1.4\npago remoto"
        result = sync.pdf_manifest(self.conn, fake)
        self.assertEqual(result["available"], 2)
        self.assertEqual(result["missing_source"], 1)
        self.assertEqual(result["r2_missing"], 1)
        self.assertEqual([x["id"] for x in result["items"]], [1, 2])
        self.assertEqual(result["items"][0]["sha256"],
                         hashlib.sha256(self.pdf).hexdigest())
        self.assertEqual(result["items"][1]["sha256"],
                         hashlib.sha256(fake.files[2]).hexdigest())
        self.assertNotIn("pdf_conta", str(result))
        self.assertNotIn("/Windows/", str(result))

    def test_r2_legacy_backfill_only_missing_never_overwrites_paid(self):
        fake = FakeR2(known=[2])
        fake.files[2] = b"%PDF-1.4\npago remoto"
        result = sync.upload_missing_to_r2(self.conn, fake)
        self.assertEqual(result, {"uploaded": 1, "remaining": 0, "failures": 0})
        self.assertEqual(fake.uploaded, [(1, self.pdf)])
        self.assertEqual(fake.files[2], b"%PDF-1.4\npago remoto")
        result = sync.upload_missing_to_r2(self.conn, fake)
        self.assertEqual(result["uploaded"], 0)

    def test_sem_r2_manifest_ainda_recupera_pdf_vps(self):
        fake = FakeR2(enabled=False)
        result = sync.pdf_manifest(self.conn, fake)
        self.assertEqual(result["available"], 1)
        self.assertEqual(result["r2_missing"], 0)
        self.assertEqual(sync.upload_missing_to_r2(self.conn, fake)["uploaded"], 0)

    def test_interface_nao_usa_mais_botao_de_sincronizacao_manual(self):
        script = Path(__file__).with_name("app.js").read_text(encoding="utf-8")
        self.assertNotIn("clique em Copiar pendentes", script)
        self.assertNotIn('id="retryWindowsCopies"', script)
        self.assertIn("async function reconcileAllWindowsPDFs(", script)
        self.assertIn("windowsFileSHA256", script)
        self.assertIn("reload().then(()=>retryWindowsCopiesAutomatically())", script)

    def test_nunca_considerar_comprovante_inexistente_como_sucesso(self):
        fake = FakeR2(enabled=True)
        result = sync.pdf_manifest(self.conn, fake)
        self.assertEqual(result["missing_source"], 2)
        self.assertEqual(result["available"], 1)


if __name__ == "__main__":
    unittest.main()
