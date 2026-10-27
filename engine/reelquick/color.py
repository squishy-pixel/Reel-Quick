"""Color handling: keep HLG tagged as HLG, or convert to SDR for sharing.

SDR conversion prefers a 3D LUT when one is available (the broadcast's own
HDR to SDR LUT gives the exact on-air look). Without one, a BT.2408 style
conversion is used: HLG reference white (75% signal, 203 nits) lands on SDR
white, with a soft knee above it so fireworks and speculars do not clip hard.
"""

from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import Capabilities
from .probe import MediaInfo

HLG_TAG_ARGS = [
    "-color_primaries", "bt2020",
    "-color_trc", "arib-std-b67",
    "-colorspace", "bt2020nc",
    "-color_range", "tv",
]

SDR_TAG_ARGS = [
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-colorspace", "bt709",
    "-color_range", "tv",
]

LUT_EXTENSIONS = (".cube", ".3dl", ".dat", ".m3d", ".csp")


@dataclass(frozen=True)
class Lut:
    path: str
    range: str = "narrow"      # 'narrow': legal-range RGB in and out. 'full': 0..1 in and out.

    def __post_init__(self):
        if self.range not in ("narrow", "full"):
            raise ValueError("lut range must be 'narrow' or 'full'")
        if not Path(self.path).is_file():
            raise FileNotFoundError(self.path)


_active_lut: Lut | None = None
_lut_resolved = False


def lut_dirs() -> list[Path]:
    dirs = []
    env = os.environ.get("REELQUICK_LUT_DIR")
    if env:
        dirs.append(Path(env))
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    dirs.append(base / "luts")
    dirs.append(Path(__file__).resolve().parent.parent / "luts")
    return dirs


_TO_SDR = re.compile(r"(hlg|hdr|pq|2100|2020)[^a-z0-9]*(2|to|->)[^a-z0-9]*(sdr|709)", re.I)
_FROM_SDR = re.compile(r"(sdr|709)[^a-z0-9]*(2|to|->)[^a-z0-9]*(hlg|hdr|pq|2100|2020)", re.I)


def pick_sdr_lut(files: list[Path]) -> Path | None:
    """Prefer a file whose name says HDR to SDR; never pick one that says SDR to HDR."""
    files = sorted(files)
    for f in files:
        if _TO_SDR.search(f.stem):
            return f
    rest = [f for f in files if not _FROM_SDR.search(f.stem)]
    return rest[0] if rest else None


def discover_lut() -> Lut | None:
    """REELQUICK_SDR_LUT, else the best LUT file in a luts folder."""
    rng = os.environ.get("REELQUICK_SDR_LUT_RANGE", "narrow")
    env = os.environ.get("REELQUICK_SDR_LUT")
    if env:
        return Lut(env, rng)
    for d in lut_dirs():
        if d.is_dir():
            best = pick_sdr_lut([p for p in d.iterdir() if p.suffix.lower() in LUT_EXTENSIONS])
            if best:
                return Lut(str(best), rng)
    return None


def set_lut(lut: Lut | None) -> None:
    """Override discovery. Pass None to force the built-in conversion."""
    global _active_lut, _lut_resolved
    _active_lut, _lut_resolved = lut, True


def active_lut() -> Lut | None:
    global _active_lut, _lut_resolved
    if not _lut_resolved:
        _active_lut, _lut_resolved = discover_lut(), True
    return _active_lut


def filter_path(path: str | Path) -> str:
    """Quote a file path for use inside an ffmpeg filter graph.

    Filter graphs treat ':' and '\\' specially, which is every Windows path.
    Forward slashes work on Windows, and the drive colon is escaped inside
    single quotes.
    """
    text = str(path).replace("\\", "/")
    text = text.replace("'", r"'\''").replace(":", r"\:")
    return f"'{text}'"


def source_tag_args(info: MediaInfo) -> list[str]:
    """Re-tag an output with whatever the source declared, so nothing is lost."""
    args: list[str] = []
    if info.color_primaries:
        args += ["-color_primaries", info.color_primaries]
    if info.color_transfer:
        args += ["-color_trc", info.color_transfer]
    if info.color_space:
        args += ["-colorspace", info.color_space]
    args += ["-color_range", "tv"]
    return args


LEGAL_BLACK = 64 / 1023
LEGAL_WHITE = 940 / 1023


def _levels(lo_in: float, hi_in: float, lo_out: float, hi_out: float) -> str:
    """colorlevels on float RGB, same mapping on all three channels."""
    ins = ":".join(f"{c}imin={lo_in:.6f}:{c}imax={hi_in:.6f}" for c in "rgb")
    outs = ":".join(f"{c}omin={lo_out:.6f}:{c}omax={hi_out:.6f}" for c in "rgb")
    return f"colorlevels={ins}:{outs}"


