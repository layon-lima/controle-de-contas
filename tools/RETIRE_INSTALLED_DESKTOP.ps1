# Retira APENAS a instalacao EXE obsoleta, apos a web e o OCR independente
# passarem nos testes. Protege SQLite, PDFs, configuracoes e perfis OCR.
param()
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LegacyDir = Join-Path $env:LOCALAPPDATA "Programs\Controle de Contas"
$DataDir = Join-Path $env:LOCALAPPDATA "ControleContas"
$Reader = Join-Path $DataDir "portable_reader_engine"
$Tesseract = Join-Path $Reader "tesseract\tesseract.exe"
$Portuguese = Join-Path $Reader "tesseract\tessdata\por.traineddata"
$Desktop = [Environment]::GetFolderPath("Desktop")
$LegacyShortcut = Join-Path $Desktop "Controle de Contas - Versao instalada (reserva).lnk"
$WebShortcut = Join-Path $Desktop "Controle de Contas.lnk"

if (-not (Test-Path -LiteralPath $LegacyDir -PathType Container)) {
    if (Test-Path -LiteralPath $LegacyShortcut) {
        Remove-Item -LiteralPath $LegacyShortcut -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Versao antiga ja ausente; aplicacao web continua como principal."
    exit 0
}
if (-not (Test-Path -LiteralPath $WebShortcut -PathType Leaf)) {
    Write-Host "AVISO: atalho web nao confirmado, nao desinstalando a versao antiga."
    exit 0
}
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($WebShortcut)
if ($Link.Arguments -notlike "*ABRIR_CONTROLE_DE_CONTAS.vbs*") {
    Write-Host "AVISO: o atalho principal ainda nao aponta para a versao web."
    exit 0
}
# O motor recuperado deve continuar funcional APOS a remocao do EXE.
$Required = @(
    (Join-Path $Reader "controle_contas\pdf_reader.pyc"),
    (Join-Path $Reader "controle_contas\ui.pyc"),
    (Join-Path $Reader "controle_contas\patches.py"),
    (Join-Path $Reader "controle_contas\layout_learning_v2.py"),
    $Tesseract, $Portuguese
)
foreach ($File in $Required) {
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        Write-Host "AVISO: OCR independente incompleto ($File). App antigo preservado."
        exit 0
    }
}
# Nao fechar processos do usuario, nem desinstalar enquanto o EXE estiver aberto.
$Running = @(Get-Process -Name "Controle de Contas" -ErrorAction SilentlyContinue)
if ($Running.Count -gt 0) {
    Write-Host "AVISO: aplicativo instalado ainda em execucao; feche-o e execute outra atualizacao."
    exit 0
}
$Uninstall = Join-Path $LegacyDir "unins000.exe"
if (-not (Test-Path -LiteralPath $Uninstall -PathType Leaf)) {
    Write-Host "AVISO: desinstalador antigo nao encontrado. Sem excluir arquivos manualmente."
    exit 0
}
if (-not (Test-Path -LiteralPath (Join-Path $DataDir "data\controle_contas.db") -PathType Leaf)) {
    Write-Host "AVISO: banco de dados compartilhado nao localizado. Desinstalacao cancelada."
    exit 0
}
Write-Host "Retirando somente o programa EXE anterior (banco, PDFs e OCR preservados)..."
$Completed = Start-Process -FilePath $Uninstall -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"
) -Wait -PassThru -ErrorAction Stop
if ($Completed.ExitCode -ne 0) {
    Write-Host "AVISO: desinstalador retornou codigo $($Completed.ExitCode). Dados intactos."
    exit 0
}
if (Test-Path -LiteralPath (Join-Path $LegacyDir "Controle de Contas.exe")) {
    Write-Host "AVISO: executavel antigo ainda esta presente. Verifique a desinstalacao."
    exit 0
}
Remove-Item -LiteralPath $LegacyShortcut -Force -ErrorAction SilentlyContinue
Write-Host "Versao instalada retirada. Banco e PDFs continuam em $DataDir"
