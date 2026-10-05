$ErrorActionPreference = "Stop"

$Projeto = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Server = Join-Path $Projeto "controller\server.py"
if (!(Test-Path $Server)) { throw "server.py nao encontrado em $Server" }

$AppData = Join-Path $env:LOCALAPPDATA "ControleContas"
New-Item -ItemType Directory -Force -Path $AppData | Out-Null
$Launcher = Join-Path $AppData "start_panel.vbs"
$OpenLauncher = Join-Path $AppData "open_panel.vbs"

$Vbs = @"
Set shell = CreateObject("WScript.Shell")
cmd = "cmd /c cd /d ""$Projeto"" && git pull --ff-only origin develop >nul 2>&1 & py -3.13 ""$Server"" --no-browser"
shell.Run cmd, 0, False
"@
Set-Content -Path $Launcher -Value $Vbs -Encoding ASCII

$OpenVbs = @"
Set shell = CreateObject("WScript.Shell")
shell.Run "wscript.exe ""$Launcher""", 0, False
WScript.Sleep 1800
shell.Run "http://127.0.0.1:8765", 1, False
"@
Set-Content -Path $OpenLauncher -Value $OpenVbs -Encoding ASCII

$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
New-Item -Path $RunKey -Force | Out-Null
Set-ItemProperty -Path $RunKey -Name "ControleContasPainel" -Value "wscript.exe `"$Launcher`""

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Controle de Contas - Painel.lnk"
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
$Shortcut.Arguments = "`"$OpenLauncher`""
$Shortcut.WorkingDirectory = $Projeto
$Shortcut.Description = "Painel de Controle do Controle de Contas"
$Shortcut.Save()

# Encerra uma instancia antiga do painel que ainda esteja usando a porta.
try {
    $listeners = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        if ($listener.OwningProcess) {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 700
} catch {
    # Se Get-NetTCPConnection nao estiver disponivel, seguimos normalmente.
}

Start-Process -FilePath "wscript.exe" -ArgumentList "`"$Launcher`"" -WindowStyle Hidden
Start-Sleep -Milliseconds 900

Write-Host ""
Write-Host "Painel instalado com sucesso." -ForegroundColor Green
Write-Host "Inicializacao automatica: ATIVA"
Write-Host "Atalho criado na Area de Trabalho: Controle de Contas - Painel"
Write-Host "Endereco: http://127.0.0.1:8765"
Write-Host ""
Write-Host "A partir de agora o painel inicia sozinho com o Windows e busca atualizacoes do painel no GitHub."