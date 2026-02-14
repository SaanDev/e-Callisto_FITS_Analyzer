#define AppName "e-CALLISTO FITS Analyzer"
#define AppVersion "2.0"
#define AppPublisher "Sahan S. Liyanage"

; Override this at compile time if needed:
; iscc /DSourceDir="C:\Users\kavin\Projects\e-Callisto_FITS_Analyzer" FITS_Analyzer_InnoSetup.iss
#ifndef SourceDir
  #define SourceDir "..\.."
#endif

; Auto-detect the PyInstaller output folder/exe so packaging works for either spec naming.
#ifexist "{#SourceDir}\dist\e-Callisto FITS Analyzer\e-Callisto FITS Analyzer.exe"
  #define DistSubdir "e-Callisto FITS Analyzer"
  #define AppExeName "e-Callisto FITS Analyzer.exe"
#elifexist "{#SourceDir}\dist\e-callisto-fits-analyzer\e-callisto-fits-analyzer.exe"
  #define DistSubdir "e-callisto-fits-analyzer"
  #define AppExeName "e-callisto-fits-analyzer.exe"
#else
  #error "PyInstaller output not found under dist\. Build the app first using FITS_Analyzer_win.spec."
#endif

[Setup]
AppId={{8D3A5938-5A86-4E5F-B6D9-0F4BE4E2A94D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#SourceDir}\dist
OutputBaseFilename=e-CALLISTO_FITS_Analyzer_v{#AppVersion}_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#SourceDir}\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\dist\{#DistSubdir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
