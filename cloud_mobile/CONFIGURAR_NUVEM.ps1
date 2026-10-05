$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CloudDir = $PSScriptRoot
$AppDataDir = Join-Path $env:LOCALAPPDATA "ControleContas"
$CloudConfig = Join-Path $AppDataDir "cloud_sync.json"
$OldMobileConfig = Join-Path $AppDataDir "web_mobile_config.json"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " CONTROLE DE CONTAS - CONFIGURACAO DA NUVEM" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js nao foi encontrado. Pare aqui e me envie esta mensagem; nao instale nada por conta propria ainda."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm nao foi encontrado. Pare aqui e me envie esta mensagem."
}

Set-Location $CloudDir
Write-Host "[1/8] Instalando Wrangler local..." -ForegroundColor Yellow
npm install --no-audit --no-fund

Write-Host "[2/8] Abrindo login da Cloudflare..." -ForegroundColor Yellow
Write-Host "O navegador pode abrir. Autorize a conta que controla fazendanovohorizonte.com." -ForegroundColor DarkGray
npx wrangler login

Write-Host "[3/8] Criando banco D1..." -ForegroundColor Yellow
$d1Output = (& npx wrangler d1 create controle-contas-cloud 2>&1 | Out-String)
Write-Host $d1Output
$match = [regex]::Match($d1Output, 'database_id\s*=\s*["'']([0-9a-fA-F-]{36})["'']')
if (-not $match.Success) {
    $match = [regex]::Match($d1Output, '"database_id"\s*:\s*"([0-9a-fA-F-]{36})"')
}
if (-not $match.Success) {
    throw "Nao consegui obter o ID do D1 automaticamente. Me envie a saida exibida acima."
}
$d1Id = $match.Groups[1].Value

$wranglerPath = Join-Path $CloudDir "wrangler.toml"
$wranglerText = Get-Content $wranglerPath -Raw
$wranglerText = $wranglerText.Replace("__D1_DATABASE_ID__", $d1Id)
Set-Content -Path $wranglerPath -Value $wranglerText -Encoding UTF8

Write-Host "[4/8] Criando armazenamento privado de PDFs (R2)..." -ForegroundColor Yellow
& npx wrangler r2 bucket create controle-contas-pdfs 2>&1 | Out-Host


Write-Host "[5/8] Criando tabelas da nuvem..." -ForegroundColor Yellow
npx wrangler d1 migrations apply controle-contas-cloud --remote

Write-Host "[6/8] Publicando painel web temporario em workers.dev..." -ForegroundColor Yellow
$deployOutput = (& npx wrangler deploy 2>&1 | Out-String)
Write-Host $deployOutput
$urlMatch = [regex]::Match($deployOutput, 'https://[^\s\x1b]+\.workers\.dev')
if (-not $urlMatch.Success) {
    throw "Nao consegui identificar o endereco workers.dev. Me envie a saida de deploy acima."
}
$BaseUrl = $urlMatch.Value.TrimEnd('/')

function New-SafeToken([int]$Bytes = 40) {
    $raw = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($raw)
    return [Convert]::ToBase64String($raw).TrimEnd('=').Replace('+','-').Replace('/','_')
}

$ViewToken = $null
if (Test-Path $OldMobileConfig) {
    try {
        $old = Get-Content $OldMobileConfig -Raw | ConvertFrom-Json
        if ($old.view_token -and $old.view_token.Length -ge 32) { $ViewToken = [string]$old.view_token }
    } catch {}
}
if (-not $ViewToken) { $ViewToken = New-SafeToken 40 }
$SyncToken = New-SafeToken 48

Write-Host "[7/8] Gravando chaves privadas no Worker..." -ForegroundColor Yellow
$ViewToken | npx wrangler secret put VIEW_TOKEN | Out-Host
$SyncToken | npx wrangler secret put SYNC_TOKEN | Out-Host

New-Item -ItemType Directory -Path $AppDataDir -Force | Out-Null
$config = [ordered]@{
    enabled = $true
    base_url = $BaseUrl
    sync_token = $SyncToken
    view_token = $ViewToken
    configured_at = (Get-Date).ToString("o")
}
$config | ConvertTo-Json | Set-Content -Path $CloudConfig -Encoding UTF8

$privateLink = "$BaseUrl/#$ViewToken"
@"
CONTROLE DE CONTAS - ACESSO MOBILE NA NUVEM

LINK PRIVATIVO:
$privateLink

Este link funciona mesmo quando o computador estiver desligado, usando a ultima sincronizacao enviada para a nuvem.
Nao compartilhe publicamente.
"@ | Set-Content -Path (Join-Path $AppDataDir "ACESSO_MOBILE_CLOUD.txt") -Encoding UTF8

Write-Host "[8/8] Configuracao concluida." -ForegroundColor Green
Write-Host ""
Write-Host "ENDERECO TEMPORARIO PARA TESTE:" -ForegroundColor Cyan
Write-Host $privateLink -ForegroundColor Green
Write-Host ""
Write-Host "Agora volte para a raiz do projeto e execute: python .\app.py" -ForegroundColor Yellow
Write-Host "Na primeira sincronizacao, os PDFs podem levar alguns minutos." -ForegroundColor DarkGray
