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

    def test_novo_funcionario_precisa_da_chave_pix_antes_de_vincular(self):
        with self.assertRaisesRegex(ValueError, "chave Pix"):
            h.assign(self.db, 1, {
                "holerite": "sim", "funcionario_nome": "Carlos Pires",
                "competencia": "2026-11", "pix_padrao": ""
            })
        self.assertEqual(h.get_employees(self.db), [])
        self.assertEqual(h.linked_accounts(self.db), {})

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

    def test_detect_pdf_by_content_only_but_never_creates_accounts(self):
        # Nome/observações não podem disparar a classificação.
        self.assertFalse(h.detect_from_document(
            "HOLERITE OUTUBRO 2026.pdf",
            fields={"observacoes": "CONTRACHEQUE", "fornecedor": "Empregadora SA"}
        )["detected"])
        self.assertFalse(h.detect_from_document("nota fiscal.pdf")["detected"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "anexo.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "RECIBO DE PAGAMENTO DE SALARIO")
                page.insert_text((40, 90), "FUNCIONARIO: MARIA DE SOUZA")
                page.insert_text((40, 110), "COMPETENCIA: 10/2026")
                doc.save(str(path))
            guess = h.detect_from_document("arquivo.pdf", path=path)
            self.assertTrue(guess["detected"])
            self.assertEqual(guess["funcionario_sugestao"], "MARIA DE SOUZA")
            self.assertEqual(guess["competencia_sugestao"], "2026-10")
        self.assertFalse(h.tables_exist(self.db))

    def test_holerite_sem_palavra_holerite_no_arquivo_ou_na_pagina(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "documento_83919.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "NOME DO FUNCIONARIO: MARIA DE SOUZA")
                page.insert_text((40, 90), "SALARIO BASE: 4500,00")
                page.insert_text((40, 110), "PROVENTOS      DESCONTOS")
                page.insert_text((40, 130), "INSS 450,00     FGTS 360,00")
                page.insert_text((40, 150), "COMPETENCIA: 11/2026")
                doc.save(str(path))
            result = h.detect_from_document("documento_83919.pdf", path=path)
            self.assertTrue(result["detected"])
            self.assertEqual(result["funcionario_sugestao"], "MARIA DE SOUZA")
            self.assertEqual(result["competencia_sugestao"], "2026-11")

    def test_nome_holerite_em_pdf_comum_nao_basta(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "holerite.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "NOTA FISCAL DE VENDA")
                page.insert_text((40, 90), "MILHO 350 SACAS")
                page.insert_text((40, 110), "VALOR TOTAL R$ 65000")
                doc.save(str(path))
            result = h.detect_from_document(
                "HOLERITE NOVEMBRO.pdf", path=path,
                fields={"observacoes": "HOLERITE"}
            )
            self.assertFalse(result["detected"])
            self.assertFalse(result["funcionario_sugestao"])

    def test_pdf_sem_texto_pode_ser_reconhecido_por_ocr_sem_nome(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_001.pdf"
            with fitz.open() as doc:
                doc.new_page()
                doc.save(str(path))
            ocr = (
                b"RECIBO DE PAGAMENTO DE SALARIO\n"
                b"FUNCIONARIO: MARIA DE SOUZA\n"
                b"COMPETENCIA: 11/2026\n"
            )
            with patch("subprocess.run", return_value=SimpleNamespace(
                    returncode=0, stdout=ocr)) as mocked:
                result = h.detect_from_document("scan_001.pdf", path=path)
            mocked.assert_called_once()
            self.assertTrue(result["detected"])
            self.assertEqual(result["funcionario_sugestao"], "MARIA DE SOUZA")
            self.assertEqual(result["origem_sugestao"], "OCR local do PDF")

    def test_cabecalho_holerite_com_nome_apenas_na_imagem_aciona_ocr(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "papel_1.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "HOLERITE")
                doc.save(str(path))
            with patch("subprocess.run", return_value=SimpleNamespace(
                    returncode=0, stdout=(
                        b"NOME DO FUNCIONARIO: JOSE DE OLIVEIRA\n"
                        b"COMPETENCIA: 11/2026\n"
                    ))) as mocked:
                result = h.detect_from_document("papel_1.pdf", path=path)
            mocked.assert_called_once()
            self.assertTrue(result["detected"])
            self.assertEqual(result["funcionario_sugestao"], "JOSE DE OLIVEIRA")
            self.assertEqual(result["origem_sugestao"], "OCR local do PDF")

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

    def test_nome_na_linha_seguinte_e_nome_simples_no_holerite(self):
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contracheque.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "RECIBO DE PAGAMENTO DE SALARIO")
                page.insert_text((40, 90), "NOME DO EMPREGADO")
                page.insert_text((40, 110), "ANA MARIA DE LIMA")
                page.insert_text((40, 130), "COMPETENCIA: 10/2026")
                doc.save(str(path))
            result = h.detect_from_document("arquivo.pdf", path)
            self.assertTrue(result["detected"])
            self.assertEqual(result["funcionario_sugestao"], "ANA MARIA DE LIMA")
            self.assertEqual(result["competencia_sugestao"], "2026-10")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outro.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "HOLERITE")
                page.insert_text((40, 90), "NOME: PEDRO HENRIQUE SOUZA")
                doc.save(str(path))
            result = h.detect_from_document("outro.pdf", path)
            self.assertEqual(result["funcionario_sugestao"], "PEDRO HENRIQUE SOUZA")

    def test_nomes_simples_ambiguos_nao_geram_funcionario(self):
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nomes.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "HOLERITE")
                page.insert_text((40, 90), "NOME: EMPRESA NOVO HORIZONTE")
                page.insert_text((40, 110), "NOME: MARIA SILVA")
                doc.save(str(path))
            result = h.detect_from_document("nomes.pdf", path)
            self.assertTrue(result["detected"])
            self.assertFalse(result["funcionario_sugestao"])

    def test_nao_tratar_cabecalho_discriminada_como_funcionario(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "contra-cheque.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 70), "HOLERITE")
                page.insert_text((40, 90), "FUNCIONARIO: DO DISCRIMINADA")
                page.insert_text((40, 110), "DESCONTOS VENCIMENTOS TOTAL")
                doc.save(str(path))
            info = h.detect_from_document("holerite.pdf", path=path)
        self.assertTrue(info["detected"])
        self.assertEqual(info["funcionario_sugestao"], "")
        self.assertFalse(h.valid_employee_name("DO DISCRIMINADA"))
        self.assertEqual(h.valid_employee_name("JOSE MARIA DA SILVA"),
                         "JOSE MARIA DA SILVA")

    def test_old_holerite_candidate_is_only_a_suggestion(self):
        self.assertTrue(h.guess_existing({
            "observacoes": "CONTRACHEQUE DE OUTUBRO.pdf"
        }))
        self.assertFalse(h.guess_existing({
            "observacoes": "ALUGUEL DO TRATOR.pdf"
        }))


if __name__ == "__main__":
    unittest.main()
