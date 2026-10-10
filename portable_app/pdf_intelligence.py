"""Leitura automática do portátil: reutiliza o leitor do desktop instalado.

O leitor original possui OCR, perfis V1/V2, validações DARF, FGTS e boletos.
Executado em processo isolado para não misturar PySide6 com o servidor HTTP.
Uma extração sem o motor original disponível nunca é apresentada como equivalente.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sysconfig
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE_TIMEOUT = 120
ENGINE_NAMES = ("_local_build_1_5_0",)
PREPARE_LOCK = threading.RLock()


class ReaderUnavailable(RuntimeError):
    pass


def find_tesseract_binary(engine_root: Path | None = None) -> Path | None:
    """Localiza o OCR do antigo EXE ou do Windows sem depender do PATH."""
    local = Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local"))
    candidates = []
    if engine_root:
        candidates.append(engine_root / "tesseract" / "tesseract.exe")
    for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_var)
        if root:
            candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")
    candidates.extend([
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        local / "Programs" / "Controle de Contas" / "_internal" / "tesseract" / "tesseract.exe",
        local / "Programs" / "Controle de Contas" / "tesseract" / "tesseract.exe",
        local / "ControleContas" / "portable_reader_engine" / "tesseract" / "tesseract.exe",
    ])
    found = shutil.which("tesseract")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _preserve_ocr_assets(destination: Path) -> None:
    """Copia tessdata e exe embarcados para sobreviver a eventual desinstalação.

    Não remove nem modifica a instalação antiga e não exige admin.
    """
    target = destination / "tesseract" / "tesseract.exe"
    if target.is_file():
        return
    binary = find_tesseract_binary(destination)
    if not binary:
        return
    if binary.parent.resolve() == target.parent.resolve():
        return
    # Apenas na primeira preparação. Nunca copiar pastas para dentro delas mesmas.
    source_dir = binary.parent
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target.parent, dirs_exist_ok=True)


def find_legacy_engine(root: Path = ROOT) -> Path:
    # Preferência: cópia independente em APPDATA com patches OCR atuais.
    # Não depender de diretório de build nem da permanência do EXE instalado.
    try:
        return _materialize_engine(root)
    except ReaderUnavailable:
        pass
    candidates = sorted(
        (p for p in root.glob(".local_build_*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    candidates += [root / x for x in ENGINE_NAMES]
    for candidate in candidates:
        package = candidate / "controle_contas"
        needed = ("patches.py", "layout_learning_v2.py")
        if all((package / n).is_file() for n in needed) and (
            (package / "pdf_reader.py").is_file()
            or (package / "pdf_reader.pyc").is_file()
        ) and (
            (package / "ui.py").is_file() or (package / "ui.pyc").is_file()
        ):
            return candidate
    raise ReaderUnavailable(
        "Não foi possível preparar o leitor OCR do desktop a partir da "
        "distribuição nem dos builds locais. A versão instalada não foi alterada."
    )


def _materialize_engine(root: Path) -> Path:
    appdir = Path(os.environ.get("LOCALAPPDATA") or
                  str(Path.home() / "AppData" / "Local")) / "ControleContas"
    destination = appdir / "portable_reader_engine"
    source = root / "releases" / "CONTROLE_CONTAS_1.4.4_SOURCE.zip"
    overlay = root / "releases" / "CONTROLE_CONTAS_1.4.5_OVERLAY.b64"
    patched = root / "source_current" / "controle_contas"
    if not source.is_file() or not overlay.is_file():
        raise ReaderUnavailable(
            "Motor OCR recuperado não está disponível no projeto local. "
            "O aplicativo instalado permanece inalterado."
        )
    with PREPARE_LOCK:
        package = destination / "controle_contas"
        if not (
            (package / "pdf_reader.pyc").is_file()
            and (package / "ui.pyc").is_file()
        ):
            destination.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source) as archive:
                archive.extractall(destination)
            with tempfile.NamedTemporaryFile(
                suffix=".zip", dir=destination, delete=False
            ) as tmp:
                tmp.write(base64.b64decode(overlay.read_text(encoding="utf-8")))
                overlay_zip = Path(tmp.name)
            try:
                with zipfile.ZipFile(overlay_zip) as archive:
                    archive.extractall(destination)
            finally:
                overlay_zip.unlink(missing_ok=True)
        if not (package / "pdf_reader.pyc").is_file() or not (package / "ui.pyc").is_file():
            raise ReaderUnavailable(
                "Os arquivos compilados originais do leitor não foram localizados."
            )
        for filename in ("patches.py", "layout_learning_v2.py"):
            original = patched / filename
            if not original.is_file():
                raise ReaderUnavailable("Código do OCR original não encontrado: " + filename)
            target = package / filename
            if not target.is_file() or original.read_bytes() != target.read_bytes():
                temp = target.with_name(target.name + "." + os.urandom(4).hex() + ".tmp")
                try:
                    shutil.copy2(original, temp)
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
        _preserve_ocr_assets(destination)
        return destination


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


def learn_from_confirmed(path: Path, fields: dict, predicted: dict, *,
                         root: Path = ROOT, timeout: int = 60) -> dict:
    """Usa o mesmo aprendizado_layouts_v2.json do app original.

    O aprendizado é posterior ao commit e não deve desfazer um cadastro válido.
    """
    engine = find_legacy_engine(root)
    command = [
        sys.executable, "-X", "utf8",
        str(Path(__file__).with_name("ocr_worker.py")),
        str(engine), str(path), "--learn",
    ]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        command,
        input=json.dumps({"final": fields, "predicted": predicted},
                         ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise ReaderUnavailable(
            "O cadastro foi salvo, mas o aprendizado do layout não foi concluído: "
            + (result.stderr or result.stdout or "")[-300:]
        )
    try:
        response = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ReaderUnavailable("Resposta inválida do aprendizado do leitor.") from exc
    if not response.get("ok"):
        raise ReaderUnavailable(str(response.get("error") or "Aprendizado não confirmado."))
    return response.get("learning") or {}
