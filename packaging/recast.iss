; Inno Setup script for Recast. Built by packaging\build.ps1 (pass /DAppVersion=x.y.z).
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\Recast.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{7B6A3F5E-2C41-4E7B-9F0A-5D3C2B1A9E44}
AppName=Recast
AppVersion={#AppVersion}
AppPublisher=Recast
DefaultDirName={localappdata}\Programs\Recast
DefaultGroupName=Recast
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Recast-Setup-{#AppVersion}
SetupIconFile=..\brand\recast.ico
UninstallDisplayIcon={app}\Recast.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Tasks]
Name: "autostart"; Description: "Start Recast with Windows"; GroupDescription: "Options:"
Name: "sendto"; Description: "Add Recast to the ""Send to"" menu"; GroupDescription: "Options:"
Name: "ffmpeg"; Description: "Install FFmpeg for audio and video (uses winget)"; GroupDescription: "Options:"; Check: not FFmpegFound

[Files]
Source: "..\dist\Recast\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Recast"; Filename: "{app}\Recast.exe"
Name: "{usersendto}\Recast"; Filename: "{app}\Recast.exe"; Parameters: "wheel"; Tasks: sendto

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Recast"; \
  ValueData: """{app}\Recast.exe"" --background"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "winget"; Parameters: "install --id Gyan.FFmpeg.Essentials -e --accept-source-agreements --accept-package-agreements"; \
  StatusMsg: "Installing FFmpeg..."; Tasks: ffmpeg; Flags: runhidden waituntilterminated
Filename: "{app}\Recast.exe"; Description: "Start Recast now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /im Recast.exe /f"; Flags: runhidden; RunOnceId: "StopRecast"

[UninstallDelete]
Type: files; Name: "{usersendto}\Recast.lnk"

[Code]
function FFmpegFound: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c where ffmpeg >nul 2>nul', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;
