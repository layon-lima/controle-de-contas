param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# Sempre construa a partir da versão atual da develop.
# Sem esta etapa, uma cópia local antiga executava testes já corrigidos no GitHub.
Write-Host "=== Conferindo fonte local antes do build ==="
$Branch = (& git -C $Root branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $Branch -ne "develop") {
    throw "A pasta local precisa estar na branch develop (atual: '$Branch'). Nenhum arquivo foi alterado."
}
& git -C $Root pull --ff-only origin develop
if ($LASTEXITCODE -ne 0) {
    throw "Não foi possível sincronizar com origin/develop. Resolva eventuais alterações locais e tente novamente. Nenhum aplicativo foi instalado."
}
$LocalCommit = (& git -C $Root rev-parse HEAD).Trim()
$RemoteCommit = (& git -C $Root rev-parse refs/remotes/origin/develop).Trim()
if ($LASTEXITCODE -ne 0 -or $LocalCommit -ne $RemoteCommit) {
    throw "A versão local não corresponde à develop remota; build interrompido para evitar usar testes antigos."
}
Write-Host "Origem confirmada: develop @ $($LocalCommit.Substring(0, 10))"

if (-not $Version) {
    $Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Versão inválida: $Version" }
$PreferredWork = Join-Path $Root ".local_build_$($Version.Replace('.','_'))"
$Work = $PreferredWork

Write-Host "=== Controle de Contas $Version - build local ==="
Write-Host "Raiz: $Root"

# Atualizadores anteriores podem continuar abertos pelo Windows. Nunca tente
# forcar a exclusao de um EXE em uso nem interrompa a nova instalacao por isso.
if (Test-Path $PreferredWork) {
    try {
        Remove-Item -LiteralPath $PreferredWork -Recurse -Force -ErrorAction Stop
    } catch {
        Write-Host "O build anterior esta em uso; criando uma area de trabalho independente."
        $Work = Join-Path ([System.IO.Path]::GetTempPath()) (
            "CC_BUILD_$($Version.Replace('.','_'))_$([guid]::NewGuid().ToString('N').Substring(0, 10))"
        )
    }
}
New-Item -ItemType Directory -Force -Path $Work -ErrorAction Stop | Out-Null
Write-Host "Area de trabalho: $Work"

$SourceZip = Join-Path $Root "releases\CONTROLE_CONTAS_1.4.4_SOURCE.zip"
$OverlayB64 = Join-Path $Root "releases\CONTROLE_CONTAS_1.4.5_OVERLAY.b64"

Expand-Archive -Path $SourceZip -DestinationPath $Work -Force

$OverlayZip = Join-Path $Work "overlay_145.zip"
$b64 = Get-Content $OverlayB64 -Raw
[IO.File]::WriteAllBytes($OverlayZip, [Convert]::FromBase64String($b64))
Expand-Archive -Path $OverlayZip -DestinationPath $Work -Force
Remove-Item $OverlayZip -Force

Copy-Item (Join-Path $Root "source_current\app.py") (Join-Path $Work "app.py") -Force
Copy-Item (Join-Path $Root "source_current\requirements.txt") (Join-Path $Work "requirements.txt") -Force
Get-ChildItem (Join-Path $Root "source_current\controle_contas") -File -Filter "*.py" | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $Work "controle_contas\$($_.Name)") -Force
}
$IconsSource = Join-Path $Root "source_current\assets\ui_icons"
if (!(Test-Path (Join-Path $IconsSource "brand.svg"))) {
    throw "Icones SVG nao encontrados. Sincronize o painel."
}
Copy-Item -Path $IconsSource -Destination (Join-Path $Work "assets") -Recurse -Force
Copy-Item (Join-Path $Root "source_current\Controle de Contas.spec") (Join-Path $Work "Controle de Contas.spec") -Force

$VersionInfoOld = Join-Path $Work "version_info_1.4.5.txt"
$VersionInfoNew = Join-Path $Work "version_info_$Version.txt"
Copy-Item $VersionInfoOld $VersionInfoNew -Force

$parts = $Version.Split(".")
if ($parts.Count -ne 3) { throw "Versão inválida: $Version" }
$major = [int]$parts[0]
$minor = [int]$parts[1]
$patch = [int]$parts[2]

$v = Get-Content $VersionInfoNew -Raw
$v = $v -replace '\(1, 4, 5, 0\)', "($major, $minor, $patch, 0)"
$v = $v -replace '1\.4\.5\.0', "$Version.0"
Set-Content $VersionInfoNew $v -Encoding UTF8

