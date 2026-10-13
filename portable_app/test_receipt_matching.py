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


    def test_bradesco_dynamic_pix_id_differs_from_registered_qr_but_unique_account(self):
        receipt = pdf("Comprovante Pix", "Dados de quem recebeu",
                      "Nome: RECEITA FEDERAL", "Valor: R$ 25,00",
                      "Vencimento: 21/10/2026",
                      "Identificador: 07012626564439319046470798",
                      "Data do debito: 13/10/2026")
        record = account(55,
                         "00020101021226900014br.gov.bcb.pix2568qrcodepix.bb.com.br/"
                         "pix/v2/cobv/78def1eb-2249-49b6-9f9c-7b7205947d79",
                         amount=25, due="2026-10-21")
        result = rm.discover_receipt(receipt, [record], [])
        self.assertEqual(result["status"], "suggested")
        self.assertEqual(result["matched_account_id"], 55)
        self.assertTrue(result["identifier_found"])
        self.assertEqual(result["receipt_value"], 25)
        self.assertEqual(result["receipt_due"], "2026-10-21")

    def test_different_identifier_and_two_identical_accounts_stay_ambiguous(self):
        receipt = pdf("Comprovante Pix", "Dados de quem recebeu",
                      "Nome: RECEITA FEDERAL", "Valor: R$ 25,00",
                      "Vencimento: 21/10/2026",
                      "Identificador: 07012626564439319046470798")
        first = account(1, "000201010212" + "X"*60, amount=25, due="2026-10-21")
        second = account(2, "000201010212" + "Y"*60, amount=25, due="2026-10-21")
        result = rm.discover_receipt(receipt, [first, second], [])
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["matched_account_id"])
        self.assertEqual(result["candidate_ids"], [1, 2])

    def test_metadata_requires_recipient_value_and_due_never_value_alone(self):
        receipt = pdf("Comprovante Pix", "Dados de quem recebeu",
                      "Nome: EMPRESA ESTRANHA", "Valor: R$ 25,00",
                      "Vencimento: 21/10/2026")
        known = account(2, "", amount=25, due="2026-10-21")
        result = rm.discover_receipt(receipt, [known], [])
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["matched_account_id"])

    def test_metadata_fallback_on_paid_account_is_blocked(self):
        receipt = pdf("Comprovante Pix", "Dados de quem recebeu",
                      "Nome: RECEITA FEDERAL", "Valor: R$ 25,00",
                      "Vencimento: 21/10/2026",
                      "Identificador: 07012626564439319046470798")
        known = {**account(1, "", amount=25, due="2026-10-21"), "is_paid": 1}
        result = rm.discover_receipt(receipt, [known], [])
        self.assertEqual(result["status"], "conflict")

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


if __name__ == "__main__":
    unittest.main()
