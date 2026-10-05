$ErrorActionPreference = "Stop"

$Projeto = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Server = Join-Path $Projeto "controller\server.py"
if (!(Test-Path $Server)) { throw "server.py nao encontrado em $Server" }

$AppData = Join-Path $env:LOCALAPPDATA "ControleContas"
New-Item -ItemType Directory -Force -Path $AppData | Out-Null
$Launcher = Join-Path $AppData "start_panel.vbs"
$OpenLauncher = Join-Path $AppData "open_panel.vbs"

$ConfigPath = Join-Path $AppData "controller.json"
$Config = @{
    repo_full_name = "layon-lima/controle-de-contas"
    host = "127.0.0.1"
    port = 8766
    app_executable = ""
    auto_open_browser = $false
}
$Config | ConvertTo-Json | Set-Content -Path $ConfigPath -Encoding UTF8


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
shell.Run "http://127.0.0.1:8766", 1, False
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


# Encerra qualquer backend antigo que ainda esteja usando a porta 8766.
function Stop-OldPanel {
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue)
        foreach ($listener in $listeners) {
            $ownerPid = [int]$listener.OwningProcess
            if ($ownerPid -gt 0 -and $ownerPid -ne $PID) {
                Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {}

    # Fallback para instalações onde Get-NetTCPConnection não retorna o listener.
    try {
        $lines = netstat -ano -p tcp | Select-String ":8766\s+.*LISTENING\s+(\d+)$"
        foreach ($line in $lines) {
            if ($line.Matches.Count -gt 0) {
                $ownerPid = [int]$line.Matches[0].Groups[1].Value
                if ($ownerPid -gt 0 -and $ownerPid -ne $PID) {
                    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
                }
            }
        }
    } catch {}

    for ($i = 0; $i -lt 20; $i++) {
        $stillListening = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
        if (-not $stillListening) { break }
        Start-Sleep -Milliseconds 250
    }
}

Stop-OldPanel

# Inicia a versão nova silenciosamente.
Start-Process -FilePath "wscript.exe" -ArgumentList "`"$Launcher`"" -WindowStyle Hidden

# Só considera concluído quando o backend novo responder na porta.
$ready = $false
$reportedVersion = ""
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 300
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8766/api/status?t=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())" -TimeoutSec 2
        $reportedVersion = [string]$status.controller_version
        if ($reportedVersion) {
            $ready = $true
            break
        }
    } catch {}
}

if (-not $ready) {
    throw "O backend do painel não iniciou corretamente na porta 8766."
}

if ($reportedVersion -ne "0.3.0") {
    Stop-OldPanel
    throw "O backend iniciado ainda é antigo ($reportedVersion). Execute novamente a atualização do painel."
}

Write-Host ""
Write-Host "Painel instalado/atualizado com sucesso." -ForegroundColor Green
Write-Host "Backend: $reportedVersion"
Write-Host "Inicializacao automatica: ATIVA"
Write-Host "Atalho criado na Area de Trabalho: Controle de Contas - Painel"
Write-Host "Endereco: http://127.0.0.1:8766"
Write-Host ""
Write-Host "A partir de agora o painel inicia sozinho com o Windows e busca atualizacoes do painel no GitHub."
