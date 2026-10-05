$ErrorActionPreference = "SilentlyContinue"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Server = Join-Path $Root "controller\server.py"
$Port = 8766

Start-Sleep -Milliseconds 700

$ids = @()
try {
    $ids += @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
} catch {}

try {
    $matches = netstat -ano -p tcp | Select-String ":$Port\s+.*LISTENING\s+(\d+)$"
    foreach ($m in $matches) {
        if ($m.Matches.Count -gt 0) {
            $ids += [int]$m.Matches[0].Groups[1].Value
        }
    }
} catch {}

$ids = @($ids | Where-Object { $_ -and $_ -ne $PID } | Sort-Object -Unique)
foreach ($ownerPid in $ids) {
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
}

for ($i = 0; $i -lt 30; $i++) {
    $busy = $false
    try {
        $busy = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    } catch {}
    if (-not $busy) { break }
    Start-Sleep -Milliseconds 200
}

$arg = "& { Set-Location -LiteralPath '$($Root.Replace("'","''"))'; py -3.13 '$($Server.Replace("'","''"))' --no-browser }"
Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-WindowStyle","Hidden","-Command",$arg) -WindowStyle Hidden
