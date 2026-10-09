$ErrorActionPreference = "Stop"
# Publica somente o Worker/PWA atual. Nao recria banco, buckets, tokens
# nem chama scripts que reescrevem a fonte antes de publicar.
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CloudDir = Join-Path $Root "cloud_mobile"
$Config = Join-Path $env:LOCALAPPDATA "ControleContas\cloud_sync.json"
if (!(Test-Path (Join-Path $CloudDir "src\index.js"))) { throw "Worker ausente. Sincronize o painel." }
if (!(Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js nao encontrado." }
if (!(Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm nao encontrado." }
Set-Location $CloudDir

Write-Host "[1/5] Instalando dependencias locais..." -ForegroundColor Cyan
npm ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "Falha no npm ci." }

Write-Host "[2/5] Conferindo login Cloudflare..." -ForegroundColor Cyan
& npx --no-install wrangler whoami
if ($LASTEXITCODE -ne 0) { throw "Wrangler sem login. Autorize a conta Cloudflare e tente novamente." }

Write-Host "[3/5] Aplicando somente migracoes D1 pendentes..." -ForegroundColor Cyan
& npx --no-install wrangler d1 migrations apply controle-contas-cloud --remote
if ($LASTEXITCODE -ne 0) { throw "Falha ao aplicar migracoes D1." }

Write-Host "[4/5] Publicando Worker e PWA atualizados..." -ForegroundColor Cyan
& npx --no-install wrangler deploy
if ($LASTEXITCODE -ne 0) { throw "Falha ao publicar Worker." }

Write-Host "[5/5] Conferindo rota de sugestoes..." -ForegroundColor Cyan
if (Test-Path $Config) {
    $cfg = Get-Content $Config -Raw | ConvertFrom-Json
    $base = ([string]$cfg.base_url).TrimEnd('/')
    $token = [string]$cfg.sync_token
    if ($base -and $token) {
        $headers = @{"X-Sync-Token" = $token}
        $reply = Invoke-RestMethod -Method POST -Uri "$base/api/desktop/suggest-batch" -Headers $headers -ContentType "application/json; charset=utf-8" -Body '{"account_ids":[]}' -TimeoutSec 30
        if (-not $reply.ok) { throw "A rota de sugestoes nao respondeu corretamente." }
        Write-Host "Sugestao em lote: rota OK" -ForegroundColor Green
    } else {
        Write-Host "Configuracao cloud incompleta, sem teste remoto." -ForegroundColor Yellow
    }
} else {
    Write-Host "cloud_sync.json ausente: teste remoto omitido." -ForegroundColor Yellow
}
Write-Host "Worker publicado. Banco, bucket e credenciais preservados." -ForegroundColor Green
