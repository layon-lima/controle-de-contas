# Controle de Contas: configuracao privada do agente Windows via SSH.
# So consulta PDF por ID, sem enviar arquivo ou modificar pagamentos.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$')]
    [string]$SshTarget,
    [string]$VpsRepository = '/opt/apps/contas'
)
$ErrorActionPreference = 'Stop'
if ($VpsRepository -notmatch '^/[A-Za-z0-9/_-]+$') {
    throw 'Pasta do repositorio VPS invalida.'
}
$pdfRoot = 'C:\Users\User\Documents\BOLETOS'
$db = Join-Path $env:LOCALAPPDATA 'ControleContas\data\controle_contas.db'
$config = Join-Path $env:LOCALAPPDATA 'ControleContas\agent_vps.json'
$agent = Join-Path $PSScriptRoot 'CONTAS_AGENTE_VPS.py'
if (-not (Test-Path -LiteralPath $db -PathType Leaf)) {
    throw "Banco original nao encontrado: $db. Nada foi criado."
}
if (-not (Test-Path -LiteralPath $pdfRoot -PathType Container)) {
    throw "Pasta BOLETOS original ausente: $pdfRoot. Nada foi criado."
}
if (-not (Test-Path -LiteralPath $agent -PathType Leaf)) {
    throw "Codigo do agente nao encontrado: $agent"
}
if (Test-Path -LiteralPath $config) {
    throw "Configuracao privada ja existe: $config. Nao sera sobrescrita."
}
if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) {
    throw 'OpenSSH Client nao esta instalado neste Windows.'
}
$vpsFile = $VpsRepository + '/deploy/vps/broker.env'
$sshCmd = "sed -n 's/^CONTAS_BROKER_AGENT_TOKEN=//p' '$vpsFile'"
# Recebe o token por stdout de canal SSH cifrado. Nao imprimir o resultado.
$lines = @(& ssh.exe $SshTarget $sshCmd)
if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) {
    throw 'Nao foi possivel acessar as credenciais do agente na VPS via SSH.'
}
$secret = ([string]$lines[0]).Trim()
if ($secret.Length -lt 32 -or $secret.StartsWith('SUBSTITUA')) {
    throw 'Credencial de agente invalida. Nada foi configurado.'
}
$data = [ordered]@{
    broker_url = 'http://127.0.0.1:18767'
    agent_token = $secret
    documents_root = $pdfRoot
    database = $db
}
$temp = $config + '.tmp'
try {
    [IO.File]::WriteAllText($temp, ($data | ConvertTo-Json -Depth 4),
        (New-Object System.Text.UTF8Encoding($false)))
    # Nao depender do idioma do Windows: ACLs expressas com SIDs.
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $userAcl = '*' + $userSid + ':(F)'
    & icacls.exe $temp '/inheritance:r' '/grant:r' $userAcl '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Falha ao restringir permissoes de credenciais do agente.'
    }
    Move-Item -LiteralPath $temp -Destination $config -ErrorAction Stop
} catch {
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    throw
} finally {
    $secret = $null
    $lines = $null
}
Write-Host 'Configuração segura do agente concluida (token nao exibido).'
Write-Host 'Sem PDF, banco ou pagamento alterado.'
Write-Host ''
Write-Host 'TESTE: em OUTRO PowerShell, execute e deixe aberto:'
Write-Host ('ssh -N -L 127.0.0.1:18767:127.0.0.1:18767 ' + $SshTarget)
Write-Host ''
Write-Host 'Depois, com o tunel aberto, execute:'
Write-Host ('python "' + $agent + '" --config "' + $config + '" --once')
Write-Host 'A instalacao do agente automatico sera liberada apos validar o teste.'
