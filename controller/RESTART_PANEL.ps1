$ErrorActionPreference = "Stop"
# Reinicio supervisionado. Esta rotina so e usada pelo atalho, nao pelo botao sincronizar.
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Server = Join-Path $Root "controller\server.py"
$Port = 8766
$LogDir = Join-Path $env:LOCALAPPDATA "ControleContas"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "panel_restart.log"
$Stdout = Join-Path $LogDir "panel_restart_stdout.log"
$Stderr = Join-Path $LogDir "panel_restart_stderr.log"

function Get-PanelPids {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique)
}
try {
    if (!(Test-Path -LiteralPath $Server)) {
        throw "server.py nao encontrado: $Server"
    }
    foreach ($listenerPid in @(Get-PanelPids)) {
        if (-not $listenerPid -or $listenerPid -eq $PID) { continue }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
        $cmd = [string]$proc.CommandLine
        if ($cmd -notmatch 'controller[\\/]server[.]py') {
            throw "Porta $Port ocupada por outro processo (PID $listenerPid). Nao foi encerrado."
        }
        Stop-Process -Id $listenerPid -Force -ErrorAction Stop
    }
    for ($i=0; $i -lt 40; $i++) {
        if (-not (Get-PanelPids)) { break }
        Start-Sleep -Milliseconds 200
    }
    if (Get-PanelPids) { throw "Porta $Port ainda ocupada." }

    $launch = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launch) {
        $exe = $launch.Source
        $args = '-3.13 "{0}" --no-browser' -f $Server
    } else {
        $launch = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if (-not $launch) { throw "Python nao encontrado." }
        $exe = $launch.Source
        $args = '"{0}" --no-browser' -f $Server
    }
    Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr

    $ready = $false
    for ($i=0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 350
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
            if ($health.ok) { $ready = $true; break }
        } catch {}
    }
    if (-not $ready) {
        $lastError = if (Test-Path $Stderr) { (Get-Content $Stderr -Tail 10 | Out-String) } else { "" }
        throw "Backend nao respondeu em 127.0.0.1:$Port. $lastError"
    }
    Add-Content -Path $Log -Value ("[{0}] Painel online." -f (Get-Date).ToString("s")) -Encoding UTF8
} catch {
    Add-Content -Path $Log -Value ("[{0}] ERRO: {1}" -f (Get-Date).ToString("s"), $_.Exception.Message) -Encoding UTF8
    throw
}
