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

    def test_drop_receipt_never_confirms_based_only_on_matching_amount(self):
        receipt = pdf("COMPROVANTE", "VALOR PAGO: R$ 270,35")
        result = rm.discover_receipt(receipt, [self.one, self.two], [])
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["matched_account_id"])
        self.assertEqual(result["candidate_ids"], [1, 2])
        self.assertEqual(result["receipt_value"], 270.35)

    def test_drop_receipt_without_any_comparable_data_requires_manual_choice(self):
        receipt = pdf("COMPROVANTE BANCARIO SEM NUMEROS")
        result = rm.discover_receipt(receipt, [self.one, self.two], [])
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["matched_account_id"])
        self.assertEqual(result["candidate_ids"], [])

    def test_drop_receipt_paid_account_is_identified_not_rerouted(self):
        paid = {**self.two, "is_paid": 1}
        receipt = pdf("COMPROVANTE", "LINHA DIGITAVEL: " + paid["codigo_pagamento"])
        result = rm.discover_receipt(receipt, [self.one, paid], [])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["matched_account_id"], 2)

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


if __name__ == "__main__":
    unittest.main()
