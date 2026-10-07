"""Pictures of the Music tab, for checking its look by eye.

Opens the real tab in a throwaway profile (tests/_support.py: nothing of
yours is read or written) on the second screen, muted, searches the live
public catalogue, and saves window grabs of each page:

    .venv312\\Scripts\\python.exe tools\\music_preview.py "an artist" [out_dir] [width height]

  home (idle), search results (top and scrolled), the first artist's page,
  the first album's page, home while playing, and the full-screen view.

Public catalogue data only: no sign-in, no history, no cookies. The grabs
are of the app's own window (QWidget.grab), never of the screen.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))
import _support  # noqa: E402,F401 -- first: the throwaway profile
from _support import build_window, qapp, settle  # noqa: E402


def main(argv):
    query = argv[1] if len(argv) > 1 else "coldplay"
    out = argv[2] if len(argv) > 2 else os.path.join(os.environ.get("TEMP", "."), "music-preview")
    size = (int(argv[3]), int(argv[4])) if len(argv) > 4 else (1360, 860)
    os.makedirs(out, exist_ok=True)
    qapp()
    from PySide6.QtMultimedia import QMediaPlayer

    win, tabs = build_window(tabs=("video", "images", "music", "browser", "download"), size=size)
    mt = tabs["music"]
    win.tabs.setCurrentWidget(mt)
    mt.audio.setMuted(True)
    settle(1200)

    def wait(cond, ms):
        t = time.time()
        while time.time() - t < ms / 1000.0:
            if cond():
                return True
            settle(100)
        return cond()

    def grab(name):
        settle(500)
        path = os.path.join(out, name)
        win.grab().save(path)
        print("saved", path)

    grab("1_home_idle.png")
    mt.run_search(query)
    wait(lambda: len(mt._search.get("parts", {})) == 3, 20000)
    settle(2500)
    grab("2_search.png")
    page = mt._pages["search"]
    page.verticalScrollBar().setValue(page.hero.height() - 40)
    grab("3_search_scrolled.png")
    page.verticalScrollBar().setValue(0)
    music = mt._search["parts"].get("music", (None, ""))[0] or {}
    if music.get("artists"):
        mt.go("artist", music["artists"][0])
        wait(lambda: mt._pages["artist"].sec.count() > 1, 15000)
        settle(2500)
        grab("4_artist.png")
    if music.get("albums"):
        mt.go("album", music["albums"][0])
        wait(lambda: mt._pages["album"].sec.count() > 1, 15000)
        settle(2000)
        grab("5_album.png")
    if music.get("songs"):
        mt.play_from(music["songs"][0], music["songs"])
        wait(lambda: mt.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
             and mt.player.position() > 1500, 30000)
        mt.go("home")
        settle(2500)
        grab("6_home_playing.png")
        mt.open_full(lyrics=False)
        settle(1500)
        grab("7_full_screen.png")
        mt.close_full()
    mt.player.stop()
    win.close()
    settle(300)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