def lut_filter(info: MediaInfo, lut: Lut, bake: bool = True) -> str:
    """YCbCr (as tagged) -> R'G'B' -> 3D LUT -> BT.709 YCbCr 4:2:0.

    zscale always hands over full-range RGB (0.0 = legal black, 1.0 = legal
    white). A LUT built on 10-bit code values ('narrow', the broadcast norm,
    64 = black and 940 = white) needs the signal squeezed into that range
    first and expanded again after. By default that is baked into a cached
    full-range copy of the LUT (see cubelut.py); bake=False keeps the two
    colorlevels passes, which is slower but useful for cross-checking.
    """
    matrix_in = info.color_space or "bt2020nc"
    trc_in = info.color_transfer or "arib-std-b67"
    prim_in = info.color_primaries or "bt2020"
    parts = [
        f"zscale=tin={trc_in}:pin={prim_in}:min={matrix_in}:rin=tv:t={trc_in}:p={prim_in}:m=gbr:r=pc",
        "format=gbrpf32le",
    ]
    path = lut.path
    narrow_passes = lut.range == "narrow"
    if narrow_passes and bake:
        from .cubelut import ensure_baked
        path = str(ensure_baked(lut.path))
        narrow_passes = False
    if narrow_passes:
        parts.append(_levels(0.0, 1.0, LEGAL_BLACK, LEGAL_WHITE))
    parts.append(f"lut3d=file={filter_path(path)}:interp=tetrahedral")
    if narrow_passes:
        parts.append(_levels(LEGAL_BLACK, LEGAL_WHITE, 0.0, 1.0))
    parts += [
        "zscale=tin=bt709:pin=bt709:min=gbr:rin=pc:t=bt709:p=bt709:m=bt709:r=tv",
        "format=yuv420p",
    ]
    return ",".join(parts)


def builtin_sdr_filter(info: MediaInfo, caps: Capabilities, peak_nits: int = 1000) -> str:
    """BT.2408 style HLG/PQ to SDR without a LUT.

    Every zscale step names its input and output matrix, transfer, primaries
    and range in full. Newer zscale builds refuse to guess the RGB matrix when
    leaving YUV ("no path between colorspaces"), and the patch files used by
    lut-check may arrive with unknown transfer tags, so nothing is left to
    frame metadata.
    """
    if not info.is_hdr:
        return "format=yuv420p"
    if caps.tonemap and caps.has_filter("exposure"):
        trc = "arib-std-b67" if info.is_hlg else "smpte2084"
        matrix_in = info.color_space or "bt2020nc"
        # zimg linear light is scaled so 1.0 = peak_nits. Reference white is 203 nits,
        # so multiply by peak/203 (2.3 EV at 1000) to put it at 1.0, then knee the rest.
        ratio = peak_nits / 203.0
        ev = math.log2(ratio)
        return (
            f"zscale=tin={trc}:pin=bt2020:min={matrix_in}:rin=tv:t=linear:p=bt2020:m=gbr:r=pc:npl={peak_nits},"
            "format=gbrpf32le,"
            f"exposure=exposure={ev:.3f},"
            "zscale=tin=linear:pin=bt2020:min=gbr:rin=pc:t=linear:p=bt709:m=gbr:r=pc,"
            f"tonemap=mobius:param=0.9:peak={ratio:.3f},"
            "zscale=tin=linear:pin=bt709:min=gbr:rin=pc:t=bt709:p=bt709:m=bt709:r=tv,"
            "format=yuv420p"
        )
    if caps.has_filter("colorspace"):
        return "colorspace=all=bt709:iall=bt2020:fast=1,format=yuv420p"
    return "format=yuv420p"


def deinterlace_prefix(info: MediaInfo) -> str:
    """bwdif in front of anything that will be viewed progressive (proxy, thumbnails, share MP4)."""
    return "bwdif=mode=send_frame," if info.interlaced else ""


def sdr_filter(info: MediaInfo, caps: Capabilities, peak_nits: int = 1000) -> str:
    """Filter chain that turns the source into BT.709 SDR 8-bit 4:2:0 progressive."""
    pre = deinterlace_prefix(info)
    if not info.is_hdr:
        return pre + "format=yuv420p"
    lut = active_lut()
    if lut is not None:
        return pre + lut_filter(info, lut)
    return pre + builtin_sdr_filter(info, caps, peak_nits)


def thumbnail_filter(info: MediaInfo, caps: Capabilities, width: int) -> str:
    """Scale first, then convert, so the expensive part runs on a small picture."""
    scale = f"scale={width}:-2"
    if not info.is_hdr:
        return f"{scale},format=yuv420p"
    return f"{scale},{sdr_filter(info, caps)}"
