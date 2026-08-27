#define AppName "Dbx Telegram Monitor"
#ifndef AppVersion
#define AppVersion "5.0.0"
#endif
#define AppPublisher "Dbx"
#define AppExeName "Dbx Telegram Monitor.exe"

[Setup]
AppId={{52FF51D3-33EC-41B2-9C8D-C65F8B5850F6}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Dbx Telegram Monitor
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir=installer
OutputBaseFilename=Dbx-Telegram-Monitor-Setup
SetupIconFile=assets\final_icon.ico
LicenseFile=TERMOS_DE_USO.txt
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
