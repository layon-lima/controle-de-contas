# Instala a sincronizacao de PDFs abertos Windows -> VPS -> Cloudflare R2.
# Nao muda nem migra o banco, apenas le PDFs existentes.
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'ControleContas'
$script = Join-Path $folder 'CONTAS_PDFS_WINDOWS_R2.py'
$config = Join-Path $folder 'r2-pdf-agent.json'
$database = Join-Path $folder 'data\controle_contas.db'
$repo = 'https://raw.githubusercontent.com/layon-lima/controle-de-contas/main/tools/CONTAS_PDFS_WINDOWS_R2.py'
$server = 'root@187.127.63.29'
$taskName = 'ControleContas-PDF-R2'

if (-not (Test-Path -LiteralPath $database)) {
    throw 'Banco original do Windows nao localizado. Nenhuma alteracao foi feita.'
}
$python = Get-Command py.exe -ErrorAction Stop
New-Item -ItemType Directory -Force -Path $folder | Out-Null
Write-Host 'Baixando sincronizador da main do GitHub...'
Invoke-WebRequest -Uri $repo -OutFile $script -UseBasicParsing

if (-not (Test-Path -LiteralPath $config)) {
    Write-Host 'Informe sua senha SSH da VPS para configurar o agente.'
    $token = (& ssh $server 'cat /opt/apps/contas-private/r2-agent-token')
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token) -or $token.Trim().Length -lt 32) {
        throw 'Nao foi possivel obter token do agente na VPS. Ative o R2 na VPS primeiro.'
    }
    $settings = @{
        base_url = 'https://contas-cloud.fazendanovohorizonte.com'
        agent_token = $token.Trim()
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($config, $settings, (New-Object System.Text.UTF8Encoding($false)))
    & icacls.exe $config /inheritance:r /grant:r "$($env:USERNAME):(R)" 'SYSTEM:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Nao foi possivel proteger credenciais locais. Configure permissoes NTFS antes de continuar.'
    }
    Remove-Variable token -ErrorAction SilentlyContinue
}
Write-Host 'Enviando PDFs de contas em aberto (sem alterar os arquivos locais)...'
& $python.Source $script
if ($LASTEXITCODE -ne 0) {
    throw 'Primeira sincronizacao nao concluiu; veja o aviso acima. Nenhuma tarefa automatica foi criada.'
}

Write-Host 'Agendando sincronizacao automatica a cada 5 minutos, quando o PC estiver ligado...'
$action = New-ScheduledTaskAction -Execute $python.Source -Argument ('"' + $script + '"')
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Write-Host 'PRONTO: PDFs de contas abertas sincronizados e atualizacao automatica ativa.'
Write-Host 'PDFs originais no Windows: NAO ALTERADOS.'
Write-Host 'O PC deve estar ligado para enviar novas alteracoes; a VPS continua online sem ele.'
