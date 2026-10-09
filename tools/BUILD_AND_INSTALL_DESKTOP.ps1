param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Version) {
    $Version = (Get-Content (Join-Path $Root "VERSION") -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+ Join-Path $Root ".local_build_$($Version.Replace('.','_'))"

Write-Host "=== Controle de Contas $Version - build local ==="
Write-Host "Raiz: $Root"

if (Test-Path $Work) {
    Remove-Item $Work -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Work | Out-Null

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
Write-Host "Fechando o app atual..."
taskkill /IM "Controle de Contas.exe" /T /F 2>$null | Out-Null
Start-Sleep -Milliseconds 800

Write-Host "Abrindo instalador $Version..."
Start-Process -FilePath $Updater -WorkingDirectory (Split-Path $Updater -Parent)
Write-Host "Instalador iniciado."
) { throw "Versão inválida: $Version" }
$Work = Join-Path $Root ".local_build_$($Version.Replace('.','_'))"

Write-Host "=== Controle de Contas $Version - build local ==="
Write-Host "Raiz: $Root"

if (Test-Path $Work) {
    Remove-Item $Work -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Work | Out-Null

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
Write-Host "Fechando o app atual..."
taskkill /IM "Controle de Contas.exe" /T /F 2>$null | Out-Null
Start-Sleep -Milliseconds 800

Write-Host "Abrindo instalador $Version..."
Start-Process -FilePath $Updater -WorkingDirectory (Split-Path $Updater -Parent)
Write-Host "Instalador iniciado."
