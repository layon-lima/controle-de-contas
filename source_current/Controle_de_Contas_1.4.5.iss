#define MyAppName "Controle de Contas"
#define MyAppVersion "1.4.5"
#define MyAppPublisher "Layon Lima"
#define MyAppExeName "Controle de Contas.exe"

[Setup]
AppId={{A81D6309-5F1C-4B54-B73F-C01E4D422591}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} versão {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Controle de Contas
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=atualizador
OutputBaseFilename=ATUALIZADOR_CONTROLE_DE_CONTAS_1.4.5
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
SetupIconFile=assets\icone_controle_contas.ico
UninstallDisplayName={#MyAppName} versão {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[InstallDelete]
Type: files; Name: "{app}\{#MyAppExeName}"
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\assets"
Type: filesandordirs; Name: "{app}\tesseract"

[Files]
Source: "dist\Controle de Contas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Controle de Contas"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Controle de Contas"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na Área de Trabalho"; GroupDescription: "Atalhos:"; Flags: checkedonce

[Registry]
; Remove somente o autostart da arquitetura mobile LOCAL antiga.
; O ControleContasCloudSync atual é preservado para manter o vínculo Cloudflare/mobile.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "ControleContasMobile"; Flags: deletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Controle de Contas"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  { O sincronizador Cloud usa o mesmo executável com --cloud-sync-v11 e pode }
  { manter DLLs abertas. Encerra todas as instâncias antes de substituir o build. }
  Exec(
    ExpandConstant('{cmd}'),
    '/C taskkill /F /T /IM "Controle de Contas.exe" >nul 2>&1',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Sleep(900);
  Result := '';
end;
