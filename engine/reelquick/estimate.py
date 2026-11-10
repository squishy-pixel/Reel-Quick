"""How much disk a render will take, and whether the destination has it.

The numbers are deliberately on the safe side: ProRes is close to constant
bit rate, so its figure is good; the MP4 figures are the presets' caps, which
real footage stays under; a stream copy takes the source's own video rate.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .probe import MediaInfo

# Apple's ProRes target rates for 1920x1080 at 29.97, in Mb/s. Scaled by pixels and frame rate below.
PRORES_MBPS_1080_2997 = {"proxy": 45, "lt": 102, "standard": 147, "hq": 220}
# Share MP4 peak rates at 1080p59.94, in Mb/s: the slack preset's cap, and safe ceilings for review and high.
MP4_MBPS_1080_5994 = {"slack": 5, "review": 18, "high": 30}
STILL_BYTES = {"png": 4_000_000, "jpg": 600_000, "tiff": 6_500_000}
PCM_MONO_BPS = 48_000 * 24          # one 24-bit 48 kHz track
SLACK_HEIGHT = 720


def _scale(info: MediaInfo, height: int | None = None) -> float:
    """This picture's pixel rate against 1920x1080."""
    h = min(height or info.height, info.height)
    w = info.width * h / info.height
    return (w * h) / (1920 * 1080)


def video_bps(info: MediaInfo, fmt: str, prores_profile: str = "standard", mp4_quality: str = "review",
              mp4_height: int | None = None) -> float:
    fps = float(info.rate.fps)
    if fmt == "prores":
        return PRORES_MBPS_1080_2997.get(prores_profile, 147) * 1e6 * _scale(info) * fps / 29.97
    if fmt == "mp4":
        h = min(mp4_height or SLACK_HEIGHT, SLACK_HEIGHT) if mp4_quality == "slack" else mp4_height
        return MP4_MBPS_1080_5994.get(mp4_quality, 18) * 1e6 * _scale(info, h) * fps / 59.94
    # copy: the source's own video rate, its audio taken out
    total = info.size_bytes * 8 / info.duration if info.duration else 0.0
    return max(0.0, total - PCM_MONO_BPS * len(info.audio))


def audio_bps(fmt: str, surround: bool) -> float:
    if fmt == "mp4":
        return 192_000 + (448_000 if surround else 0)
    return PCM_MONO_BPS * (2 + (6 if surround else 0))


def free_space(path: str | Path) -> tuple[Path, int | None]:
    """Free bytes on the drive that holds `path`, found through its nearest existing folder."""
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return p, shutil.disk_usage(p).free
    except OSError:
        return p, None
