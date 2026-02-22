#define AppName "e-CALLISTO FITS Analyzer"
#ifndef AppVersion
  #define AppVersion "2.1"
#endif
#define AppPublisher "Sahan S. Liyanage"
#define AppExeName "e-Callisto FITS Analyzer.exe"

; Override this at compile time if needed:
; iscc /DRepoRoot="C:\Users\kavin\Projects\e-Callisto_FITS_Analyzer" FITS_Analyzer_InnoSetup.iss
; If not provided, use the local default root below.
#ifndef RepoRoot
  #define RepoRoot AddBackslash(SourcePath) + "..\.."
#endif

#ifndef DistDir
  #define DistDir RepoRoot + "\dist\e-Callisto FITS Analyzer"
#endif

[Setup]
AppId={{8D3A5938-5A86-4E5F-B6D9-0F4BE4E2A94D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#RepoRoot}\dist
OutputBaseFilename=e-CALLISTO_FITS_Analyzer_v{#AppVersion}_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#RepoRoot}\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
