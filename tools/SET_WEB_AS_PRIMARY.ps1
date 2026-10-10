# Define a versão web como entrada principal APENAS após passar nos testes.
# Não desinstala ou altera o banco/PDFs. Guarda o atalho instalado como reserva.
param()
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Vbs = Join-Path $Root "ABRIR_CONTROLE_DE_CONTAS.vbs"
if (-not (Test-Path -LiteralPath $Vbs)) {
    throw "Iniciador web indisponível: $Vbs"
}
# A Area de Trabalho pode ser redirecionada pelo OneDrive. Nunca
# informar sucesso caso o atalho nao tenha sido criado.
$Desktop = [Environment]::GetFolderPath("Desktop")
if (-not $Desktop -or -not (Test-Path -LiteralPath $Desktop -PathType Container)) {
    $ShellDesktop = New-Object -ComObject WScript.Shell
    $Desktop = [string]$ShellDesktop.SpecialFolders.Item("Desktop")
}
if (-not $Desktop -or -not (Test-Path -LiteralPath $Desktop -PathType Container)) {
    throw "A pasta Area de Trabalho do Windows nao foi encontrada; atalho nao criado."
}
$ShortcutPath = Join-Path $Desktop "Controle de Contas.lnk"
$LegacyPath = Join-Path $Desktop "Controle de Contas - Versao instalada (reserva).lnk"
$Shell = New-Object -ComObject WScript.Shell

if (Test-Path -LiteralPath $ShortcutPath) {
    $Current = $Shell.CreateShortcut($ShortcutPath)
    $AlreadyWeb = ($Current.TargetPath -match '(?i)wscript\.exe$') -and
        ($Current.Arguments -like "*ABRIR_CONTROLE_DE_CONTAS.vbs*")
    if (-not $AlreadyWeb -and -not (Test-Path -LiteralPath $LegacyPath)) {
        Copy-Item -LiteralPath $ShortcutPath -Destination $LegacyPath -ErrorAction Stop
        Write-Host "Atalho da versao antiga preservado em: $LegacyPath"
    }
}
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
$Shortcut.Arguments = '"' + $Vbs + '"'
$Shortcut.WorkingDirectory = $Root
$Shortcut.Description = "Controle de Contas web local - sem instalador"
$Shortcut.WindowStyle = 1

$Icon = Join-Path $Root "source_current\assets\controle_contas_web.ico"
if (-not (Test-Path -LiteralPath $Icon -PathType Leaf)) {
    # Backup para clones antigos sem o recurso ja incluido no GitHub.
    $IconScript = Join-Path $Root "tools\CREATE_WEB_ICON.ps1"
    if (Test-Path -LiteralPath $IconScript -PathType Leaf) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $IconScript
    }
}
if (-not (Test-Path -LiteralPath $Icon -PathType Leaf)) {
    throw "Icone Windows web indisponivel."
}
$Shortcut.IconLocation = $Icon + ",0"
$Shortcut.Save()
if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
    throw "O Windows nao confirmou a criacao do atalho: $ShortcutPath"
}
$Confirmed = $Shell.CreateShortcut($ShortcutPath)
if ($Confirmed.Arguments -notlike "*ABRIR_CONTROLE_DE_CONTAS.vbs*" -or
    $Confirmed.IconLocation -notlike "*controle_contas_web.ico*") {
    throw "O atalho foi criado, mas o destino ou icone nao foram confirmados."
}
Write-Host "Atalho principal confirmado: $ShortcutPath (versao web)"
