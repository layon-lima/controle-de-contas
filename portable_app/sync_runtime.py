"""Inicializacao e verificacao do sincronizador existente no modo web."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("cloud_sync_worker.py")
sys.path.insert(0, str(ROOT / "source_current"))
from controle_contas import cloud_sync


def is_running() -> bool:
    try:
        with socket.create_connection(
            (cloud_sync.SYNC_LOCK_HOST, cloud_sync.SYNC_LOCK_PORT), timeout=0.4
        ):
            return True
    except OSError:
        return False


def status() -> dict:
    configured = cloud_sync.cloud_enabled()
    return {
        "configured": configured,
        "running": is_running() if configured else False,
        "backend": "Cloudflare Worker + D1/R2",
    }


def ensure_running(*, register_startup: bool = False) -> dict:
    if not cloud_sync.cloud_enabled():
        return status()
    if not is_running():
        python = Path(sys.executable)
        # pythonw.exe inicia sem abrir terminal no Windows.
        pythonw = python.with_name("pythonw.exe")
        runner = pythonw if os.name == "nt" and pythonw.is_file() else python
        flags = 0
        for attr in ("CREATE_NO_WINDOW", "DETACHED_PROCESS",
                     "CREATE_NEW_PROCESS_GROUP", "CREATE_BREAKAWAY_FROM_JOB"):
            flags |= getattr(subprocess, attr, 0)
        subprocess.Popen(
            [str(runner), str(WORKER)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
    if register_startup and os.name == "nt":
        # Substitui APENAS a entrada de startup do sincronizador (nao o EXE
        # instalado nem atalhos do usuario), mantendo as credenciais existentes.
        import winreg
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        runner = pythonw if pythonw.is_file() else python
        command = subprocess.list2cmdline([str(runner), str(WORKER)])
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key, "ControleContasCloudSync", 0, winreg.REG_SZ, command
            )
    return status()
