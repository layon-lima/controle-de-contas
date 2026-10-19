# Controle de Contas: vigia o servidor web local sem modificar dados.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $Root 'portable_app\launcher.py'
$UserData = Join-Path $env:LOCALAPPDATA 'ControleContas'
$Database = Join-Path $UserData 'data\controle_contas.db'
$Log = Join-Path $UserData 'servidor_vigia.log'
$Offline = Join-Path $UserData 'servidor_vigia_offline.txt'

function Logar([string]$mensagem) {
  try {
    $entry = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' | ' + $mensagem
    Add-Content -LiteralPath $Log -Encoding UTF8 -Value $entry
  } catch { }
}
function Saudavel {
  try {
    $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8767/health' -TimeoutSec 3 -ErrorAction Stop
    return ($status.online -eq $true)
  } catch { return $false }
}
function PortaOcupada {
  $tcp = New-Object Net.Sockets.TcpClient
  try { $tcp.Connect('127.0.0.1', 8767); return $true }
  catch { return $false }
  finally { $tcp.Dispose() }
}
function ProcessosDoContas {
  $server = Join-Path $Root 'portable_app\server.py'
  return @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cmd = [string]$_.CommandLine
    $cmd -and ($cmd.Contains($Launcher) -or $cmd.Contains($server))
  })
}
$mutex = New-Object System.Threading.Mutex($false, 'Local\ControleContasServidorWatchdog')
$locked = $false
try {
  try { $locked = $mutex.WaitOne(0) }
  catch [System.Threading.AbandonedMutexException] { $locked = $true }
  if (-not $locked) { return }
  if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) {
    if (Test-Path -LiteralPath $UserData) {
      Logar "Banco original nao encontrado: $Database. Servidor nao iniciado."
    }
    return
  }
  if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    Logar "Iniciador nao encontrado: $Launcher"
    return
  }
  if (Saudavel) {
    if (Test-Path -LiteralPath $Offline) {
      Remove-Item -LiteralPath $Offline -Force -ErrorAction SilentlyContinue
      Logar 'Servidor voltou a responder.'
    }
    return
  }
  $processes = @(ProcessosDoContas)
  $busy = PortaOcupada
  if ($processes.Count -gt 0 -or $busy) {
    if (-not (Test-Path -LiteralPath $Offline)) {
      [IO.File]::WriteAllText($Offline, [DateTime]::UtcNow.ToString('o'))
      Logar 'Servidor sem resposta; aguardando ate 3 minutos para recuperar.'
      return
    }
    try { $since = [DateTime]::Parse([IO.File]::ReadAllText($Offline)).ToUniversalTime() }
    catch { $since = [DateTime]::UtcNow }
    if (([DateTime]::UtcNow - $since).TotalSeconds -lt 180) { return }
    if ($processes.Count -eq 0) {
      Logar 'Porta 8767 ocupada por aplicativo desconhecido; nao encerrar.'
      return
    }
    if ($busy) {
      $owners = @(Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)
      $known = @($processes | Select-Object -ExpandProperty ProcessId)
      if (-not @($owners | Where-Object { $known -contains $_ }).Count) {
        Logar 'Porta 8767 pertence a outro processo; nao encerrar.'
        return
      }
    }
    foreach ($proc in $processes) {
      try {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        Logar "Encerrado somente servidor Contas travado, PID $($proc.ProcessId)."
      } catch { Logar "Falha ao recuperar PID $($proc.ProcessId): $_" }
    }
    Start-Sleep -Seconds 2
    if (PortaOcupada) {
      Logar 'Porta ainda ocupada. Nao abrir outro processo.'
      return
    }
  }
  Remove-Item -LiteralPath $Offline -Force -ErrorAction SilentlyContinue
  $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = Join-Path $env:ProgramFiles 'Python313\python.exe'
  }
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Logar 'Python 3.13 nao encontrado; nao instalar automaticamente.'
    return
  }
  $env:CONTAS_PORTABLE_NO_BROWSER = '1'
  $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
  $stdout = Join-Path $UserData ('servidor_auto_' + $stamp + '.log')
  $stderr = Join-Path $UserData ('servidor_auto_' + $stamp + '.err.log')
  try {
    $child = Start-Process -FilePath $python -ArgumentList @('-u', ('"' + $Launcher + '"')) -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Logar "Servidor iniciado automaticamente; PID $($child.Id)."
  } catch {
    Logar "Falha ao iniciar servidor: $_"
  }
} finally {
  if ($locked) { $mutex.ReleaseMutex() | Out-Null }
  $mutex.Dispose()
}
