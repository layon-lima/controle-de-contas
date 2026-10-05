$ErrorActionPreference = "Stop"

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
Remove-ItemProperty -Path $RunKey -Name "ControleContasPainel" -ErrorAction SilentlyContinue

$AppData = Join-Path $env:LOCALAPPDATA "ControleContas"
$Launcher = Join-Path $AppData "start_panel.vbs"
$OpenLauncher = Join-Path $AppData "open_panel.vbs"
Remove-Item $Launcher -Force -ErrorAction SilentlyContinue
Remove-Item $OpenLauncher -Force -ErrorAction SilentlyContinue

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Controle de Contas - Painel.lnk"
$LegacyUrl = Join-Path $Desktop "Controle de Contas - Painel.url"
Remove-Item $ShortcutPath -Force -ErrorAction SilentlyContinue
Remove-Item $LegacyUrl -Force -ErrorAction SilentlyContinue

Write-Host "Inicializacao automatica e atalho do painel removidos." -ForegroundColor Green