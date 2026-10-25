; MorphTo installer (Inno Setup 6). Built by .github/workflows/release.yml:
;   ISCC /DAppVersion=0.4.2 packaging\installer.iss   ->  dist\MorphTo-0.4.2-setup.exe
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
Autostart=Start MorphTo with Windows (recommended: the hotkey works right away)
RemoveData=Also delete MorphTo settings and downloaded components (FFmpeg, HEIC encoder, LibreOffice)?
UpdateCaption=Update MorphTo %1 → %2
UpdateNote=MorphTo %1 is installed and will be updated to %2. Your settings and components are kept.
russian.AddToPath=Добавить команду "morphto" в PATH (для командной строки)
russian.Autostart=Запускать MorphTo вместе с Windows (рекомендуется: сочетание клавиш работает сразу)
russian.RemoveData=Удалить также настройки MorphTo и загруженные компоненты (FFmpeg, кодировщик HEIC, LibreOffice)?
russian.UpdateCaption=Обновление MorphTo %1 → %2
russian.UpdateNote=Установлена MorphTo %1, она будет обновлена до %2. Настройки и компоненты сохранятся.
ukrainian.AddToPath=Додати команду "morphto" до PATH (для командного рядка)
ukrainian.Autostart=Запускати MorphTo разом з Windows (рекомендовано: сполучення клавіш працює одразу)
ukrainian.RemoveData=Видалити також налаштування MorphTo та завантажені компоненти (FFmpeg, кодувальник HEIC, LibreOffice)?
ukrainian.UpdateCaption=Оновлення MorphTo %1 → %2
ukrainian.UpdateNote=Встановлено MorphTo %1, її буде оновлено до %2. Налаштування та компоненти збережуться.

[Tasks]
Name: "autostart"; Description: "{cm:Autostart}"
Name: "addtopath"; Description: "{cm:AddToPath}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MorphTo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; Same value the app writes in Settings → Start with Windows (so the checkbox there shows it).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MorphTo"; ValueData: """{app}\{#AppExe}"" --background"; Flags: uninsdeletevalue; Tasks: autostart

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Started by the app's "Update" button (/SILENT /update=1): bring MorphTo back when done.
Filename: "{app}\{#AppExe}"; Parameters: "--background"; Flags: nowait; Check: IsAppUpdate

[UninstallRun]
; Remove autostart and the Explorer menu entries the app created.
Filename: "{app}\{#AppExe}"; Parameters: "--cleanup"; Flags: runhidden waituntilterminated; RunOnceId: "Cleanup"

[Code]
const
  EnvKey = 'Environment';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6E3D2C4A-1F7B-4C9E-9B57-4D2A8C1E7F30}_is1';

var
  PrevVersion: string;  // version already installed, '' on a fresh install

function InitializeSetup: Boolean;
begin
  PrevVersion := '';
  if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', PrevVersion) then
    RegQueryStringValue(HKLM, UninstallKey, 'DisplayVersion', PrevVersion);
  Result := True;
end;

procedure InitializeWizard;
begin
  if PrevVersion <> '' then
    WizardForm.Caption := FmtMessage(CustomMessage('UpdateCaption'), [PrevVersion, '{#AppVersion}']);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PrevVersion <> '') and (PageID = wpLicense);  // already accepted at the first install
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (PrevVersion <> '') and ((CurPageID = wpSelectTasks) or (CurPageID = wpReady)) then
    WizardForm.PageDescriptionLabel.Caption :=
      FmtMessage(CustomMessage('UpdateNote'), [PrevVersion, '{#AppVersion}']);
end;

function IsAppUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:update|0}') = '1');
end;

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
