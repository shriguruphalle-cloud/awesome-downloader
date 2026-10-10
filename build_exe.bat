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
REM The lock file pins every version the release is built with, so two
REM builds of the same commit ship the same code. requirements.txt (loose
REM ranges) is only the fallback for a checkout without one.
if exist "requirements-lock.txt" (
    ".venv312\Scripts\python.exe" -m pip install -r requirements-lock.txt -q
) else (
    ".venv312\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller -q
)

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

REM The Browser tab's two bundled pieces: AdGuard (the official release,
REM unmodified, GPL-3.0 -- see NOTICE.md) and the WebView2 SDK files that
REM load the engine Windows already has. Both cached in vendor\. Unlike
REM ffmpeg there is no in-app fallback for these, so a failure stops the build.
echo Preparing AdGuard and the WebView2 SDK...
".venv312\Scripts\python.exe" build_vendor.py
if errorlevel 1 (
    echo.
    echo Could not prepare vendor\adguard-dl or vendor\webview2 -- see above.
    pause
    exit /b 1
)

echo Generating version-info metadata...
".venv312\Scripts\python.exe" build_version_info.py

REM ui_qt/fonts/ (bundled Inter .ttf), app_icon.png (the topbar/browser-home
REM wordmark logo, loaded by path, not import), and ui_qt/browser_assets/
REM (the ad-block domain list + overlay button logo the Browser tab reads
REM at runtime) are all added as data explicitly for the same reason:
REM PyInstaller's import scanner has no way to see a plain file path.
REM A folder build (--onedir), not --onefile. A onefile .exe unpacks its
REM whole bundle -- Qt and Chromium included, several hundred MB -- into a
REM temp folder on *every* launch before the first window can appear:
REM measured 8.4 s cold start for 2.4.0. The installer puts the folder in
REM Program Files once, and the app then starts straight from it.
REM
REM No --collect-all PySide6 either. That pulled in every Qt module that
REM exists (Qt3D, Charts, Multimedia, ...), which the app never imports;
REM PyInstaller's own PySide6 hooks collect exactly the modules it does.
REM
REM Since 2.5 the Browser tab runs on Microsoft Edge WebView2 (part of
REM Windows) instead of Qt WebEngine, so none of Qt's Chromium ships any
REM more: the WebEngine modules are excluded outright, along with QtQml and
REM QtQuick (whose hook copies every QML plugin in Qt) and pywebview (only
REM the source of the SDK files, which go in as data: "webview2").
echo Building Awesome Downloader ...
if exist "dist\Awesome Downloader.exe" del /q "dist\Awesome Downloader.exe"
".venv312\Scripts\python.exe" -m PyInstaller --noconfirm --onedir --windowed --name "Awesome Downloader" --icon "app_icon.ico" --add-data "app_icon.ico;." --add-data "app_icon.png;." --add-data "ui_qt\fonts;ui_qt\fonts" --add-data "ui_qt\assets;ui_qt\assets" --add-data "ui_qt\browser_assets;ui_qt\browser_assets" --add-data "vendor\adguard-dl\unpacked;adguard" --add-data "vendor\webview2;webview2" --version-file "version_info.txt" --collect-all libtorrent --collect-all PIL --collect-all qframelesswindow --collect-all curl_cffi --collect-all yt_dlp_ejs --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQuickWidgets --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtWebChannel --exclude-module webview main_qt.py
if errorlevel 1 (
    echo.
    echo PyInstaller failed -- see the output above.
    pause
    exit /b 1
)

REM Leaves out the parts of Qt the app never loads (~46 MB: software OpenGL,
REM Qt's translations, the PDF image plugin, the on-screen keyboard with its
REM Qt Quick/QML) -- after checking that nothing left in the build links to
REM any of it; if something does, it removes nothing and the build stops.
".venv312\Scripts\python.exe" build_trim.py "dist\Awesome Downloader"
if errorlevel 1 (
    echo.
    echo Trimming unused Qt parts failed -- see above.
    pause
    exit /b 1
)

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
        signtool sign /f "%CODESIGN_CERT_PATH%" /p "%CODESIGN_CERT_PASSWORD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Awesome Downloader\Awesome Downloader.exe"
    ) else (
        signtool sign /f "%CODESIGN_CERT_PATH%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Awesome Downloader\Awesome Downloader.exe"
    )
    if errorlevel 1 (
        echo WARNING: Signing failed -- continuing with an unsigned .exe.
    )
)

echo.
echo Done. Find your app at: dist\Awesome Downloader\Awesome Downloader.exe
echo (the whole "dist\Awesome Downloader" folder is the app -- installer.iss packs it)
pause
