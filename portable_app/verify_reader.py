"""Verificação do motor OCR legado antes de abrir o portátil."""
from __future__ import annotations

import sys
from pathlib import Path

import fitz  # garante que o PyMuPDF necessário à mesclagem está instalado
import pytesseract  # garante o pacote de OCR já usado no desktop
from pdf_intelligence import find_legacy_engine

engine = find_legacy_engine()
package = engine / "controle_contas"
if not package.is_dir():
    raise SystemExit("Falha: pacote OCR não encontrado.")
if not (package / "patches.py").is_file():
    raise SystemExit("Falha: correções de OCR do desktop não disponíveis.")
print("Motor de leitura original localizado:", engine)
print("PyMuPDF:", getattr(fitz, "VersionBind", "disponível"))
print("Leitura/OCR preparado. A validação real será feita por documento.")
