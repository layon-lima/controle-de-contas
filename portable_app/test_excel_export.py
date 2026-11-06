"""Testes do XLSX exportado com base fictícia; não acessam contas reais."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import unittest

from portable_app import excel_export as e

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def entry(**kwargs):
    defaults = {
        "fornecedor": "BRADESCO AUTO",
        "observacoes": "SEGURO ULTIMA PARCELA CAR 3.pdf",
        "vencimento": "2026-11-06",
        "valor": 1589.15,
        "status": "Vencida",
        "suggested_at": "2026-11-05T10:00:00",
        "enviado_pagamento_em": "2026-11-06T14:10:00",
    }
    defaults.update(kwargs)
    return defaults


def xml_sheet(binary):
    with ZipFile(BytesIO(binary)) as archive:
        assert set(archive.namelist()) >= {
            "[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels", "xl/styles.xml",
            "xl/worksheets/sheet1.xml",
        }
        for file in archive.namelist():
            if file.endswith(".xml") or file.endswith(".rels") or file == "[Content_Types].xml":
                ET.fromstring(archive.read(file))
        return ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))


def cell(sheet, address):
    found = sheet.find(f".//x:c[@r='{address}']", NS)
    if found is None:
        raise AssertionError(f"Célula {address} não encontrada.")
    return found


class ExcelExportTests(unittest.TestCase):
    def test_excel_contains_visible_columns_and_real_money_and_dates(self):
        book = e.build_xlsx([entry(), entry(
            fornecedor="AGREX", valor=55.7, status="Paga",
            vencimento="2026-11-07", suggested_at="", enviado_pagamento_em=""
        )], label="Contas em aberto | Teste", generated=datetime(2026, 11, 6, 16, 30))
        self.assertTrue(book.startswith(b"PK"))
        sheet = xml_sheet(book)
        self.assertEqual(cell(sheet, "A5").find(".//x:t", NS).text, "BRADESCO AUTO")
        self.assertEqual(cell(sheet, "A6").find(".//x:t", NS).text, "AGREX")
        self.assertEqual(cell(sheet, "E5").find(".//x:t", NS).text, "Vencida")
        self.assertEqual(cell(sheet, "E6").find(".//x:t", NS).text, "Paga")
        self.assertEqual(cell(sheet, "D5").find("x:v", NS).text, "1589.15")
        self.assertEqual(cell(sheet, "D6").find("x:v", NS).text, "55.70")
        self.assertNotEqual(cell(sheet, "C5").get("t"), "inlineStr")
        self.assertIsNone(cell(sheet, "F6").find("x:v", NS))
        self.assertEqual(cell(sheet, "A1").find(".//x:t", NS).text,
                         "CONTROLE DE CONTAS  /  EXPORTAÇÃO")
        self.assertIn("Contas em aberto", cell(sheet, "A2").find(".//x:t", NS).text)
        self.assertIsNotNone(sheet.find("x:autoFilter", NS))
        self.assertEqual(sheet.find("x:autoFilter", NS).get("ref"), "A4:G6")
        self.assertEqual(sheet.find("x:sheetViews/x:sheetView/x:pane", NS).get("ySplit"), "4")

    def test_text_is_not_excel_formula_and_invalid_xml_removed(self):
        book = e.build_xlsx([entry(
            fornecedor='=HYPERLINK("https://attacker.invalid","clique")',
            observacoes="Teste\x01&<tag>com conteúdo",
            valor=12.0,
        )])
        sheet = xml_sheet(book)
        supplier = cell(sheet, "A5")
        self.assertEqual(supplier.get("t"), "inlineStr")
        self.assertIsNone(supplier.find("x:f", NS))
        self.assertTrue(supplier.find(".//x:t", NS).text.startswith("=HYPERLINK("))
        self.assertEqual(cell(sheet, "B5").find(".//x:t", NS).text,
                         "Teste&<tag>com conteúdo")

    def test_limits_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "6.000|6000"):
            e.build_xlsx([entry()] * (e.MAX_ROWS + 1))
        with self.assertRaises(ValueError):
            e.build_xlsx(None)

    def test_utc_times_convert_to_brazil_before_excel_serialization(self):
        expected = e._date_number("2026-11-06T12:00:00", True)
        received = e._date_number("2026-11-06T15:00:00Z", True)
        self.assertEqual(expected, received)


if __name__ == "__main__":
    unittest.main()
