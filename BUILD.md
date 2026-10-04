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
4. generates `version_info.txt` from `app/config.py` via `build_version_info.py`
5. runs PyInstaller as a **folder build** (`--onedir`)
6. deletes the `*.debug.pak` / `*.debug.bin` QtWebEngine resources — the
   PySide6 wheel carries a second copy of them for debug builds of Qt, which
   a release build never opens (about 77 MB)

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
  --add-data "ui_qt\fonts;ui_qt\fonts" ^
  --add-data "ui_qt\browser_assets;ui_qt\browser_assets" ^
  --version-file "version_info.txt" ^
  --collect-all libtorrent --collect-all PIL ^
  --collect-all qframelesswindow --collect-all curl_cffi ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuickWidgets ^
  main_qt.py
```

- The `--add-data` folders are required: the Inter font, the logo and the
  Browser tab's assets are loaded at runtime by path, so PyInstaller's import
  scanner cannot discover them on its own.
- There is deliberately no `--collect-all PySide6`: PyInstaller's own Qt
  hooks collect exactly the Qt modules the app imports, QtWebEngine's helper
  process and resources included.
- The three `--exclude-module` flags stop the QtQml hook from copying every
  QML plugin (and with them Qt3D, Charts, Location, ...). QtWebEngine links
  against the QML/Quick *DLLs*, which are still collected as ordinary binary
  dependencies; it never needs the QML plugins.

## Build the installer

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

Output: `Output\AwesomeVideoDownloaderSetup.exe`

`installer.iss` packs the whole `dist\Awesome Downloader\` folder plus
`app_icon.ico`, `README.md` and `vendor\ffmpeg.exe`, so build the application
first. On an upgrade it clears the old `{app}\_internal` folder before
copying, so no stale library from a previous version is left beside the new
ones.

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
output is the removal of the debug-only QtWebEngine resources (step 6).

## Running from source

```bat
.venv312\Scripts\python.exe main_qt.py
```

`main_qt.py` is the only entry point.
