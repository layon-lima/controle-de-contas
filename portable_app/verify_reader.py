"""Teste de ponta a ponta do MESMO motor OCR original do desktop.

Executado antes de abrir a versão web. Usa um PDF descartável e não consulta
nem modifica o banco de dados ou perfis de aprendizado do usuário.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
import pytesseract

from pdf_intelligence import analyze_pdf, find_legacy_engine, find_tesseract_binary


def main():
    engine = find_legacy_engine()
    package = engine / "controle_contas"
    required = ("patches.py", "layout_learning_v2.py")
    for name in required:
        if not (package / name).is_file():
            raise RuntimeError("Motor OCR original incompleto: " + name)
    engine_binary = find_tesseract_binary(engine)
    if engine_binary is not None:
        pytesseract.pytesseract.tesseract_cmd = str(engine_binary)
    try:
        tesseract_version = str(pytesseract.get_tesseract_version())
        languages = pytesseract.get_languages(config="")
        if "por" not in languages:
            raise RuntimeError(
                "Tesseract está sem o idioma português (por.traineddata). "
                "A leitura de boletos brasileiros perderia qualidade."
            )
    except Exception as exc:
        raise RuntimeError(
            "Motor Tesseract português não está pronto. PDFs escaneados "
            "deixariam de ser lidos como no desktop: " + str(exc)
        ) from exc

    # Usar uma guia genérica com campos reais; a exigência aqui é que o
    # subprocesso original inicie, abra um PDF e devolva campos em um dict.
    # Não treinamos nem salvamos conta.
    with tempfile.TemporaryDirectory(prefix="cc_ocr_probe_") as d:
        path = Path(d) / "PROVA LEITOR ORIGINAL.pdf"
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            sample = (
                "DOCUMENTO DE COBRANCA\n"
                "BENEFICIARIO: EMPRESA TESTE\n"
                "PAGADOR: CLIENTE TESTE\n"
                "VENCIMENTO: 25/12/2026\n"
                "VALOR DO DOCUMENTO: 123,45\n"
            )
            page.insert_text((72, 90), sample, fontsize=12)
            doc.save(str(path))
        finally:
            doc.close()
        result = analyze_pdf(path, timeout=100)
        if result.get("engine") != "desktop-ocr-v2":
            raise RuntimeError("O leitor original não foi usado.")
        if not isinstance(result.get("fields"), dict):
            raise RuntimeError("O motor OCR não devolveu campos verificáveis.")
    print("OCR ORIGINAL OK: PDF real aberto, subprocesso executado e campos retornados.")
    print("Tesseract:", tesseract_version)
    print("Motor:", engine)


if __name__ == "__main__":
    main()
