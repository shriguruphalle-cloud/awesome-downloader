"""Scripts the Browser tab puts into every page, at document creation.

Each talks back to the app with chrome.webview.postMessage({type: ...}), which
arrives as WebView2Widget.webMessage. Only the top frame ever posts: frames
run these scripts too, and an ad iframe reporting "no video here" must not
overwrite what the page itself said.

Everything is built with DOM calls, never innerHTML -- sites with a
Trusted Types policy (YouTube among them) throw on any innerHTML string.
"""
import base64
import json
import os

from app.logging_setup import get_logger

logger = get_logger("browser_scripts")

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_assets")
OVERLAY_LOGO_PATH = os.path.join(_ASSETS_DIR, "overlay_logo.png")

_POST = """
  function __awdPost(msg) {
    try { if (window.chrome && chrome.webview) chrome.webview.postMessage(msg); } catch (e) {}
  }
"""


def _logo_data_uri(size=72):
    """The logo for the in-page button, at the size it's drawn (twice over,
    for high-DPI). This script is parsed by every page and frame, so what it
    carries matters: the full-size logo made it 2.8 MB."""
    try:
        import io
        from PIL import Image
        img = Image.open(OVERLAY_LOGO_PATH).convert("RGBA").resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 -- the button works without its icon
        logger.exception("Could not load the overlay button logo")
        return ""


def overlay_button():
    """The in-page download button: a small glass "Download" pill pinned to
    the top-right corner of the video that's playing (or the largest one in
    view), following it as the page scrolls. It fades in when there is a
    video to download and away when there isn't, and turns ember under the
    pointer. It used to be a large logo in the page's own bottom-right
    corner, which sat over unrelated content -- a sidebar, a comment.

    It also tells the toolbar whether a video is present, which is what
    lights the toolbar's Download button."""
    return """
(function () {
  if (window.top !== window.self || window.__awdOverlay) return;
  window.__awdOverlay = true;
  %(post)s
  var LOGO = %(logo)s;
  var pill = null, shown = false, reported = null, timer = null, target = null, raf = 0;

  function build() {
    pill = document.createElement('button');
    pill.id = '__awd_overlay_btn';
    pill.title = 'Download with Awesome Downloader';
    pill.setAttribute('aria-label', 'Download with Awesome Downloader');
    var img = document.createElement('img');
    img.src = LOGO; img.width = 18; img.height = 18; img.alt = '';
    img.style.cssText = 'display:block;width:18px;height:18px;pointer-events:none';
    var label = document.createElement('span');
    label.textContent = 'Download';
    label.style.cssText = 'pointer-events:none';
    pill.appendChild(img);
    pill.appendChild(label);
    pill.style.cssText = [
      'position:fixed', 'z-index:2147483646', 'top:0', 'left:0', 'height:32px', 'padding:0 13px 0 9px',
      'display:flex', 'align-items:center', 'gap:7px', 'border-radius:16px', 'cursor:pointer',
      'font:600 12.5px/1 "Segoe UI Variable","Segoe UI",system-ui,sans-serif', 'letter-spacing:.2px',
      'color:#f4f7ff', 'background:rgba(10,16,32,.72)', 'border:1px solid rgba(255,255,255,.18)',
      'box-shadow:0 4px 16px rgba(0,0,0,.35)', 'backdrop-filter:blur(10px) saturate(1.4)',
      '-webkit-backdrop-filter:blur(10px) saturate(1.4)', 'opacity:0', 'pointer-events:none',
      'transform:translateY(-6px)', 'transition:opacity .2s ease, transform .25s cubic-bezier(.2,.9,.3,1.15), background .15s, border-color .15s'
    ].join(';');
    pill.addEventListener('mouseenter', function () {
      pill.style.background = 'linear-gradient(180deg,#ff8b47,#ff6a13)';
      pill.style.borderColor = 'rgba(255,170,120,.9)';
      pill.style.color = '#170b04';
    });
    pill.addEventListener('mouseleave', function () {
      pill.style.background = 'rgba(10,16,32,.72)';
      pill.style.borderColor = 'rgba(255,255,255,.18)';
      pill.style.color = '#f4f7ff';
    });
    pill.addEventListener('click', function (e) {
      e.preventDefault(); e.stopPropagation();
      pill.style.transform = 'translateY(0) scale(.94)';
      setTimeout(function () { if (shown) pill.style.transform = 'translateY(0) scale(1)'; }, 140);
      __awdPost({type: 'download', url: location.href});
    }, true);
    (document.body || document.documentElement).appendChild(pill);
  }

  function pickVideo() {
    var vids = document.getElementsByTagName('video'), best = null, bestArea = 0;
    var vw = window.innerWidth, vh = window.innerHeight;
    for (var i = 0; i < vids.length; i++) {
      var r = vids[i].getBoundingClientRect();
      if (r.width < 200 || r.height < 110) continue;
      var w = Math.min(r.right, vw) - Math.max(r.left, 0), h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
      if (w <= 0 || h <= 60) continue;
      var area = w * h + (!vids[i].paused ? 1e7 : 0);   // the playing one wins
      if (area > bestArea) { best = vids[i]; bestArea = area; }
    }
    return best;
  }

  function place() {
    raf = 0;
    if (!pill || !target || !shown) return;
    var r = target.getBoundingClientRect();
    var top = Math.max(8, r.top + 12), right = Math.min(window.innerWidth, r.right) - 12;
    pill.style.left = Math.max(8, right - pill.offsetWidth) + 'px';
    pill.style.top = top + 'px';
  }
  function schedulePlace() { if (!raf) raf = requestAnimationFrame(place); }

  function sync() {
    timer = null;
    var video = document.fullscreenElement ? null : pickVideo();
    var present = !!video;
    if (present !== reported) {
      reported = present;
      __awdPost({type: 'media-present', value: present});
    }
    if (present && !pill && document.body) build();
    if (!pill) return;
    if (!pill.isConnected) (document.body || document.documentElement).appendChild(pill);
    target = video;
    if (present !== shown) {
      shown = present;
      pill.style.opacity = present ? '1' : '0';
      pill.style.transform = present ? 'translateY(0) scale(1)' : 'translateY(-6px)';
      pill.style.pointerEvents = present ? 'auto' : 'none';
    }
    place();
  }

  function later() { if (!timer) timer = setTimeout(sync, 350); }

  function start() {
    sync();
    new MutationObserver(later).observe(document.documentElement, {childList: true, subtree: true});
    window.addEventListener('resize', function () { later(); schedulePlace(); });
    window.addEventListener('scroll', schedulePlace, {passive: true, capture: true});
    document.addEventListener('loadedmetadata', later, true);
    document.addEventListener('play', later, true);
    document.addEventListener('fullscreenchange', later);
    setInterval(sync, 2500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
""" % {"post": _POST, "logo": json.dumps(_logo_data_uri())}


