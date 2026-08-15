"""AWESOME DOWNLOADER -- PySide6 UI preview launcher.

Runs the in-progress PySide6 rewrite (ui_qt/) instead of the real, shipped
CustomTkinter app (which main.py/main.pyw still launch, completely
untouched). Use this to try the new UI and click through it yourself --
Video, Torrent, Images, Downloads, and History tabs are all ported so far.
The Downloads tab is the new IDM-style multi-connection queue (Phase 3) --
add a direct file URL, select its row, and you should see the segmented
per-connection progress bar animate as each chunk downloads.

Known open issue to look for specifically: switching between tabs may show
a brief ghosting/afterimage of the previous tab (see ui_qt/main_window.py's
"KNOWN ISSUE" comment) -- this couldn't be reliably confirmed fixed or
broken via automated screenshots in the dev environment, so a real look is
exactly what's needed here.
"""
import sys

try:
    from app.main_qt import main
except ImportError:
    print("Missing dependencies. Install them with:  pip install -r requirements.txt")
    sys.exit(1)

if __name__ == "__main__":
    main()
