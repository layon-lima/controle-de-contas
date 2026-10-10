"""Sincronizador mobile do Controle de Contas web, sem desktop Qt nem EXE.

Usa a mesma implementacao de Cloudflare/D1/R2, banco e token do desktop.
O socket de lock da versao V11 garante apenas um sincronizador por maquina.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "source_current"))

from controle_contas.cloud_sync import run_cloud_sync_forever

if __name__ == "__main__":
    run_cloud_sync_forever()
