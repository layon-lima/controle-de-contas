#!/usr/bin/env bash
# Publica SOMENTE a PWA mobile do Controle de Contas no Cloudflare.
# Executar pela VPS: bash deploy/vps/PUBLICAR_MOBILE_CLOUDFLARE.sh
# Credenciais Cloudflare ficam no conteiner temporario, NAO na VPS.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOBILE="$ROOT/cloud_mobile"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERRO: Docker nao encontrado." >&2
  exit 1
fi
if [[ "$(git -C "$ROOT" branch --show-current)" != "main" ]]; then
  echo "ERRO: execute somente a partir da branch main." >&2
  exit 1
fi
if [[ -n "$(git -C "$ROOT" status --porcelain -- cloud_mobile)" ]]; then
  echo "ERRO: existem alteracoes locais em cloud_mobile; nao vou sobrescreve-las." >&2
  git -C "$ROOT" status --short -- cloud_mobile
  exit 1
fi
if [[ ! -f "$MOBILE/wrangler.toml" || ! -f "$MOBILE/package-lock.json" ]]; then
  echo "ERRO: projeto Worker incompleto." >&2
  exit 1
fi
for required in \
  'name = "controle-contas-cloud"' \
  'pattern = "contas-cloud.fazendanovohorizonte.com"' \
  'bucket_name = "controle-contas-pdfs"' \
  'database_name = "controle-contas-cloud"'; do
  if ! grep -Fq "$required" "$MOBILE/wrangler.toml"; then
    echo "ERRO: binding ou dominio Cloudflare diferente do esperado: $required" >&2
    exit 1
  fi
done

printf '\nPUBLICACAO MOBILE CLOUDFLARE\n'
printf 'Repositorio: %s\n' "$ROOT"
printf 'Commit main: %s\n' "$(git -C "$ROOT" rev-parse --short HEAD)"
printf 'Dominio: https://contas-cloud.fazendanovohorizonte.com\n'
printf 'Nao altera Caddy, GFARM, PLUV, conteiner broker nem PDFs Windows.\n'
printf 'O login Cloudflare sera autorizado no seu navegador usando codigo temporario.\n\n'

# 'docker run --rm': ambiente de deploy e OAuth descartados ao encerrar.
docker run --rm -it \
  --mount "type=bind,source=$MOBILE,target=/work" \
  --workdir /work \
  node:24-bookworm bash -euc '
    npm ci --no-audit --no-fund
    node --test tests/r2_paid_policy.test.mjs
    npx wrangler deploy --dry-run --keep-vars

    echo
    echo "AUTORIZACAO: abra no navegador o endereco indicado abaixo."
    echo "Use a MESMA conta Cloudflare do dominio fazendanovohorizonte.com."
    echo "O codigo temporario expira em cerca de 5 minutos."
    npx wrangler login --device --browser=false

    echo
    echo "Conta Cloudflare autenticada:"
    npx wrangler whoami

    echo
    echo "Conferindo o Worker ja existente; nao criar outro Worker:"
    npx wrangler deployments list --name controle-contas-cloud

    echo
    echo "Destino: Worker EXISTENTE controle-contas-cloud"
    echo "Rota: https://contas-cloud.fazendanovohorizonte.com"
    read -r -p "Digite PUBLICAR para atualizar o sistema mobile: " approval
    if [[ "$approval" != "PUBLICAR" ]]; then
      echo "Cancelado: nenhuma atualizacao publicada."
      exit 1
    fi

    npx wrangler deploy --keep-vars
    echo
    echo "Cloudflare: deploy do mobile concluido. Confira o site no navegador."
  '
