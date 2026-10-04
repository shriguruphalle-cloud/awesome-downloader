# Third-party components

Awesome Downloader is licensed under the **GNU General Public License v3.0**
(see `LICENSE`). GPL v3 was chosen because the released installer bundles an
FFmpeg build compiled with `--enable-gpl --enable-version3`, whose terms
apply to the combined distribution.

The following components are redistributed with the application.

| Component | Version | Licence | Bundled in the installer |
|---|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 2026.07.04 | Unlicense (public domain) | Yes (inside the executable) |
| [curl_cffi](https://github.com/lexiforest/curl_cffi) | 0.15.0 | MIT | Yes (inside the executable) |
| [FFmpeg](https://ffmpeg.org/) (`essentials` build by [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)) | 9.0 | **GPL v3** (`--enable-gpl --enable-version3`) | Yes (`ffmpeg.exe`) |
| [libtorrent](https://libtorrent.org/) | 2.0.13 | BSD 3-Clause | Yes (inside the executable) |
| [Qt](https://www.qt.io/) via [PySide6](https://doc.qt.io/qtforpython/) | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only (used under LGPL v3) | Yes (inside the executable) |
| [Pillow](https://python-pillow.org/) | 12.3.0 | MIT-CMU | Yes (inside the executable) |
| [PySideSix-Frameless-Window](https://github.com/zhiyiYo/PyQt-Frameless-Window) | 0.8.2 | LGPL v3 | Yes (inside the executable) |
| [pywin32](https://github.com/mhammond/pywin32) | 312 | PSF | Yes (inside the executable) |
| [Inter](https://rsms.me/inter/) typeface | variable | SIL Open Font License 1.1 | Yes (`ui_qt/fonts/InterVariable.ttf`) |
| [Instrument Serif](https://github.com/Instrument/instrument-serif) typeface (regular, italic) | Latin subset, woff2 (and the same files unpacked to TrueType, unmodified, for Qt) | SIL Open Font License 1.1 | Yes (`ui_qt/browser_assets/home/fonts/`, `ui_qt/fonts/InstrumentSerif-*.ttf`) |
| [Archivo](https://github.com/Omnibus-Type/Archivo) typeface (variable 400–700) | Latin subset, woff2 | SIL Open Font License 1.1 | Yes (`ui_qt/browser_assets/home/fonts/`) |
| [IBM Plex Mono](https://github.com/IBM/plex) typeface (400, 500) | Latin subset, woff2 | SIL Open Font License 1.1 | Yes (`ui_qt/browser_assets/home/fonts/`) |
| [Python](https://www.python.org/) | 3.12 | PSF License | Yes (runtime, via PyInstaller) |
| [AdGuard AdBlocker](https://github.com/AdguardTeam/AdguardBrowserExtension) (Chrome MV3 build) | 5.5.2.3 | **GPL v3** | Yes (`_internal/adguard/`, unmodified) |
| [Microsoft Edge WebView2 SDK](https://learn.microsoft.com/microsoft-edge/webview2/) (`Microsoft.Web.WebView2.Core.dll`, `WebView2Loader.dll`) | 1.0.3856.49 | BSD 3-Clause (Microsoft) | Yes (`_internal/webview2/`) |
| [Python.NET](https://github.com/pythonnet/pythonnet) (pythonnet, clr_loader) | 3.1.0 / 0.3.1 | MIT | Yes (inside the executable) |

## Obtaining the source

The complete source for this application is in this repository. FFmpeg's
source is available from [ffmpeg.org](https://ffmpeg.org/download.html), and
the source for the exact bundled build from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/); FFmpeg is downloaded
unmodified at build time by `build_exe.bat` and is not patched here.

AdGuard AdBlocker is the unmodified official Chrome MV3 release,
`chrome-mv3.zip` from
[its GitHub releases](https://github.com/AdguardTeam/AdguardBrowserExtension/releases/tag/v5.5.2.3),
downloaded at build time by `build_vendor.py`. Its complete source is at
[github.com/AdguardTeam/AdguardBrowserExtension](https://github.com/AdguardTeam/AdguardBrowserExtension)
(tag `v5.5.2.3`). It runs as a browser extension inside the Browser tab; the
app only sends it the same messages its own settings page sends.

The Browser tab's engine, Microsoft Edge WebView2, is part of Windows 10 and
11 and is not redistributed; only the SDK files that load it are.

## LGPL relinking (Qt / PySide6)

Qt is used under the LGPL v3 via unmodified PySide6 wheels from PyPI. It is
dynamically linked, and no Qt source is modified. To use a different build of
Qt, replace the PySide6 package in the build environment and rebuild with
`build_exe.bat`; the pinned version is in `requirements.txt`.
