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
| [Python](https://www.python.org/) | 3.12 | PSF License | Yes (runtime, via PyInstaller) |

## Obtaining the source

The complete source for this application is in this repository. FFmpeg's
source is available from [ffmpeg.org](https://ffmpeg.org/download.html), and
the source for the exact bundled build from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/); FFmpeg is downloaded
unmodified at build time by `build_exe.bat` and is not patched here.

## LGPL relinking (Qt / PySide6)

Qt is used under the LGPL v3 via unmodified PySide6 wheels from PyPI. It is
dynamically linked, and no Qt source is modified. To use a different build of
Qt, replace the PySide6 package in the build environment and rebuild with
`build_exe.bat`; the pinned version is in `requirements.txt`.
