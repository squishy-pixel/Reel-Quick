"""Grab single frames as images: one representative still per clip, or any frame by hand.

Default is an 8-bit PNG tone-mapped to SDR, which is what a visual reference
needs to be to open anywhere. The HDR option writes a 16-bit PNG with the
source's color tags left alone, for the rare case the still goes back into a
grade.
"""

from __future__ import annotations

from pathlib import Path

from .color import sdr_filter
from .ffmpeg import capabilities, fmt_seconds, run_ffmpeg
from .probe import MediaInfo
from .timecode import frames_to_seconds, frames_to_tc

FORMATS = {"png": ".png", "jpg": ".jpg", "tiff": ".tif"}


def still_args(info: MediaInfo, frame: int, out_path: Path, fmt: str = "png", hdr: bool = False,
               width: int | None = None) -> list[str]:
    if frame < 0 or frame >= info.nb_frames:
        raise ValueError(f"frame {frame} outside the recording (0..{info.nb_frames - 1})")
    parts: list[str] = []
    if width and width < info.width:
        parts.append(f"scale={width}:-2")
    if hdr:
        parts.append("format=rgb48le" if fmt != "jpg" else "format=yuvj444p")
    elif fmt == "jpg":
        parts.append(sdr_filter(info, capabilities()).replace("format=yuv420p", "format=yuvj444p"))
    else:
        parts.append(sdr_filter(info, capabilities()).replace("format=yuv420p", "format=rgb24"))
    tc = frames_to_tc(info.start_frames + frame, info.rate, info.drop_frame)
    args = [
        "-ss", fmt_seconds(frames_to_seconds(frame, info.rate)),
        "-i", info.path,
        "-map", f"0:{info.video_stream_index}",
        "-frames:v", "1",
        "-vf", ",".join(parts),
        "-metadata", f"comment=source={Path(info.path).name} tc={tc} frame={frame}",
    ]
    if fmt == "jpg":
        args += ["-q:v", "2"]
    args.append(str(out_path))
    return args


def grab_still(info: MediaInfo, frame: int, out_dir: str | Path, name: str, fmt: str = "png",
               hdr: bool = False, width: int | None = None, overwrite: bool = False) -> Path:
    if fmt not in FORMATS:
        raise ValueError(f"still format must be one of {sorted(FORMATS)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (name + FORMATS[fmt])
    if out_path.exists() and not overwrite:
        raise FileExistsError(str(out_path))
    run_ffmpeg(still_args(info, frame, out_path, fmt, hdr, width))
    return out_path
