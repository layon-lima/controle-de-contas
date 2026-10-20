#!/usr/bin/env bash
# Instala SOMENTE a API privada do Contas, reaproveitando PostgreSQL ja preparado.
# Preserva todos os dados. NAO muda DNS, Caddy, GFARM, PLUV, R2, nem banco Windows.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE="$ROOT/deploy/vps/compose.central.yml"
SECRETS="$ROOT/deploy/vps/central.env"
cd "$ROOT"
for tool in git docker python3 curl; do
  command -v "$tool" >/dev/null || { echo "ERRO: ferramenta ausente: $tool"; exit 1; }
done
[[ "$(git branch --show-current)" == "main" ]] || {
  echo "ERRO: execute a partir da main."; exit 1;
}
[[ -f "$SECRETS" ]] || {
  echo "ERRO: banco ainda nao preparado (central.env ausente)."; exit 1;
}
python3 - "$SECRETS" <<'PY'
import os, pathlib, secrets, sys
p = pathlib.Path(sys.argv[1])
data = p.read_text()
pairs = dict(line.split("=", 1) for line in data.splitlines() if "=" in line)
if len(pairs.get("CONTAS_DB_PASSWORD", "")) < 32:
    raise SystemExit("ERRO: senha do banco ausente ou invalida.")
if "CONTAS_API_TOKEN" not in pairs:
    token = secrets.token_urlsafe(48)
    with p.open("a", encoding="utf-8") as f:
        if data and not data.endswith("\n"):
            f.write("\n")
        f.write("CONTAS_API_TOKEN=" + token + "\n")
    print("Novo token privado da API criado na VPS (nao exibido).")
elif len(pairs["CONTAS_API_TOKEN"]) < 32:
    raise SystemExit("ERRO: token da API invalido.")
else:
    print("Token privado da API existente preservado.")
os.chmod(p, 0o600)
PY

docker compose --env-file "$SECRETS" -f "$COMPOSE" config --quiet
echo "Iniciando somente PostgreSQL e API exclusivos do Controle de Contas..."
# Recriar APENAS a API para aplicar o mapeamento corrigido da porta.
# O PostgreSQL e seu volume persistente nao sao reiniciados.
docker compose --env-file "$SECRETS" -f "$COMPOSE" up -d --build --no-deps --force-recreate contas-api

# Detectar ausencia de mapeamento imediatamente (antes de aguardar HTTP).
published="$(docker compose --env-file "$SECRETS" -f "$COMPOSE" port contas-api 8768 2>/dev/null || true)"
echo "Porta privada da API: ${published:-NAO PUBLICADA}"
if [[ "$published" != "127.0.0.1:18768" ]]; then
  echo "ERRO: Docker nao publicou a porta 18768 no localhost. Nao altere o banco."
  echo "Confira as redes e o mapeamento exclusivo do Contas."
  exit 1
fi

echo "Aguardando API responder localmente..."
status=""
for i in $(seq 1 40); do
  status="$(curl --noproxy '*' -fsS --connect-timeout 1 --max-time 3 http://127.0.0.1:18768/health 2>/dev/null || true)"
  if [[ "$status" == *'"ok":true'* ]] && [[ "$status" == *'"database":"online"'* ]]; then break; fi
  sleep 2
done
if [[ "$status" != *'"ok":true'* ]]; then
  echo "ERRO: API ainda sem resposta. Consulte logs APENAS do contas-api."
  echo "docker compose --env-file deploy/vps/central.env -f deploy/vps/compose.central.yml logs --tail=50 contas-api"
  exit 1
fi
echo "API PRIVADA CONTAS: ONLINE (127.0.0.1:18768)"
echo "Verificando autenticacao sem exibir token:"
python3 - "$SECRETS" <<'PY'
import json, pathlib, sys, urllib.request
pairs = dict(line.split("=",1) for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if "=" in line)
url = "http://127.0.0.1:18768/api/v1/status"
request = urllib.request.Request(url, headers={"Authorization": "Bearer " + pairs["CONTAS_API_TOKEN"]})
with urllib.request.urlopen(request, timeout=6) as r:
    data = json.load(r)
print("Banco central: conectado.")
print("Registros de contas:", data.get("accounts"))
print("Pagamentos:", data.get("payments"))
print("Migracao concluida:", data.get("migration_complete"))
assert data.get("migration_complete") is False, "Migracao detectada: nao supor estado inicial vazio."
PY
echo "FIM: sem migracao e sem publicar API na internet."
