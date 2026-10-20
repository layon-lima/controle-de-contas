#!/usr/bin/env bash
# Controle de Contas VPS: instala SOMENTE broker privado de metadados.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
COMPOSE="$ROOT/deploy/vps/compose.broker.yml"
SECRETS="$ROOT/deploy/vps/broker.env"
for exe in docker python3 git; do
  command -v "$exe" >/dev/null || { echo "Dependencia ausente: $exe" >&2; exit 1; }
done
docker compose version >/dev/null
docker info >/dev/null
[[ "$(git branch --show-current)" == "main" ]] || {
  echo "Instale SOMENTE a main do projeto Controle de Contas." >&2; exit 1;
}
[[ -f "$COMPOSE" && -f "$ROOT/deploy/vps/broker.py" ]] || {
  echo "Arquivos do broker nao encontrados." >&2; exit 1;
}
if [[ ! -f "$SECRETS" ]]; then
  TEMP="$(mktemp "$ROOT/deploy/vps/.broker-temp.XXXXXXXX")"
  trap 'rm -f "$TEMP"' EXIT
  python3 - "$TEMP" <<'PY'
import secrets, sys
from pathlib import Path
p = Path(sys.argv[1])
p.write_text("CONTAS_BROKER_ADMIN_TOKEN=" + secrets.token_urlsafe(48) + "\n"
             + "CONTAS_BROKER_AGENT_TOKEN=" + secrets.token_urlsafe(48) + "\n")
p.chmod(0o600)
PY
  ln "$TEMP" "$SECRETS" || { echo "Credenciais ja criadas; confira e repita." >&2; exit 1; }
  rm -f "$TEMP"
  trap - EXIT
  echo "Credenciais novas gravadas SOMENTE na VPS, sem mostrar valores."
else
  echo "Credenciais existentes mantidas."
fi
chmod 600 "$SECRETS"
python3 - "$SECRETS" <<'PY'
import sys
from pathlib import Path
items = dict(line.split("=", 1) for line in
             Path(sys.argv[1]).read_text().splitlines() if "=" in line)
a = items.get("CONTAS_BROKER_ADMIN_TOKEN", "")
b = items.get("CONTAS_BROKER_AGENT_TOKEN", "")
if len(a) < 32 or len(b) < 32 or a == b or any(
        x.startswith("SUBSTITUA") for x in (a, b)):
    raise SystemExit("Credenciais invalidas. Broker nao iniciado.")
PY
docker compose -f "$COMPOSE" config --quiet
docker compose -f "$COMPOSE" up -d --build contas-agent-broker
python3 - <<'PY'
import json, time, urllib.request
for attempt in range(40):
    try:
        with urllib.request.urlopen("http://127.0.0.1:18767/health", timeout=2) as res:
            status = json.load(res)
        if status.get("online") is True and status.get("stores_pdf") is False:
            print("BROKER ONLINE: localhost:18767, somente metadados.")
            break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("Broker sem resposta; consulte os logs SOMENTE do Contas.")
PY
echo "Caddy, Cloudflare, GFARM, PLUV e o dominio existente nao foram alterados."
