"""Refreshes the Browser tab's ad-block domain list from a live, actively
maintained public source, instead of relying solely on the static snapshot
bundled into ui_qt/browser_assets/adblock_domains.txt at build time.

That bundled list only covers what happened to be curated whenever it was
last generated -- checked directly, several of the highest-traffic ad
networks on the web (doubleclick.net, googlesyndication.com, facebook.net,
criteo.com, taboola.com, outbrain.com among them) were missing from it
entirely, which is what "ad-block doesn't work sometimes" actually was:
real coverage gaps, not a request-interception bug. A one-time patch to
the bundled file fixes today's snapshot; this is the durable fix, so
coverage doesn't just go stale again the same way.

Framework-agnostic (no Qt imports), same as the rest of app/core/ --
ui_qt/browser_tab.py's _load_adblock_domains() reads whatever this last
wrote out, unioned with the bundled file, same pattern update_checker.py
already established for yt-dlp.
"""
import os
import re
import urllib.request

from .. import config
from ..logging_setup import get_logger

logger = get_logger("adblock_updater")

# StevenBlack/hosts: a single, actively maintained, widely trusted plain
# hosts-format list (ads + tracking + malware domains), MIT-licensed. Not
# EasyList itself (that's ABP filter syntax, not a plain domain list, and
# would need real filter-rule parsing this app has no other use for) --
# this is a much closer match to what adblock_domains.txt already is: one
# domain per line, no rule syntax to interpret.
ADBLOCK_LIST_URL = "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
_REQUEST_TIMEOUT = 20

EXTRA_DOMAINS_PATH = os.path.join(config.APPDATA_DIR, "browser_adblock_extra.txt")

_HOSTS_LINE_RE = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1)\s+(\S+)")


def _parse_hosts_format(text):
    domains = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _HOSTS_LINE_RE.match(line)
        if m:
            host = m.group(1).lower()
            if host and host not in ("localhost", "localhost.localdomain", "local", "broadcasthost"):
                domains.add(host)
    return domains


def refresh_domain_list():
    """Blocking. Downloads the current list and writes the resulting
    domain set to EXTRA_DOMAINS_PATH, replacing whatever was cached there
    before. Raises on failure (network down, host unreachable, ...) -- the
    caller decides how to present that, same pattern as
    update_checker.upgrade_yt_dlp(). Never touches the bundled
    adblock_domains.txt itself (that file ships inside the frozen .exe and
    isn't writable at runtime); this is purely additive, unioned in by
    ui_qt/browser_tab.py's _load_adblock_domains().

    Returns the number of domains written, so the caller can show something
    more concrete than a bare "done".
    """
    req = urllib.request.Request(ADBLOCK_LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    domains = _parse_hosts_format(text)
    if not domains:
        raise RuntimeError("Downloaded list contained no usable domains")

    os.makedirs(config.APPDATA_DIR, exist_ok=True)
    tmp_path = EXTRA_DOMAINS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(domains)))
    os.replace(tmp_path, EXTRA_DOMAINS_PATH)
    return len(domains)


def extra_domains_status():
    """(count_or_None, path) -- for the Updates dialog to show what's
    currently cached, without triggering a network call."""
    if not os.path.isfile(EXTRA_DOMAINS_PATH):
        return None, EXTRA_DOMAINS_PATH
    try:
        with open(EXTRA_DOMAINS_PATH, "r", encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
        return count, EXTRA_DOMAINS_PATH
    except OSError:
        return None, EXTRA_DOMAINS_PATH
