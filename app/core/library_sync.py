"""Keeping the library the same on every phone you sign in on.

The library is a handful of things -- liked songs, albums and artists, playlists, the recently played
list, your taste picks, and a few of the app's own (pinned playlists, recent searches) -- each kept on
the server as one item ("doc": kind + key + data), the later change winning.

How a sync goes (Kotlin drives it, the server answers in between):
  prepare(app_docs) -> {"since", "changes"}   what changed here since the last sync, found by comparing
                                               every item with what was last agreed (a hash of each)
  apply(docs, rev)  -> {"app": [...]}          what the server has that's newer: put into the library,
                                               and the app's own items handed back to Kotlin
A change made between the two isn't lost: only what was sent, and what came back, counts as agreed.
"""
import hashlib
import json
import os
import time

from app import config
from app.utils import music_library, secure_store

PATH = os.path.join(config.APPDATA_DIR, "sync_state.json")
SEP = "\u0001"
BUCKETS = ("songs", "albums", "artists")


def _state():
    try:
        st = secure_store.read_json(PATH)
    except (FileNotFoundError, ValueError, OSError):
        st = {}
    st.setdefault("rev", 0)
    st.setdefault("base", {})
    st.setdefault("pending", {})
    return st


def _save(st):
    secure_store.write_json(PATH, st)


def _hash(data):
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def local_docs(taste, app_docs=None):
    """{(kind, key): data} for everything kept here now, newest first within each kind."""
    lib = music_library._load()
    docs = {}
    for b in BUCKETS:
        for x in lib[b]:
            docs[("like", x["id"])] = dict(x)
    for p in lib.get("playlists") or []:
        docs[("playlist", p["id"])] = dict(p)
    docs[("recent", "list")] = list(lib.get("recent") or [])
    if taste is not None:
        docs[("taste", "picks")] = {"onboarded": taste.state.get("onboarded"), "seeds": taste.state.get("seeds"),
                                    "prefs": taste.state.get("prefs")}
    for key, data in (app_docs or {}).items():
        docs[("app", key)] = data
    return docs


def prepare(taste, app_docs=None, now_ms=None):
    """What changed here since the last sync: {"since": rev, "changes": [...], "first": bool}. On a phone that
    has never synced, nothing is sent yet ("first"): the server's library comes in first (apply), so a new
    phone's empty lists don't count as the newest; then a second prepare sends what this phone adds."""
    st = _state()
    if not st.get("pulled"):
        st["pending"] = {}
        _save(st)
        return {"since": 0, "changes": [], "first": True}
    now = int(now_ms or time.time() * 1000)
    docs = local_docs(taste, app_docs)
    base = st["base"]
    changes, pending = [], {}
    # liked items newest first: an item seen for the first time gets a time just under the one before it,
    # so the order they were liked in survives on the other phone
    order = {k: i for i, k in enumerate(docs)}
    for (kind, key), data in docs.items():
        k = kind + SEP + key
        h = _hash(data)
        if base.get(k) != h:
            changes.append({"kind": kind, "key": key, "data": data, "updated": now - order[(kind, key)]})
            pending[k] = h
    for k in list(base):
        kind, key = k.split(SEP, 1)
        if (kind, key) not in docs and kind != "app":
            changes.append({"kind": kind, "key": key, "deleted": True, "updated": now})
            pending[k] = None
    st["pending"] = pending
    _save(st)
    return {"since": st["rev"], "changes": changes, "first": False}


def apply(taste, docs, rev):
    """The server's newer items into the library. Returns {"app": [{key, data, deleted}], "changed": n}."""
    st = _state()
    base = st["base"]
    for k, h in (st.get("pending") or {}).items():
        if h is None:
            base.pop(k, None)
        else:
            base[k] = h
    st["pending"] = {}
    lib = music_library._load()
    app_out, changed = [], 0
    # newest first: the library lists liked items and playlists most recent at the top
    for d in sorted(docs or [], key=lambda d: d.get("updated") or 0):
        kind, key, data, gone = d.get("kind"), d.get("key"), d.get("data"), bool(d.get("deleted"))
        k = kind + SEP + key
        if gone:
            base.pop(k, None)
        else:
            base[k] = _hash(data)
        if kind == "like":
            for b in BUCKETS:
                lib[b][:] = [x for x in lib[b] if x["id"] != key]
            if not gone and isinstance(data, dict):
                lib[music_library._bucket(data)].insert(0, music_library.slim(data))
            changed += 1
        elif kind == "playlist":
            pls = lib.setdefault("playlists", [])
            pls[:] = [p for p in pls if p["id"] != key]
            if not gone and isinstance(data, dict):
                pls.insert(0, data)
            changed += 1
        elif kind == "recent" and isinstance(data, list):
            lib["recent"] = data[:music_library.RECENT_MAX]
            changed += 1
        elif kind == "taste" and isinstance(data, dict) and taste is not None:
            for f in ("onboarded", "seeds", "prefs"):
                if data.get(f) is not None:
                    taste.state[f] = data[f]
            taste.save()
            changed += 1
        elif kind == "app":
            app_out.append({"key": key, "data": data, "deleted": gone})
    if changed:
        music_library._save()
    st["rev"] = max(int(rev or 0), st["rev"])
    st["pulled"] = True
    _save(st)
    return {"app": app_out, "changed": changed}


def forget():
    """Signed out: the next sign-in starts afresh (and merges what's here with what's there)."""
    try:
        os.remove(PATH)
    except OSError:
        pass
