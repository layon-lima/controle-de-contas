# CONTROLE DE CONTAS - CONFIGURAR SERVIDOR PERSISTENTE
# Execute uma vez como administrador, na conta que possui os dados originais.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Supervisor = Join-Path $PSScriptRoot 'CONTAS_SERVIDOR_SUPERVISOR.ps1'
$Database = Join-Path $env:LOCALAPPDATA 'ControleContas\data\controle_contas.db'
$Task = 'Controle de Contas - Servidor Automatico'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$administrator = New-Object Security.Principal.WindowsPrincipal($identity)

if (-not $administrator.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Execute este script no PowerShell COMO ADMINISTRADOR.'
}
if ($identity.IsSystem) {
    throw 'Nao execute como SYSTEM. Use sua conta do Windows.'
}
if (-not (Test-Path -LiteralPath $Database -PathType Leaf)) {
    throw "Banco original nao encontrado em $Database. Nada foi instalado."
}
if (-not (Test-Path -LiteralPath $Supervisor -PathType Leaf)) {
    throw "Supervisor nao encontrado em $Supervisor."
}
$python1 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$python2 = Join-Path $env:ProgramFiles 'Python313\python.exe'
if (-not (Test-Path -LiteralPath $python1 -PathType Leaf) -and
    -not (Test-Path -LiteralPath $python2 -PathType Leaf)) {
    throw 'Python 3.13 nao encontrado; a configuracao anterior foi preservada.'
}

# Substituir a tarefa antiga (vigia um minuto e encerra) pela permanente.
$existing = Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $Task -ErrorAction Stop
    Start-Sleep -Seconds 1
}
$powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Supervisor + '"'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $Root
$boot = New-ScheduledTaskTrigger -AtStartup
$login = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
# Rodar usando a conta do usuario, mesmo antes do login, sem mudar LOCALAPPDATA.
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType S4U -RunLevel Limited
# Sem limite de duracao; Windows reinicia a tarefa caso ela morra.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 20 -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $Task -Action $action -Trigger @($boot, $login) -Principal $principal -Settings $settings -Description 'Supervisor persistente do servidor local de Contas, sem atualizar nem alterar dados' -Force | Out-Null

# PC ligado e sem suspender quando conectado na tomada.
& powercfg.exe /change standby-timeout-ac 0 | Out-Null

Start-ScheduledTask -TaskName $Task
Start-Sleep -Seconds 6
$actual = Get-ScheduledTask -TaskName $Task
$info = Get-ScheduledTaskInfo -TaskName $Task
Write-Host ('Tarefa: ' + $actual.TaskName)
Write-Host ('Estado: ' + $actual.State)
Write-Host ('Ultimo resultado: ' + $info.LastTaskResult)
Write-Host ('Conta: ' + $identity.Name)
Write-Host ('Banco preservado: ' + $Database)
Write-Host ('Log do supervisor: ' + (Join-Path $env:LOCALAPPDATA 'ControleContas\servidor_supervisor.log'))
try {
    $h = Invoke-RestMethod 'http://127.0.0.1:8767/health' -TimeoutSec 3
    Write-Host ('Servidor ONLINE: ' + $h.online)
} catch {
    Write-Host 'Servidor ainda inicializando. A tarefa fica rodando e tentara recupera-lo.'
}
