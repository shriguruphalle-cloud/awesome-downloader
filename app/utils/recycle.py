"""Moves files to the Windows Recycle Bin instead of deleting them outright.

History's bulk delete can take dozens of files in one click; sending them
to the Recycle Bin (SHFileOperation with FOF_ALLOWUNDO) means a mistaken
click is one "Restore" away rather than permanent.
"""
import ctypes
import os
import sys
from ctypes import wintypes

from ..logging_setup import get_logger

logger = get_logger("recycle")

_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOERRORUI = 0x0400


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _recycle_one(path):
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = _FO_DELETE
    # A list of paths, each NUL-terminated, the whole thing double-NUL-terminated.
    op.pFrom = os.path.abspath(path) + "\0\0"
    op.pTo = None
    op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted


def to_recycle_bin(paths):
    """Recycles each existing path. Returns (recycled, failed) lists."""
    recycled, failed = [], []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            ok = _recycle_one(path) if sys.platform == "win32" else False
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't recycle %s", path)
            ok = False
        if ok and not os.path.exists(path):
            recycled.append(path)
        else:
            failed.append(path)
    return recycled, failed
