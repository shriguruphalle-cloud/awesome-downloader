"""Sets up a Python 3.12 environment with libtorrent installed -- the exact
sequence already proven to work by hand: install Python 3.12 side-by-side
(doesn't touch whichever Python is already the system default), create a
dedicated venv in the project folder, install requirements.txt into it
(libtorrent included, since 3.12 is within its supported wheel range).
"""
import os
import subprocess

from .. import config
from ..logging_setup import get_logger

logger = get_logger("python_env")

VENV_DIR_NAME = ".venv312"


def _run(args, timeout=300):
    kwargs = {"capture_output": True, "text": True, "timeout": timeout}
    if config.CREATE_NO_WINDOW:
        kwargs["creationflags"] = config.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def python312_available():
    try:
        return _run(["py", "-3.12", "--version"], timeout=15).returncode == 0
    except Exception:
        return False


def venv_dir():
    return os.path.join(config.BASE_DIR, VENV_DIR_NAME)


def venv_python():
    return os.path.join(venv_dir(), "Scripts", "python.exe")


def is_set_up():
    return os.path.exists(venv_python())


def setup(progress_callback=None):
    """Blocking. Calls progress_callback(str) at each step. Raises
    RuntimeError with a human-readable message on failure."""

    def report(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    if not python312_available():
        report("Installing Python 3.12 (winget)...")
        result = _run(
            ["winget", "install", "--id", "Python.Python.3.12", "--source", "winget",
             "--accept-package-agreements", "--accept-source-agreements", "-e"],
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Couldn't install Python 3.12:\n{(result.stderr or result.stdout or '')[-400:]}"
            )
        if not python312_available():
            raise RuntimeError(
                "Python 3.12 installed but 'py -3.12' still isn't found. Try restarting the app."
            )

    vpython = venv_python()
    if not os.path.exists(vpython):
        report("Creating virtual environment (.venv312)...")
        result = _run(["py", "-3.12", "-m", "venv", venv_dir()], timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Couldn't create the virtual environment:\n{(result.stderr or '')[-400:]}")

    report("Installing dependencies into .venv312 (this can take a minute)...")
    _run([vpython, "-m", "pip", "install", "--upgrade", "pip"], timeout=120)
    req_path = os.path.join(config.BASE_DIR, "requirements.txt")
    result = _run([vpython, "-m", "pip", "install", "-r", req_path], timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Dependency install failed:\n{(result.stderr or '')[-400:]}")

    report("Done.")
    return venv_dir()
