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
