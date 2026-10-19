# Execute UMA VEZ no PowerShell como Administrador, na sua conta do Windows.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Watchdog = Join-Path $PSScriptRoot 'CONTAS_WATCHDOG.ps1'
$Database = Join-Path $env:LOCALAPPDATA 'ControleContas\data\controle_contas.db'
$TaskName = 'Controle de Contas - Servidor Automatico'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$user = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $user.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw 'Abra PowerShell como administrador e tente novamente.'
}
if ($identity.IsSystem) {
  throw 'Nao executar como SYSTEM. Use sua propria conta do Windows.'
}
if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) {
  throw "Banco original nao encontrado: $Database. Nenhuma tarefa foi criada."
}
if (-not (Test-Path -LiteralPath $Watchdog -PathType Leaf)) {
  throw "Monitor nao encontrado: $Watchdog."
}
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$alternative = Join-Path $env:ProgramFiles 'Python313\python.exe'
if (-not (Test-Path $python) -and -not (Test-Path $alternative)) {
  throw 'Python 3.13 ausente; nenhum programa foi instalado.'
}
$pwsh = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$argsForTask = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Watchdog + '"'
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $argsForTask -WorkingDirectory $Root
$startup = New-ScheduledTaskTrigger -AtStartup
$minute = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
# S4U roda com a identidade da conta existente, inclusive sem login.
# Evita o erro de executar como SYSTEM e procurar um banco vazio.
$taskUser = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($startup, $minute) -Principal $taskUser -Settings $settings -Description 'Mantem Controle de Contas web online, sem atualizar nem alterar o banco' -Force | Out-Null
Write-Host "Servidor automatico configurado para a conta $($identity.Name)."
Write-Host 'Iniciando a primeira verificacao...'
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
$taskResult = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "Ultimo resultado da tarefa: $($taskResult.LastTaskResult)"
try {
  $health = Invoke-RestMethod 'http://127.0.0.1:8767/health' -TimeoutSec 3
  Write-Host "Servidor online: $($health.online)"
} catch {
  Write-Host 'Servidor ainda nao respondeu. Monitor verificara novamente em 1 minuto.'
}
Write-Host ('Log: ' + (Join-Path $env:LOCALAPPDATA 'ControleContas\servidor_vigia.log'))
