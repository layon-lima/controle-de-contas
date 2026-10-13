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
            patch.object(s, "PENDING_BATCH", {}),
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

    def test_payment_text_uses_requested_order_and_blank_line_before_pix(self):
        pix = ("00020101021226900014br.gov.bcb.pix2568qrcodepix.bb.com.br/"
               "pix/v2/cobv/78def1eb-2249-49b6-9f9c-7b7205947d79")
        data = {
            "fornecedor": "RECEITA FEDERAL",
            "documento": "00000000000100",
            "pagador": "JONAS NELMAR MARLOW",
            "referencia": "ITR",
            "valor": 270.35,
            "vencimento": "2026-10-09",
            "tipo_pagamento": "PIX",
            "codigo_pagamento": pix,
            "observacoes": "ITR CIB 9463750-4",
        }
        expected = (
            "RECEBEDOR: RECEITA FEDERAL\n"
            "PAGADOR: JONAS NELMAR MARLOW\n"
            "VALOR: R$ 270,35\n"
            "VENCIMENTO: 2026-10-09\n"
            "OBSERVAÇÕES: ITR CIB 9463750-4\n\n"
            "CÓDIGO PIX / LINHA DIGITÁVEL: " + pix
        )
        self.assertEqual(s.payment_text(data), expected)
        # Sem código, não deixar uma linha vazia desnecessária no final.
        data["codigo_pagamento"] = ""
        self.assertEqual(s.payment_text(data), expected.split("\n\n")[0])

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

    def test_tags_feature_hidden_without_deleting_legacy_tables(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(
                "CREATE TABLE cc_tags(id INTEGER PRIMARY KEY,name TEXT);"
                "CREATE TABLE cc_account_tags(account_id INTEGER,tag_id INTEGER);"
                "INSERT INTO cc_tags VALUES(1,'ANTIGA');"
                "INSERT INTO cc_account_tags VALUES(1,1);"
            )
            conn.commit()
        state = s.list_accounts()
        self.assertNotIn("tags", state)
        self.assertNotIn("tags", state["accounts"][0])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT name FROM cc_tags WHERE id=1").fetchone()[0], "ANTIGA")


    def test_manual_edit_of_existing_pdf_trains_corrected_field(self):
        """A correção FEITA DEPOIS do cadastro também ensina o OCR."""
        bill = self._real_pdf("BOLETO PARA CORRECAO")
        s.upload_pdf(1, bill, "CONTA TESTE.pdf")
        with patch.object(s.pdf_intelligence, "learn_from_confirmed", return_value={
            "learned": True, "fields": ["recebedor"], "corrected_fields": ["recebedor"],
        }) as train:
            saved = s.edit_account(1, {
                "fornecedor": "FORNECEDOR CORRIGIDO", "valor": 123.45,
                "vencimento": date.today().isoformat(),
                "observacoes": "Teste de cadastro",
            })
        self.assertFalse(saved["warning"])
        self.assertTrue(train.called)
        self.assertEqual(train.call_args.args[1]["fornecedor"], "FORNECEDOR CORRIGIDO")
        self.assertEqual(s.list_accounts()["accounts"][0]["fornecedor"], "FORNECEDOR CORRIGIDO")

    def test_failed_ocr_learning_does_not_lose_manual_correction(self):
        """Feedback transparente: edicao salva mesmo se aprendizado falhar."""
        bill = self._real_pdf("BOLETO PARA CORRECAO")
        s.upload_pdf(1, bill, "CONTA TESTE.pdf")
        with patch.object(s.pdf_intelligence, "learn_from_confirmed", return_value={
            "learned": True, "fields": ["valor"], "corrected_fields": [],
        }):
            saved = s.edit_account(1, {
                "fornecedor": "NOME CORRIGIDO", "valor": 123.45,
                "vencimento": date.today().isoformat(),
            })
        self.assertIn("não foi aprendida", saved["warning"])
        self.assertEqual(s.list_accounts()["accounts"][0]["fornecedor"], "NOME CORRIGIDO")

    def test_learning_must_reapply_correction_before_claiming_success(self):
        """Localizar no treinamento nao basta: leitura apos treinamento deve bater."""
        bill = self._real_pdf("BOLETO")
        s.upload_pdf(1, bill, "BOLETO.pdf")
        with patch.object(s.pdf_intelligence, "learn_from_confirmed", return_value={
            "learned": True, "fields": ["recebedor"],
            "corrected_fields": ["recebedor"], "verified_fields": [],
        }):
            result = s.edit_account(1, {
                "fornecedor": "NOME CONFERIDO", "valor": 123.45,
                "vencimento": date.today().isoformat(),
            })
        self.assertIn("não foi aprendida", result["warning"])
        self.assertEqual(s.list_accounts()["accounts"][0]["fornecedor"], "NOME CONFERIDO")

    def test_existing_pdf_without_changes_is_not_retrained(self):
        bill = self._real_pdf("BOLETO")
        s.upload_pdf(1, bill, "CONTA TESTE.pdf")
        with patch.object(s.pdf_intelligence, "learn_from_confirmed") as train:
            result = s.edit_account(1, {
                "fornecedor": "PROVA SEM PDF", "valor": 123.45,
                "vencimento": date.today().isoformat(),
                "observacoes": "Observação alterada; não é campo OCR",
            })
        self.assertEqual(result["warning"], "")
        train.assert_not_called()

    def test_attach_pdf_and_do_not_delete_physical_document(self):
        result = s.upload_pdf(1, self._real_pdf("NOTA FISCAL"), "nota fiscal.pdf")
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
        first_pdf = self._real_pdf("PRIMEIRA FATURA")
        second_pdf = self._real_pdf("SEGUNDA FATURA")
        result = s.upload_pdf(1, first_pdf, "qualquer.pdf")
        target = s.pdf_path(1)
        self.assertTrue(result["organized"])
        self.assertEqual(
            target.parent, base / "BOLETOS 2026" / "10 - OUTUBRO"
        )
        self.assertEqual(target.name, "BOLETO FATURA TESTE.pdf")
        self.assertEqual(target.read_bytes(), first_pdf)
        repeated = s.upload_pdf(1, second_pdf, "qualquer.pdf")
        other = s.pdf_path(1)
        self.assertEqual(other.name, "BOLETO FATURA TESTE (1).pdf")
        self.assertNotEqual(target, other)
        self.assertEqual(target.read_bytes(), first_pdf)
        self.assertEqual(other.read_bytes(), second_pdf)
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
        self.assertIn("/api/import/preview", js)
        self.assertIn("preview.original_filename", js)
        self.assertIn("notice(saved.warning,true,10000)", js)
        self.assertIn("**edit_account(account_id, data)", (s.ROOT / "portable_app" / "server.py").read_text(encoding="utf-8"))
        self.assertNotIn('id="ocrMode"', html)
        self.assertNotIn('value="legacy"', html)
        self.assertIn("'X-Ocr-Mode':'hybrid'", js)
        self.assertIn('id="dropZone"', html)
        self.assertIn("document.addEventListener('drop'", js)
        self.assertIn("importNewPDF(files[0])", js)
        self.assertIn('data-label="Valor"', js)
        self.assertIn("@media(max-width:990px)", html)
        self.assertIn('rel="icon"', html)
        self.assertTrue((s.ROOT / "tools" / "CREATE_WEB_ICON.ps1").is_file())
        self.assertTrue((s.ROOT / "tools" / "RETIRE_INSTALLED_DESKTOP.ps1").is_file())
        shortcut_script = (s.ROOT / "ABRIR_CONTROLE_DE_CONTAS.vbs").read_text(encoding="utf-8")
        self.assertIn("SET_WEB_AS_PRIMARY.ps1", shortcut_script)
        self.assertIn("If ready Then", shortcut_script)
        self.assertIn("controle_contas_web.ico", (s.ROOT / "tools" / "SET_WEB_AS_PRIMARY.ps1").read_text(encoding="utf-8"))
        self.assertIn("analysis?.ocr_comparison", js)
        self.assertIn("/api/import/commit", js)
        self.assertIn("/pay-with-receipt", js)
        self.assertIn("data-action=\"reanalyze\"", html)
        self.assertIn("data-action=\"copy-code\"", html)
        self.assertIn("data-action=\"cancel-suggestion\"", html)
        self.assertNotIn('id="bulkTags"', html)
        self.assertNotIn('id="tagFilter"', html)
        self.assertNotIn('data-action="tags"', html)
        self.assertNotIn('data-label="TAG"', js)
        self.assertNotIn("/api/batch/tags", js)
        self.assertNotIn('id="bulkBar"', html)
        self.assertNotIn('class="bulk-check"', js)
        self.assertNotIn('id="bulkSuggest"', html)
        self.assertNotIn('id="bulkSend"', html)
        self.assertNotIn('id="bulkPay"', html)
        self.assertNotIn("function checkedIds(", js)
        self.assertNotIn("function payBulk(", js)
        self.assertIn("await copyText(ready.text)", js)
        self.assertIn('id="resetColumns"', html)
        self.assertIn('id="aiConnect"', html)
        self.assertIn("'/api/ai/connect'", js)
        self.assertIn("'/api/ai/status'", js)
        self.assertIn("'/api/ai/consent'", js)
        self.assertIn("'/api/ai/disconnect'", js)
        # GPT somente sob demanda, sem uso em importacao/releitura.
        server_source = (s.ROOT / "portable_app" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("AI_ASSIST.enhance(", server_source)
        self.assertIn('route == "/api/ai/correct"', server_source)
        self.assertIn("AI_ASSIST.correct(pdf_file, current)", server_source)
        self.assertIn('id="correctGPT"', js)
        self.assertIn("api('/api/ai/correct','POST',request)", js)
        self.assertIn("clique em Salvar para treinar o OCR", js)
        self.assertNotIn("Complementar automaticamente campos vazios", js)
        self.assertIn("Nenhum PDF será enviado automaticamente", js)
        self.assertIn("if(preview?.ticket)activePreviewTicket=preview.ticket", js)
        self.assertIn("initializeColumnResizing()", js)
        self.assertIn("window.localStorage.setItem(COL_KEY", js)
        self.assertIn("Math.max(COL_MIN[i]", js)
        self.assertIn("touch-action:none", html)
        self.assertNotIn('class="footer"', html)
        self.assertNotIn('data-tab="paid"', html)
        self.assertIn('id="paidCard"', html)
        self.assertIn("<small>PAGAS</small>", html)
        self.assertNotIn("PAGAS (ESTE MÊS)", html)
        self.assertIn("totals.Paga[0]++", js)
        self.assertIn("togglePaidView", js)
        self.assertIn("setAccountView('paid')", js)
        self.assertIn("if(!saved&&$('accountForm')&&!window.confirm(", js)
        self.assertIn("if(ticket&&!saved)api('/api/import/cancel'", js)
        self.assertIn("if(ok)closeModal({saved:true})", js)
        self.assertIn("addEventListener('click',()=>closeModal())", js)

    def test_cancelled_ocr_preview_leaves_no_record_or_document(self):
        from portable_app import pdf_workflows as flow
        flow.PREVIEWS.clear()
        self.addCleanup(flow.PREVIEWS.clear)
        data = self._real_pdf("DOCUMENTO CANCELADO")
        staged = flow.preview(
            data, "cancelado.pdf", self.root,
            lambda _: {"fields": {
                "fornecedor": "TESTE", "valor": 100,
                "vencimento": "2026-12-20",
            }},
        )
        temporary, _filename, _predicted = flow.take_preview(staged["ticket"])
        self.assertTrue(temporary.is_file())
        self.assertEqual(s.list_accounts()["count"], 1)
        flow.consume_preview(staged["ticket"])
        self.assertFalse(temporary.exists())
        self.assertEqual(s.list_accounts()["count"], 1)
        with self.assertRaises(ValueError):
            flow.take_preview(staged["ticket"])

    def test_gpt_correction_requires_click_and_does_not_save_preview(self):
        """Botao GPT apenas devolve sugestoes; PDF e banco so mudam ao salvar."""
        from portable_app import pdf_workflows as flow
        baseline = {"fornecedor": "FORNECEDOR ERRADO", "valor": 40.5,
                    "vencimento": "2026-12-21", "pagador": ""}
        preview = flow.preview(self._real_pdf("BOLETO TESTE"),
                               "DOCUMENTO.pdf", self.root,
                               lambda _path: {"fields": baseline.copy()})
        ticket = preview["ticket"]
        self.addCleanup(flow.consume_preview, ticket)
        httpd = s.LocalServer(("127.0.0.1", 0), s.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(2)))
        url = "http://127.0.0.1:" + str(httpd.server_port) + "/api/ai/correct"
        payload = json.dumps({"ticket": ticket, "fields": baseline}).encode()
        request = Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json", "X-Local-Token": s.TOKEN,
        })
        with patch.object(s.AI_ASSIST, "status", return_value={"enabled": False}), \
             patch.object(s.AI_ASSIST, "correct") as correct:
            with self.assertRaises(HTTPError) as refusal:
                urlopen(request, timeout=5)
            self.assertEqual(refusal.exception.code, 409)
            refusal.exception.close()
            correct.assert_not_called()
        suggestions = {"fields": dict(baseline, fornecedor="EMPRESA CORRETA"),
                       "changed": ["fornecedor"]}
        with patch.object(s.AI_ASSIST, "status", return_value={"enabled": True}), \
             patch.object(s.AI_ASSIST, "correct", return_value=suggestions) as correct:
            with urlopen(request, timeout=5) as response:
                result = json.load(response)
            correct.assert_called_once()
        self.assertEqual(result["fields"]["fornecedor"], "EMPRESA CORRETA")
        self.assertEqual(flow.take_preview(ticket)[2], baseline)
        self.assertEqual(s.list_accounts()["count"], 1)
        self.assertTrue(flow.take_preview(ticket)[0].is_file())

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
        # prepare_send e list_accounts sao somente leitura. Desde a remocao
        # das TAGs, este teste deixou de realizar uma escrita e, portanto,
        # nao gerava o backup que tentava renomear.
        s.edit_account(1, {
            "fornecedor": "PROVA SEM PDF", "valor": 124.50,
            "vencimento": date.today().isoformat(),
            "observacoes": "Edicao para verificar backup e fechamento de conexoes",
        })
        s.prepare_send(1)
        s.list_accounts()
        backups = list((self.root / "backups").glob("*.db"))
        self.assertEqual(len(backups), 1, "A primeira edicao deve criar um backup SQLite")
        backup = backups[0]
        # Em Windows, renomear um arquivo aberto pelo SQLite dá WinError 32.
        renamed = backup.with_suffix(".renamed")
        backup.rename(renamed)
        renamed.rename(backup)
        moved = self.db.with_suffix(".moved")
        self.db.rename(moved)
        moved.rename(self.db)

    def test_batch_send_requires_clipboard_confirmation(self):
        prepared = s.prepare_batch_send({"ids": [1]})
        self.assertIn("VALOR: R$ 123,45", prepared["text"])
        with s.connection() as conn:
            self.assertFalse(s.account(conn, 1)["enviado_pagamento_em"])
        with self.assertRaises(ValueError):
            s.confirm_batch_send({"nonce": "invalido"})
        confirmed = s.confirm_batch_send({"nonce": prepared["nonce"]})
        self.assertEqual(confirmed["count"], 1)
        with s.connection() as conn:
            self.assertTrue(s.account(conn, 1)["enviado_pagamento_em"])
        with self.assertRaises(ValueError):
            s.confirm_batch_send({"nonce": prepared["nonce"]})

    def test_batch_pay_keeps_real_results_and_does_not_duplicate(self):
        new_id = s.create_account({
            "fornecedor": "SEGUNDO", "valor": 88.0,
            "vencimento": "2026-12-12"
        })
        result = s.batch_pay({
            "ids": [1, new_id], "data_pagamento": date.today().isoformat()
        })
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["errors"], {})
        repeated = s.batch_pay({
            "ids": [1, new_id], "data_pagamento": date.today().isoformat()
        })
        self.assertEqual(repeated["count"], 0)
        self.assertEqual(len(repeated["errors"]), 2)

    def test_folder_config_shared_with_desktop_and_keeps_existing_folder(self):
        target = self.root / "PASTA BOLETOS"
        target.mkdir()
        info = s.set_document_folder({"folder": str(target)})
        self.assertTrue(info["configured"])
        self.assertEqual(info["folder"], str(target))
        self.assertEqual(s.get_document_folder()["folder"], str(target))
        self.assertEqual(
            json.loads((self.root / "config_documentos.json").read_text(
                encoding="utf-8"))["pasta_base"], str(target)
        )
        with self.assertRaises(FileNotFoundError):
            s.set_document_folder({"folder": str(self.root / "inexistente")})
        self.assertEqual(s.get_document_folder()["folder"], str(target))

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
        self.assertEqual(info["observacoes"], "BOLETO REVISADO.pdf")
        self.assertEqual(Path(info["pdf_conta"]).name, "BOLETO REVISADO.pdf")
        self.assertEqual(Path(info["pdf_conta"]).read_bytes(), data)
        self.assertTrue(result["learning"]["learned"])
        self.assertEqual(learn.call_args.args[1]["fornecedor"], "CORRIGIDO PELO USUARIO")
        with self.assertRaises(ValueError):
            s.create_account_from_preview({
                "ticket": preview["ticket"], "fornecedor": "OUTRA",
                "valor": 99, "vencimento": "2026-11-07"
            })


    def test_new_pdf_preview_uses_original_filename_even_when_ocr_suggests_another_name(self):
        """Nome exibido vem do PDF, nao de Observacoes sugeridas pelo OCR."""
        from portable_app import pdf_workflows as flow
        flow.PREVIEWS.clear()
        self.addCleanup(flow.PREVIEWS.clear)
        contents = self._real_pdf("BOLETO DE OUTUBRO")
        result = flow.preview(
            contents, r"C:\\Documentos\\NOTA DE OUTUBRO.pdf", self.root,
            lambda _: {"fields": {
                "fornecedor": "TESTE", "valor": 78.90,
                "vencimento": "2026-10-30",
                "observacoes": "TEXTO OCR QUE NAO DEVE VIRAR NOME.pdf",
            }},
        )
        self.assertEqual(result["original_filename"], "NOTA DE OUTUBRO.pdf")
        self.assertEqual(flow.take_preview(result["ticket"])[1], "NOTA DE OUTUBRO.pdf")

    def test_new_pdf_blank_observations_preserve_original_filename_and_record(self):
        """Vazio na confirmacao: nome original persiste no cadastro e arquivo."""
        from portable_app import pdf_workflows as flow
        flow.PREVIEWS.clear()
        self.addCleanup(flow.PREVIEWS.clear)
        contents = self._real_pdf("ARQUIVO SEM OBSERVACOES")
        result = flow.preview(
            contents, "CONTA ORIGINAL.pdf", self.root,
            lambda _: {"fields": {
                "fornecedor": "TESTE", "valor": 134.20,
                "vencimento": "2026-12-10",
            }},
        )
        self.assertEqual(result["original_filename"], "CONTA ORIGINAL.pdf")
        with patch.object(s.pdf_intelligence, "learn_from_confirmed", return_value={}):
            committed = s.create_account_from_preview({
                "ticket": result["ticket"], "fornecedor": "FORNECEDOR",
                "valor": 134.20, "vencimento": "2026-12-10",
                "observacoes": "   ",
            })
        with s.connection() as conn:
            record = s.account(conn, committed["id"])
        self.assertEqual(record["observacoes"], "CONTA ORIGINAL.pdf")
        self.assertEqual(Path(record["pdf_conta"]).name, "CONTA ORIGINAL.pdf")
        self.assertEqual(Path(record["pdf_conta"]).read_bytes(), contents)

    def test_new_pdf_custom_observations_rename_file_and_receipt_keeps_that_name(self):
        """Renomeia so na confirmacao inicial; anexar comprovante nao renomeia."""
        from portable_app import pdf_workflows as flow
        flow.PREVIEWS.clear()
        self.addCleanup(flow.PREVIEWS.clear)
        base = self.root / "BOLETOS PRINCIPAIS"
        base.mkdir()
        (self.root / "config_documentos.json").write_text(
            json.dumps({"pasta_base": str(base)}, ensure_ascii=False), encoding="utf-8"
        )
        bill = self._real_pdf("BOLETO")
        proof = self._real_pdf("COMPROVANTE")
        result = flow.preview(
            bill, "nome que veio do banco.pdf", self.root,
            lambda _: {"fields": {
                "fornecedor": "COBRANCA", "valor": 215.30,
                "vencimento": "2026-12-10",
            }},
        )
        with patch.object(s.pdf_intelligence, "learn_from_confirmed", return_value={}):
            committed = s.create_account_from_preview({
                "ticket": result["ticket"], "fornecedor": "COBRANCA",
                "valor": 215.30, "vencimento": "2026-12-10",
                "observacoes": "DOCUMENTO FINAL PERSONALIZADO",
            })
        account_id = committed["id"]
        saved_path = s.pdf_path(account_id)
        self.assertEqual(saved_path.name, "DOCUMENTO FINAL PERSONALIZADO.pdf")
        self.assertEqual(saved_path.parent, base / "BOLETOS 2026" / "12 - DEZEMBRO")
        merged = s.register_payment_with_receipt(account_id, date.today().isoformat(), proof)
        self.assertTrue(merged["merged"])
        self.assertEqual(s.pdf_path(account_id), saved_path)
        self.assertEqual(saved_path.name, "DOCUMENTO FINAL PERSONALIZADO.pdf")
        with s.connection() as conn:
            record = s.account(conn, account_id)
        self.assertEqual(record["observacoes"], "DOCUMENTO FINAL PERSONALIZADO")
        self.assertEqual(Path(record["pdf_conta"]), saved_path)
        import fitz
        with fitz.open(str(saved_path)) as document:
            self.assertEqual(document.page_count, 2)

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

    def test_receipt_rolls_back_pdf_when_database_insert_fails(self):
        """Falha depois da mesclagem não pode destruir o boleto."""
        bill = self._real_pdf("PDF ORIGINAL")
        proof = self._real_pdf("PAGAMENTO FAKE")
        s.upload_pdf(1, bill, "boleto.pdf")
        target = s.pdf_path(1)
        before = target.read_bytes()
        with patch.object(s, "stamp", side_effect=RuntimeError("Falha simulada DB")):
            with self.assertRaisesRegex(RuntimeError, "Falha simulada DB"):
                s.register_payment_with_receipt(1, date.today().isoformat(), proof)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(s.list_accounts()["accounts"][0]["status"] != "Paga", True)
        self.assertFalse(list(target.parent.glob("*.restore.pdf")))

    def test_receipt_keeps_original_backup_if_windows_blocks_restore(self):
        """Se a restauração falhar, o backup não pode ser excluído pelo finally."""
        bill = self._real_pdf("BOLETO RESERVA")
        proof = self._real_pdf("COMPROVANTE RESERVA")
        s.upload_pdf(1, bill, "boleto.pdf")
        target = s.pdf_path(1)
        replace_orig = s.os.replace

        def replace_checked(src, dst):
            if str(src).endswith(".restore.pdf"):
                raise PermissionError("WinError 32 simulado: arquivo em uso")
            return replace_orig(src, dst)

        with patch.object(s.os, "replace", side_effect=replace_checked):
            with patch.object(s, "stamp", side_effect=RuntimeError("Falha simulada DB")):
                with self.assertRaisesRegex(RuntimeError, "PRESERVADO"):
                    s.register_payment_with_receipt(1, date.today().isoformat(), proof)

        backup = list(target.parent.glob("*.restore.pdf"))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_bytes(), bill)
        self.assertNotEqual(target.read_bytes(), bill)

    def test_portable_sync_is_not_started_when_cloud_is_disabled(self):
        with patch.object(s.sync_runtime.cloud_sync, "cloud_enabled", return_value=False):
            with patch.object(s.sync_runtime.subprocess, "Popen") as spawn:
                result = s.sync_runtime.ensure_running()
                self.assertFalse(result["configured"])
                spawn.assert_not_called()

    def test_readonly_database_audit_does_not_change_any_bytes(self):
        from portable_app import verify_data
        before = self.db.read_bytes()
        with patch.object(verify_data, "DB", self.db):
            report = verify_data.audit()
        after = self.db.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(report["database_integrity"], "ok")
        self.assertEqual(report["accounts"], 1)
        self.assertTrue(report["no_database_changes"])

    def _clone_same_due_amount_for_receipt_check(self, other_code=""):
        with s.connection(write=True) as conn:
            conn.execute("UPDATE contas SET fornecedor=?,codigo_pagamento=? WHERE id=1",
                         ("RECEITA FEDERAL", "12345678901234567890123456789012345678901234567"))
            cursor = conn.execute(
                "INSERT INTO contas(fornecedor,valor,vencimento,codigo_pagamento,"
                "observacoes,criado_em,atualizado_em) VALUES(?,?,?,?,?,?,?)",
                ("RECEITA FEDERAL",123.45,date.today().isoformat(),
                 other_code or "76543210987654321098765432109876543210987654321",
                 "Outra cobranca",s.stamp(),s.stamp())
            )
            return cursor.lastrowid

    def test_wrong_account_receipt_identifier_blocks_before_pdf_merge(self):
        second = self._clone_same_due_amount_for_receipt_check()
        original = self._real_pdf("BOLETO ORIGINAL")
        s.upload_pdf(1, original, "BOLETO.pdf")
        path = s.pdf_path(1)
        before = path.read_bytes()
        proof = self._real_pdf(
            "LINHA DIGITAVEL: 76543210987654321098765432109876543210987654321"
        )
        verified = s.check_receipt_for_account(1, proof)
        self.assertEqual(verified["status"], "conflict")
        self.assertEqual(verified["matched_account_id"], second)
        with self.assertRaisesRegex(ValueError, "corresponde à conta"):
            s.register_payment_with_receipt(1, date.today().isoformat(), proof,
                                            manual_confirmed=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(s.list_accounts()["count"], 2)
        self.assertNotEqual(s.list_accounts()["accounts"][0]["status"], "Paga")

    def test_identical_accounts_force_explicit_confirmation_before_merge(self):
        second = self._clone_same_due_amount_for_receipt_check()
        original = self._real_pdf("BOLETO ORIGINAL")
        s.upload_pdf(1, original, "BOLETO.pdf")
        path = s.pdf_path(1)
        before = path.read_bytes()
        proof = self._real_pdf("COMPROVANTE SEM NUMERO")
        check = s.check_receipt_for_account(1, proof)
        self.assertEqual(check["status"], "ambiguous")
        self.assertTrue(check["manual_required"])
        with self.assertRaisesRegex(ValueError, "Confirme a associação"):
            s.register_payment_with_receipt(1, date.today().isoformat(), proof)
        self.assertEqual(path.read_bytes(), before)
        saved = s.register_payment_with_receipt(
            1, date.today().isoformat(), proof, manual_confirmed=True
        )
        self.assertTrue(saved["merged"])
        with s.connection() as conn:
            row = conn.execute(
                "SELECT resultado_conferencia,detalhes_conferencia "
                "FROM pagamentos WHERE conta_id=1"
            ).fetchone()
        self.assertEqual(row["resultado_conferencia"], "Confirmado pelo usuário")
        self.assertIn("SHA256:", row["detalhes_conferencia"])
        self.assertEqual(s.list_accounts()["accounts"][0]["status"], "Paga")

    def test_receipt_hash_reuse_blocked_even_when_user_confirms(self):
        self._clone_same_due_amount_for_receipt_check()
        proof = self._real_pdf("COMPROVANTE DUPLICADO")
        s.register_payment_with_receipt(
            1, date.today().isoformat(), proof, manual_confirmed=True
        )
        with self.assertRaisesRegex(ValueError, "já foi usado"):
            s.register_payment_with_receipt(
                2, date.today().isoformat(), proof, manual_confirmed=True
            )
        with s.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM pagamentos").fetchone()[0]
        self.assertEqual(total, 1)

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
