#define MyAppName "Controle de Contas"
#define MyAppVersion "1.3.1"
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
PrivilegesRequired=lowest
OutputDir=atualizador
OutputBaseFilename=ATUALIZADOR_CONTROLE_DE_CONTAS_1.3.1
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#MyAppName} versão {#MyAppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\Controle de Contas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Controle de Contas"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Controle de Contas"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Controle de Contas"; Flags: nowait postinstall skipifsilent
