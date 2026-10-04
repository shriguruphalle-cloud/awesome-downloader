; ============================================================================
; AWESOME DOWNLOADER — Inno Setup installer script
; ============================================================================
; This turns your already-built app into a real Windows installer wizard:
; Start Menu shortcut, optional Desktop shortcut, "Add/Remove Programs"
; entry, a proper uninstaller, and registration as the handler for
; magnet: links (so clicking one in a browser offers to open this app) --
; all automatic.
;
; HOW TO USE (see the "Building a Windows installer" section in README.md
; for the fully detailed walkthrough):
;   1. Build the app first by running build_exe.bat
;      -> this creates the folder:  dist\Awesome Downloader\
;   2. Install Inno Setup (free): https://jrsoftware.org/isdl.php
;   3. Double-click this file (installer.iss) to open it in Inno Setup
;   4. Press Ctrl+F9 (or Build > Compile)
;   5. Your installer appears at:  Output\AwesomeVideoDownloaderSetup.exe
; ============================================================================

#define MyAppName "AWESOME DOWNLOADER"
; How the app is named where people read it: the Start menu, Settings > Apps,
; the wizard. MyAppName stays for the install folder and Start menu group, so
; an upgrade lands in the same place as every earlier version.
#define MyAppDisplayName "Awesome Downloader"
#define MyAppVersion "2.5.0"
#define MyAppPublisher "Shriguru Phalle"
#define MyAppExeName "Awesome Downloader.exe"
#define MyAppURL "https://awesome-downloader.pages.dev"
#define MyAppRepo "https://github.com/shriguruphalle-cloud/awesome-downloader"
; The app holds this mutex while it runs (app/utils/single_instance.py).
#define MyAppMutex "AwesomeDownloaderRunning"
; The built app to pack. build_exe.bat builds into dist\; a build made
; elsewhere is packed with  ISCC /DAppSource="that\folder" installer.iss
#ifndef AppSource
  #define AppSource "dist\Awesome Downloader"
#endif

