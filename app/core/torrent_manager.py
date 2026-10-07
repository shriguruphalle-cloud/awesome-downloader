"""Thin wrapper around a libtorrent session: add/remove/list torrents, and
keep each one's fast-resume data.

Fast-resume data is libtorrent's own record of a torrent: which pieces are
already on disk, its file priorities, and (saved with save_info_dict) the
torrent's metadata itself. Without it, every launch re-added each torrent
from its magnet link or .torrent file and libtorrent re-hashed every byte
already downloaded ("Checking Files" on a 5 GB download, for minutes), and a
magnet link had to find peers just to learn its own file list again. Worse,
a finished torrent then looked unfinished until the check reached 100%, and
the app took that for it finishing *now* -- "finished today, took 4 s" on a
film downloaded days earlier (reported, twice).

So the data is written per torrent (keyed by info-hash) whenever libtorrent
says it changed, when metadata arrives, when a torrent finishes, and on the
way out; and a torrent with saved data comes back from it, complete and
checked, in an instant.
"""
import os
import time

from .. import config
from ..logging_setup import get_logger

logger = get_logger("torrent")

try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError:
    lt = None
    LIBTORRENT_AVAILABLE = False

RESUME_DIR = os.path.join(config.APPDATA_DIR, "torrent_resume")


class TorrentManager:
    def __init__(self):
        cat = lt.alert.category_t
        settings = {
            "listen_interfaces": "0.0.0.0:6881,[::]:6881",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
            # Resume-data answers, metadata and "finished" are what this app
            # listens for; nothing chattier.
            "alert_mask": int(cat.error_notification | cat.status_notification | cat.storage_notification),
        }
        self.session = lt.session(settings)
        self._resume_pending = 0

    # ---- adding ----
    def add_magnet(self, uri, save_path):
        params = lt.parse_magnet_uri(uri)
        params.save_path = save_path
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        return self.session.add_torrent(params)

    def add_torrent_file(self, torrent_path, save_path, priorities=None):
        info = lt.torrent_info(torrent_path)
        params = {
            "ti": info,
            "save_path": save_path,
            "storage_mode": lt.storage_mode_t.storage_mode_sparse,
        }
        handle = self.session.add_torrent(params)
        if priorities is not None:
            handle.prioritize_files(priorities)
        return handle

    def add_from_resume(self, key, save_path=None):
        """Re-adds a torrent from its saved fast-resume data: no re-check, no
        metadata lookup. None when there is no usable data for `key`."""
        if not key:
            return None
        try:
            with open(self.resume_path(key), "rb") as f:
                data = f.read()
            params = lt.read_resume_data(data)
        except FileNotFoundError:
            return None
        except Exception:   # noqa: BLE001 -- unreadable: fall back to a re-check
            logger.exception("Couldn't read fast-resume data for %s", key)
            return None
        if save_path:
            params.save_path = save_path
        try:
            return self.session.add_torrent(params)
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't add %s from its fast-resume data", key)
            return None

    # ---- identity ----
    @staticmethod
    def key(handle):
        """The torrent's info-hash as hex: the name of its resume file."""
        try:
            hashes = handle.info_hashes()
            if hashes.has_v1():
                return str(hashes.v1)
            return str(hashes.v2)[:64]
        except Exception:   # noqa: BLE001 -- older bindings
            try:
                return str(handle.info_hash())
            except Exception:   # noqa: BLE001
                return None

    @staticmethod
    def resume_path(key):
        return os.path.join(RESUME_DIR, key + ".fastresume")

    # ---- resume data ----
    def request_resume(self, handle, force=False):
        """Asks libtorrent for fresh resume data for `handle` -- if it has
        changed since the last save, or always with force. The answer comes
        back as an alert; process_alerts() writes it."""
        try:
            if not handle.is_valid():
                return False
            status = handle.status()
            if not status.has_metadata:
                return False
            if not force and not status.need_save_resume:
                return False
            handle.save_resume_data(lt.save_resume_flags_t.save_info_dict)
            self._resume_pending += 1
            return True
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't request resume data")
            return False

    def process_alerts(self):
        """Handles what libtorrent has posted since the last call. Returns the
        handles that finished downloading in the meantime."""
        finished = []
        try:
            alerts = self.session.pop_alerts()
        except Exception:   # noqa: BLE001
            return finished
        for a in alerts:
            if isinstance(a, lt.save_resume_data_alert):
                self._resume_pending = max(0, self._resume_pending - 1)
                self._write_resume(a)
            elif isinstance(a, lt.save_resume_data_failed_alert):
                self._resume_pending = max(0, self._resume_pending - 1)
            elif isinstance(a, lt.metadata_received_alert):
                self.request_resume(a.handle, force=True)
            elif isinstance(a, lt.torrent_finished_alert):
                self.request_resume(a.handle, force=True)
                finished.append(a.handle)
        return finished

    def _write_resume(self, alert):
        try:
            key = self.key(alert.handle)
            if not key:
                return
            data = lt.write_resume_data_buf(alert.params)
            os.makedirs(RESUME_DIR, exist_ok=True)
            path = self.resume_path(key)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(bytes(data))
            os.replace(tmp, path)
        except Exception:   # noqa: BLE001
            logger.exception("Couldn't write fast-resume data")

    def save_all_resume(self, handles, timeout=4.0):
        """On the way out: fresh resume data for every torrent, waiting (a
        few seconds at most) for libtorrent to hand it over."""
        for handle in handles:
            self.request_resume(handle, force=True)
        end = time.monotonic() + timeout
        while self._resume_pending > 0 and time.monotonic() < end:
            try:
                self.session.wait_for_alert(200)
            except Exception:   # noqa: BLE001
                break
            self.process_alerts()

    def forget_resume(self, handle):
        key = self.key(handle)
        if key:
            try:
                os.remove(self.resume_path(key))
            except OSError:
                pass

    # ---- control ----
    def pause(self, handle):
        """Pause a torrent so it *stays* paused.

        handle.pause() on its own doesn't hold: torrents are added with
        libtorrent's auto_managed flag set (it's in default_flags, and
        parse_magnet_uri sets it too), which hands start/stop control to the
        session's auto-manager. That manager re-evaluates on its own timer
        and promptly resumes anything it thinks should be active -- so a
        manual pause visibly snapped straight back to downloading (reported
        directly: "even after clicking on the pause button it automatically
        resumes instantly"). Clearing auto_managed first is what makes the
        pause a manual, sticky decision.
        """
        try:
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.pause()
            return True
        except Exception:
            logger.exception("Failed to pause torrent")
            return False

    def resume(self, handle):
        """Resume, handing control back to the auto-manager so queue limits
        and seeding rules apply again -- the mirror of pause() above."""
        try:
            handle.set_flags(lt.torrent_flags.auto_managed)
            handle.resume()
            return True
        except Exception:
            logger.exception("Failed to resume torrent")
            return False

    def move(self, handle, save_path):
        """Saves the torrent into `save_path` from now on, taking along what
        it already wrote. A file of the same name already there is kept
        (dont_replace): the torrent checks and uses it rather than
        overwriting something that may be the person's own."""
        try:
            handle.move_storage(save_path, lt.move_flags_t.dont_replace)
            return True
        except Exception:
            logger.exception("Failed to move torrent to %s", save_path)
            return False

    def remove(self, handle, delete_files=False):
        self.forget_resume(handle)
        try:
            if delete_files:
                self.session.remove_torrent(handle, lt.options_t.delete_files)
            else:
                self.session.remove_torrent(handle)
        except Exception:
            logger.exception("Failed to remove torrent")
