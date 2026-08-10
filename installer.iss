; Inno Setup script for VideoTrim (Windows 11 installer).
; Build with: iscc installer.iss   (or run build-installer.ps1)
; Expects the PyInstaller onedir output at build\windows-python\dist\VideoTrim\
; produced by build-windows.ps1.

#define AppName "VideoTrim"
#define AppVersion "1.0.0"
#define AppPublisher "pblab"
#define AppExeName "VideoTrim.exe"
#define DistDir "build\windows-python\dist\VideoTrim"

[Setup]
AppId={{B2A1F3E4-7C6D-4B2A-9E1F-1D0E0A0B0C0D}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=build\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
