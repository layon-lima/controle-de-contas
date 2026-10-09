param()
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $Root "portable_app"
$Launcher = Join-Path $AppDir "launcher.py"
$StartLog = Join-Path $env:LOCALAPPDATA "ControleContas\portable_startup.log"
$ProgressPreference = "SilentlyContinue"

function Progress([int]$Percent, [string]$Title) {
    Write-Host "CC_PROGRESS|$Percent|$Title"
}
function Check($Command, $ArgsList, $Message) {
    & $Command @ArgsList
    if ($LASTEXITCODE -ne 0) { throw $Message }
}

if (!(Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw "O iniciador portatil nao foi encontrado. A sincronizacao com GitHub e necessaria."
}

Progress 18 "Conferindo componentes do aplicativo portatil"
$PythonPath = (& py -3.13 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or !$PythonPath) {
    throw "Python 3.13 nao encontrado. O sistema instalado continua preservado."
}
$PythonPath = $PythonPath.Trim()
Write-Host "Python: $PythonPath"

Progress 25 "Validando integridade do codigo local"
Check $PythonPath @("-X", "utf8", "-m", "py_compile", (Join-Path $AppDir "server.py"), $Launcher) "Erro no codigo Python portatil."
Check $PythonPath @("-X", "utf8", "-m", "unittest", "discover", "-s", $AppDir, "-p", "test_*.py", "-v") "O aplicativo portatil nao passou nos testes. Nenhum dado foi alterado."
# Testes isolados usam TEMP e nao acessam o banco real.
Progress 50 "Testes do aplicativo portatil aprovados"

$TargetCommit = (& git -C $Root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or !$TargetCommit) { throw "Nao foi possivel confirmar a revisao GitHub." }

Progress 68 "Iniciando a versao portatil sem instalacao"
$StartDir = Split-Path $StartLog -Parent
New-Item -ItemType Directory -Force -Path $StartDir | Out-Null
# O launcher reinicia APENAS o servidor portatil, e nao o app instalado.
# Nao abrir console; manter um unico processo Python com logs em arquivo.
$Process = Start-Process -FilePath $PythonPath -ArgumentList @(
    "-u", ('"' + $Launcher + '"')
) -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $StartLog -ErrorAction Stop

Progress 77 "Aguardando o servidor portatil ficar disponivel"
$Online = $false
for ($i=0; $i -lt 90; $i++) {
    Start-Sleep -Milliseconds 400
    try {
        $Response = Invoke-RestMethod -Uri "http://127.0.0.1:8767/health" -TimeoutSec 2
        if ($Response.online -eq $true -and $Response.revision -eq $TargetCommit) {
            $Online = $true
            break
        }
    } catch {
        # O processo ainda esta iniciando/reiniciando.
    }
    if ($Process.HasExited) {
        Write-Host "O iniciador encerrou com codigo $($Process.ExitCode)."
        break
    }
}
if (-not $Online) {
    if (Test-Path $StartLog) { Get-Content $StartLog -Tail 30 | Out-Host }
    throw "O aplicativo portatil nao respondeu com a revisao atual. Nao sera marcado como atualizado."
}
Progress 88 "Controle de Contas portatil atualizado e aberto"
Write-Host "APP_PORTATIL_OK|$TargetCommit|http://127.0.0.1:8767/"
