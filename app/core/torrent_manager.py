"""Thin wrapper around a libtorrent session: add/remove/list torrents."""
from ..logging_setup import get_logger

logger = get_logger("torrent")

try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError:
    lt = None
    LIBTORRENT_AVAILABLE = False


class TorrentManager:
    def __init__(self):
        settings = {
            "listen_interfaces": "0.0.0.0:6881,[::]:6881",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
        }
        self.session = lt.session(settings)

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

    def remove(self, handle, delete_files=False):
        try:
            if delete_files:
                self.session.remove_torrent(handle, lt.options_t.delete_files)
            else:
                self.session.remove_torrent(handle)
        except Exception:
            logger.exception("Failed to remove torrent")
