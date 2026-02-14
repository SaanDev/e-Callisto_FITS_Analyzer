#define AppName "e-CALLISTO FITS Analyzer"
#define AppVersion "2.0"
#define AppPublisher "Sahan S. Liyanage"

; Override this at compile time if needed:
; iscc /DRepoRoot="C:\Users\kavin\Projects\e-Callisto_FITS_Analyzer" FITS_Analyzer_InnoSetup.iss
; If not provided, resolve repo root from this script path: <repo>\src\Installation\*.iss -> <repo>
#ifndef RepoRoot
  #ifdef SourceDir
    ; Backward compatibility with old /DSourceDir override.
    #define RepoRoot SourceDir
  #else
    #define RepoRoot SourcePath + "..\.."
  #endif
#endif

; Auto-detect PyInstaller output for either onedir/onefile and naming variants.
#ifexist "{#RepoRoot}\dist\e-Callisto FITS Analyzer\e-Callisto FITS Analyzer.exe"
  #define DistSource RepoRoot + "\dist\e-Callisto FITS Analyzer\*"
  #define DistFlags "ignoreversion recursesubdirs createallsubdirs"
  #define AppExeName "e-Callisto FITS Analyzer.exe"
#elifexist "{#RepoRoot}\dist\e-callisto-fits-analyzer\e-callisto-fits-analyzer.exe"
  #define DistSource RepoRoot + "\dist\e-callisto-fits-analyzer\*"
  #define DistFlags "ignoreversion recursesubdirs createallsubdirs"
  #define AppExeName "e-callisto-fits-analyzer.exe"
#elifexist "{#RepoRoot}\dist\e-Callisto FITS Analyzer.exe"
  #define DistSource RepoRoot + "\dist\e-Callisto FITS Analyzer.exe"
  #define DistFlags "ignoreversion"
  #define AppExeName "e-Callisto FITS Analyzer.exe"
#elifexist "{#RepoRoot}\dist\e-callisto-fits-analyzer.exe"
  #define DistSource RepoRoot + "\dist\e-callisto-fits-analyzer.exe"
  #define DistFlags "ignoreversion"
  #define AppExeName "e-callisto-fits-analyzer.exe"
#else
  #error "PyInstaller output not found under <repo>\dist. Build first, or pass /DRepoRoot=<repo path>."
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
Source: "{#DistSource}"; DestDir: "{app}"; Flags: {#DistFlags}

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
