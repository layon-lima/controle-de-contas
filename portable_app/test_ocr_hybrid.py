"""Testes determinísticos do OCR híbrido: nunca acessam contas reais nem layouts salvos."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable_app import ocr_hybrid as hybrid
from portable_app import pdf_intelligence


def bank_line():
    a, b, c = "001905009", "0123456789", "1234567890"
    return (
        a + str(hybrid._mod10(a))
        + b + str(hybrid._mod10(b))
        + c + str(hybrid._mod10(c))
        + "123456789012345"
    )


def pix_payload():
    prefix = "00020101021226330014BR.GOV.BCB.PIX0111email@teste5204000053039865802BR6304"
    crc = 0xFFFF
    for b in prefix.encode("utf-8"):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return prefix + f"{crc:04X}"


class HybridOcrTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="cc_hybrid_test_")
        self.addCleanup(self.folder.cleanup)
        self.pdf = Path(self.folder.name) / "nota.pdf"

    def _digital_pdf(self, text):
        import fitz
        with fitz.open() as doc:
            page = doc.new_page(width=595, height=842)
            page.insert_text((52, 65), text, fontsize=11)
            doc.save(str(self.pdf))

    def test_validator_rejects_fake_boleto_and_accepts_real_checksum_structure(self):
        code = bank_line()
        self.assertEqual(len(code), 47)
        self.assertTrue(hybrid.valid_bank_slip(code))
        bad = code[:9] + str((int(code[9]) + 1) % 10) + code[10:]
        self.assertFalse(hybrid.valid_bank_slip(bad))
        self.assertFalse(hybrid.valid_bank_slip("1" * 47))
        self.assertIsNone(hybrid.payment_code(bad))

    def test_collection_slip_48_digits_requires_four_real_checksums(self):
        first = "83612345678"  # Identificador 8 / referência 6: mod 10.
        chunks = [first, "12345678901", "98765432109", "13579135791"]
        code = "".join(c + str(hybrid._mod10(c)) for c in chunks)
        self.assertEqual(len(code), 48)
        self.assertTrue(hybrid.valid_collection_slip(code))
        self.assertEqual(hybrid.payment_code(code), code)
        bad = code[:23] + str((int(code[23]) + 1) % 10) + code[24:]
        self.assertFalse(hybrid.valid_collection_slip(bad))
        self.assertIsNone(hybrid.payment_code(bad))

    def test_labeled_values_on_separate_lines_are_still_verified(self):
        result = hybrid.read_labeled_text(
            "BENEFICIARIO\nEMPRESA BOLETO LTDA\n"
            "VENCIMENTO\n28/02/2028\n"
            "VALOR TOTAL\nR$ 78,50"
        )
        self.assertEqual(result["vencimento"], "2028-02-28")
        self.assertEqual(result["valor"], 78.50)
        self.assertEqual(result["fornecedor"], "empresa boleto ltda")
        self.assertNotIn("valor", hybrid.read_labeled_text(
            "VALOR TOTAL\n- R$ 80,00"
        ))

    def test_pix_crc_accepts_valid_code_and_rejects_corruption(self):
        pix = pix_payload()
        self.assertTrue(hybrid.valid_pix_emv(pix))
        self.assertEqual(hybrid.payment_code(pix), pix)
        self.assertFalse(hybrid.valid_pix_emv(pix[:-1] + ("A" if pix[-1] != "A" else "B")))
        self.assertFalse(hybrid.valid_pix_emv("0002016304ABCD"))
        self.assertIsNone(hybrid.payment_code("0002016304ABCD"))

    def test_date_and_money_are_conservative(self):
        self.assertEqual(hybrid.due_date("Vencimento: 25/12/2026"), "2026-12-25")
        self.assertIsNone(hybrid.due_date("Vencimento: 31/02/2026"))
        self.assertIsNone(hybrid.due_date("Vencimento: 32/12/2026"))
        self.assertEqual(hybrid.amount("Total R$ 1.234,56"), 1234.56)
        self.assertIsNone(hybrid.amount("Total 123456"))
        self.assertIsNone(hybrid.amount("Total R$ -2,00"))

    def test_requires_explicit_labels_and_refuses_ambiguous_multiple_values(self):
        self.assertEqual(hybrid.read_labeled_text("R$ 999,99\n01/01/2026"), {})
        self.assertNotIn("valor", hybrid.read_labeled_text(
            "Valor total: R$ 10,00\nValor total: R$ 20,00"))
        self.assertEqual(hybrid.read_labeled_text(
            "BENEFICIARIO: EMPRESA CONFIAVEL LTDA\n"
            "VENCIMENTO: 25/12/2026\n"
            "VALOR TOTAL: R$ 1.234,56\n"
            "LINHA DIGITAVEL: " + bank_line()
        )["valor"], 1234.56)

    def test_missing_fields_are_filled_but_original_fields_never_overwritten(self):
        self._digital_pdf(
            "BENEFICIARIO: FORNECEDOR MODELO LTDA\n"
            "VALOR TOTAL: R$ 1.234,56\n"
            "VENCIMENTO: 25/12/2026\n"
            "LINHA DIGITAVEL: " + bank_line()
        )
        baseline = {
            "engine": "desktop-ocr-v2",
            "fields": {
                "fornecedor": "BENEFICIARIO ANTIGO CORRETO",
                "valor": 600.0,
                "vencimento": "",
                "codigo_pagamento": "",
                "observacoes": "arquivo.pdf",
            },
            "warnings": [],
        }
        before = json.dumps(baseline, sort_keys=True, ensure_ascii=False)
        with patch.object(hybrid, "_read_scanned", side_effect=AssertionError("OCR extra desnecessário")):
            result = hybrid.enhance_pdf(self.pdf, baseline)
        self.assertEqual(result["fields"]["fornecedor"], "BENEFICIARIO ANTIGO CORRETO")
        self.assertEqual(result["fields"]["valor"], 600.0)
        self.assertEqual(result["fields"]["vencimento"], "2026-12-25")
        self.assertEqual(result["fields"]["codigo_pagamento"], bank_line())
        self.assertIn("valor", result["ocr_comparison"]["conflicts"])
        self.assertIn("fornecedor", result["ocr_comparison"]["conflicts"])
        self.assertTrue(result["ocr_comparison"]["legacy_preserved"])
        self.assertEqual(json.dumps(baseline, sort_keys=True, ensure_ascii=False), before)

    def test_scanned_pass_only_runs_if_critical_fields_missing(self):
        self.pdf.write_bytes(b"%PDF-1.4\nsynthetic")
        base = {"fields": {
            "fornecedor": "ANTIGO", "valor": 0, "vencimento": "",
            "codigo_pagamento": "",
        }, "warnings": []}
        with patch.object(hybrid, "_read_digital", return_value=("", 0)):
            with patch.object(hybrid, "_read_scanned", return_value=
                "VALOR TOTAL: R$ 27,35\nVENCIMENTO: 30/12/2026"
            ) as scanner:
                result = hybrid.enhance_pdf(self.pdf, base)
        scanner.assert_called_once()
        self.assertEqual(result["fields"]["valor"], 27.35)
        self.assertEqual(result["fields"]["vencimento"], "2026-12-30")
        self.assertEqual(result["fields"]["fornecedor"], "ANTIGO")

    def test_scan_is_skipped_if_legacy_already_recognized_critical_fields(self):
        self.pdf.write_bytes(b"%PDF-1.4\nsynthetic")
        base = {"fields": {
            "valor": 99.0, "vencimento": "2026-12-30",
            "codigo_pagamento": bank_line(),
        }}
        with patch.object(hybrid, "_read_digital", return_value=("", 0)):
            with patch.object(hybrid, "_read_scanned", side_effect=AssertionError()) as scanner:
                result = hybrid.enhance_pdf(self.pdf, base)
        scanner.assert_not_called()
        self.assertEqual(result["fields"], base["fields"])

    def test_original_mode_never_invokes_hybrid_reader(self):
        self.pdf.write_bytes(b"%PDF-1.4\nsynthetic")
        data = {"ok": True, "fields": {"fornecedor": "LEGADO",
                                       "valor": 101.0,
                                       "vencimento": "2026-12-31"}}
        from subprocess import CompletedProcess
        with patch.object(pdf_intelligence, "find_legacy_engine",
                          return_value=Path(self.folder.name)), \
             patch.object(pdf_intelligence.subprocess, "run",
                          return_value=CompletedProcess([], 0, json.dumps(data), "")), \
             patch.object(hybrid, "enhance_pdf",
                          side_effect=AssertionError("Novo OCR não deve rodar")) as extra:
            result = pdf_intelligence.analyze_pdf(self.pdf, enhanced=False)
        extra.assert_not_called()
        self.assertEqual(result["fields"]["fornecedor"], "LEGADO")
        self.assertEqual(result["ocr_comparison"]["mode"], "original_somente")

    def test_failing_secondary_engine_preserves_legacy_result(self):
        self.pdf.write_bytes(b"%PDF-1.4\nsynthetic")
        data = {
            "ok": True, "fields": {"fornecedor": "CORRETO", "valor": 120.0,
                                    "vencimento": "2026-12-30"}
        }
        from subprocess import CompletedProcess
        with patch.object(pdf_intelligence, "find_legacy_engine", return_value=Path(self.folder.name)), \
             patch.object(pdf_intelligence.subprocess, "run",
                          return_value=CompletedProcess([], 0, json.dumps(data), "")), \
             patch.object(hybrid, "enhance_pdf", side_effect=RuntimeError("OCR complementar falhou")):
            result = pdf_intelligence.analyze_pdf(self.pdf)
        self.assertEqual(result["fields"]["fornecedor"], "CORRETO")
        self.assertEqual(result["fields"]["valor"], 120.0)
        self.assertEqual(result["ocr_comparison"]["mode"], "legado_preservado")
        self.assertTrue(result["ocr_comparison"]["legacy_preserved"])


if __name__ == "__main__":
    unittest.main()
