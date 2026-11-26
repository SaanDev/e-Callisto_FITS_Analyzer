; e-CALLISTO FITS Analyzer Lite: the Windows installer, after the full app's
; (packaging/windows/FITS_Analyzer_InnoSetup.iss). Its own AppId, install
; folder and Start menu group, so it installs beside the full app.
;
; Build with build_windows_installer.ps1, or:
;   iscc /DAppVersion=3.3.0-lite /DNumericVersion=3.3.0 /DStageDir=...\stage Lite_InnoSetup.iss
; The installer is e-CALLISTO_FITS_Analyzer_v3.3.0-lite_Setup.exe, the name
; the update check looks for in the release tag (v3.3.0).

#define AppName "e-CALLISTO FITS Analyzer Lite"
#ifndef AppVersion
  #define AppVersion "3.3.0-lite"
#endif
; Windows' version fields take numbers only: 3.3.0-lite -> 3.3.0.
#ifndef NumericVersion
  #define NumericVersion "3.3.0"
#endif
#define AppPublisher "Sahan S. Liyanage"
#define AppExeName "e-CALLISTO FITS Analyzer Lite.exe"

#ifndef RepoRoot
  #define RepoRoot AddBackslash(SourcePath) + "..\..\.."
#endif

#ifndef StageDir
  #define StageDir RepoRoot + "\lite\dist\stage"
#endif

; Target CPU: x64 (default) or arm64 (build_windows_installer.ps1 -Arch arm64
; passes /DArch=arm64). The arm64 installer is
; e-CALLISTO_FITS_Analyzer_v3.3.0-lite_arm64_Setup.exe and refuses x64
; machines; both share one AppId, so either replaces the other.
#ifndef Arch
  #define Arch "x64"
#endif
#if Arch == "arm64"
  #define ArchAllowed "arm64"
  #define ArchSuffix "_arm64"
#else
  #define ArchAllowed "x64compatible"
  #define ArchSuffix ""
#endif

[Setup]
AppId={{ABFBF10E-8013-4726-B4D1-9694707E525F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://ecallistoanalyzer.org/
AppSupportURL=https://ecallistoanalyzer.org/
AppUpdatesURL=https://ecallistoanalyzer.org/
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#RepoRoot}\lite\dist
OutputBaseFilename=e-CALLISTO_FITS_Analyzer_v{#AppVersion}{#ArchSuffix}_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed={#ArchAllowed}
ArchitecturesInstallIn64BitMode={#ArchAllowed}
SetupIconFile={#RepoRoot}\assets\branding\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
VersionInfoVersion={#NumericVersion}
VersionInfoProductTextVersion={#AppVersion}
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
