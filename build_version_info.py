"""Generates version_info.txt for PyInstaller's --version-file flag, so the
built .exe has real CompanyName/FileDescription/ProductName/FileVersion
metadata instead of none at all (right-click -> Properties -> Details on an
unsigned .exe with no version info is one of the small signals SmartScreen
and a wary user both look at -- this doesn't fix the "unrecognized
publisher" warning by itself, but it's a real, free step that's part of
looking like a legitimate, maintained piece of software rather than a
bare, anonymous binary. Actual code signing is a separate, much bigger step
that needs a real certificate the app doesn't have yet -- see build_exe.bat's
CODESIGN_CERT_PATH handling for the no-op-by-default hook for that).

Run standalone (build_exe.bat does this before the PyInstaller build):
    python build_version_info.py
"""
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

from app import config


def _version_tuple(version_str):
    parts = [int(p) for p in version_str.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def main():
    version = _version_tuple(config.APP_VERSION)

    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=version,
            prodvers=version,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,  # VOS_NT_WINDOWS32
            fileType=0x1,  # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    "040904B0",  # US English, Unicode
                    [
                        StringStruct("CompanyName", config.APP_PUBLISHER),
                        StringStruct("FileDescription", config.APP_NAME),
                        StringStruct("FileVersion", config.APP_VERSION),
                        StringStruct("InternalName", config.APP_NAME),
                        StringStruct("LegalCopyright", f"Copyright (c) {config.APP_PUBLISHER}"),
                        StringStruct("OriginalFilename", "Awesome Downloader.exe"),
                        StringStruct("ProductName", config.APP_NAME),
                        StringStruct("ProductVersion", config.APP_VERSION),
                    ],
                )
            ]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

    with open("version_info.txt", "w", encoding="utf-8") as f:
        f.write(str(info))
    print("Wrote version_info.txt")


if __name__ == "__main__":
    main()
