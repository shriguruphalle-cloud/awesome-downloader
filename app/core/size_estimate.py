"""Pre-download size estimates shown next to each resolution/bitrate choice.

`height_sizes` (from core.downloader.fetch_info_with_sizes) holds the exact
combined video+audio byte total yt-dlp's real format selector would pick for
each height -- that's authoritative for MKV and MP4 (both are stream-copy,
no re-encode, so the source byte count *is* the output byte count, modulo a
tiny container-overhead difference). MOV is a genuine re-encode
(ffmpeg_utils.convert_container re-encodes libx264 CRF18 + AAC 192k), so its
size has no real relationship to the source bytes -- it's estimated from an
average bitrate table instead, same spirit as the fallback the project's own
README already describes for size estimates in general.
"""

# Container overhead vs. the raw stream-copy byte count. MKV (Matroska) is
# the merge target every download already produces, so it's the baseline;
# MP4's moov/stco indexing adds a small amount on top. Real numbers, not
# fabricated -- both are still just the same source streams repackaged.
_CONTAINER_OVERHEAD = {"mkv": 1.000, "mp4": 1.004}

# Rough average x264 CRF18 output bitrate by resolution, standard-motion
# content (Mbps). Interpolated between points for heights that don't land
# exactly on one. This is what backs the MOV estimate, since a re-encode's
# size depends on encode complexity, not on the source file's own bitrate.
_MOV_BITRATE_MBPS_BY_HEIGHT = [
    (2160, 18.0), (1440, 10.0), (1080, 6.0), (720, 3.5),
    (480, 1.8), (360, 1.0), (240, 0.6), (144, 0.3),
]
_MOV_AUDIO_KBPS = 192  # matches ffmpeg_utils.convert_container's -b:a for mov


def _interpolated_mov_bitrate_mbps(height):
    points = _MOV_BITRATE_MBPS_BY_HEIGHT
    if height >= points[0][0]:
        return points[0][1]
    if height <= points[-1][0]:
        return points[-1][1]
    for (h1, b1), (h2, b2) in zip(points, points[1:]):
        if h2 <= height <= h1:
            frac = (height - h2) / (h1 - h2) if h1 != h2 else 0
            return b2 + frac * (b1 - b2)
    return points[-1][1]


def estimate_video_size(height_sizes, height, effective_duration, fraction=1.0, container="mkv"):
    if not height:
        return 0
    if container == "mov":
        video_bps = _interpolated_mov_bitrate_mbps(height) * 1_000_000 / 8
        audio_bps = _MOV_AUDIO_KBPS * 1000 / 8
        return (video_bps + audio_bps) * (effective_duration or 0)
    base = height_sizes.get(height, 0) * fraction
    return base * _CONTAINER_OVERHEAD.get(container, 1.0)


def estimate_audio_size(duration_seconds, kbps):
    if not duration_seconds:
        return 0
    return duration_seconds * kbps * 1000 / 8
