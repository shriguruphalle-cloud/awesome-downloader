import logging
import os
from logging.handlers import RotatingFileHandler

from . import config

_configured = False


def setup_logging():
    """Idempotent: safe to call from multiple modules on import."""
    global _configured
    if _configured:
        return
    try:
        os.makedirs(config.APPDATA_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            config.LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root = logging.getLogger("awesome_downloader")
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except Exception:
        # Logging setup itself must never crash the app; fall back to no file logging.
        logging.getLogger("awesome_downloader").addHandler(logging.NullHandler())
    _configured = True


def get_logger(name=None):
    setup_logging()
    return logging.getLogger(f"awesome_downloader.{name}" if name else "awesome_downloader")
