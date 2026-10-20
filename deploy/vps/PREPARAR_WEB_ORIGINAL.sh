#!/usr/bin/env bash
# Constroi o MESMO aplicativo web portátil do PC para Linux, sem arrancar
# dados do Windows, sem publicar porta externa e sem criar banco vazio.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
[[ "$(git branch --show-current)" == "main" ]] || {
  echo "ERRO: execute pela main."; exit 1;
}
command -v docker >/dev/null && docker compose version >/dev/null
mkdir -p /opt/apps/contas-runtime/data
COMPOSE=deploy/vps/compose.portable-web.yml
echo "Construindo o app web ORIGINAL do Windows para a VPS..."
docker compose -f "$COMPOSE" build contas-web
echo "Imagem preparada. Nao foi criado banco vazio nem modificada qualquer conta."
if [[ ! -s /opt/apps/contas-runtime/data/controle_contas.db ]]; then
  echo "BANCO AINDA NO WINDOWS. Nao iniciar app com dados vazios."
  echo "Falta transferir o arquivo ORIGINAL de banco SQLite para:"
  echo "/opt/apps/contas-runtime/data/controle_contas.db"
  echo "Depois: bash deploy/vps/INICIAR_WEB_ORIGINAL.sh"
  exit 0
fi
echo "Banco no diretorio VPS encontrado. Inicializacao exige confirmacao de integridade."
