#!/usr/bin/env bash
# Inicio somente do aplicativo web original APOS haver banco real transferido.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
DATABASE=/opt/apps/contas-runtime/data/controle_contas.db
COMPOSE=deploy/vps/compose.portable-web.yml
[[ -s "$DATABASE" ]] || { echo "ERRO: banco REAL nao encontrado na VPS. Nada iniciado."; exit 1; }
python3 - "$DATABASE" <<'PY'
import sqlite3,sys
p=sys.argv[1]
with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as db:
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise SystemExit("ERRO: banco SQLite nao passou na integridade.")
    names={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"contas","pagamentos"}.issubset(names):
        raise SystemExit("ERRO: banco recebido sem tabelas contas e pagamentos.")
    print("BANCO ORIGINAL NA VPS: contas=",db.execute("SELECT count(*) FROM contas").fetchone()[0],
          " pagamentos=",db.execute("SELECT count(*) FROM pagamentos").fetchone()[0])
PY
# Preserva a origem HTTPS salva pela publicacao em futuras atualizacoes.
SITE_ENV=/opt/apps/contas-runtime/public-site.env
if [[ -f "$SITE_ENV" ]]; then
  PUBLIC_ORIGIN="$(sed -n 's/^CONTAS_PUBLIC_ORIGIN=//p' "$SITE_ENV" | head -1)"
  PROXY_NETWORK="$(sed -n 's/^CONTAS_CADDY_NETWORK=//p' "$SITE_ENV" | head -1)"
  if [[ "$PUBLIC_ORIGIN" =~ ^https://[a-z0-9.-]+$ ]]; then
    export CONTAS_PUBLIC_ORIGIN="$PUBLIC_ORIGIN"
  else
    echo "ERRO: origem HTTPS salva invalida; nao recriarei o app."; exit 1
  fi
fi
docker compose -f "$COMPOSE" up -d --no-deps contas-web
if [[ -n "${PROXY_NETWORK:-}" ]] && docker network inspect "$PROXY_NETWORK" >/dev/null 2>&1; then
  if ! docker inspect contas-web-original-contas-web-1 --format '{{json .NetworkSettings.Networks}}' | grep -Fq "$PROXY_NETWORK"; then
    docker network connect --alias contas3-backend "$PROXY_NETWORK" contas-web-original-contas-web-1
  fi
fi
printf 'Testando http://127.0.0.1:18769/health ...\n'
for i in $(seq 1 30); do
  if curl -fsS --noproxy '*' --max-time 3 http://127.0.0.1:18769/health; then
    echo
    echo "APP WEB ORIGINAL: ativo no servidor."
    if [[ -n "${CONTAS_PUBLIC_ORIGIN:-}" ]]; then
      echo "Acesso publico protegido configurado: $CONTAS_PUBLIC_ORIGIN"
    else
      echo "Ainda restrito ao localhost VPS:18769."
    fi
    exit 0
  fi
  sleep 2
done
echo "ERRO: veja docker logs --tail=60 contas-web-original-contas-web-1"
exit 1