$Spec = Join-Path $Work "Controle de Contas.spec"
$specText = Get-Content $Spec -Raw
$specText = $specText -replace 'version_info_1\.4\.5\.txt', "version_info_$Version.txt"
Set-Content $Spec $specText -Encoding UTF8

$IssOld = Join-Path $Work "Controle_de_Contas_1.4.5.iss"
$IssNew = Join-Path $Work "Controle_de_Contas_$Version.iss"
Copy-Item $IssOld $IssNew -Force
$issText = Get-Content $IssNew -Raw
$issText = $issText -replace '1\.4\.5', $Version
Set-Content $IssNew $issText -Encoding UTF8

Set-Location $Work
py -3.13 -m pip install -r .\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependências." }

py -3.13 -m py_compile .\app.py
if ($LASTEXITCODE -ne 0) { throw "Falha no app.py." }
py -3.13 -m py_compile .\controle_contas\patches.py
if ($LASTEXITCODE -ne 0) { throw "Falha no patches.py." }
py -3.13 -m py_compile .\controle_contas\cloud_sync.py
if ($LASTEXITCODE -ne 0) { throw "Falha no cloud_sync.py." }
py -3.13 -m py_compile .\controle_contas\cloud_mobile_ui.py
if ($LASTEXITCODE -ne 0) { throw "Falha no cloud_mobile_ui.py." }

# Antes de instalar, valida o layout REAL do PySide6 com um banco temporario.
# Erro no teste interrompe o build, preservando a instalacao anterior.
$QaScript = Join-Path $Root "tools\qa_desktop_dashboard.py"
if (!(Test-Path $QaScript)) { throw "Smoke test da interface nao encontrado." }
Copy-Item $QaScript (Join-Path $Work "qa_desktop_dashboard.py") -Force
$BeforeQt = $env:QT_QPA_PLATFORM
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    py -3.13 ".\qa_desktop_dashboard.py"
    if ($LASTEXITCODE -ne 0) { throw "A nova interface nao passou no teste funcional. Instalacao interrompida." }
} finally {
    $env:QT_QPA_PLATFORM = $BeforeQt
}

py -3.13 -m PyInstaller --clean --noconfirm ".\Controle de Contas.spec"
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o aplicativo." }

$Exe = Join-Path $Work "dist\Controle de Contas\Controle de Contas.exe"
if (!(Test-Path $Exe)) { throw "Executável não foi gerado." }

$FileVersion = (Get-Item $Exe).VersionInfo.FileVersion
Write-Host "EXE gerado: $FileVersion"
if ($FileVersion -notlike "$Version*") {
    throw "Versão incorreta no EXE: $FileVersion"
}

$Candidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)

$Iscc = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    $Iscc = Get-ChildItem "$env:ProgramFiles", "${env:ProgramFiles(x86)}", "$env:LOCALAPPDATA\Programs" -Filter ISCC.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Iscc) {
    throw "Inno Setup 6 não encontrado neste PC."
}

& $Iscc $IssNew
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o atualizador." }

$Updater = Join-Path $Work "atualizador\ATUALIZADOR_CONTROLE_DE_CONTAS_$Version.exe"
if (!(Test-Path $Updater)) {
    throw "Atualizador não foi gerado."
}

Write-Host "Atualizador pronto: $Updater"

# Nunca abra o atualizador diretamente de dentro da pasta de build: enquanto
# o instalador estiver aberto, o Windows bloquearia a limpeza do proximo build.
# Copia para uma pasta exclusiva fora do workspace e confirma os bytes.
$LaunchFolder = Join-Path ([System.IO.Path]::GetTempPath()) (
    "CC_INSTALL_$($Version.Replace('.','_'))_$([guid]::NewGuid().ToString('N').Substring(0, 10))"
)
New-Item -ItemType Directory -Force -Path $LaunchFolder -ErrorAction Stop | Out-Null
$LaunchExe = Join-Path $LaunchFolder (Split-Path -Leaf $Updater)
Copy-Item -LiteralPath $Updater -Destination $LaunchExe -ErrorAction Stop
if ((Get-FileHash -LiteralPath $Updater -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $LaunchExe -Algorithm SHA256).Hash) {
    throw "A copia do atualizador nao passou pela verificacao de integridade."
}

Write-Host "Fechando o app atual..."
taskkill /IM "Controle de Contas.exe" /T /F 2>$null | Out-Null
Start-Sleep -Milliseconds 800

Write-Host "Abrindo instalador $Version..."
Start-Process -FilePath $LaunchExe -WorkingDirectory $LaunchFolder -ErrorAction Stop
Write-Host "Instalador iniciado a partir da area independente: $LaunchFolder"
