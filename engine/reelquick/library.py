"""Catalogue finished clips by shell and pull reference frames from each.

Last year's clips are named like 2025WK04-SEAvsARI_MatchUp_01_HotAirBalloons00260425.mov:
game, shell, sequence, optional note, then the source frame number. That is
enough to build a labelled reference set for the detector without opening a
sheet, and a zip of small frames is light enough to share.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .color import thumbnail_filter
from .ffmpeg import FfmpegError, capabilities, fmt_seconds, run_ffmpeg
from .probe import probe

CLIP_RE = re.compile(
    r"^(?P<game>\d{4}WK\w+?-[A-Za-z]+vs[A-Za-z]+)_(?P<shell>[A-Za-z0-9]+)_(?P<seq>\d{2})_?(?P<note>[A-Za-z][A-Za-z0-9 -]*?)?(?P<frame>\d{6,9})?$",
    re.I,
)
MEDIA_EXT = {".mov", ".mp4", ".mxf", ".mkv"}

# Last season's file-name shells mapped onto the current shell list.
SHELL_ALIASES = {
    "baseballcard": "Player Card", "playercard": "Player Card", "booth": "Booth", "intheknow": "In The Know Classic",
    "matchup": "Match-Up", "nbapromo": "NBA Promo", "playerlist": "Player List", "lineup": "Player List",
    "playerlineup": "Player List", "playoffbracket": "Playoff Bracket", "playoffpicture": "Playoff Picture",
    "primestories": "Prime Stories", "singlepagestory": "Single Page Story", "qbcomparison": "QB Comparison",
    "repurposedshell": "Repurposed Shell", "scenic": "Scenic Comp", "sceniccomp": "Scenic Comp",
    "scenicdroneshow": "Scenic Droneshow", "drone": "Scenic Droneshow", "schedules": "Schedules", "score": "Score",
    "standings": "Standings", "takeover": "Takeover", "tnftalk": "TNF Talk", "whoyagot": "Who Ya Got",
    "wingstop": "Wingstop", "video2box": "Video 2-Box", "scorewithvideo": "Score with Video", "custom": "Custom",
    "nextweekpromo": "Next Week Promo", "promo": "Promo", "special": "Special",
}


@dataclass
class LibraryClip:
    path: str
    game: str
    shell_raw: str
    shell: str
    seq: int
    note: str
    source_frame: int | None
    duration: float
    width: int
    height: int
    is_hdr: bool
    frames: list[str]        # thumbnail paths relative to the library root


def parse_name(path: Path) -> dict | None:
    m = CLIP_RE.match(path.stem)
    if not m:
        return None
    raw = m.group("shell")
    return {
        "game": m.group("game"),
        "shell_raw": raw,
        "shell": SHELL_ALIASES.get(raw.lower(), re.sub(r"(?<!^)(?=[A-Z])", " ", raw)),
        "seq": int(m.group("seq")),
        "note": (m.group("note") or "").strip(" -"),
        "source_frame": int(m.group("frame")) if m.group("frame") else None,
    }


def find_clips(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in MEDIA_EXT and not p.name.startswith("."))


def _grab(info, t: float, out: Path, width: int) -> None:
    run_ffmpeg(["-ss", fmt_seconds(t), "-i", info.path, "-frames:v", "1",
                "-vf", thumbnail_filter(info, capabilities(), width), "-q:v", "4", str(out)])


def build_library(folder: str | Path, out_dir: str | Path, frames_per_clip: int = 3, width: int = 320,
                  workers: int = 6, on_progress: Callable[[float, int, int], None] | None = None,
                  cancel: threading.Event | None = None, make_zip: bool = True) -> dict:
    folder, out_dir = Path(folder), Path(out_dir)
    thumbs = out_dir / "frames"
    thumbs.mkdir(parents=True, exist_ok=True)
    paths = find_clips(folder)
    total = len(paths)
    clips: list[LibraryClip] = []
    unmatched: list[str] = []
    errors: list[str] = []
    done = 0

    def work(p: Path) -> LibraryClip | None:
        meta = parse_name(p)
        if meta is None:
            unmatched.append(str(p))
            return None
        info = probe(p)
        shell_dir = thumbs / re.sub(r"[^A-Za-z0-9]+", "", meta["shell"])
        shell_dir.mkdir(exist_ok=True)
        rel: list[str] = []
        for k in range(frames_per_clip):
            t = info.duration * (k + 1) / (frames_per_clip + 1)     # skip the handles at both ends
            out = shell_dir / f"{p.stem}_{k + 1}.jpg"
            if not out.exists():
                _grab(info, t, out, width)
            rel.append(str(out.relative_to(out_dir)))
        return LibraryClip(path=str(p), duration=info.duration, width=info.width, height=info.height,
                           is_hdr=info.is_hdr, frames=rel, **meta)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(work, p): p for p in paths}
        for fut in as_completed(futs):
            if cancel is not None and cancel.is_set():
                break
            try:
                c = fut.result()
                if c:
                    clips.append(c)
            except (FfmpegError, ValueError, OSError) as e:
                errors.append(f"{futs[fut].name}: {str(e).splitlines()[-1][:160]}")
            done += 1
            if on_progress:
                on_progress(done / max(1, total), done, total)

    clips.sort(key=lambda c: (c.game, c.shell, c.seq))
    by_shell = Counter(c.shell for c in clips)
    catalog = {
        "source_folder": str(folder),
        "clips": [asdict(c) for c in clips],
        "shells": dict(sorted(by_shell.items())),
        "games": sorted({c.game for c in clips}),
        "unmatched": unmatched,
        "errors": errors,
        "frames_per_clip": frames_per_clip,
        "width": width,
    }
    (out_dir / "library.json").write_text(json.dumps(catalog, indent=1), encoding="utf-8")
    if make_zip:
        zip_base = out_dir.parent / (out_dir.name + "-frames")
        shutil.make_archive(str(zip_base), "zip", root_dir=out_dir)
        catalog["zip"] = str(zip_base) + ".zip"
    return catalog
