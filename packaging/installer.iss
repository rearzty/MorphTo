; MorphTo installer (Inno Setup 6). Built by .github/workflows/release.yml:
;   ISCC /DAppVersion=0.1.0 packaging\installer.iss   ->  dist\MorphTo-0.1.0-setup.exe
; Per-user install (no admin rights): %LOCALAPPDATA%\Programs\MorphTo.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "MorphTo"
#define AppExe "MorphTo.exe"

[Setup]
AppId={{6E3D2C4A-1F7B-4C9E-9B57-4D2A8C1E7F30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=rearzty
AppPublisherURL=https://github.com/rearzty/MorphTo
AppSupportURL=https://github.com/rearzty/MorphTo/issues
AppUpdatesURL=https://github.com/rearzty/MorphTo/releases
VersionInfoVersion={#AppVersion}
VersionInfoCopyright=Copyright (C) 2026 rearzty. GPL-3.0-or-later.
LicenseFile=..\LICENSE
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-setup
SetupIconFile=..\build\MorphTo.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[CustomMessages]
AddToPath=Add the "morphto" command to PATH (for the command line)
RemoveData=Also delete MorphTo settings and downloaded components (FFmpeg, HEIC encoder)?
russian.AddToPath=Добавить команду "morphto" в PATH (для командной строки)
russian.RemoveData=Удалить также настройки MorphTo и загруженные компоненты (FFmpeg, кодировщик HEIC)?
ukrainian.AddToPath=Додати команду "morphto" до PATH (для командного рядка)
ukrainian.RemoveData=Видалити також налаштування MorphTo та завантажені компоненти (FFmpeg, кодувальник HEIC)?

[Tasks]
Name: "addtopath"; Description: "{cm:AddToPath}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MorphTo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove autostart and the Explorer menu entries the app created.
Filename: "{app}\{#AppExe}"; Parameters: "--cleanup"; Flags: runhidden waituntilterminated; RunOnceId: "Cleanup"

[Code]
const
  EnvKey = 'Environment';

function PathContains(Paths, Dir: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0;
end;

procedure AddToUserPath(Dir: string);
var
  Paths: string;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Paths := '';
  if PathContains(Paths, Dir) then
    exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then
    Paths := Paths + ';';
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths + Dir);
end;

procedure RemoveFromUserPath(Dir: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Dir) + 1);
  Paths := Copy(Paths, 2, Length(Paths) - 2);
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    AddToUserPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveFromUserPath(ExpandConstant('{app}'));
  if CurUninstallStep = usPostUninstall then
    if (not UninstallSilent) and
       (MsgBox(CustomMessage('RemoveData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
    begin
      DelTree(ExpandConstant('{userappdata}\MorphTo'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\MorphTo'), True, True, True);
    end;
end;
