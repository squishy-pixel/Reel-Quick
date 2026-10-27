"""Measure how an SDR conversion treats known HLG levels.

Feeds flat patches at 25%, 50%, 75% (reference white, 203 nits) and 100% HLG
signal through the conversion and reports the resulting 8-bit BT.709 luma.
Used to pick the right range convention for a LUT and to catch a conversion
that comes out dark, as the first tone-map chain here did.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .color import Lut, builtin_sdr_filter, lut_filter
from .ffmpeg import capabilities, ffmpeg_bin
from .probe import MediaInfo

LEVELS = (0.10, 0.25, 0.50, 0.65, 0.75, 0.85, 1.00)
# What a BT.2408 style conversion gives: white at 235, highlights kneed.
EXPECTED_WHITE = 235


@dataclass
class PatchResult:
    label: str
    luma: dict[float, float]

    @property
    def white(self) -> float:
        return self.luma[0.75]

    @property
    def summary(self) -> str:
        cells = " ".join(f"{int(l*100):3d}%:{v:5.1f}" for l, v in self.luma.items())
        return f"{self.label:<12} {cells}"


def _fake_hlg_info(path: str) -> MediaInfo:
    return MediaInfo(path=path, size_bytes=0, duration=1, container="matroska", video_stream_index=0,
                     codec="h264", profile="", width=256, height=144, pix_fmt="yuv422p10le", bit_depth=10,
                     rate_num=1, rate_den=1, is_intra=True, color_primaries="bt2020",
                     color_transfer="arib-std-b67", color_space="bt2020nc", start_timecode=None)


def _patch(level: float, out: Path) -> Path:
    y = round(64 + 876 * level)
    subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=256x144:r=1",
                    "-frames:v", "1", "-vf", f"format=yuv422p10le,lutyuv=y={y}:u=512:v=512",
                    "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv422p10le",
                    "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
                    "-color_range", "tv", str(out)], check=True)
    return out


def _mean_luma(src: Path, vf: str) -> float:
    proc = subprocess.run([ffmpeg_bin(), "-v", "info", "-y", "-i", str(src), "-vf",
                           f"{vf},signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"],
                          capture_output=True, text=True)
    m = re.search(r"YAVG=([0-9.]+)", proc.stderr)
    if not m:
        lines = [l for l in proc.stderr.strip().splitlines() if l.strip()]
        tail = "\n".join(lines[-10:]) if lines else "no output"
        caps = capabilities()
        raise RuntimeError(
            f"ffmpeg could not run the conversion.\n"
            f"ffmpeg: {caps.ffmpeg} ({caps.version})\n"
            f"filters present: zscale={caps.has_filter('zscale')} tonemap={caps.has_filter('tonemap')} "
            f"exposure={caps.has_filter('exposure')} lut3d={caps.has_filter('lut3d')} colorlevels={caps.has_filter('colorlevels')}\n"
            f"filter: {vf}\n"
            f"ffmpeg said:\n{tail}"
        )
    return float(m.group(1))


def measure(vf: str, label: str, workdir: Path) -> PatchResult:
    luma = {}
    for lvl in LEVELS:
        src = workdir / f"hlg_{lvl}.mkv"
        if not src.exists():
            _patch(lvl, src)
        luma[lvl] = _mean_luma(src, vf)
    return PatchResult(label, luma)


def check(lut_path: str | None = None) -> list[PatchResult]:
    """Measure the built-in conversion and, if given, the LUT under both range conventions."""
    results = []
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        info = _fake_hlg_info("")
        results.append(measure(builtin_sdr_filter(info, capabilities()), "built-in", wd))
        if lut_path:
            for rng in ("narrow", "full"):
                results.append(measure(lut_filter(info, Lut(lut_path, rng)), f"lut {rng}", wd))
    return results


def recommend(results: list[PatchResult]) -> str | None:
    """Pick the LUT range whose 75% patch lands nearest SDR white, if a LUT was measured."""
    luts = [r for r in results if r.label.startswith("lut ")]
    if not luts:
        return None
    best = min(luts, key=lambda r: abs(r.white - EXPECTED_WHITE))
    return best.label.split()[1]
