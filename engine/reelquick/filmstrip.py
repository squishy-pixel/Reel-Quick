"""Extract one thumbnail every few seconds across the whole recording.

A six hour file at one thumbnail every two seconds is about 11,000 images.
Each is a seek plus a single frame decode, which is cheap on an intra codec.
The strip is the fastest way for a person to find a graphic by eye, and later
the frames feed the detector.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .color import thumbnail_filter
from .ffmpeg import FfmpegError, capabilities, fmt_seconds, run_ffmpeg
from .probe import MediaInfo
from .timecode import frames_to_seconds, frames_to_tc

INDEX_NAME = "filmstrip.json"


@dataclass
class FilmstripIndex:
    interval_frames: int
    width: int
    count: int
    frames: list[dict]

    def to_dict(self) -> dict:
        return {
            "interval_frames": self.interval_frames,
            "width": self.width,
            "count": self.count,
            "frames": self.frames,
        }


def thumbnail_at(info: MediaInfo, frame: int, out_path: Path, width: int, use_hw: bool = False) -> None:
    """Grab a single frame as JPEG."""
    seek = frames_to_seconds(frame, info.rate)
    vf = thumbnail_filter(info, capabilities(), width)
    args: list[str] = []
    if use_hw:
        args += ["-hwaccel", "cuda"]
    args += [
        "-ss", fmt_seconds(seek),
        "-i", info.path,
        "-map", f"0:{info.video_stream_index}",
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", "4",
        str(out_path),
    ]
    run_ffmpeg(args)


def build_filmstrip(
    info: MediaInfo,
    out_dir: str | Path,
    interval_seconds: float = 2.0,
    width: int = 320,
    workers: int = 8,
    on_progress: Callable[[float, int, int], None] | None = None,
    cancel: threading.Event | None = None,
    use_hw: bool = False,
) -> FilmstripIndex:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rate = info.rate
    interval_frames = max(1, round(interval_seconds * float(rate.fps)))
    frames = list(range(0, info.nb_frames, interval_frames))
    total = len(frames)
    entries: dict[int, dict] = {}
    done = 0
    errors: list[str] = []

    def work(i: int, frame: int) -> tuple[int, dict]:
        name = f"{i:06d}.jpg"
        path = out_dir / name
        if not path.exists():
            thumbnail_at(info, frame, path, width, use_hw=use_hw)
        return i, {
            "i": i,
            "frame": frame,
            "seconds": float(frames_to_seconds(frame, rate)),
            "tc": frames_to_tc(info.start_frames + frame, rate, info.drop_frame),
            "file": name,
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, i, f) for i, f in enumerate(frames)]
        for fut in as_completed(futures):
            if cancel is not None and cancel.is_set():
                pool.shutdown(cancel_futures=True)
                raise FfmpegError(["filmstrip"], -1, "cancelled")
            try:
                i, entry = fut.result()
                entries[i] = entry
            except FfmpegError as e:
                errors.append(str(e))
            done += 1
            if on_progress:
                on_progress(done / total, done, total)

    if errors and not entries:
        raise FfmpegError(["filmstrip"], -1, errors[0])
    ordered = [entries[i] for i in sorted(entries)]
    index = FilmstripIndex(interval_frames=interval_frames, width=width, count=len(ordered), frames=ordered)
    (out_dir / INDEX_NAME).write_text(json.dumps(index.to_dict(), indent=1))
    return index


def load_filmstrip(out_dir: str | Path) -> FilmstripIndex | None:
    p = Path(out_dir) / INDEX_NAME
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return FilmstripIndex(d["interval_frames"], d["width"], d["count"], d["frames"])
