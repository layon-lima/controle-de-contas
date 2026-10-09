"""Testes reais da versão portátil em um banco de exemplo, nunca no usuário."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from portable_app import server as s


SCHEMA = """
CREATE TABLE contas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fornecedor TEXT NOT NULL, documento TEXT, pagador TEXT, referencia TEXT,
  valor REAL NOT NULL, vencimento TEXT NOT NULL, codigo_pagamento TEXT,
  tipo_pagamento TEXT, observacoes TEXT, criado_em TEXT, atualizado_em TEXT,
  enviado_pagamento_em TEXT, pdf_conta TEXT
);
CREATE TABLE pagamentos(
  id INTEGER PRIMARY KEY AUTOINCREMENT,conta_id INTEGER NOT NULL,
  valor_pago REAL NOT NULL,data_pagamento TEXT,beneficiario TEXT,
  documento_beneficiario TEXT,pdf_comprovante TEXT,resultado_conferencia TEXT,
  detalhes_conferencia TEXT,criado_em TEXT,atualizado_em TEXT
);
"""


class PortableDataTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="cc_portable_test_")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.db = self.data / "controle_contas.db"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO contas (fornecedor,valor,vencimento,observacoes,"
                "codigo_pagamento,criado_em,atualizado_em,enviado_pagamento_em)"
                " VALUES (?,?,?,?,?,?,?,?)",
                ("PROVA SEM PDF", 123.45, date.today().isoformat(),
                 "Teste de cadastro", "123 456 789", s.stamp(), s.stamp(), "")
            )
            conn.commit()
        self.patchers = [
            patch.object(s, "DB_PATH", self.db),
            patch.object(s, "APP_DIR", self.root),
            patch.object(s, "BACKUP_DONE", False),
            patch.object(s, "PENDING", {}),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_read_uses_same_database_and_never_requires_pdf(self):
        state = s.list_accounts()
        self.assertEqual(len(state["accounts"]), 1)
        account = state["accounts"][0]
        self.assertEqual(account["fornecedor"], "PROVA SEM PDF")
        self.assertFalse(account["has_pdf"])
        self.assertNotEqual(account["status"], "Paga")

    def test_send_is_not_persisted_until_acknowledgment(self):
        ready = s.prepare_send(1)
        self.assertIn("VALOR: R$ 123,45", ready["text"])
        with closing(sqlite3.connect(self.db)) as conn:
            before = conn.execute(
                "SELECT enviado_pagamento_em FROM contas WHERE id=1").fetchone()
        self.assertEqual(before[0], "")
        with self.assertRaises(ValueError):
            s.confirm_send(1, "invalid nonce")
        date_sent = s.confirm_send(1, ready["nonce"])
        self.assertTrue(date_sent)
        # O nonce é de uso único, não permite marcar várias contas de uma vez.
        with self.assertRaises(ValueError):
            s.confirm_send(1, ready["nonce"])
        other = s.prepare_send(1)
        self.assertEqual(s.confirm_send(1, other["nonce"]), date_sent)
        self.assertEqual(len(list((self.root / "backups").glob("*.db"))), 1)

    def test_account_edit_does_not_touch_pdf_or_sent_status(self):
        s.confirm_send(1, s.prepare_send(1)["nonce"])
        with closing(sqlite3.connect(self.db)) as conn:
            before = conn.execute(
                "SELECT enviado_pagamento_em FROM contas WHERE id=1").fetchone()[0]
        s.edit_account(1, {
            "fornecedor": "EDITADA", "valor": 139.20, "vencimento": "2026-12-30",
            "observacoes": "Editada sem sobrescrever movimento"
        })
        after = s.list_accounts()["accounts"][0]
        self.assertEqual(after["fornecedor"], "EDITADA")
        self.assertEqual(after["enviado_pagamento_em"], before)

    def test_payment_without_receipt_and_duplicate_block(self):
        s.register_payment(1, {"data_pagamento": date.today().isoformat()})
        self.assertEqual(s.list_accounts()["accounts"][0]["status"], "Paga")
        with self.assertRaises(ValueError):
            s.register_payment(1, {"data_pagamento": date.today().isoformat()})
        with self.assertRaises(ValueError):
            s.remove_account(1)

    def test_tags_with_real_extra_tables(self):
        s.assign_tags(1, [], "SERVIÇOS")
        result = s.list_accounts()
        self.assertEqual(result["accounts"][0]["tags"][0]["name"], "SERVIÇOS")
        s.assign_tags(1, [])
        self.assertEqual(s.list_accounts()["accounts"][0]["tags"], [])

    def test_attach_pdf_and_do_not_delete_physical_document(self):
        result = s.upload_pdf(1, b"%PDF-1.4\nsample content", "nota fiscal.pdf")
        self.assertTrue(result["name"].endswith(".pdf"))
        self.assertFalse(result["organized"])  # sem pasta-base configurada
        saved = s.pdf_path(1)
        self.assertTrue(saved.exists())
        self.assertEqual(saved.parent, self.root / "documents" / "portatil")
        s.remove_account(1)
        self.assertTrue(saved.exists())  # nunca apagar documentos ao excluir cadastro

    def test_imported_pdf_uses_existing_year_month_structure(self):
        """Mesmo destino definido no app legado: BOLETOS ano / mês."""
        base = self.root / "MINHA PASTA DE BOLETOS"
        base.mkdir()
        (self.root / "config_documentos.json").write_text(
            json.dumps({"pasta_base": str(base)}, ensure_ascii=False),
            encoding="utf-8"
        )
        s.edit_account(1, {
            "fornecedor": "FORNECEDOR/TESTE", "valor": 123.45,
            "vencimento": "2026-10-15", "observacoes": "BOLETO FATURA TESTE.pdf"
        })
        result = s.upload_pdf(1, b"%PDF-1.4\nfirst content", "qualquer.pdf")
        target = s.pdf_path(1)
        self.assertTrue(result["organized"])
        self.assertEqual(
            target.parent, base / "BOLETOS 2026" / "10 - OUTUBRO"
        )
        self.assertEqual(target.name, "BOLETO FATURA TESTE.pdf")
        self.assertEqual(target.read_bytes(), b"%PDF-1.4\nfirst content")
        repeated = s.upload_pdf(1, b"%PDF-1.4\nsecond content", "qualquer.pdf")
        other = s.pdf_path(1)
        self.assertEqual(other.name, "BOLETO FATURA TESTE (1).pdf")
        self.assertNotEqual(target, other)
        self.assertEqual(target.read_bytes(), b"%PDF-1.4\nfirst content")
        self.assertEqual(other.read_bytes(), b"%PDF-1.4\nsecond content")
        self.assertTrue(repeated["organized"])
        self.assertEqual(len(list((self.root / "backups").glob("*.db"))), 1)

    def test_configured_base_already_boletos_year_is_normalized(self):
        base = self.root / "ARQUIVOS"
        (base / "BOLETOS 2026").mkdir(parents=True)
        (self.root / "config_documentos.json").write_text(
            json.dumps({"pasta_base": str(base / "BOLETOS 2026")}),
            encoding="utf-8"
        )
        self.assertEqual(s._pasta_documentos_configurada(), base)

    def test_cannot_write_to_missing_database(self):
        with patch.object(s, "DB_PATH", self.root / "not_exists.db"):
            with self.assertRaises(FileNotFoundError):
                s.create_account({
                    "fornecedor": "FAKE", "valor": 100, "vencimento": "2026-12-30"
                })
            self.assertFalse((self.root / "not_exists.db").exists())

    def test_portable_list_has_no_actions_column_and_popup_on_double_click(self):
        html = (s.WEB_DIR / "index.html").read_text(encoding="utf-8")
        js = (s.WEB_DIR / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("<th>Ações</th>", html)
        self.assertIn('id="menu"', html)
        self.assertIn('data-action="send"', html)
        self.assertIn("addEventListener('dblclick'", js)
        self.assertIn("/icons/sent.svg", html)
        self.assertIn("window.__CONTAS_TOKEN__", js)
        self.assertNotIn('class="footer"', html)
        self.assertNotIn('data-tab="paid"', html)
        self.assertIn('id="paidCard"', html)
        self.assertIn("<small>PAGAS</small>", html)
        self.assertNotIn("PAGAS (ESTE MÊS)", html)
        self.assertIn("totals.Paga[0]++", js)
        self.assertIn("togglePaidView", js)
        self.assertIn("setAccountView('paid')", js)

    def test_shutdown_only_local_portable_service_with_token(self):
        httpd = s.LocalServer(("127.0.0.1", 0), s.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(2)))
        base = "http://127.0.0.1:" + str(httpd.server_port)
        url = base + "/api/control/stop"
        with self.assertRaises(HTTPError) as response:
            urlopen(Request(url, data=b"{}", method="POST",
                            headers={"Content-Type": "application/json"}),timeout=4)
        self.assertEqual(response.exception.code, 403)
        response.exception.close()
        request = Request(
            url, data=b"{}", method="POST",
            headers={"Content-Type": "application/json", "X-Local-Token": s.TOKEN}
        )
        with urlopen(request, timeout=4) as reply:
            self.assertTrue(json.load(reply)["ok"])
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive(), "Servidor portátil não fechou")

    def test_database_handles_closed_before_temp_cleanup(self):
        """A edição, o backup e a consulta não devem deixar .db travados."""
        s.assign_tags(1, [], "TESTE")
        s.prepare_send(1)
        s.list_accounts()
        backup = next((self.root / "backups").glob("*.db"))
        # Em Windows, renomear um arquivo aberto pelo SQLite dá WinError 32.
        renamed = backup.with_suffix(".renamed")
        backup.rename(renamed)
        renamed.rename(backup)
        moved = self.db.with_suffix(".moved")
        self.db.rename(moved)
        moved.rename(self.db)

    def _real_pdf(self, title="Teste de PDF"):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), title)
        content = doc.tobytes()
        doc.close()
        return content

    def test_ocr_preview_requires_confirmation_and_preserves_corrected_fields(self):
        """Motor OCR não grava registros; cadastro e PDF só após confirmação."""
        from portable_app import pdf_workflows as flow
        flow.PREVIEWS.clear()
        self.addCleanup(flow.PREVIEWS.clear)
        data = self._real_pdf("Beneficiario ORIGINAL TESTE")
        with patch("portable_app.pdf_intelligence.analyze_pdf", return_value={
            "engine": "desktop-ocr-v2",
            "fields": {"fornecedor": "ORIGINAL", "valor": 99.50,
                       "vencimento": "2026-11-07", "observacoes": "PDF DE TESTE.pdf"},
            "insecure_fields": ["recebedor"],
        }):
            preview = flow.preview(
                data, "documento.pdf", self.root,
                lambda _: {"fields": {"fornecedor": "ORIGINAL", "valor": 99.50,
                                      "vencimento": "2026-11-07",
                                      "observacoes": "PDF DE TESTE.pdf"}}
            )
        self.assertEqual(s.list_accounts()["count"], 1)
        self.assertTrue((self.root / "portable_staging").exists())
        with patch.object(s.pdf_intelligence, "learn_from_confirmed",
                          return_value={"learned": True}) as learn:
            result = s.create_account_from_preview({
                "ticket": preview["ticket"],
                "fornecedor": "CORRIGIDO PELO USUARIO",
                "valor": "99.50", "vencimento": "2026-11-07",
                "observacoes": "BOLETO REVISADO.pdf",
            })
        self.assertEqual(s.list_accounts()["count"], 2)
        with s.connection() as conn:
            info = s.account(conn, result["id"])
        self.assertEqual(info["fornecedor"], "CORRIGIDO PELO USUARIO")
        self.assertEqual(Path(info["pdf_conta"]).read_bytes(), data)
        self.assertTrue(result["learning"]["learned"])
        self.assertEqual(learn.call_args.args[1]["fornecedor"], "CORRIGIDO PELO USUARIO")
        with self.assertRaises(ValueError):
            s.create_account_from_preview({
                "ticket": preview["ticket"], "fornecedor": "OUTRA",
                "valor": 99, "vencimento": "2026-11-07"
            })

    def test_preview_same_pdf_is_blocked_even_with_different_fields(self):
        from portable_app import pdf_workflows as flow
        flow.PREVIEWS.clear()
        self.addCleanup(flow.PREVIEWS.clear)
        data = self._real_pdf("Mesmo boleto")
        def analyzer(_):
            return {"fields": {"fornecedor": "EXEMPLO", "valor": 20.0,
                               "vencimento": "2026-11-20"}}
        first = flow.preview(data, "repetido.pdf", self.root, analyzer)
        with patch.object(s.pdf_intelligence, "learn_from_confirmed", return_value={}):
            s.create_account_from_preview({
                "ticket": first["ticket"], **first["fields"]
            })
        second = flow.preview(data, "repetido.pdf", self.root, analyzer)
        with self.assertRaisesRegex(ValueError, "já está cadastrado"):
            s.create_account_from_preview({
                "ticket": second["ticket"], "fornecedor": "NOME DIFERENTE",
                "valor": 99.99, "vencimento": "2028-02-01"
            })
        self.assertEqual(s.list_accounts()["count"], 2)

    def test_payment_with_receipt_merges_pdf_and_disallows_second_payment(self):
        bill = self._real_pdf("BOLETO ORIGINAL")
        receipt = self._real_pdf("COMPROVANTE DE PAGAMENTO")
        s.upload_pdf(1, bill, "boleto.pdf")
        first = s.pdf_path(1)
        original = first.read_bytes()
        result = s.register_payment_with_receipt(1, date.today().isoformat(), receipt)
        self.assertTrue(result["merged"])
        self.assertEqual(result["pages"], 2)
        import fitz
        with fitz.open(str(s.pdf_path(1))) as doc:
            self.assertEqual(doc.page_count, 2)
            self.assertIn("BOLETO ORIGINAL", doc[0].get_text())
            self.assertIn("COMPROVANTE DE PAGAMENTO", doc[1].get_text())
        self.assertEqual(s.pdf_path(1, receipt=True), s.pdf_path(1))
        new_contents = first.read_bytes()
        self.assertNotEqual(new_contents, original)
        with self.assertRaisesRegex(ValueError, "já está paga"):
            s.register_payment_with_receipt(1, date.today().isoformat(), receipt)
        self.assertEqual(first.read_bytes(), new_contents)
        self.assertEqual(s.list_accounts()["accounts"][0]["status"], "Paga")

    def test_receipt_only_creates_file_when_bill_pdf_is_absent(self):
        receipt = self._real_pdf("COMPROVANTE SEM BOLETO")
        result = s.register_payment_with_receipt(1, date.today().isoformat(), receipt)
        self.assertEqual(result["pages"], 1)
        self.assertTrue(s.pdf_path(1).exists())
        self.assertEqual(s.pdf_path(1, receipt=True), s.pdf_path(1))

    def test_reader_does_not_fabricate_ocr_without_original_engine(self):
        from portable_app import pdf_intelligence as intel
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(intel.ReaderUnavailable):
                intel.find_legacy_engine(Path(tmp))
        self.assertEqual(s.list_accounts()["count"], 1)

    def test_http_api_rejects_request_without_token_or_from_other_origin(self):
        httpd = s.LocalServer(("127.0.0.1", 0), s.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(2)))
        base = "http://127.0.0.1:" + str(httpd.server_port)
        with self.assertRaises(HTTPError) as response:
            urlopen(base + "/api/accounts", timeout=4)
        self.assertEqual(response.exception.code, 403)
        response.exception.close()
        with self.assertRaises(HTTPError) as response:
            urlopen(Request(
                base + "/api/accounts",headers={"X-Local-Token": s.TOKEN,
                                                "Origin": "https://outro-site.com"}
            ), timeout=4)
        self.assertEqual(response.exception.code, 403)
        response.exception.close()
        req = Request(base + "/api/accounts",headers={"X-Local-Token":s.TOKEN})
        with urlopen(req, timeout=4) as reply:
            items = json.loads(reply.read())
        self.assertEqual(items["count"], 1)
        with urlopen(base + "/", timeout=4) as reply:
            document = reply.read().decode("utf-8")
        self.assertIn("__CONTAS_TOKEN__", document)
        self.assertNotIn("Ações</th>", document)


if __name__ == "__main__":
    unittest.main()
