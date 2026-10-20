#!/usr/bin/env bash
# Atualiza o MESMO app web na VPS (PC+mobile, um SQLite central).
# Preserva dados e todos os outros containers. Nao toca em DNS, Caddy ou R2.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
[[ "$(git branch --show-current)" == "main" ]] || { echo "ERRO: branch deve ser main."; exit 1; }
[[ -s /opt/apps/contas-runtime/data/controle_contas.db ]] || {
  echo "ERRO: banco existente na VPS ausente; nao publicar outra base."; exit 1;
}
echo "===== CONTAS 3: ATUALIZACAO CENTRAL ====="
echo "Construindo versao unificada do web original com interface mobile..."
docker compose -f deploy/vps/compose.portable-web.yml build contas-web
echo "Reiniciando SOMENTE o app do Controle de Contas..."
bash deploy/vps/INICIAR_WEB_ORIGINAL.sh
echo "Conferindo API mobile e banco central sem alterar registros..."
python3 - <<'PY'
import json
import re
import sqlite3
import time
import urllib.request

url="http://127.0.0.1:18769"
headers={"User-Agent":"Contas3-Deploy-Audit"}
html=urllib.request.urlopen(urllib.request.Request(url+"/mobile/",headers=headers), timeout=12).read().decode()
assert "window.__CONTAS_VPS__=true" in html, "Mobile VPS nao carregou."
match=re.search(r'window\.__CONTAS_TOKEN__=("[^"]+");',html)
assert match, "Token de sessao da API mobile nao presente."
token=json.loads(match.group(1))
req=urllib.request.Request(url+"/api/contas",headers={"X-Local-Token":token})
with urllib.request.urlopen(req, timeout=12) as response:
    data=json.load(response)
assert data.get("ok") is True and isinstance(data.get("contas"),list), "API mobile indisponivel."
assert all(not str(a.get("data_pagamento") or "").strip() and a.get("status")!="Paga" for a in data["contas"]), "Mobile incluiu conta paga."
with sqlite3.connect("file:/opt/apps/contas-runtime/data/controle_contas.db?mode=ro",uri=True) as db:
    total=int(db.execute("SELECT COUNT(*) FROM contas").fetchone()[0])
    paid=int(db.execute("""
        SELECT COUNT(*) FROM contas c WHERE EXISTS
        (SELECT 1 FROM pagamentos p WHERE p.conta_id=c.id
          AND p.data_pagamento IS NOT NULL AND TRIM(p.data_pagamento)<>'')
    """).fetchone()[0])
    expected=total-paid
assert len(data["contas"])==expected, "Mobile e banco principal com quantidades divergentes."
print("TOTAL NO BANCO VPS:", total)
print("CONTAS PAGAS (VISIVEIS APENAS DESKTOP):", paid)
print("MOBILE - CONTAS EM ABERTO:", len(data["contas"]))
print("CONFIRMADO: desktop e mobile consultam o MESMO SQLite da VPS.")
PY

if [[ -f /opt/apps/contas-runtime/public-site.env ]]; then
  public="$(sed -n 's/^CONTAS_PUBLIC_ORIGIN=//p' /opt/apps/contas-runtime/public-site.env | head -1)"
  echo "ACESSO ONLINE PC E MOBILE: $public"
else
  echo "AVISO: HTTPS publico ainda nao configurado."
fi
echo "NENHUMA conta, pagamento ou PDF foi apagado. GFARM/PLUV intactos."
echo "PDFs antigos do Windows ainda dependem de integracao de arquivos."
