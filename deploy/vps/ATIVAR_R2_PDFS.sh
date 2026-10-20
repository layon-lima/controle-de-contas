#!/usr/bin/env bash
# Habilita os arquivos R2 NO APP ORIGINAL, sem alterar banco nem publicar tokens.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
CONF=/opt/apps/contas-runtime/r2-agent.json
[[ -s "$CONF" ]] || { echo "ERRO: primeiro execute python3 deploy/vps/CONFIGURAR_R2.py"; exit 1; }
[[ -s /opt/apps/contas-runtime/data/controle_contas.db ]] || {
  echo "ERRO: banco SQLite original da VPS ausente."; exit 1;
}
echo "Compilando o mesmo Controle de Contas com acesso aos PDFs do R2..."
docker compose -f deploy/vps/compose.portable-web.yml build contas-web

echo "Verificando credenciais Cloudflare R2 antes de reiniciar o app..."
docker compose -f deploy/vps/compose.portable-web.yml run --rm --no-deps \
  --entrypoint python contas-web -c '
from portable_app import r2_bridge
import secrets
cfg=r2_bridge._config()
assert cfg is not None, "Arquivo privado R2 invalido"
client=r2_bridge._client(cfg)
key="contas3-health/"+secrets.token_hex(18)+".txt"
client.put_object(Bucket=cfg["bucket"],Key=key,Body=b"R2 ok",ContentType="text/plain")
try:
    item=client.get_object(Bucket=cfg["bucket"],Key=key)
    assert item["Body"].read()==b"R2 ok"
finally:
    client.delete_object(Bucket=cfg["bucket"],Key=key)
print("R2: acesso confirmado, escrita/leitura/exclusao OK")
'

echo "Reiniciando somente o Controle de Contas com imagem verificada..."
bash deploy/vps/INICIAR_WEB_ORIGINAL.sh

echo "Consultando lista de PDFs pendentes pela API privada do agente..."
python3 - <<'PY'
import json
import urllib.request
from pathlib import Path
token=Path("/opt/apps/contas-private/r2-agent-token").read_text().strip()
req=urllib.request.Request("http://127.0.0.1:18769/api/agent/pending",
    headers={"X-Agent-Token":token})
with urllib.request.urlopen(req,timeout=90) as resp:
    data=json.load(resp)
assert data.get("ok") and isinstance(data.get("pending"),list)
print("CONTAS ABERTAS RECONHECIDAS:", len(data["pending"]))
print("R2 PRONTO: falta somente o sincronizador do Windows enviar os arquivos.")
PY
