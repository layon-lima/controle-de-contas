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
docker compose -f "$COMPOSE" up -d --no-deps contas-web
printf 'Testando http://127.0.0.1:18769/health ...\n'
for i in $(seq 1 30); do
  if curl -fsS --noproxy '*' --max-time 3 http://127.0.0.1:18769/health; then
    echo
    echo "APP WEB ORIGINAL: ativo SOMENTE em localhost VPS:18769"
    echo "Ainda NAO publicar externamente antes de proteger acesso e revisar PDFs/OCR."
    exit 0
  fi
  sleep 2
done
echo "ERRO: veja docker logs --tail=60 contas-web-original-contas-web-1"
exit 1