# Cosmetic hiding under AdGuard: a short list of well-known ad containers,
# kept as a floor for private tabs and pages AdGuard hasn't reached yet.
AD_CSS = (
    ".adsbygoogle, ins.adsbygoogle, [id^='google_ads_iframe'], [id^='div-gpt-ad'],"
    " .ytp-ad-module, .ytp-ad-overlay-container, .video-ads, .ytp-ad-player-overlay,"
    " #player-ads, ytd-ad-slot-renderer, ytd-display-ad-renderer,"
    " ytd-promoted-sparkles-web-renderer, ytd-promoted-video-renderer,"
    " ytd-in-feed-ad-layout-renderer, ytd-banner-promo-renderer"
    " { display: none !important; }"
)


def ad_css():
    return """
(function () {
  if (window.__awdAdCss) return;
  window.__awdAdCss = true;
  function add() {
    var s = document.createElement('style');
    s.id = '__awd_ad_css';
    s.textContent = %s;
    (document.head || document.documentElement).appendChild(s);
  }
  if (document.head || document.documentElement) add();
  else document.addEventListener('DOMContentLoaded', add);
})();
""" % json.dumps(AD_CSS)


def media_watch():
    """What is playing, for the toolbar's Now Playing control. Adapted from
    the media watcher in the user's own browser project: Media Session data
    when the site sets it, the <video>/<audio> element otherwise; reports are
    debounced, because adaptive players pause for a moment on every quality
    switch and each blip would flicker the control."""
    return """
(function () {
  if (window.top !== window.self || window.__awdMedia) return;
  %(post)s
  var SKIP = {
    'open.spotify.com':  {next: '[data-testid="control-button-skip-forward"]', prev: '[data-testid="control-button-skip-back"]'},
    'music.youtube.com': {next: '.next-button', prev: '.previous-button'},
    'www.youtube.com':   {next: '.ytp-next-button', prev: '.ytp-prev-button'},
    'm.youtube.com':     {next: '.ytp-next-button', prev: '.ytp-prev-button'},
    'soundcloud.com':    {next: '.skipControl__next', prev: '.skipControl__previous'},
    'music.apple.com':   {next: '.playback-controls__next', prev: '.playback-controls__previous'}
  };
  function primary() {
    var els = Array.prototype.slice.call(document.querySelectorAll('video, audio'));
    if (!els.length) return null;
    var live = els.filter(function (el) { return !el.paused && !el.ended; });
    var pool = live.length ? live : els;
    return pool.sort(function (a, b) { return (b.duration || 0) - (a.duration || 0); })[0];
  }
  var last = '';
  function report() {
    try {
      var el = primary();
      var ms = navigator.mediaSession, meta = ms && ms.metadata;
      // Only media that has actually played: a page with an idle embedded
      // video shouldn't put a player in the toolbar.
      var started = el ? ((el.played && el.played.length > 0) || el.currentTime > 0)
                       : !!(ms && ms.playbackState && ms.playbackState !== 'none');
      if ((!el && !(meta && meta.title)) || !started) {
        if (last !== 'none') { last = 'none'; __awdPost({type: 'media', present: false}); }
        return;
      }
      var playing = el ? (!el.paused && !el.ended && el.readyState > 2) : (ms && ms.playbackState === 'playing');
      var art = null;
      if (meta && meta.artwork && meta.artwork.length) art = meta.artwork[meta.artwork.length - 1].src;
      var msg = {
        type: 'media', present: true, playing: !!playing,
        title: (meta && meta.title) || document.title || '',
        artist: (meta && meta.artist) || location.hostname.replace(/^www\\./, ''),
        artwork: art,
        position: el && isFinite(el.currentTime) ? el.currentTime : 0,
        duration: el && isFinite(el.duration) ? el.duration : 0,
        canSkip: !!SKIP[location.hostname]
      };
      var key = [msg.playing, msg.title, msg.artist, Math.round(msg.duration)].join('|');
      if (key !== last || msg.playing) { last = key; __awdPost(msg); }
    } catch (e) {}
  }
  var t = null;
  function soon() { clearTimeout(t); t = setTimeout(report, 300); }
  ['play', 'pause', 'emptied', 'ended', 'seeked', 'loadedmetadata'].forEach(function (n) {
    document.addEventListener(n, soon, true);
  });
  setInterval(function () { var el = primary(); if (el && !el.paused) report(); }, 2000);

  function click(sel) { var b = sel && document.querySelector(sel); if (b) { b.click(); return true; } return false; }
  window.__awdMedia = {
    toggle: function () {
      var el = primary();
      if (!el) return false;
      if (el.paused) el.play(); else el.pause();
      return true;
    },
    next: function () { var s = SKIP[location.hostname]; return click(s && s.next); },
    prev: function () { var s = SKIP[location.hostname]; return click(s && s.prev); },
    seek: function (seconds) {
      var el = primary();
      if (!el || !isFinite(el.duration) || !(el.duration > 0)) return false;
      el.currentTime = Math.max(0, Math.min(el.duration - 0.25, el.currentTime + seconds));
      soon();
      return true;
    }
  };
})();
""" % {"post": _POST}