[Setup]
AppId={{B6E1B6C0-6D2F-4F1A-9C0A-1F1B2A9F1A11}}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppRepo}/issues
AppUpdatesURL={#MyAppRepo}/releases/latest
; Settings > Apps lists it by this name, with its icon, version, publisher
; and size, and its Uninstall button runs the uninstaller below.
UninstallDisplayName={#MyAppDisplayName}
; Setup and the uninstaller both check whether the app is running and ask
; for it to be closed first: replacing or deleting files it has open left
; half an upgrade, or half an app behind. Both names: this session's, and
; machine-wide for a copy running in another user's session.
AppMutex={#MyAppMutex},Global\{#MyAppMutex}
; Windows is told the magnet: association changed, at install and uninstall.
ChangesAssociations=yes
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AwesomeVideoDownloaderSetup
SetupIconFile=app_icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Branded wizard graphics -- without these Inno falls back to its own stock
; images, which is why the setup screens still showed generic art after the
; app icon itself had been replaced. Comma-separated sizes let Inno pick the
; right one for the user's DPI scaling instead of upscaling a small bitmap.
WizardImageFile=installer_assets\wizard_large.bmp,installer_assets\wizard_large@2x.bmp
WizardSmallImageFile=installer_assets\wizard_small.bmp,installer_assets\wizard_small@2x.bmp
UninstallDisplayIcon={app}\{#MyAppExeName}
; Without these the setup binary ships with an empty FileVersion: Windows'
; file properties, and most download sites that read it, then show a blank
; where the version should be -- on the one file a first-time user inspects
; before deciding to run an unsigned installer.
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductName={#MyAppDisplayName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppDisplayName} Setup
VersionInfoCopyright=Copyright (C) {#MyAppPublisher}
ArchitecturesInstallIn64BitMode=x64compatible
; The bundled app is a 64-bit build (PyInstaller + 64-bit Python, verified
; from its PE header: machine = AMD64), so it physically cannot run on
; 32-bit Windows. Without this line Setup itself -- which is a 32-bit
; bootstrap and so starts anywhere -- would happily install onto such a
; machine, and the failure only showed up afterwards as Windows' "This app
; can't run on your PC" (reported by real users). Declaring it here makes
; Setup refuse up front with a clear explanation instead of half-installing
; something unusable. x64compatible also covers ARM64, which runs x64
; binaries under emulation.
ArchitecturesAllowed=x64compatible
; Installer itself needs admin rights to write to Program Files
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; ffmpeg is the one bundled dependency that's a genuinely standalone binary
; (yt-dlp/libtorrent are compiled directly into the .exe by PyInstaller, so
; there's no separate "latest version" to fetch for those at install time --
; the bundled build-day ffmpeg.exe from [Files] below already covers most
; users; this is just an optional refresh to whatever is newest right now).
; Unchecked by default and fully best-effort (see UpdateFFmpegIfRequested in
; [Code]) so a slow/unavailable network can never fail the install itself.
Name: "updateffmpeg"; Description: "Download the latest version of ffmpeg during setup (recommended, needs internet)"; GroupDescription: "Additional options:"; Flags: unchecked

[InstallDelete]
; 2.5 ships as a folder (the .exe plus its _internal libraries) instead of a
; single self-extracting .exe. Clearing the old library folder before
; copying means an upgrade never leaves a stale DLL from the previous
; version beside the new ones.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; The whole PyInstaller output folder: the .exe and its _internal libraries.
; A ffmpeg.exe beside a test build's exe is skipped: ffmpeg comes from vendor\ below, once.
Source: "{#AppSource}\*"; DestDir: "{app}"; Excludes: "\ffmpeg.exe"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
; Optional: bundle ffmpeg so users don't need to install it separately.
; To enable, create a "vendor" folder next to this .iss file and put
; ffmpeg.exe inside it. If the folder/file doesn't exist, this line is
; skipped automatically and the installer still works fine.
Source: "vendor\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app_icon.ico"
Name: "{group}\Uninstall {#MyAppDisplayName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\app_icon.ico"
Name: "{autodesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon

[Registry]
; No PATH entry. This used to append {app} to the user's Path so the bundled
; ffmpeg could be found -- but the app never needed it: ffmpeg_utils.ffmpeg_path()
; looks for ffmpeg.exe beside the executable (config.BASE_DIR, which is
; os.path.dirname(sys.executable) in a frozen build) and only falls back to PATH
; if that is missing. [Files] installs vendor\ffmpeg.exe to exactly that folder.
;
; Worse than unnecessary, it was wrong: with PrivilegesRequired=admin the HKCU
; hive being written is the *elevating* account's, so when an admin installs for
; someone else the entry lands in the wrong user's environment -- and it
; permanently modified a PATH the app doesn't read.

; Register as a handler for magnet: links (same technique real torrent
; clients like qBittorrent/uTorrent use). Written to HKA, not HKCU: HKA
; resolves to HKLM when Setup is running elevated (this installer requires
; admin, so the association covers every account on the machine, matching an
; install into Program Files) and to HKCU when it is not. Hard-coding HKCU
; registered the handler for whichever account happened to click through the
; UAC prompt, which is not necessarily the person who will use the app.
; Chrome/Edge
; will offer "Open AWESOME DOWNLOADER?" the next time a magnet link is
; clicked once this is registered. The app receives the link as a command-
; line argument (see app/main_qt.py) and routes it straight into the Torrent
; tab's existing add-magnet flow.
; Not uninsdeletekey: by the time of an uninstall another torrent client may
; have taken magnet: over, and deleting the whole key would break it. The
; uninstaller removes the key only if it still points here ([Code] below).
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: ""; \
    ValueData: "URL:Magnet Link"
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: "URL Protocol"; \
    ValueData: ""
Root: HKA; Subkey: "Software\Classes\magnet\DefaultIcon"; ValueType: string; ValueName: ""; \
    ValueData: "{app}\app_icon.ico"
Root: HKA; Subkey: "Software\Classes\magnet\shell\open\command"; ValueType: string; ValueName: ""; \
    ValueData: """{app}\{#MyAppExeName}"" ""%1"""


[Run]
; runasoriginaluser: Setup runs as administrator, and without this the app it
; launched ran as administrator too -- its browser, its downloads and its
; files then belonged to the elevated account, not to whoever installed it.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppDisplayName}}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; What the app or the ffmpeg refresh may have added beside the installed
; files. Never the whole {app}: it is whatever folder was chosen at install,
; which could hold other things.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\ffmpeg.exe"
Type: dirifempty; Name: "{app}"

[Code]
// Best-effort only: downloads a fresh ffmpeg.exe over whatever build-day
// copy [Files] already installed. Every failure mode (no internet, both
// mirrors down, PowerShell missing/blocked) is swallowed by the script's
// own try/catch and by ignoring Exec's result code here -- this can only
// ever improve on the bundled ffmpeg, never break the install, since the
// working build-day copy is never removed before the replacement is
// actually ready.
procedure UpdateFFmpegIfRequested;
var
  ResultCode: Integer;
  PSCommand: string;
  AppFfmpegPath: string;
begin
  if not WizardIsTaskSelected('updateffmpeg') then
    exit;

  AppFfmpegPath := ExpandConstant('{app}\ffmpeg.exe');

  PSCommand :=
    '$ErrorActionPreference=''SilentlyContinue''; ' +
    'try { ' +
    '  $urls = @(' +
    '    ''https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'', ' +
    '    ''https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip''' +
    '  ); ' +
    '  $tmpZip = Join-Path $env:TEMP ''awesomedl_ffmpeg_setup.zip''; ' +
    '  $tmpDir = Join-Path $env:TEMP ''awesomedl_ffmpeg_setup_extract''; ' +
    '  $ok = $false; ' +
    '  foreach ($u in $urls) { ' +
    '    try { Invoke-WebRequest -Uri $u -OutFile $tmpZip -UseBasicParsing -TimeoutSec 90; $ok = $true; break } catch { } ' +
    '  } ' +
    '  if ($ok) { ' +
    '    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue; ' +
    '    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force; ' +
    '    $exe = Get-ChildItem -Path $tmpDir -Recurse -Filter ffmpeg.exe | Select-Object -First 1; ' +
    '    if ($exe) { Copy-Item -Path $exe.FullName -Destination ''' + AppFfmpegPath + ''' -Force } ' +
    '  } ' +
    '  Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue; ' +
    '  Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue; ' +
    '} catch { } ';

  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "' + PSCommand + '"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    UpdateFFmpegIfRequested;
  end;
end;

// Everything the app remembers -- settings, download and browsing history,
// bookmarks, the queued-link list, the browser profile with its logins --
// lives in %LOCALAPPDATA%\Awesome Downloader, outside {app}, so uninstalling
// never touched it. That is the right default: an uninstall is often really
// a reinstall, and silently throwing away someone's history and saved logins
// because they updated the app would be indefensible.
//
// But it left no way to remove it either, which is its own problem for
// anyone actually leaving. So: asked once, at uninstall, defaulting to No.
// What the app itself registered for the person using it, outside anything
// Setup installed: magnet: links (Torrent tab, "Open magnet links with this
// app") and Launch at Windows startup (Settings). Each is removed only if it
// still points into this install -- if another torrent client has since
// taken magnet: over, it keeps it.
function PointsHere(Value: string): Boolean;
begin
  Result := Pos(Lowercase(AddBackslash(ExpandConstant('{app}'))), Lowercase(Value)) > 0;
end;

procedure RemoveMagnetHandler(RootKey: Integer);
var
  Command: string;
begin
  if RegQueryStringValue(RootKey, 'Software\Classes\magnet\shell\open\command', '', Command) and
     PointsHere(Command) then
    RegDeleteKeyIncludingSubkeys(RootKey, 'Software\Classes\magnet');
  if RegQueryStringValue(RootKey, 'Software\Classes\AwesomeDownloader.Magnet\shell\open\command', '', Command) and
     PointsHere(Command) then
    RegDeleteKeyIncludingSubkeys(RootKey, 'Software\Classes\AwesomeDownloader.Magnet');
end;

procedure RemoveStartupEntry;
var
  Command: string;
begin
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AwesomeDownloader', Command) and
     PointsHere(Command) then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AwesomeDownloader');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    RemoveMagnetHandler(HKCU);
    RemoveMagnetHandler(HKLM);
    RemoveStartupEntry;
    exit;
  end;
  if CurUninstallStep <> usPostUninstall then
    exit;

  DataDir := ExpandConstant('{localappdata}\Awesome Downloader');
  if not DirExists(DataDir) then
    exit;

  // Suppressible, answered No: a silent uninstall (/SILENT, /VERYSILENT)
  // never stops to ask, and never deletes anyone's data.
  if SuppressibleMsgBox('Also remove your Awesome Downloader data?' + #13#10 + #13#10 +
            'This deletes your settings, download history, browsing history, ' +
            'bookmarks, queued links, and any sites you signed in to inside ' +
            'the app''s browser.' + #13#10 + #13#10 +
            'Choose No to keep it all for a future reinstall.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
  begin
    DelTree(DataDir, True, True, True);
  end;
end;
