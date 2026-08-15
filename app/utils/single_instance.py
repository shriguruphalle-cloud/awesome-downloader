"""Single-instance enforcement + magnet-link forwarding.

Without this, every magnet-link click spawns a brand new process -- each
with its own libtorrent session trying to bind the same listen port, which
degrades DHT/peer connectivity for *all* of them (a very plausible cause of
"metadata never arrives"), on top of just being visually wrong (a pile of
windows instead of one).

Mechanism: a fixed localhost TCP port acts as both the "is anyone already
listening" check and the IPC channel. The first instance to start listens on
it; every later launch tries to connect first -- success means someone's
already running, so it forwards its magnet argument (if any) as a single
line of text and exits immediately without ever building a window.

"""
import socket
import threading

from ..logging_setup import get_logger

logger = get_logger("single_instance")

_PORT = 47653  # arbitrary, fixed so repeat launches agree on where to look
_FOCUS_MESSAGE = "__focus__"


def forward_to_existing(magnet_uri=None, timeout=0.5):
    """Returns True if another instance is already running (and has been
    sent the magnet URI, if any) -- caller should exit without building a UI.
    Returns False if this is the first instance."""
    try:
        with socket.create_connection(("127.0.0.1", _PORT), timeout=timeout) as sock:
            payload = (magnet_uri or _FOCUS_MESSAGE) + "\n"
            sock.sendall(payload.encode("utf-8"))
        return True
    except OSError:
        return False


def start_listener(on_magnet, on_focus_request=None):
    """Starts a background thread listening for later launches. on_magnet(uri)
    is called for a forwarded magnet link; on_focus_request() for a plain
    relaunch with nothing to hand off (bring the window to front).
    Silently does nothing if the port's already taken by something else --
    that just means forwarding won't work this run, not a crash.
    """
    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", _PORT))
        except OSError:
            logger.info("Single-instance port already taken, skipping listener")
            return
        srv.listen(5)
        while True:
            try:
                conn, _addr = srv.accept()
                with conn:
                    data = conn.recv(4096).decode("utf-8", errors="ignore").strip()
                if not data:
                    continue
                if data == _FOCUS_MESSAGE:
                    if on_focus_request:
                        on_focus_request()
                elif data.startswith("magnet:"):
                    on_magnet(data)
            except Exception:
                logger.exception("single-instance listener error")

    threading.Thread(target=serve, daemon=True).start()