def link_clicks():
    """Middle-click and Ctrl+click on a link open it in a background tab --
    the engine reports every "open elsewhere" the same way, so the page says
    which kind this was. Ctrl+Shift+click opens it in front."""
    return """
(function () {
  if (window.top !== window.self || window.__awdLinks) return;
  window.__awdLinks = true;
  %(post)s
  function target(e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a || a.hasAttribute('download')) return null;
    var href = a.href || '';
    return /^https?:/i.test(href) ? href : null;
  }
  document.addEventListener('click', function (e) {
    if (e.button !== 0 || !(e.ctrlKey || e.metaKey)) return;
    var href = target(e);
    if (!href) return;
    e.preventDefault();
    __awdPost({type: 'open-tab', url: href, background: !e.shiftKey});
  }, true);
  document.addEventListener('auxclick', function (e) {
    if (e.button !== 1) return;
    var href = target(e);
    if (!href) return;
    e.preventDefault();
    __awdPost({type: 'open-tab', url: href, background: true});
  }, true);
})();
"""  % {"post": _POST}


def ad_skip():
    """In-player video ads -- a pre-roll the site's own player serves from
    its own servers -- can't always be stopped at the network. This presses
    the player's own "Skip Ad" button the moment it can be pressed: the
    known skip buttons of common players (YouTube, Google IMA, JW Player,
    Fluid Player, video.js), and any control whose whole label is "Skip Ad".
    On YouTube it also keeps an ad muted while it plays, the technique the
    user's own browser project used. Runs in every frame: ad players often
    live in an iframe of their own."""
    return r"""
(function () {
  if (window.__awdAdSkip) return;
  window.__awdAdSkip = true;
  var KNOWN = '.ytp-ad-skip-button, .ytp-skip-ad-button, .ytp-ad-skip-button-modern, .videoAdUiSkipButton,'
            + ' .ima-skip-button, .jw-skip.jw-skippable, .fluid_skip_btn, .vjs-ad-skip, .skip-ad-button,'
            + ' .skipAdButton, [class*="skip-ad-btn"], [class*="skipAdBtn"]';
  var LABEL = /^skip(\s+(the\s+)?ad(s|vert(isement)?)?)?\s*[\u203a>\u00bb\u25b6\u2192]*$/i;
  var clicked = typeof WeakMap !== 'undefined' ? new WeakMap() : null;

  function shown(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity || '1') > 0.1
        && cs.pointerEvents !== 'none' && !el.disabled;
  }
  function press(el) {
    var now = Date.now();
    if (clicked && clicked.has(el) && now - clicked.get(el) < 1500) return;
    if (clicked) clicked.set(el, now);
    el.click();
  }
  function clickable(node) {
    var el = node.parentElement;
    for (var i = 0; el && i < 4; i++, el = el.parentElement) {
      if (el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button'
          || getComputedStyle(el).cursor === 'pointer') return el;
    }
    return node.parentElement;
  }
  function playerArea(video) {
    // The player's own box: a few levels up from the <video>, stopping once
    // an ancestor is much larger than the video itself.
    var el = video, box = video.getBoundingClientRect();
    for (var i = 0; i < 6 && el.parentElement && el.parentElement !== document.body; i++) {
      var r = el.parentElement.getBoundingClientRect();
      if (r.width > box.width * 1.6 || r.height > box.height * 1.8) break;
      el = el.parentElement;
    }
    return el;
  }
  function byLabel(root) {
    // Only text that is the whole label ("Skip Ad", "Skip ad >"), never a
    // sentence that merely mentions skipping. Only inside a player, so a big
    // page isn't walked end to end twice a second.
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var t = (n.nodeValue || '').trim();
        return t.length >= 4 && t.length <= 24 && /skip/i.test(t) && /ad/i.test(t) && LABEL.test(t)
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    var n, found = [];
    while ((n = walker.nextNode())) found.push(n);
    return found;
  }
  function tick() {
    try {
      var videos = document.body ? document.getElementsByTagName('video') : [];
      if (!videos.length) return;
      var known = document.querySelectorAll(KNOWN);
      for (var i = 0; i < known.length; i++) if (shown(known[i])) press(known[i]);
      for (var v = 0; v < videos.length && v < 4; v++) {
        var labels = byLabel(playerArea(videos[v]));
        for (var j = 0; j < labels.length; j++) {
          var target = clickable(labels[j]);
          if (shown(target)) press(target);
        }
      }
      if (/(^|\.)youtube\.com$/.test(location.hostname)) {
        var player = document.querySelector('.html5-video-player');
        var video = document.querySelector('video.html5-main-video') || document.querySelector('video');
        var ad = player && (player.classList.contains('ad-showing') || player.classList.contains('ad-interrupting'));
        var close = document.querySelector('.ytp-ad-overlay-close-button');
        if (close) close.click();
        if (video && ad && !video.__awdMuted) { video.__awdMuted = true; video.__awdWasMuted = video.muted; video.muted = true; }
        else if (video && !ad && video.__awdMuted) { video.__awdMuted = false; video.muted = !!video.__awdWasMuted; }
      }
    } catch (e) {}
  }
  setInterval(tick, 600);
})();
"""


def all_scripts():
    return [ad_css(), overlay_button(), media_watch(), link_clicks(), ad_skip()]
