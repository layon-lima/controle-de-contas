#!/usr/bin/env bash
# Prepara SOMENTE o banco privado central do Controle de Contas na VPS.
# NAO migra dados, nao expõe porta, nao altera GFARM/PLUV/Caddy/Cloudflare.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="$ROOT/deploy/vps/compose.central.yml"
SECRETS="$ROOT/deploy/vps/central.env"
cd "$ROOT"

for program in git docker python3; do
  command -v "$program" >/dev/null || { echo "ERRO: falta $program" >&2; exit 1; }
done
docker compose version >/dev/null
docker info >/dev/null
[[ "$(git branch --show-current)" == "main" ]] || {
  echo "ERRO: instalacao permitida somente pela main."; exit 1;
}
[[ -f "$COMPOSE" ]] || { echo "ERRO: compose.central.yml ausente."; exit 1; }
if [[ -n "$(git status --porcelain -- deploy/vps/compose.central.yml deploy/vps/INSTALAR_BANCO_CONTAS.sh)" ]]; then
  echo "ERRO: arquivos de implantacao modificados localmente."; exit 1;
fi

if [[ ! -f "$SECRETS" ]]; then
  python3 - "$SECRETS" <<'PY'
import os, pathlib, secrets, sys
path = pathlib.Path(sys.argv[1])
value = "CONTAS_DB_PASSWORD=" + secrets.token_urlsafe(48) + "\n"
fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as out:
    out.write(value)
PY
  echo "Credencial gerada na VPS (valor nao exibido)."
else
  echo "Credencial existente preservada."
fi
chmod 600 "$SECRETS"
python3 - "$SECRETS" <<'PY'
import pathlib, sys
pairs = dict(line.split("=",1) for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if "=" in line)
secret = pairs.get("CONTAS_DB_PASSWORD","")
if len(secret) < 32 or "\n" in secret or "\r" in secret:
    raise SystemExit("ERRO: credencial central.env invalida.")
PY

echo "Verificando definicao isolada do banco..."
docker compose --env-file "$SECRETS" -f "$COMPOSE" config --quiet

echo "Iniciando SOMENTE o banco do Controle de Contas..."
docker compose --env-file "$SECRETS" -f "$COMPOSE" up -d contas-db

echo "Aguardando banco saudavel..."
container_id="$(docker compose --env-file "$SECRETS" -f "$COMPOSE" ps -q contas-db)"
[[ -n "$container_id" ]] || { echo "ERRO: container do banco nao encontrado."; exit 1; }
healthy=""
for i in $(seq 1 40); do
  healthy="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
  if [[ "$healthy" == "healthy" ]]; then break; fi
  [[ "$healthy" == "unhealthy" ]] && break
  sleep 2
done
[[ "$healthy" == "healthy" ]] || {
  echo "ERRO: o banco ainda nao esta saudavel (status: ${healthy:-desconhecido})."
  echo "Examine APENAS logs do container do Contas antes de prosseguir."
  exit 1
}

echo "BANCO CENTRAL CONTAS: ONLINE e isolado (sem porta publica)."
echo "IMPORTANTE: banco novo vazio. Nenhum dado Windows foi migrado, e o PC continua sendo a origem atual."
echo "Nenhum serviço GFARM, PLUV, Caddy ou Cloudflare foi alterado."
