"""Settings load and save the way the rest of the app assumes."""
import json

import _support
from _support import check

from app import config
from app.utils import settings as settings_store

# ---- defaults, and the new keys are all registered -------------------------
s = settings_store.load_settings()
for key in ("max_concurrent", "preferred_quality", "default_format", "backdrop",
            "reduce_motion", "check_app_updates", "cookies_from_browser"):
    check(key in s, "missing default: %s" % key)
print("defaults:", {k: s[k] for k in ("max_concurrent", "backdrop", "preferred_quality")})

# ---- unknown keys are dropped, known ones kept -----------------------------
with open(config.SETTINGS_PATH, "w", encoding="utf-8") as f:
    json.dump({"theme": "light", "max_concurrent": 5, "nonsense": 1}, f)
s = settings_store.load_settings()
check(s["theme"] == "light" and s["max_concurrent"] == 5, s)
check("nonsense" not in s, "an unknown key survived loading")

# ---- the save_dirs default is not shared between loads ---------------------
# dict(DEFAULTS) was a shallow copy, so recording a folder on one settings
# object edited the defaults every later load started from.
import os
a = settings_store.load_settings()
b = settings_store.load_settings()
check(a["save_dirs"] is not b["save_dirs"], "two loads share one save_dirs dict")
check(a["save_dirs"] is not settings_store.DEFAULTS["save_dirs"], "a load aliases DEFAULTS")
folder = os.path.join(_support.STATE_DIR, "somewhere")
os.makedirs(folder)
settings_store.set_save_dir(a, "video", folder)
check(settings_store.DEFAULTS["save_dirs"]["video"] is None, "set_save_dir edited DEFAULTS")
print("save_dirs isolated: ok")

# ---- max_concurrent is clamped whatever the file says ----------------------
for raw, want in ((0, 1), (-3, 1), (2, 2), (6, 6), (40, 6), ("x", 3), (None, 3)):
    got = settings_store.max_concurrent({"max_concurrent": raw})
    check(got == want, "max_concurrent(%r) = %r, want %r" % (raw, got, want))
print("max_concurrent clamped: ok")

print("\nSETTINGS OK")
