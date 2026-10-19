# CONTROLE DE CONTAS - SUPERVISOR PERSISTENTE (Windows PowerShell 5.1)
# Executado no Agendador como o usuario original. Nao atualiza, nao instala,
# nao cria banco, nao altera contas/PDFs, nao abre janelas.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root 'portable_app\server.py'
$Launcher = Join-Path $Root 'portable_app\launcher.py'
$Data = Join-Path $env:LOCALAPPDATA 'ControleContas'
$Db = Join-Path $Data 'data\controle_contas.db'
$Log = Join-Path $Data 'servidor_supervisor.log'
$env:CONTAS_PORTABLE_NO_BROWSER = '1'
$env:CONTAS_PORTABLE_PORT = '8767'

function Registrar([string]$text) {
    try {
        if (Test-Path -LiteralPath $Data -PathType Container) {
            Add-Content -LiteralPath $Log -Encoding UTF8 -Value (
                (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' | ' + $text)
        }
    } catch { }
}

function Saudavel {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8767/health' -TimeoutSec 4 -ErrorAction Stop
        return ($response.online -eq $true)
    } catch {
        return $false
    }
}

function ProcessosDoServidor {
    try {
        $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction Stop)
        return @($processes | Where-Object {
            $command = [string]$_.CommandLine
            $command.Length -gt 0 -and (
                $command.IndexOf($Server, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                $command.IndexOf($Launcher, [StringComparison]::OrdinalIgnoreCase) -ge 0
            )
        })
    } catch {
        Registrar "Falha ao listar processos do Python: $_"
        return @()
    }
}

function DonosDaPorta {
    try {
        return @(Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess)
    } catch {
        return @()
    }
}

$mutex = New-Object System.Threading.Mutex($false, 'Global\ControleContasSupervisorPermanente')
$temTrava = $false
try {
    try {
        $temTrava = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $temTrava = $true
    }
    if (-not $temTrava) {
        Registrar 'Outro supervisor ja esta rodando. Esta instancia encerrou.'
        exit 0
    }

    Registrar "Supervisor iniciado. Usuario: $([Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    $semRespostaDesde = $null
    $ultimoAviso = [DateTime]::MinValue

    while ($true) {
        try {
            # NUNCA iniciar com outro usuario sem o banco original.
            if (-not (Test-Path -LiteralPath $Db -PathType Leaf)) {
                if ((Get-Date).Subtract($ultimoAviso).TotalMinutes -ge 5) {
                    Registrar "BANCO ORIGINAL AUSENTE em $Db. Nenhum servidor sera iniciado."
                    $ultimoAviso = Get-Date
                }
                Start-Sleep -Seconds 30
                continue
            }
            if (-not (Test-Path -LiteralPath $Server -PathType Leaf)) {
                Registrar "Servidor nao encontrado em $Server."
                Start-Sleep -Seconds 60
                continue
            }
            if (Saudavel) {
                if ($null -ne $semRespostaDesde) {
                    Registrar 'Servidor voltou a responder normalmente.'
                }
                $semRespostaDesde = $null
                Start-Sleep -Seconds 15
                continue
            }

            $processos = @(ProcessosDoServidor)
            $donos = @(DonosDaPorta)
            $portaOcupada = ($donos.Count -gt 0)

            if ($processos.Count -gt 0 -or $portaOcupada) {
                if ($null -eq $semRespostaDesde) {
                    $semRespostaDesde = Get-Date
                    Registrar 'Servidor sem resposta. Aguardando 90 segundos antes de recuperar.'
                }
                if ((Get-Date).Subtract($semRespostaDesde).TotalSeconds -lt 90) {
                    Start-Sleep -Seconds 15
                    continue
                }
                if ($portaOcupada) {
                    $nossosPids = @($processos | ForEach-Object { [int]$_.ProcessId })
                    $alheios = @($donos | Where-Object { $nossosPids -notcontains [int]$_ })
                    if ($alheios.Count -gt 0 -or $nossosPids.Count -eq 0) {
                        if ((Get-Date).Subtract($ultimoAviso).TotalMinutes -ge 5) {
                            Registrar 'Porta 8767 ocupada por outro processo. Nada sera encerrado.'
                            $ultimoAviso = Get-Date
                        }
                        Start-Sleep -Seconds 30
                        continue
                    }
                }
                if ($processos.Count -gt 0) {
                    foreach ($processo in $processos) {
                        try {
                            Stop-Process -Id $processo.ProcessId -Force -ErrorAction Stop
                            Registrar "Reiniciando somente processo do Contas travado: PID $($processo.ProcessId)."
                        } catch {
                            Registrar "Falha ao encerrar servidor travado: $_"
                        }
                    }
                    Start-Sleep -Seconds 3
                    if (@(DonosDaPorta).Count -gt 0) {
                        Registrar 'Porta 8767 continua ocupada; tentativa adiada.'
                        Start-Sleep -Seconds 30
                        continue
                    }
                }
                $semRespostaDesde = $null
            }

            $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
            if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
                $python = Join-Path $env:ProgramFiles 'Python313\python.exe'
            }
            if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
                if ((Get-Date).Subtract($ultimoAviso).TotalMinutes -ge 5) {
                    Registrar 'Python 3.13 nao encontrado. Nenhuma instalacao automatica sera feita.'
                    $ultimoAviso = Get-Date
                }
                Start-Sleep -Seconds 30
                continue
            }

            # Inicia diretamente server.py: launcher.py derruba uma instancia
            # anterior ao ser executado, portanto NAO deve ser usado no monitor.
            $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
            $stdout = Join-Path $Data ('servidor_' + $stamp + '.stdout.log')
            $stderr = Join-Path $Data ('servidor_' + $stamp + '.stderr.log')
            $arguments = '-u "' + $Server + '"'
            $processoNovo = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -ErrorAction Stop
            Registrar "Processo iniciado: PID $($processoNovo.Id). Erros em $stderr"
            # Tempo para subir OCR, sincronizador e API; nao abrir outro Python.
            $semRespostaDesde = Get-Date
            Start-Sleep -Seconds 20
            if (Saudavel) {
                Registrar "Servidor ONLINE em http://127.0.0.1:8767/"
                $semRespostaDesde = $null
            } elseif ($processoNovo.HasExited) {
                Registrar "Servidor terminou com codigo $($processoNovo.ExitCode). Consulte $stderr"
            }
        } catch {
            Registrar "ERRO NO SUPERVISOR: $_"
            Start-Sleep -Seconds 20
        }
        Start-Sleep -Seconds 15
    }
} finally {
    if ($temTrava) { $mutex.ReleaseMutex() | Out-Null }
    $mutex.Dispose()
}
