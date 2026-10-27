"""Color handling: keep HLG tagged as HLG, or tone-map to SDR for sharing."""

from __future__ import annotations

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


def sdr_filter(info: MediaInfo, caps: Capabilities, peak_nits: int = 1000) -> str:
    """Filter chain that turns the source into BT.709 SDR 8-bit 4:2:0.

    Uses zimg plus the tonemap filter when the ffmpeg build has them. Without
    them the picture is still converted to 709 primaries where possible so
    colors are not wildly off, but highlights will clip.
    """
    if not info.is_hdr:
        return "format=yuv420p"
    if caps.tonemap:
        trc = "arib-std-b67" if info.is_hlg else "smpte2084"
        return (
            f"zscale=t=linear:npl={peak_nits}:tin={trc}:pin=bt2020:min=bt2020nc,"
            "format=gbrpf32le,"
            "zscale=p=bt709,"
            "tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,"
            "format=yuv420p"
        )
    if caps.has_filter("colorspace"):
        return "colorspace=all=bt709:iall=bt2020:fast=1,format=yuv420p"
    return "format=yuv420p"


def thumbnail_filter(info: MediaInfo, caps: Capabilities, width: int) -> str:
    """Scale first, then tone-map, so the expensive part runs on a small picture."""
    scale = f"scale={width}:-2"
    if not info.is_hdr:
        return f"{scale},format=yuv420p"
    return f"{scale},{sdr_filter(info, caps)}"
