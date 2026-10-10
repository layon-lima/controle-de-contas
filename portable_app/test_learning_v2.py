"""Regressoes do aprendizado OCR V2, sem tocar nos PDFs nem no banco real."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "source_current" / "controle_contas" / "layout_learning_v2.py"
spec = importlib.util.spec_from_file_location("_contas_v2_regression", MODULE)
learning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(learning)


class LayoutV2RegressionTests(unittest.TestCase):
    def test_exact_corrected_value_rectangle_wins_over_wrong_neighbor(self):
        """Mesmo alinhamento: aprender o SEGUNDO valor, nunca o primeiro da linha."""
        line = {
            "page": 0, "text": "Valor anterior R$ 999,99 Valor atual R$ 123,45",
            "rect": [0.1, 0.15, 0.9, 0.20], "labels": [],
            "words": [
                {"text": "999,99", "x0": 0.25, "y0": 0.16, "x1": 0.35, "y1": 0.19},
                {"text": "123,45", "x0": 0.70, "y0": 0.16, "x1": 0.80, "y1": 0.19},
            ],
        }
        model = {"pages": [{"page": 0, "lines": [line], "words": line["words"]}]}
        rule = {
            "page": 0, "rect": [0.68, 0.15, 0.82, 0.20],
            "line_rect": [0.1, 0.15, 0.9, 0.20],
        }
        self.assertEqual(learning._read_rule(model, rule, "valor"), 123.45)


    @staticmethod
    def _sample_bill(path, *, beneficiary, due, amount, layout="boleto"):
        """PDF digital real; nome, emissao e vencimento podem ocupar a mesma linha."""
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        if layout == "boleto":
            page.insert_text((72, 74), "FICHA DE COMPENSACAO", fontsize=11)
            page.insert_text((72, 126), "BENEFICIARIO: " + beneficiary, fontsize=10)
            page.insert_text((72, 170), "EMISSAO 04/10/2026  VENCIMENTO: " + due, fontsize=10)
            page.insert_text((72, 216), "VALOR ORIGINAL 10,00  VALOR COBRADO: " + amount, fontsize=10)
        else:
            page.insert_text((74, 310), "GUIA DE PAGAMENTO - OUTRO MODELO", fontsize=11)
            page.insert_text((74, 355), "PAGADOR: " + beneficiary, fontsize=10)
            page.insert_text((74, 420), "DATA: " + due, fontsize=10)
            page.insert_text((74, 480), "TOTAL GUIA: " + amount, fontsize=10)
        doc.save(str(path))
        doc.close()

    def test_real_pdf_corrected_name_date_and_amount_apply_to_next_bill(self):
        """Dois boletos com o MESMO layout e valores variaveis: nao repetir o erro."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "boleto_1.pdf"
            second = root / "boleto_2.pdf"
            self._sample_bill(first, beneficiary="COOPERATIVA ALFA LTDA",
                              due="20/10/2026", amount="105,33")
            self._sample_bill(second, beneficiary="COOPERATIVA ALFA LTDA",
                              due="21/11/2026", amount="219,75")
            learner = learning.LayoutLearningV2(root)
            taught = learner.learn(first, final_values={
                "recebedor": "COOPERATIVA ALFA LTDA",
                "vencimento": "2026-10-20", "valor": 105.33,
            }, predicted_values={
                "recebedor": "BANCO INCORRETO",
                "vencimento": "2026-10-04", "valor": 10.0,
            })
            self.assertTrue({"recebedor", "vencimento", "valor"}.issubset(taught["corrected_fields"]))
            confirmed, applied = learner.apply(first, {"campos_inseguros": []})
            self.assertEqual(confirmed.get("recebedor"), "COOPERATIVA ALFA LTDA")
            self.assertEqual(confirmed.get("vencimento"), "2026-10-20")
            self.assertAlmostEqual(confirmed.get("valor"), 105.33)
            self.assertTrue({"recebedor", "vencimento", "valor"}.issubset(applied))
            another, fields = learner.apply(second, {"campos_inseguros": []})
            self.assertEqual(another.get("recebedor"), "COOPERATIVA ALFA LTDA")
            self.assertEqual(another.get("vencimento"), "2026-11-21")
            self.assertAlmostEqual(another.get("valor"), 219.75)
            self.assertTrue({"recebedor", "vencimento", "valor"}.issubset(fields))
            # O aprendizado deve sobreviver ao encerramento do processo OCR.
            restarted = learning.LayoutLearningV2(root)
            persisted, used = restarted.apply(second, {"campos_inseguros": []})
            self.assertEqual(persisted.get("vencimento"), "2026-11-21")
            self.assertIn("valor", used)

    def test_other_layout_does_not_inherit_learned_boleto_rules(self):
        """A leitura por perfil nao e uma regra universal de fornecedor ou valor."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "boleto.pdf"
            different = root / "outro_tipo.pdf"
            self._sample_bill(original, beneficiary="COOPERATIVA ALFA LTDA",
                              due="20/10/2026", amount="105,33")
            self._sample_bill(different, beneficiary="OUTRA EMPRESA",
                              due="20/10/2026", amount="105,33", layout="guia")
            learner = learning.LayoutLearningV2(root)
            learner.learn(original, final_values={
                "recebedor": "COOPERATIVA ALFA LTDA", "valor": 105.33,
            }, predicted_values={"recebedor": "ERRO", "valor": 10})
            _, fields = learner.apply(different, {"campos_inseguros": []})
            self.assertEqual(fields, set(), "Layout distinto nao pode herdar correcao do boleto.")

    def test_read_name_from_exact_region_when_label_shares_same_line(self):
        """Rótulo e valor na mesma linha sao comuns em boletos."""
        words = [
            {"text": "BENEFICIARIO:", "x0": .1, "y0": .2, "x1": .28, "y1": .23},
            {"text": "EMPRESA", "x0": .32, "y0": .2, "x1": .46, "y1": .23},
            {"text": "CORRETA", "x0": .47, "y0": .2, "x1": .62, "y1": .23},
        ]
        line = {"page": 0, "text": "BENEFICIARIO: EMPRESA CORRETA",
                "rect": [.1, .2, .62, .23],
                "words": words, "labels": ["beneficiario"]}
        model = {"pages": [{"page": 0, "lines": [line], "words": words}]}
        rule = {"page": 0, "rect": [.30, .2, .64, .23],
                "line_rect": [.1, .2, .62, .23], "confirmations": 1}
        self.assertEqual(learning._read_rule(model, rule, "recebedor"), "EMPRESA CORRETA")

    def test_learn_tries_ocr_for_corrected_field_missing_from_embedded_text(self):
        """Apenas campos nao localizados acionam OCR de imagem suplementar."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "document.pdf"
            path.write_bytes(b"placeholder para _cache_key")
            base_words = [{"text": "BENEFICIARIO:", "x0": .1, "y0": .2, "x1": .28, "y1": .23}]
            native_line = {"page": 0, "text": "BENEFICIARIO:",
                           "rect": [.1, .2, .28, .23], "words": base_words,
                           "labels": ["beneficiario"]}
            native = {"source": "native", "pages": [{
                "page": 0, "aspect": .707, "lines": [native_line], "words": base_words,
            }]}
            scanned_words = base_words + [
                {"text": "EMPRESA", "x0": .32, "y0": .2, "x1": .46, "y1": .23},
                {"text": "CORRETA", "x0": .47, "y0": .2, "x1": .62, "y1": .23},
            ]
            scanned = {"source": "ocr", "pages": [{
                "page": 0, "aspect": .707,
                "lines": [{"page": 0, "text": "BENEFICIARIO: EMPRESA CORRETA",
                           "rect": [.1, .2, .62, .23], "words": scanned_words,
                           "labels": ["beneficiario"], "source": "ocr"}],
                "words": scanned_words,
            }]}
            learner = learning.LayoutLearningV2(tmp)
            with patch.object(learner, "_model", return_value=native), patch.object(
                learner, "_ocr_model", return_value=scanned
            ) as image_pass:
                result = learner.learn(path, final_values={
                    "recebedor": "EMPRESA CORRETA",
                }, predicted_values={"recebedor": "EMPRESA ERRADA"})
                self.assertIn("recebedor", result["corrected_fields"])
                self.assertEqual(image_pass.call_count, 1)
                replay, applied = learner.apply(path, {"campos_inseguros": []})
                self.assertIn("recebedor", applied)
                self.assertEqual(replay["recebedor"], "EMPRESA CORRETA")

    def test_duplicate_nearly_identical_profiles_prefer_user_corrected_rule(self):
        """Perfis historicos do mesmo layout nao podem bloquear correcoes."""
        signature = {
            "pages": 1, "aspects": [0.707], "markers": ["boleto"],
            "anchors": ["p0:beneficiario:x1:y2"],
            "skeleton": ["p0:x1:y2:beneficiario"],
            "grid": [["p0:x1:y2:w2", 1]],
        }
        with tempfile.TemporaryDirectory() as tmp:
            detector = learning.LayoutLearningV2(tmp)
            auto = {"id": "automatico", "signatures": [signature],
                    "fields": {"recebedor": {"corrections": 0, "confirmations": 5}}, "saves": 5}
            corrected = {"id": "corrigido", "signatures": [signature],
                         "fields": {"recebedor": {"corrections": 2, "confirmations": 1}}, "saves": 2}
            detector._data = {"version": 2, "profiles": [auto, corrected]}
            with patch.object(detector, "_signature", return_value=signature):
                chosen, best, second = detector.recognize("arquivo.pdf")
        self.assertEqual(chosen["id"], "corrigido")
        self.assertGreaterEqual(best, .95)
        self.assertGreaterEqual(second, .95)

    def test_ambiguous_different_document_profiles_remain_rejected(self):
        """Nao aplicar regras de outra especie de guia so por geometria."""
        a = {"pages": 1, "aspects": [0.707], "anchors": ["a"], "skeleton": ["s"],
             "grid": [["same", 1]], "markers": ["fgts"]}
        b = dict(a, markers=["itr"])
        with tempfile.TemporaryDirectory() as tmp:
            detector = learning.LayoutLearningV2(tmp)
            detector._data = {"version": 2, "profiles": [
                {"id": "fgts", "signatures": [a], "fields": {}},
                {"id": "itr", "signatures": [b], "fields": {}},
            ]}
            with patch.object(detector, "_signature", return_value=dict(a, markers=[])):
                choice, _, _ = detector.recognize("arquivo.pdf")
        self.assertIsNone(choice)


if __name__ == "__main__":
    unittest.main()
