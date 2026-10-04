"""macOS-style frosted-glass window backdrop, done the Qt-native way.

An earlier Tk-based attempt at this used a
-transparentcolor color-key hack, which produced pink/magenta fringing at
every rounded-corner edge because Tk has no real alpha compositing -- it just
punches a hole to a fixed key color and hopes anti-aliasing doesn't touch it.

Qt's frameless-window model is different: qframelesswindow talks straight to
DWM (DwmSetWindowAttribute / SetWindowCompositionAttribute) to composite a
real blur-behind-window surface, the same mechanism Windows' own Settings
app and File Explorer use. There is no color key and nothing for
anti-aliasing to blend toward, so the fringing failure mode this module
exists to avoid does not apply here. Verified two ways: (1) a rounded
QPushButton over the blurred surface renders with clean edges, no artifacts;
(2) a real screen capture (not a QWidget.grab(), which bypasses DWM
compositing entirely and would show a false pass) over a light desktop
wallpaper shows genuine gaussian-blurred wallpaper content through the
window, confirming the backdrop is actually compositing and not just a flat
fill.

Mica vs Acrylic, and why this picks Acrylic: Windows 11's Mica is real but
deliberately subtle -- Fluent Design uses it as a flat, low-opacity tint
sampled from the wallpaper's dominant color, not a visible blur of what's
behind the window. It's what File Explorer/Settings use. Verified directly:
setMicaEffect() produces a uniform flat dark fill with no visible wallpaper
texture even over a high-contrast wallpaper. That doesn't match "macOS-style
frosted glass" -- macOS vibrancy (and what the user asked for) shows a
visibly blurred backdrop. Acrylic is the DWM effect that actually does that
(same mechanism, heavier blur + noise), and it's confirmed working via the
screen-capture test above, so it's used on both Win10 and Win11 here rather
than switching to Mica on Win11 per the "typical Fluent app" convention.

NOTE -- this real blur was removed once (see git history around the
"solid opaque background" change) specifically because a live DWM-composited
region sharing the window with a live Chromium compositor surface (the
Browser tab's QWebEngineView) kept desyncing: caption-button icons ghosting
through, and the backdrop vanishing to bare wallpaper until something forced
a re-apply. It was restored at the user's own explicit request, in full
knowledge that this same class of bug is likely to recur -- if it does, the
fix that actually held was removing this file's real blur calls entirely and
painting a plain opaque fill in MainWindow._TranslucentSurface.paintEvent
instead, not another reactive patch on top of the live blur.
"""
import ctypes
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from qframelesswindow.windows import WindowsWindowEffect

# DWM window-corner-preference attribute (Win11 22000+ only) -- frameless/
# custom-titlebar windows lose Windows 11's default rounded corners since
# they opt out of the normal non-client frame that corner rounding is
# normally tied to; this restores it explicitly. Not in qframelesswindow's
# own WindowsWindowEffect wrapper, so called directly via ctypes/dwmapi
# (the same underlying DLL that class wraps for its own DWM calls).
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2
_DWMWCP_DONOTROUND = 1

# ARGB hex strings (RRGGBBAA) for setAcrylicEffect's gradientColor param --
# translucent+dark/light to match the app's existing palette (tokens.py
# WINDOW_BG dark = #161618, light = #eef0f3). Alpha kept fairly low (~0x66)
# so the blur is clearly visible rather than reading as a near-solid panel.
_ACRYLIC_TINT_DARK = "16161866"
_ACRYLIC_TINT_LIGHT = "EEF0F366"


def apply_frosted_glass(window, dark_mode=True):
    """Applies a real, DWM-composited frosted-glass backdrop to `window`.

    Caller must have already set `window.setAttribute(Qt.WA_TranslucentBackground)`
    -- without it, Qt's own opaque raster backing store paints over whatever
    DWM composites behind the window every frame, and the effect call
    silently no-ops visually (confirmed: the DWM call succeeds, nothing
    behind the glass is ever visible without this attribute).

    No-ops on non-Windows platforms -- theme.py's flat QSS colors are the
    fallback there, same safety-net philosophy the earlier Tk failure
    taught us to keep rather than half-implementing a platform-specific
    effect with no graceful degradation.

    Returns the WindowsWindowEffect instance (the caller must keep a
    reference alive on the window object -- letting it get garbage-collected
    can drop the effect) or None if not applicable on this platform.
    """
    if not IS_WINDOWS:
        return None

    effect = WindowsWindowEffect(window)
    handle = window.winId()
    # Matches qframelesswindow's own AcrylicWindow.updateFrameless() exactly
    # (enableBlurBehindWindow + addWindowAnimation before setAcrylicEffect,
    # not just the Acrylic call alone) -- setAcrylicEffect() by itself sets
    # the backdrop *material* via SetWindowCompositionAttribute, but without
    # enableBlurBehindWindow's DwmEnableBlurBehindWindow call establishing a
    # real DWM blur region first, the compositor doesn't reliably invalidate
    # that region on repaint. Symptom without it: switching QTabWidget pages
    # left visible ghosting -- old tabs' content stayed alpha-blended into
    # the translucent surface instead of clearing (verified via real screen
    # capture, fixed by adding this call).
    effect.enableBlurBehindWindow(handle)
    effect.addWindowAnimation(handle)
    # enableShadow=False, which despite the name is what controls the accent
    # BORDER, not the shadow. Left at its default the library passes
    # accentFlags = 0x20|0x40|0x80|0x100 -- DrawLeftBorder | DrawTopBorder |
    # DrawRightBorder | DrawBottomBorder -- and DWM strokes a 1px accent line
    # around the acrylic region. That line is square while the window's
    # corners are rounded, so it cannot follow them: the result is a thin
    # border that breaks at every corner with the desktop showing through the
    # gap. The window's shadow is unaffected; it comes from the extended
    # frame, not from these flags.
    effect.setAcrylicEffect(
        handle,
        gradientColor=_ACRYLIC_TINT_DARK if dark_mode else _ACRYLIC_TINT_LIGHT,
        enableShadow=False,
    )
    _apply_dwm_rounded_corners(handle, _should_round(window))
    return effect


