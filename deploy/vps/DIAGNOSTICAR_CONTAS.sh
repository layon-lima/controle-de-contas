#!/usr/bin/env bash
# Controle de Contas — diagnostico seguro e SOMENTE LEITURA na VPS.
# Nao mostra variaveis de ambiente, tokens, dados financeiros ou PDFs.
# Uso: bash deploy/vps/DIAGNOSTICAR_CONTAS.sh
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOMAIN="https://contas-cloud.fazendanovohorizonte.com"
echo "===== CONTAS 3: DIAGNOSTICO VPS (SOMENTE LEITURA) ====="
printf 'Data UTC: '; date -u '+%Y-%m-%d %H:%M:%S' 2>/dev/null || true
printf 'Sistema: '; uname -srm 2>/dev/null || true
echo
echo "--- Git do Controle de Contas ---"
if [[ -d "$ROOT/.git" ]] && command -v git >/dev/null 2>&1; then
  printf 'Branch: '; git -C "$ROOT" branch --show-current 2>/dev/null || true
  printf 'Commit: '; git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true
  printf 'Arquivos alterados: '; git -C "$ROOT" status --porcelain 2>/dev/null | wc -l
else
  echo "Repositorio Git nao localizado em $ROOT"
fi
echo
echo "--- Servicos exclusivos do Controle de Contas ---"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker ps -a --filter 'name=contas' --format 'Container={{.Names}} Status={{.Status}}' 2>/dev/null || true
else
  echo "Docker indisponivel ou sem permissao."
fi
if command -v systemctl >/dev/null 2>&1; then
  printf 'Caddy systemd: '; systemctl is-active caddy 2>/dev/null || true
fi
if [[ -f /etc/caddy/Caddyfile ]]; then
  if grep -qF 'contas-cloud.fazendanovohorizonte.com' /etc/caddy/Caddyfile; then
    echo "Rota Contas no Caddyfile: SIM (nao exibe configuracao)"
  else
    echo "Rota Contas no Caddyfile: NAO"
  fi
else
  echo "Caddyfile padrao: nao localizado."
fi
echo
echo "--- Broker privado atual ---"
if command -v curl >/dev/null 2>&1; then
  code="$(curl --noproxy '*' -sS -o /dev/null --max-time 5 -w '%{http_code}' 'http://127.0.0.1:18767/health' 2>/dev/null || true)"
  echo "Broker localhost:18767 /health HTTP ${code:-000} (esperado 200 se instalado)"
else
  echo "curl indisponivel."
fi
echo
echo "--- Dominio publico e APIs ---"
if command -v getent >/dev/null 2>&1; then
  resolved="$(getent ahostsv4 contas-cloud.fazendanovohorizonte.com 2>/dev/null | head -n 1 | awk '{print $1}')"
  if [[ -n "$resolved" ]]; then echo "DNS: resolvido"; else echo "DNS: falha ao resolver"; fi
fi
if command -v curl >/dev/null 2>&1; then
  for endpoint in / /api/health /api/contas /api/manifest /app.js; do
    status="$(curl -sSL -o /dev/null --connect-timeout 5 --max-time 12 -w '%{http_code} | tipo %{content_type}' "$DOMAIN$endpoint" 2>/dev/null || true)"
    echo "$endpoint -> ${status:-000}"
  done
fi
echo
echo "--- Codigo presente na VPS ---"
for file in \
  "$ROOT/cloud_mobile/src/index.js" \
  "$ROOT/cloud_mobile/wrangler.toml" \
  "$ROOT/deploy/vps/broker.py" \
  "$ROOT/portable_app/server.py" \
  "$ROOT/deploy/vps/compose.broker.yml"; do
  if [[ -f "$file" ]]; then echo "${file#"$ROOT"/}: SIM"; else echo "${file#"$ROOT"/}: NAO"; fi
done
echo
echo "FIM. Nenhuma configuracao, arquivo, banco, container ou servico foi alterado."
