# Building Awesome Downloader

Every released binary is produced by the steps below, from this repository,
with no manual edits to the output.

## Requirements

- Windows 10 or 11, 64-bit
- **Python 3.12**, 64-bit ([python.org](https://www.python.org/downloads/))
  — 3.12 specifically, because libtorrent publishes no wheels for 3.14 yet
  (see `LIBTORRENT_MIN_PY` / `LIBTORRENT_MAX_PY` in `app/config.py`)
- [Inno Setup 6](https://jrsoftware.org/isdl.php), only for the installer

## Build the application

```bat
build_exe.bat
```

That script is the whole build. It:

1. creates `.venv312` (Python 3.12) if missing
2. installs `requirements.txt` plus PyInstaller into it
3. downloads FFmpeg to `vendor\ffmpeg.exe` if not already present
   (cached — fetched once, not on every rebuild)
4. generates `version_info.txt` from `app/config.py` via `build_version_info.py`
5. runs PyInstaller

Output: `dist\Awesome Downloader.exe`

The PyInstaller invocation, if you prefer to run it directly:

```bat
.venv312\Scripts\python.exe -m PyInstaller --onefile --windowed ^
  --name "Awesome Downloader" --icon "app_icon.ico" ^
  --add-data "app_icon.ico;." --add-data "ui_qt\fonts;ui_qt\fonts" ^
  --version-file "version_info.txt" ^
  --collect-all libtorrent --collect-all PIL ^
  --collect-all PySide6 --collect-all qframelesswindow ^
  main_qt.py
```

`--add-data "ui_qt\fonts;..."` is required: the bundled Inter font is loaded
at runtime by path (`ui_qt/theme.py`, `load_custom_fonts`), so PyInstaller's
import scanner cannot discover it on its own.

## Build the installer

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

Output: `Output\AwesomeVideoDownloaderSetup.exe`

`installer.iss` bundles `dist\Awesome Downloader.exe`, `app_icon.ico`,
`README.md` and `vendor\ffmpeg.exe`, so build the application first.

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

Byte-for-byte reproducible builds are **not** currently claimed. PyInstaller
embeds timestamps, and dependency versions float within the ranges pinned in
`requirements.txt`. What is guaranteed is that the released binary is built
from this source by the commands above, with no post-build modification.

To reproduce a specific release more closely, pin every dependency to the
exact versions recorded in `NOTICE.md` for that version.

## Running from source

```bat
.venv312\Scripts\python.exe main_qt.py
```

`main_qt.py` is the only entry point.