def remove_frosted_glass(window):
    """Turns the acrylic blur off again -- Settings > Appearance > Backdrop
    moving away from "desktop". The painted backdrops fill every pixel, so
    all this has to undo is DWM's own accent and blur region."""
    if not IS_WINDOWS or window is None:
        return
    try:
        effect = WindowsWindowEffect(window)
        handle = window.winId()
        effect.removeBackgroundEffect(handle)
        effect.disableBlurBehindWindow(handle)
        _apply_dwm_rounded_corners(handle, _should_round(window))
    except Exception:
        pass


def set_rounded_corners(window, rounded):
    """Rounds the window's corners, or squares them off.

    Windows 11 rounds a floating window and squares a maximized one -- a
    maximized window's corners sit in the screen's own corners, where there is
    nothing to round against. This window forced ROUND permanently, so while
    maximized it kept its rounded cut-outs: the desktop showed through all
    four corners and the border arc ran off the edge of the screen, which is
    what reads as a broken, unseamless border (reported with the whole screen
    to look at).
    """
    if not IS_WINDOWS or window is None:
        return
    try:
        pref = ctypes.c_int(_DWMWCP_ROUND if rounded else _DWMWCP_DONOTROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()), _DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass


def _should_round(window):
    """A maximized or fullscreen window gets square corners -- its corners are
    the screen's corners, and there is nothing to round against."""
    try:
        return not (window.isMaximized() or window.isFullScreen())
    except Exception:
        return True


def _apply_dwm_rounded_corners(handle, rounded=True):
    # Best-effort: silently no-ops (returns a non-zero HRESULT that's just
    # ignored) on Windows 10 or older Win11 builds without this attribute --
    # there's no real fallback for square corners other than leaving them
    # square, which is what already happens if this call does nothing.
    try:
        pref = ctypes.c_int(_DWMWCP_ROUND if rounded else _DWMWCP_DONOTROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(handle), _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass


def refresh_blur_region(window, dark_mode=True):
    """Re-establishes the DWM blur-behind region -- for a DPI/monitor-scale
    change, which needs more than just retint_frosted_glass()'s tint-only
    refresh. A moved-to-a-different-DPI-monitor window (or a system scale
    change) leaves the *region* DWM is blurring stale relative to the
    window's new size until something re-calls enableBlurBehindWindow;
    until then DWM's composited output and Qt's own repaint (already at the
    new size) can visibly disagree, which is what read as the backdrop
    randomly switching between the glass blur and a flat solid fill while
    scaling (reported directly).

    Deliberately calls enableBlurBehindWindow again but *not*
    addWindowAnimation -- replaying the open animation is specifically what
    desynced DWM's and Chromium's compositors and froze the window after a
    theme toggle while the Browser tab's QWebEngineView was live (see
    retint_frosted_glass's own docstring above). Re-establishing just the
    blur region carries none of that risk since no animation replays.
    """
    if not IS_WINDOWS:
        return None
    effect = WindowsWindowEffect(window)
    handle = window.winId()
    effect.enableBlurBehindWindow(handle)
    # enableShadow=False for the same reason as in apply_frosted_glass: it is
    # the accent-border flag, and the border it draws cannot follow the
    # window's rounded corners. Re-applying the effect must not put it back.
    effect.setAcrylicEffect(
        handle,
        gradientColor=_ACRYLIC_TINT_DARK if dark_mode else _ACRYLIC_TINT_LIGHT,
        enableShadow=False,
    )
    _apply_dwm_rounded_corners(handle, _should_round(window))
    return effect


def retint_frosted_glass(window, dark_mode=True):
    """Re-applies just the Acrylic tint color -- for re-toggling the theme
    on a window that's already had apply_frosted_glass() run once.

    toggle_theme() used to call apply_frosted_glass() again in full on every
    toggle, re-running enableBlurBehindWindow + addWindowAnimation each time.
    That was harmless while every tab was pure Qt-painted content, but the
    Browser tab's QWebEngineView owns a real native child HWND (Chromium's
    own compositor surface, not something Qt's raster backing store draws) --
    re-establishing the parent's DWM blur region and replaying its open
    animation while that child surface is live let DWM's compositor and
    Chromium's compositor fall out of sync, reported directly as the window
    going visually "fixed" (frozen, unresponsive-looking) right after a
    theme toggle. Only the tint actually needs to change on a retoggle; the
    blur region and open animation are one-time setup, not per-toggle state.
    """
    if not IS_WINDOWS:
        return None
    effect = WindowsWindowEffect(window)
    handle = window.winId()
    # enableShadow=False for the same reason as in apply_frosted_glass: it is
    # the accent-border flag, and the border it draws cannot follow the
    # window's rounded corners. Re-applying the effect must not put it back.
    effect.setAcrylicEffect(
        handle,
        gradientColor=_ACRYLIC_TINT_DARK if dark_mode else _ACRYLIC_TINT_LIGHT,
        enableShadow=False,
    )
    _apply_dwm_rounded_corners(handle, _should_round(window))
    return effect
