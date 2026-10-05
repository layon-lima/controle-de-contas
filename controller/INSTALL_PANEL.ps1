$ErrorActionPreference = "Stop"

$Projeto = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Server = Join-Path $Projeto "controller\server.py"
if (!(Test-Path $Server)) { throw "server.py nao encontrado em $Server" }

$AppData = Join-Path $env:LOCALAPPDATA "ControleContas"
New-Item -ItemType Directory -Force -Path $AppData | Out-Null
$Launcher = Join-Path $AppData "start_panel.vbs"

$Vbs = @"
Set shell = CreateObject("WScript.Shell")
cmd = "cmd /c cd /d ""$Projeto"" && py -3.13 ""$Server"" --no-browser"
shell.Run cmd, 0, False
"@
Set-Content -Path $Launcher -Value $Vbs -Encoding ASCII

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $RunKey -Force | Out-Null
Set-ItemProperty -Path $RunKey -Name "ControleContasPainel" -Value "wscript.exe `"$Launcher`""

$Desktop = [Environment]::GetFolderPath("Desktop")
$UrlFile = Join-Path $Desktop "Controle de Contas - Painel.url"
$UrlContent = @"
[InternetShortcut]
URL=http://127.0.0.1:8765
IconIndex=0
"@
Set-Content -Path $UrlFile -Value $UrlContent -Encoding ASCII

Start-Process -FilePath "wscript.exe" -ArgumentList "`"$Launcher`"" -WindowStyle Hidden
Start-Sleep -Milliseconds 900

Write-Host ""
Write-Host "Painel instalado com sucesso." -ForegroundColor Green
Write-Host "Inicializacao automatica: ATIVA"
Write-Host "Atalho criado na Area de Trabalho: Controle de Contas - Painel"
Write-Host "Endereco: http://127.0.0.1:8765"
Write-Host ""
Write-Host "A partir de agora o painel inicia sozinho com o Windows."