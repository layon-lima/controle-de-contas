$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

Write-Host ""
Write-Host "[1/6] Importando e corrigindo a fonte cloud..." -ForegroundColor Cyan
py -3.13 ".\tools\fix_mobile_permanent_access.py"
if ($LASTEXITCODE -ne 0) { throw "Falha ao preparar a cloud." }

Write-Host "[2/6] Registrando a fonte no GitHub..." -ForegroundColor Cyan
git add cloud_mobile tools/fix_mobile_permanent_access.py tools/DEPLOY_MOBILE_PERMANENT_ACCESS.ps1
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "Mobile: acesso permanente por dispositivo"
    if ($LASTEXITCODE -ne 0) { throw "Falha no commit." }
    git push origin develop
    if ($LASTEXITCODE -ne 0) { throw "Falha ao enviar para o GitHub." }
} else {
    Write-Host "Nenhuma alteracao nova para commit." -ForegroundColor DarkGray
}

Write-Host "[3/6] Instalando dependencias do Worker..." -ForegroundColor Cyan
Set-Location (Join-Path $Root "cloud_mobile")
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "Falha no npm install." }

Write-Host "[4/6] Aplicando migracao D1..." -ForegroundColor Cyan
npx wrangler d1 migrations apply controle-contas-cloud --remote
if ($LASTEXITCODE -ne 0) { throw "Falha na migracao D1." }

Write-Host "[5/6] Publicando Worker + PWA..." -ForegroundColor Cyan
npx wrangler deploy
if ($LASTEXITCODE -ne 0) { throw "Falha no deploy." }

Write-Host "[6/6] Concluido." -ForegroundColor Green
Write-Host ""
Write-Host "NOVO COMPORTAMENTO:" -ForegroundColor Green
Write-Host "- cada aparelho autorizado recebe uma credencial permanente propria;"
Write-Host "- nao existe expiracao por tempo no D1;"
Write-Host "- o PWA consegue restaurar a sessao mesmo se o cookie expirar;"
Write-Host "- um 401 nao apaga mais a credencial local automaticamente;"
Write-Host ""
Write-Host "Aparelhos que ainda estao funcionando migram automaticamente."
Write-Host "Se algum aparelho JA estiver bloqueado, abra o link privativo nele UMA ULTIMA VEZ."
