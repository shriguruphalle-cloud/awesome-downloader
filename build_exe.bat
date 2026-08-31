@echo off
REM Builds a standalone Windows .exe (no Python needed to run it afterwards).
REM Uses a dedicated Python 3.12 virtual environment (.venv312) so libtorrent
REM bundles correctly even if your system's default "python" is newer than
REM what libtorrent currently ships wheels for (see README's Troubleshooting
REM section for why).

if not exist ".venv312\Scripts\python.exe" (
    echo Creating Python 3.12 virtual environment (.venv312)...
    py -3.12 -m venv .venv312
    if errorlevel 1 (
        echo.
        echo Could not find Python 3.12 via the "py" launcher.
        echo Install it first, then re-run this script:
        echo   winget install --id Python.Python.3.12
        pause
        exit /b 1
    )
)

echo Installing build tools and dependencies...
".venv312\Scripts\python.exe" -m pip install --upgrade pip -q
".venv312\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller -q

REM Bundle ffmpeg into the installer so users never need to install it
REM separately (a real support issue from an earlier round: a user's laptop
REM was missing ffmpeg and downloads needing muxing just failed). Cached --
REM only fetched once per checkout, not on every rebuild. If the download
REM fails (no network, a mirror down), the build still continues: installer.iss
REM bundles vendor\ffmpeg.exe only if it exists, and the app's own in-app
REM "Install ffmpeg" prompt (Check for Updates panel) is still there as a
REM fallback for whoever ends up without it.
if not exist "vendor\ffmpeg.exe" (
    echo Fetching ffmpeg to bundle into the installer...
    ".venv312\Scripts\python.exe" -c "import sys, os; sys.path.insert(0, '.'); from app import config; from app.core import ffmpeg_utils; os.makedirs('vendor', exist_ok=True); config.FFMPEG_BUNDLED_PATH = os.path.join('vendor', 'ffmpeg.exe'); ffmpeg_utils.download_ffmpeg(progress_callback=print)"
    if not exist "vendor\ffmpeg.exe" (
        echo WARNING: Could not download ffmpeg -- continuing without bundling it.
        echo The installer will still work; the in-app "Install ffmpeg" prompt
        echo covers anyone who ends up without it.
    )
)

echo Generating version-info metadata...
".venv312\Scripts\python.exe" build_version_info.py

REM ui_qt/fonts/ (bundled Inter .ttf), app_icon.png (the topbar/browser-home
REM wordmark logo, loaded by path, not import), and ui_qt/browser_assets/
REM (the ad-block domain list + overlay button logo the Browser tab reads
REM at runtime) are all added as data explicitly for the same reason:
REM PyInstaller's import scanner has no way to see a plain file path.
echo Building Awesome Downloader.exe ...
".venv312\Scripts\python.exe" -m PyInstaller --onefile --windowed --name "Awesome Downloader" --icon "app_icon.ico" --add-data "app_icon.ico;." --add-data "app_icon.png;." --add-data "ui_qt\fonts;ui_qt\fonts" --add-data "ui_qt\browser_assets;ui_qt\browser_assets" --version-file "version_info.txt" --collect-all libtorrent --collect-all PIL --collect-all PySide6 --collect-all qframelesswindow --collect-all curl_cffi main_qt.py

REM Optional, no-op unless you actually have a code-signing certificate --
REM SmartScreen's "unrecognized publisher" warning needs real code signing
REM to go away, which needs a certificate this project doesn't have (an
REM OV cert, Azure Trusted Signing enrollment, or a SignPath Foundation
REM grant -- all real-world decisions for you to make, not something to
REM assume/fake here). Set CODESIGN_CERT_PATH (and CODESIGN_CERT_PASSWORD
REM if the .pfx needs one) before running this script to actually sign;
REM leave them unset and this step does nothing, same as always.
if defined CODESIGN_CERT_PATH (
    echo Signing Awesome Downloader.exe with %CODESIGN_CERT_PATH% ...
    if defined CODESIGN_CERT_PASSWORD (
        signtool sign /f "%CODESIGN_CERT_PATH%" /p "%CODESIGN_CERT_PASSWORD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Awesome Downloader.exe"
    ) else (
        signtool sign /f "%CODESIGN_CERT_PATH%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Awesome Downloader.exe"
    )
    if errorlevel 1 (
        echo WARNING: Signing failed -- continuing with an unsigned .exe.
    )
)

echo.
echo Done. Find your app at: dist\Awesome Downloader.exe
pause
