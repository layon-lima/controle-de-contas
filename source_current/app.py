from __future__ import annotations

import sys

from controle_contas.cloud_sync import run_cloud_sync_forever


if "--cloud-sync" in sys.argv:
    run_cloud_sync_forever()
    raise SystemExit(0)


from controle_contas.patches import aplicar_patches

aplicar_patches()

from controle_contas.cloud_mobile_ui import (
    cleanup_legacy_mobile_autostart,
    integrate_cloud_mobile_button,
)
from controle_contas.cloud_sync import (
    ensure_cloud_sync_autostart,
    ensure_cloud_sync_backend,
)

# A partir da 1.4.0 o mobile é 100% Cloudflare (Worker + D1 + R2).
# O servidor/túnel local antigo não é mais necessário.
cleanup_legacy_mobile_autostart()
integrate_cloud_mobile_button()
ensure_cloud_sync_backend()
ensure_cloud_sync_autostart()

from controle_contas.ui import executar


if __name__ == "__main__":
    executar()
