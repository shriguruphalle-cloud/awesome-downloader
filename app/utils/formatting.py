"""Small, dependency-free formatting helpers shared across the UI and core layers."""


def humanize_size(num_bytes):
    """Turn a byte count into a short human string, e.g. 1573312 -> '1.5 MB'."""
    if not num_bytes or num_bytes <= 0:
        return "size unknown"
    n = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def format_eta(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    m, s = divmod(max(seconds, 0), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def humanize_rate(bytes_per_sec):
    """Like humanize_size, but 0 is a real value (idle), not 'unknown'."""
    n = float(bytes_per_sec or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}/s" if unit == "B" else f"{n:.1f} {unit}/s"
        n /= 1024
    return f"{n:.1f} TB/s"


def parse_timecode(text):
    """Parse 'HH:MM:SS', 'MM:SS', or 'SS' (ints or decimals) into whole seconds.

    Returns None for an empty string, raises ValueError for anything malformed.
    """
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError("Use HH:MM:SS, MM:SS, or just seconds.")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError("Use numbers only, e.g. 1:30 or 00:01:30.")
    while len(nums) < 3:
        nums.insert(0, 0)
    h, m, s = nums
    if m >= 60 or s >= 60 or h < 0 or m < 0 or s < 0:
        raise ValueError("Minutes/seconds must be between 0 and 59.")
    return int(h * 3600 + m * 60 + s)
