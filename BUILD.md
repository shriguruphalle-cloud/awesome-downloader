# Building Awesome Downloader

Every released binary is produced by the steps below, from this repository.

## Requirements

- Windows 10 or 11, 64-bit
- **Python 3.12**, 64-bit ([python.org](https://www.python.org/downloads/))
  — 3.12 specifically, because libtorrent publishes no wheels for 3.14 yet
  (see `LIBTORRENT_MIN_PY` / `LIBTORRENT_MAX_PY` in `app/config.py`)
- [Inno Setup 6](https://jrsoftware.org/isdl.php), only for the installer

## Test first

```bat
.venv312\Scripts\python.exe tests\run_all.py
```

Runs every offline suite, each in its own process, in about 20 seconds. The
tests point `LOCALAPPDATA` and `USERPROFILE` at a throwaway folder before
anything is imported, so they never read or write your real settings, queue,
history or browser profile. Useful variants:

```bat
.venv312\Scripts\python.exe tests\run_all.py design topbar
.venv312\Scripts\python.exe tests\run_all.py --network
```

The first runs only the suites whose names contain those words; the second
also runs `tests\test_net_*.py`, which talk to YouTube and GitHub (they read
metadata only — nothing is downloaded).

## Build the application

```bat
build_exe.bat
```

That script is the whole build. It:

1. creates `.venv312` (Python 3.12) if missing
2. installs the exact versions in `requirements-lock.txt` (falls back to the
   ranges in `requirements.txt` if the lock file is missing)
3. downloads FFmpeg to `vendor\ffmpeg.exe` if not already present
   (cached — fetched once, not on every rebuild)
4. prepares the AdGuard extension and the WebView2 SDK files in `vendor\`
   via `build_vendor.py` (cached as well)
5. generates `version_info.txt` from `app/config.py` via `build_version_info.py`
6. runs PyInstaller as a **folder build** (`--onedir`)
7. leaves out the parts of Qt the app never loads, via `build_trim.py`
   (about 44 MB: the software OpenGL renderer, Qt's translations, the PDF
   image plugin and the on-screen keyboard with its Qt Quick/QML). It first
   checks that nothing left in the build links to any of them; if something
   does, it removes nothing and the build stops.

Output: the folder `dist\Awesome Downloader\` — `Awesome Downloader.exe`
plus its `_internal\` libraries. The folder is the app; the .exe alone will
not run.

Why a folder and not `--onefile`: a onefile .exe unpacks its whole bundle
(Qt and Chromium included) into a temp folder on every launch before the
first window can appear. 2.4.0 took 8.4 s to open; the 2.5.0 folder build
opens in about 1.5 s.

The PyInstaller invocation, if you prefer to run it directly:

```bat
.venv312\Scripts\python.exe -m PyInstaller --noconfirm --onedir --windowed ^
  --name "Awesome Downloader" --icon "app_icon.ico" ^
  --add-data "app_icon.ico;." --add-data "app_icon.png;." ^
  --add-data "ui_qt\fonts;ui_qt\fonts" --add-data "ui_qt\assets;ui_qt\assets" ^
  --add-data "ui_qt\browser_assets;ui_qt\browser_assets" ^
  --add-data "vendor\adguard-dl\unpacked;adguard" --add-data "vendor\webview2;webview2" ^
  --version-file "version_info.txt" ^
  --collect-all libtorrent --collect-all PIL ^
  --collect-all qframelesswindow --collect-all curl_cffi ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuickWidgets ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtWebChannel ^
  --exclude-module webview ^
  main_qt.py
.venv312\Scripts\python.exe build_trim.py "dist\Awesome Downloader"
```

- The `--add-data` folders are required: the fonts, the logo, the donate
  QR, the Browser tab's assets, AdGuard and the WebView2 SDK are all loaded
  at runtime by path, so PyInstaller's import scanner cannot discover them.
- There is deliberately no `--collect-all PySide6`: PyInstaller's own Qt
  hooks collect exactly the Qt modules the app imports.
- The Browser tab runs on Microsoft Edge WebView2 (part of Windows), so Qt
  WebEngine is excluded outright, and with it QtQml/QtQuick, whose hook
  would copy every QML plugin (Qt3D, Charts, Location, ...).

## Build the installer

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

Output: `Output\AwesomeVideoDownloaderSetup.exe`

`installer.iss` packs the whole `dist\Awesome Downloader\` folder plus
`app_icon.ico`, `README.md` and `vendor\ffmpeg.exe`, so build the application
first. A build made in another folder is packed with
`ISCC "/DAppSource=that\folder" installer.iss`. On an upgrade it clears the
old `{app}\_internal` folder before copying, so no stale library from a
previous version is left beside the new ones.

The installer waits for the app to close before replacing anything, and
installs an uninstaller (Settings > Apps, the Start menu, or Settings > Your
data > Uninstall inside the app). Uninstalling removes the app, its magnet
link and start-with-Windows entries when they still point at it, and asks
whether to keep the user's data (`%LOCALAPPDATA%\Awesome Downloader`).

## Publishing an update

The app installs updates from inside itself (**Update available** in the
title bar, a bar in the Browser tab, Settings > Updates) — but only releases
signed with your key. Each release's `update.json` (version, installer name,
SHA-256, size, notes) is signed with Ed25519; the app checks it against the
public key in `app/config.py` (`UPDATE_PUBLIC_KEY`), then downloads the
installer from GitHub over HTTPS, checks its size, checksum and version,
has Microsoft Defender scan it, and runs it silently (it relaunches the app).
Settings offers to go back to the version before, checked the same way.

**Once:** the signing key. It already exists on the development PC
(`%USERPROFILE%\.awesome-downloader\release-signing.key`, matching the
public key in `app/config.py`). **Back it up somewhere safe and never commit
or share it** — anyone with it can publish updates the app will install;
without it no update can be published (a new key means a new public key,
which people only get by installing a version by hand). To make a new one:
`.venv312\Scripts\python.exe tools\release_sign.py keygen --force`.

**Every release:**

1. Bump `APP_VERSION` in `app/config.py` and `MyAppVersion` in `installer.iss`;
   write `RELEASE_v<version>.md` (its "What's new" becomes the in-app notes).
2. `build_exe.bat`, then `ISCC installer.iss` (see above).
3. `.venv312\Scripts\python.exe tools\release_sign.py sign`
   → `Output\update.json` and `Output\update.json.sig`.
4. Publish a GitHub release tagged `v<version>` with all three files:
   ```bat
   gh release create v<version> Output\AwesomeVideoDownloaderSetup.exe ^
     Output\update.json Output\update.json.sig --notes-file RELEASE_v<version>.md
   ```

Running copies see it at their next start. A release without `update.json`
is still announced, but only opens the release page.

## Code signing

`build_exe.bat` contains an optional signing step that is a no-op unless a
certificate is configured:

```bat
set CODESIGN_CERT_PATH=C:\path\to\cert.pfx
set CODESIGN_CERT_PASSWORD=...
build_exe.bat
```

With those unset, the build runs identically and produces an unsigned binary.

## Reproducibility notes

Byte-for-byte reproducible builds are **not** claimed — PyInstaller embeds
timestamps. What is guaranteed is that the released binary is built from this
source by the commands above, with every dependency at the version pinned in
`requirements-lock.txt`, and that the only change made to PyInstaller's
output is the removal of the unused Qt parts listed in `build_trim.py`
(step 7).

## Running from source

```bat
.venv312\Scripts\python.exe main_qt.py
```

`main_qt.py` is the only entry point.
