$ErrorActionPreference = "Stop"

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $RunKey -Name "ControleContasPainel" -ErrorAction SilentlyContinue

$AppData = Join-Path $env:LOCALAPPDATA "ControleContas"
$Launcher = Join-Path $AppData "start_panel.vbs"
Remove-Item $Launcher -Force -ErrorAction SilentlyContinue

$Desktop = [Environment]::GetFolderPath("Desktop")
$UrlFile = Join-Path $Desktop "Controle de Contas - Painel.url"
Remove-Item $UrlFile -Force -ErrorAction SilentlyContinue

Write-Host "Inicializacao automatica e atalho do painel removidos." -ForegroundColor Green