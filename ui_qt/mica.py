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
    effect.setAcrylicEffect(handle, gradientColor=_ACRYLIC_TINT_DARK if dark_mode else _ACRYLIC_TINT_LIGHT)

    # Best-effort: silently no-ops (returns a non-zero HRESULT that's just
    # ignored) on Windows 10 or older Win11 builds without this attribute --
    # there's no real fallback for square corners other than leaving them
    # square, which is what already happens if this call does nothing.
    try:
        pref = ctypes.c_int(_DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(handle), _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), ctypes.sizeof(pref)
        )
    except Exception:
        pass

    return effect
