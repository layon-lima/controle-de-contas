"""Processo de OCR isolado. Saída stdout: um único JSON válido."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from pathlib import Path


def run(engine: Path, path: Path):
    # O pacote recuperado mantém o leitor pdf_reader.pyc e a UI .pyc
    # junto dos patches OCR. São módulos do app instalado, NÃO substitutos.
    if not engine.is_dir() or not path.is_file():
        raise FileNotFoundError("Arquivos do motor/PDF indisponíveis.")
    sys.path.insert(0, str(engine))
    with contextlib.redirect_stdout(io.StringIO()):
        from controle_contas.patches import aplicar_patches
        from controle_contas import ui
        aplicar_patches()
        data = ui.analisar_pdf(str(path))
    if not isinstance(data, dict):
        raise RuntimeError("O analisador original não retornou um cadastro.")
    fields = {
        "fornecedor": str(data.get("recebedor") or data.get("fornecedor") or "").strip(),
        "documento": str(data.get("documento") or "").strip(),
        "pagador": str(data.get("pagador") or "").strip(),
        "referencia": str(data.get("referencia") or "").strip(),
        "valor": data.get("valor") or 0,
        "vencimento": str(data.get("vencimento") or "").strip(),
        "codigo_pagamento": str(data.get("codigo_pagamento") or "").strip(),
        "tipo_pagamento": str(data.get("tipo_pagamento") or "").strip(),
        "observacoes": path.name,
    }
    confidence = data.get("confianca")
    reason = data.get("motivos")
    return {
        "ok": True, "fields": fields,
        "confidence": confidence if isinstance(confidence, dict) else {},
        "reason": reason if isinstance(reason, dict) else {},
        "insecure_fields": data.get("campos_inseguros") or [],
        "warnings": [],
    }


if __name__ == "__main__":
    try:
        result = run(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, ensure_ascii=False, default=str))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, default=str))
