"""Conferencia forte de comprovantes sem alterar contas nem PDF original."""
from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

import fitz
from portable_app import receipt_matching as rm


def pdf(*lines):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for n, line in enumerate(lines):
        page.insert_text((44, 74 + n * 32), line, fontsize=11)
    output = doc.tobytes()
    doc.close()
    return output


def account(id, code="", *, supplier="RECEITA FEDERAL", amount=270.35, due="2026-10-09", doc=""):
    return {"id": id, "fornecedor": supplier, "valor": amount,
            "vencimento": due, "codigo_pagamento": code, "pdf_conta": doc}


class ReceiptMatchTests(unittest.TestCase):
    def setUp(self):
        # Duas contas efetivamente diferentes, mas com os tres metadados iguais.
        self.one = account(1, "12345678901234567890123456789012345678901234567")
        self.two = account(2, "76543210987654321098765432109876543210987654321")

    def test_exact_unique_barcode_confirms_selected_bill(self):
        receipt = pdf("COMPROVANTE DE PAGAMENTO",
                      "LINHA DIGITAVEL: " + self.one["codigo_pagamento"])
        result = rm.verify_receipt(receipt, self.one, [self.one, self.two], [])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["matched_account_id"], 1)
        self.assertFalse(result["manual_required"])

    def test_same_amount_supplier_due_but_wrong_barcode_blocks(self):
        receipt = pdf("COMPROVANTE", "LINHA DIGITAVEL: " + self.two["codigo_pagamento"])
        result = rm.verify_receipt(receipt, self.one, [self.one, self.two], [])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["matched_account_id"], 2)
        self.assertIn("conta 2", result["reason"])
        self.assertFalse(result["manual_required"])

    def test_same_metadata_without_comparable_identifier_requires_review(self):
        receipt = pdf("COMPROVANTE BANCARIO", "DATA: 13/10/2026", "VALOR: 270,35")
        result = rm.verify_receipt(receipt, self.one, [self.one, self.two], [])
        self.assertEqual(result["status"], "ambiguous")
        self.assertTrue(result["manual_required"])
        self.assertEqual(result["candidate_ids"], [1, 2])

    def test_single_account_without_id_is_unverified_not_fake_match(self):
        result = rm.verify_receipt(pdf("COMPROVANTE", "R$ 270,35"),
                                   self.one, [self.one], [])
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["manual_required"])
        self.assertIsNone(result["matched_account_id"])

    def test_nosso_numero_present_in_two_original_pdfs_can_identify(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "boleto1.pdf", Path(tmp) / "boleto2.pdf"
            first.write_bytes(pdf("BANCO", "NOSSO NUMERO: 12345678910"))
            second.write_bytes(pdf("BANCO", "NOSSO NUMERO: 55566677780"))
            a, b = account(1, doc=str(first)), account(2, doc=str(second))
            proof = pdf("COMPROVANTE DE PAGAMENTO", "NOSSO NUMERO: 55566677780")
            result = rm.verify_receipt(proof, a, [a, b], [])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["matched_account_id"], 2)

    def test_e2e_is_not_mistaken_for_txid(self):
        a = account(1)
        other = account(2)
        e2e = "E12345678" + "A" * 23
        result = rm.verify_receipt(pdf("COMPROVANTE PIX", "ENDTOEND: " + e2e),
                                   a, [a, other], [])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["matched_account_id"])
        self.assertEqual(result["e2e"], e2e)

    def test_same_pdf_hash_cannot_be_registered_twice(self):
        proof = pdf("COMPROVANTE", "R$ 270,35")
        digest = hashlib.sha256(proof).hexdigest()
        result = rm.verify_receipt(proof, self.two, [self.one, self.two],
                                   ["Pago SHA256:" + digest])
        self.assertEqual(result["status"], "conflict")
        self.assertIn("já foi usado", result["reason"])

    def test_same_e2e_in_different_pdf_cannot_be_reused(self):
        e2e = "E12345678" + "B" * 23
        proof = pdf("COMPROVANTE PIX", "E2E: " + e2e)
        result = rm.verify_receipt(proof, self.one, [self.one, self.two],
                                   ["Cobranca paga E2E:" + e2e])
        self.assertEqual(result["status"], "conflict")

    def test_paid_bill_with_different_value_still_blocks_wrong_association(self):
        receipt = pdf("COMPROVANTE", "LINHA DIGITAVEL: " + self.two["codigo_pagamento"])
        unrelated = account(2, self.two["codigo_pagamento"],
                            amount=55.00, supplier="OUTRA EMPRESA")
        result = rm.verify_receipt(receipt, self.one, [self.one, unrelated], [])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["matched_account_id"], 2)


    def test_drop_receipt_finds_one_account_from_exclusive_code(self):
        receipt = pdf("COMPROVANTE DE PAGAMENTO",
                      "LINHA DIGITAVEL: " + self.two["codigo_pagamento"])
        result = rm.discover_receipt(receipt, [self.one, self.two], [])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["matched_account_id"], 2)
        self.assertEqual(result["candidate_ids"], [2])

    def test_drop_receipt_never_confirms_or_selects_based_only_on_matching_amount(self):
        receipt = pdf("COMPROVANTE", "VALOR PAGO: R$ 270,35")
        result = rm.discover_receipt(receipt, [self.one, self.two], [])
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["matched_account_id"])
        # Mesmo duas contas com valor igual, nenhuma pode ser eleita sem ID exclusivo.
        self.assertEqual(result["candidate_ids"], [])
        self.assertFalse(result["identifier_found"])
        self.assertEqual(result["receipt_value"], 270.35)

    def test_drop_receipt_without_any_comparable_data_requires_manual_choice(self):
        receipt = pdf("COMPROVANTE BANCARIO SEM NUMEROS")
        result = rm.discover_receipt(receipt, [self.one, self.two], [])
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["matched_account_id"])
        self.assertEqual(result["candidate_ids"], [])

    def test_drop_receipt_paid_account_is_blocked_and_never_rerouted(self):
        paid = {**self.two, "is_paid": 1}
        receipt = pdf("COMPROVANTE", "LINHA DIGITAVEL: " + paid["codigo_pagamento"])
        result = rm.discover_receipt(receipt, [self.one, paid], [])
        # A cobrança foi localizada em uma conta já paga: bloquear, sem anexar de novo.
        self.assertEqual(result["status"], "conflict")
        self.assertIsNone(result["matched_account_id"])
        self.assertEqual(result["candidate_ids"], [])
        self.assertIn("já paga", result["reason"])

    def test_drop_receipt_duplicate_hash_is_blocked_before_any_choice(self):
        receipt = pdf("COMPROVANTE DE PAGAMENTO", "R$ 270,35")
        digest = hashlib.sha256(receipt).hexdigest()
        result = rm.discover_receipt(receipt, [self.one, self.two],
                                     ["SHA256:" + digest])
        self.assertEqual(result["status"], "conflict")
        self.assertIsNone(result["matched_account_id"])

    def test_drop_receipt_duplicate_e2e_is_blocked(self):
        e2e = "E12345678" + "A" * 23
        receipt = pdf("COMPROVANTE PIX", "E2E: " + e2e)
        result = rm.discover_receipt(receipt, [self.one, self.two],
                                     ["E2E:" + e2e])
        self.assertEqual(result["status"], "conflict")

    def test_drop_receipt_without_unique_code_does_not_guess_from_pix_static_key(self):
        code = ("000201010211" + "0" * 100)
        a = account(1, code)
        b = account(2, code)
        receipt = pdf("COMPROVANTE PIX", code)
        result = rm.discover_receipt(receipt, [a, b], [])
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["matched_account_id"])


    def test_bradesco_receipt_reads_identifier_e2e_and_dates(self):
        """Mesmo formato do comprovante Pix Bradesco: campos reais e separados."""
        identifier = "07012626564439319046470798"
        e2e = "E60746948202610131104A57930LY8IS"
        proof = pdf("Comprovante de pagamento Pix",
                    "13/10/2026 - 08:04:20",
                    "Valor: R$ 25,00",
                    "Dados de quem recebeu",
                    "Nome: RECEITA FEDERAL",
                    "Dados do pagamento",
                    "Valor: R$ 25,00",
                    "Vencimento: 21/10/2026",
                    "Identificador: " + identifier,
                    "Data do débito: 13/10/2026 - 08:04:17",
                    "Número de controle: " + e2e,
                    "Dados de quem pagou",
                    "Nome: PAGADOR TESTE")
        text = rm.pdf_text(proof)
        ids = rm._labels(text)
        data = rm._receipt_facts(text)
        self.assertIn(identifier, ids["identifier"])
        self.assertIn(e2e, ids["e2e"])
        self.assertEqual(data["receipt_value"], 25.0)
        self.assertEqual(data["receipt_due"], "2026-10-21")
        self.assertEqual(data["payment_date"], "2026-10-13")
        self.assertIn("RECEITA FEDERAL", data["recipient"])
        self.assertIn("PAGADOR TESTE", data["payer"])

    def test_bradesco_receipt_selects_unique_bill_not_an_equal_value_bill(self):
        identifier = "07012626564439319046470798"
        other = "88445577669911223344556677"
        receipt = pdf("Comprovante de pagamento Pix",
                      "Valor: R$ 25,00",
                      "Vencimento: 21/10/2026",
                      "Identificador: " + identifier,
                      "Data do débito: 13/10/2026")
        one = account(1, other, amount=25, due="2026-10-21")
        two = account(2, identifier, amount=25, due="2026-10-21")
        result = rm.discover_receipt(receipt, [one, two], [])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["matched_account_id"], 2)
        self.assertTrue(result["identifier_found"])
        verification = rm.verify_receipt(receipt, two, [one, two], [])
        self.assertEqual(verification["status"], "verified")

    def test_bradesco_identifier_present_only_in_bill_pdf_can_match(self):
        identifier = "07012626564439319046470798"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cobranca.pdf"
            path.write_bytes(pdf("Receita Federal", "Identificador: " + identifier))
            target = account(17, "", amount=25, due="2026-10-21",
                             doc=str(path))
            unrelated = account(22, "", amount=25, due="2026-10-21")
            receipt = pdf("Comprovante Pix", "Valor: R$ 25,00",
                          "Vencimento: 21/10/2026",
                          "Identificador: " + identifier)
            report = rm.discover_receipt(receipt, [unrelated, target], [])
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["matched_account_id"], 17)

    def test_bradesco_identifier_present_but_not_cadastrado_is_not_missing(self):
        receipt = pdf("Comprovante Pix",
                      "Valor: R$ 25,00",
                      "Identificador: 07012626564439319046470798")
        report = rm.discover_receipt(receipt, [self.one, self.two], [])
        self.assertTrue(report["identifier_found"])
        self.assertEqual(report["status"], "unverified")
        self.assertIsNone(report["matched_account_id"])
        self.assertIn("identificador do comprovante foi lido", report["reason"])

    def test_bradesco_same_identifier_but_different_due_must_not_match(self):
        identifier = "07012626564439319046470798"
        receipt = pdf("Comprovante Pix", "Valor: R$ 25,00",
                      "Vencimento: 21/10/2026",
                      "Identificador: " + identifier)
        account_wrong_due = account(1, identifier, amount=25,
                                    due="2026-10-23")
        result = rm.discover_receipt(receipt, [account_wrong_due], [])
        self.assertEqual(result["status"], "conflict")
        self.assertIsNone(result["matched_account_id"])


    def test_eligible_receipt_shows_all_identical_bills_without_choosing(self):
        receipt = pdf("Comprovante de pagamento Pix",
                      "Dados de quem recebeu",
                      "Nome: RECEITA FEDERAL",
                      "Valor: R$ 25,00",
                      "Vencimento: 21/10/2026",
                      "Data do débito: 13/10/2026",
                      "Identificador: 07012626564439319046470798")
        first = account(7, "INCOMPATIBLE PIX ID A", amount=25, due="2026-10-21")
        second = account(9, "INCOMPATIBLE PIX ID B", amount=25, due="2026-10-21")
        result = rm.eligible_receipt_accounts(receipt, [first, second], [])
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["candidate_ids"], [7, 9])
        self.assertIsNone(result["matched_account_id"])
        self.assertEqual(result["payment_date"], "2026-10-13")

    def test_eligible_receipt_filters_recipient_amount_due_and_paid_status(self):
        receipt = pdf("Comprovante Pix",
                      "Dados de quem recebeu", "Nome: RECEITA FEDERAL",
                      "Valor: R$ 25,00", "Vencimento: 21/10/2026")
        correct = account(1, amount=25, due="2026-10-21")
        bad_vendor = account(2, supplier="RECEITA ESTADUAL", amount=25, due="2026-10-21")
        bad_value = account(3, amount=26, due="2026-10-21")
        bad_due = account(4, amount=25, due="2026-10-22")
        paid = {**account(5, amount=25, due="2026-10-21"), "is_paid": 1}
        result = rm.eligible_receipt_accounts(
            receipt, [correct, bad_vendor, bad_value, bad_due, paid], [])
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["candidate_ids"], [1])

    def test_eligible_receipt_missing_one_field_cannot_suggest_from_only_two(self):
        receipt = pdf("Comprovante Pix", "Dados de quem recebeu",
                      "Nome: RECEITA FEDERAL", "Valor: R$ 25,00")
        result = rm.eligible_receipt_accounts(
            receipt, [account(1, amount=25, due="2026-10-21")], [])
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["candidate_ids"], [])
        self.assertIn("vencimento", result["reason"])

    def test_eligible_receipt_duplicate_hash_and_e2e_still_block(self):
        e2e = "E12345678" + "A" * 23
        receipt = pdf("Comprovante Pix",
                      "Dados de quem recebeu", "Nome: RECEITA FEDERAL",
                      "Valor: R$ 25,00", "Vencimento: 21/10/2026",
                      "Número de controle: " + e2e)
        item = account(1, amount=25, due="2026-10-21")
        digest = hashlib.sha256(receipt).hexdigest()
        by_hash = rm.eligible_receipt_accounts(receipt, [item], ["SHA256:" + digest])
        by_e2e = rm.eligible_receipt_accounts(receipt, [item], ["E2E:" + e2e])
        self.assertEqual(by_hash["status"], "conflict")
        self.assertEqual(by_e2e["status"], "conflict")
        self.assertEqual(by_hash["candidate_ids"], [])
        self.assertEqual(by_e2e["candidate_ids"], [])

    def test_eligible_receipt_does_not_consider_only_paid_bills(self):
        receipt = pdf("Comprovante Pix",
                      "Dados de quem recebeu", "Nome: RECEITA FEDERAL",
                      "Valor: R$ 25,00", "Vencimento: 21/10/2026")
        already_paid = {**account(1, amount=25, due="2026-10-21"), "is_paid": 1}
        result = rm.eligible_receipt_accounts(receipt, [already_paid], [])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["candidate_ids"], [])


    def _darf_receipt(self, barcode: str, doc_number: str = "07.01.11111.2222222-3",
                      control: str = "887766554433221100"):
        return pdf("Comprovante de pagamento DARF Numerado",
                   "Numero de controle: " + control,
                   "Agencia: 1234",
                   "Codigo de barras: " + barcode,
                   "Data do pagamento: 13/10/2026",
                   "Numero do documento: " + doc_number,
                   "Valor total: R$ 25,00")

    def test_darf_reader_parses_receipt_without_recipient_or_due(self):
        barcode = "85820000000 25000385262 95070126266 88452619708"
        original = self._darf_receipt(barcode)
        extracted = rm._darf_receipt_facts(rm.pdf_text(original))
        self.assertEqual(extracted["receipt_value"], 25)
        self.assertEqual(extracted["payment_date"], "2026-10-13")
        self.assertEqual(extracted["receipt_type"], "darf")
        self.assertEqual(extracted["recipient"], "")
        self.assertEqual(extracted["receipt_due"], "")
        self.assertEqual(extracted["payment_control"], "887766554433221100")

    def test_darf_receipt_barcode44_matches_account_line48_with_checks(self):
        groups = ["85820000000", "25000385262", "95070126266", "88452619708"]
        receipt = self._darf_receipt(" ".join(groups))
        raw48 = " ".join(group+"8" for group in groups)
        paid_account = account(3, raw48, amount=25, due="2026-10-21")
        other = account(4, "85820000000 25000385262 95070126266 99999999999",
                        amount=25, due="2026-10-21")
        found = rm.eligible_receipt_accounts(receipt, [other, paid_account], [])
        self.assertEqual(found["status"], "eligible")
        self.assertEqual(found["candidate_ids"], [3])
        self.assertIsNone(found["matched_account_id"])

    def test_darf_receipt_matches_document_in_original_darf_pdf(self):
        code = "07.01.11111.2222222-3"
        receipt = self._darf_receipt(
            "85820000000 25000385262 95070126266 88452619708", code
        )
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "conta.pdf"
            original.write_bytes(pdf("Documento de Arrecadacao de Receitas Federais",
                                     "Numero do Documento", code,
                                     "Valor Total do Documento 25,00"))
            linked = account(7, "", amount=25, doc=str(original))
            unrelated = account(8, "", amount=25)
            found = rm.eligible_receipt_accounts(receipt, [unrelated, linked], [])
        self.assertEqual(found["status"], "eligible")
        self.assertEqual(found["candidate_ids"], [7])

    def test_darf_receipt_does_not_link_other_darf_same_amount(self):
        receipt = self._darf_receipt(
            "85820000000 25000385262 95070126266 88452619708",
            "07.01.11111.2222222-3"
        )
        account_other = account(
            12, "85880000000 8 25000385262 7 94070126265 2 64439319780 8",
            amount=25, due="2026-10-21"
        )
        with tempfile.TemporaryDirectory() as tmp:
            document = Path(tmp) / "outro.pdf"
            document.write_bytes(pdf("Documento de Arrecadacao de Receitas Federais",
                                     "Numero: 07.01.99999.4444444-5"))
            account_other["pdf_conta"] = str(document)
            result = rm.eligible_receipt_accounts(receipt, [account_other], [])
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["candidate_ids"], [])

    def test_darf_two_identical_matching_charges_are_all_offered(self):
        barcode = "85820000000 25000385262 95070126266 88452619708"
        receipt = self._darf_receipt(barcode)
        one = account(7, barcode, amount=25)
        two = account(8, barcode, amount=25)
        other = account(9, "85880000000 25000385262 95070126266 88452619708",
                        amount=25)
        result = rm.eligible_receipt_accounts(receipt, [one, two, other], [])
        self.assertEqual(result["status"], "eligible")
        self.assertEqual(result["candidate_ids"], [7, 8])
        self.assertIsNone(result["matched_account_id"])

    def test_darf_original_line48_with_trailing_cpf_is_parsed(self):
        line = ("85880000000 8 25000385262 7 94070126265 2 "
                "64439319780 8   CPF: 973.100.263-49")
        expected = "85880000000250003852629407012626564439319780"
        self.assertIn(expected, rm._darf_barcodes(line))

    def test_darf_receipt_blocks_reused_control_even_after_pdf_reexport(self):
        receipt = self._darf_receipt("85820000000 25000385262 95070126266 88452619708")
        candidate = account(2, "85820000000 25000385262 95070126266 88452619708",
                            amount=25)
        response = rm.eligible_receipt_accounts(
            receipt, [candidate], ["Pago DARFCTRL:887766554433221100"])
        self.assertEqual(response["status"], "conflict")
        self.assertEqual(response["candidate_ids"], [])

    def test_darf_receipt_paid_only_or_missing_matching_code_does_not_guess(self):
        barcode = "85820000000 25000385262 95070126266 88452619708"
        receipt = self._darf_receipt(barcode)
        paid = {**account(5, barcode, amount=25), "is_paid": 1}
        self.assertEqual(
            rm.eligible_receipt_accounts(receipt, [paid], [])["status"],
            "conflict"
        )
        no_codes = account(6, "", amount=25, due="2026-10-21")
        unknown = rm.eligible_receipt_accounts(receipt, [no_codes], [])
        self.assertEqual(unknown["status"], "unverified")
        self.assertEqual(unknown["candidate_ids"], [])


if __name__ == "__main__":
    unittest.main()
