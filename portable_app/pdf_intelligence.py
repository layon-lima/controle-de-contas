"""Leitura automática do portátil: reutiliza o leitor do desktop instalado.

O leitor original possui OCR, perfis V1/V2, validações DARF, FGTS e boletos.
Executado em processo isolado para não misturar PySide6 com o servidor HTTP.
Uma extração sem o motor original disponível nunca é apresentada como equivalente.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE_TIMEOUT = 120
ENGINE_NAMES = ("_local_build_1_5_0",)


class ReaderUnavailable(RuntimeError):
    pass


def find_legacy_engine(root: Path = ROOT) -> Path:
    candidates = sorted(
        (p for p in root.glob(".local_build_*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    candidates += [root / x for x in ENGINE_NAMES]
    for candidate in candidates:
        package = candidate / "controle_contas"
        # O projeto recuperado contém módulos de leitura .pyc originais,
        # mais os patches atuais. Nunca importar só os patches sem o motor.
        needed = ("patches.py", "layout_learning_v2.py")
        if all((package / n).is_file() for n in needed) and (
            (package / "pdf_reader.py").is_file()
            or (package / "pdf_reader.pyc").is_file()
        ) and (
            (package / "ui.py").is_file() or (package / "ui.pyc").is_file()
        ):
            return candidate
    raise ReaderUnavailable(
        "Motor de leitura do desktop não localizado. "
        "A importação automática fica bloqueada para evitar resultados inferiores. "
        "O aplicativo instalado permanece disponível; anexe um log para localizar "
        "os módulos recuperados."
    )


def analyze_pdf(path: Path, *, root: Path = ROOT, timeout: int = ENGINE_TIMEOUT) -> dict[str, Any]:
    """Delega ao MESMO ui.analisar_pdf patchado do desktop; não grava banco."""
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise FileNotFoundError("PDF de entrada não encontrado.")
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("O PDF excede 30 MB.")
    engine = find_legacy_engine(root)
    worker = Path(__file__).with_name("ocr_worker.py")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUTF8"] = "1"
    # Paths dos perfis de aprendizado continuam definidos pelo motor original.
    command = [sys.executable, "-X", "utf8", str(worker), str(engine), str(path)]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("A leitura excedeu o tempo limite; nada foi cadastrado.") from exc
    if proc.returncode:
        explanation = (proc.stderr or proc.stdout or "Erro desconhecido.")[-1200:]
        raise ReaderUnavailable("Leitor instalado não conseguiu analisar o PDF: " + explanation)
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise ReaderUnavailable("Resposta inválida do motor de leitura original.") from exc
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise ReaderUnavailable(str(data.get("error") if isinstance(data, dict) else
                                    "Motor de leitura indisponível."))
    raw = data.get("fields")
    if not isinstance(raw, dict):
        raise ReaderUnavailable("O motor não retornou campos válidos.")
    # Não inventar informações: campos inseguros permanecem sinalizados.
    return {
        "engine": "desktop-ocr-v2",
        "fields": {key: raw.get(key) for key in (
            "fornecedor", "documento", "pagador", "referencia", "valor",
            "vencimento", "codigo_pagamento", "tipo_pagamento", "observacoes"
        )},
        "insecure_fields": data.get("insecure_fields") or [],
        "confidence": data.get("confidence") or {},
        "reason": data.get("reason") or {},
        "warnings": data.get("warnings") or [],
    }
