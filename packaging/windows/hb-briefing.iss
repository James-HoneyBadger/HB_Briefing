; Inno Setup script for the Windows installer.
; Build: python packaging\build.py windows   (runs PyInstaller, then ISCC with /DMyAppVersion
; and /DSourceDir pointing at the frozen tree)

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "dist\hb-briefing-1.0.0-windows-x86_64"
#endif
#define MyAppName "HB Briefing"
#define MyAppPublisher "James HoneyBadger"
#define MyAppURL "https://github.com/James-HoneyBadger/HB_Briefing"
#define MyAppExeName "hb-briefing.exe"

[Setup]
; A fixed AppId keeps upgrades in one place instead of stacking duplicate entries.
AppId={{7C4C1F27-8E52-4A0B-9F1E-2A6D5C3B91D4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\HBBriefing
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: no elevation, and the database stays in the user's own profile.
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=hb-briefing-{#MyAppVersion}-windows-setup
SetupIconFile=..\icons\hb-briefing.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} (command line)"; Filename: "{cmd}"; Parameters: "/k ""{app}\hb-briefing-cli.exe"""; WorkingDir: "{app}"
Name: "{group}\{#MyAppName} (web app)"; Filename: "{app}\hb-briefing-web.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Keep the user's data directory out of Program Files: it is %LOCALAPPDATA%\HB Briefing.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only the installed program is removed: the database, feeds and .env stay in %LOCALAPPDATA%.
Type: filesandordirs; Name: "{app}"
