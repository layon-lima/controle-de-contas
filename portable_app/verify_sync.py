"""Finalizacao da migracao: sincronizacao Cloudflare sem EXE legado."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_runtime


def main():
    before = sync_runtime.status()
    if not before["configured"]:
        print("MOBILE: sincronizacao ainda nao configurada; dados locais preservados.")
        return 0
    sync_runtime.ensure_running(register_startup=True)
    for _ in range(24):
        state = sync_runtime.status()
        if state["running"]:
            print("MOBILE: sincronizador Cloudflare ativo e inicializacao independente configurada.")
            return 0
        time.sleep(0.5)
    print("ERRO: sincronizador Cloudflare nao iniciou. Verifique cloud_sync.log.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
