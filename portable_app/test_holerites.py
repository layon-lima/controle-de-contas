"""Testes do módulo de holerites; somente SQLite e PDFs fictícios."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import fitz

from portable_app import holerites as h


class PayrollTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE contas (id INTEGER PRIMARY KEY)")
        self.db.executemany("INSERT INTO contas(id) VALUES(?)", [(1,), (2,), (3,)])

    def test_read_only_operations_do_not_create_tables(self):
        self.assertFalse(h.tables_exist(self.db))
        self.assertEqual(h.linked_accounts(self.db), {})
        self.assertEqual(h.get_employees(self.db), [])
        h.assign(self.db, 1, {"holerite": "nao"})
        self.assertFalse(h.tables_exist(self.db))

    def test_employee_with_accent_and_pix_persists(self):
        eid = h.save_employee(self.db, {
            "nome": "João da Silva", "chave_pix": "joao@exemplo.com"
        })
        self.assertEqual(eid, 1)
        with self.assertRaisesRegex(ValueError, "já cadastrado"):
            h.save_employee(self.db, {
                "nome": "  JOAO   DA   SILVA ", "chave_pix": "outra"
            })
        h.assign(self.db, 1, {
            "holerite": "sim", "funcionario_id": eid, "competencia": "2026-11"
        })
        self.assertEqual(h.linked_accounts(self.db)[1]["competencia"], "2026-11")
        self.assertEqual(h.linked_one(self.db, 1)["pix_padrao"], "joao@exemplo.com")
        self.assertEqual(h.get_employees(self.db)[0]["total_holerites"], 1)
        h.save_employee(self.db, {
            "id": eid, "nome": "João da Silva", "chave_pix": "11988887777"
        })
        self.assertEqual(h.linked_one(self.db, 1)["pix_padrao"], "11988887777")
        self.assertEqual(h.linked_one(self.db, 1)["funcionario_nome"], "João da Silva")

    def test_same_employee_in_multiple_months_with_variable_amounts(self):
        h.assign(self.db, 1, {
            "holerite": "sim", "funcionario_nome": "Maria Oliveira",
            "pix_padrao": "11122233344", "competencia": "2026-10"
        })
        h.assign(self.db, 2, {
            "holerite": "sim", "funcionario_nome": "MARIA OLIVEIRA",
            "pix_padrao": "NÃO ALTERAR", "competencia": "2026-11"
        })
        self.assertEqual(len(h.get_employees(self.db)), 1)
        links = h.linked_accounts(self.db)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[1]["pix_padrao"], "11122233344")
        self.assertEqual(links[2]["pix_padrao"], "11122233344")
        h.assign(self.db, 1, {"holerite": "nao"})
        self.assertNotIn(1, h.linked_accounts(self.db))
        self.assertIn(2, h.linked_accounts(self.db))

    def test_invalid_competence_does_not_link_or_create_employee(self):
        for wrong in ("", "2026-00", "2026-13", "11/2026", "outubro"):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(
                ValueError, "Competência"
            ):
                h.assign(self.db, 1, {
                    "holerite": "sim", "competencia": wrong,
                    "funcionario_nome": "Teste Pessoa"
                })
        self.assertFalse(h.tables_exist(self.db))

    def test_detect_pdf_by_name_and_text_but_never_creates_accounts(self):
        guess = h.detect_from_document(
            "HOLERITE OUTUBRO 2026.pdf", fields={"fornecedor": "Empregadora SA"}
        )
        self.assertTrue(guess["detected"])
        self.assertFalse(h.detect_from_document("nota fiscal.pdf")["detected"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "anexo.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "RECIBO DE PAGAMENTO DE SALARIO")
                page.insert_text((40, 90), "FUNCIONARIO: MARIA DE SOUZA")
                page.insert_text((40, 110), "COMPETENCIA: 10/2026")
                doc.save(str(path))
            guess = h.detect_from_document("anexo.pdf", path=path)
            self.assertTrue(guess["detected"])
            self.assertEqual(guess["funcionario_sugestao"], "MARIA DE SOUZA")
            self.assertEqual(guess["competencia_sugestao"], "2026-10")
        self.assertFalse(h.tables_exist(self.db))

    def test_nome_do_funcionario_depende_de_quebras_de_linha(self):
        # Regressão: simplificar o documento todo achata o texto e quebra
        # ^FUNCIONARIO, impedindo a construção da imagem Docker na VPS.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracheque.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "DEMONSTRATIVO DE PAGAMENTO")
                page.insert_text((40, 90), "NOME DO FUNCIONÁRIO: JOSÉ DE OLIVEIRA")
                page.insert_text((40, 110), "COMPETÊNCIA: 11/2026")
                doc.save(str(path))
            result = h.detect_from_document("arquivo.pdf", path)
            self.assertTrue(result["detected"])
            self.assertEqual(result["funcionario_sugestao"], "JOSE DE OLIVEIRA")
            self.assertEqual(result["competencia_sugestao"], "2026-11")
        self.assertFalse(h.tables_exist(self.db))

    def test_old_holerite_candidate_is_only_a_suggestion(self):
        self.assertTrue(h.guess_existing({
            "observacoes": "CONTRACHEQUE DE OUTUBRO.pdf"
        }))
        self.assertFalse(h.guess_existing({
            "observacoes": "ALUGUEL DO TRATOR.pdf"
        }))


if __name__ == "__main__":
    unittest.main()
