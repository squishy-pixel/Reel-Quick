"""The web reel: every clip of every game, as a folder that plays in any browser.

The folder holds index.html, catalog.js and media/<game>/ (a web MP4 and a thumbnail per clip). It needs no
server: open index.html from the disk, a NAS or a synced folder, or upload the folder as it is to any web host.
catalog.js is loaded with a script tag rather than fetched, so the page also works from file://.

Rebuilding into the same folder replaces the games found in the work folder and keeps the others already in
the catalog, so archived games stay in the reel and seasons accumulate.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

from .exports import clip_row
from .ffmpeg import FfmpegError, fmt_seconds, run_ffmpeg
from .naming import slug
from .probe import probe
from .project import Project
from .render import render_clip
from .season import season_of
from .workspace import PROJECT_FILE

CATALOG = "catalog.js"
PREFIX = "window.REEL = "
WEB_FORMATS = ("mp4", "mp4_high", "mp4_slack")      # rendered files a browser plays, best first


def read_catalog(out_dir: Path) -> dict:
    p = Path(out_dir) / CATALOG
    try:
        text = p.read_text(encoding="utf-8").strip()
        return json.loads(text[len(PREFIX):].rstrip(";")) if text.startswith(PREFIX) else {}
    except (OSError, ValueError):
        return {}


def _games(work_root: Path) -> list[tuple[Path, Project]]:
    out = []
    for pf in sorted(Path(work_root).glob(f"*/{PROJECT_FILE}")):
        try:
            out.append((pf.parent, Project.load(pf)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _thumb(video: Path, dest: Path, seconds: float) -> None:
    if dest.exists() and dest.stat().st_mtime >= video.stat().st_mtime:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", fmt_seconds(max(0.0, seconds)), "-i", str(video), "-frames:v", "1",
                "-vf", "scale=480:-2", "-q:v", "4", str(dest)])


def build_reel(work_root: Path, out_dir: Path, render_missing: bool = True, on_progress=None,
               cancel: threading.Event | None = None) -> dict:
    out_dir = Path(out_dir)
    games = _games(work_root)
    clips_total = sum(len(p.clips) for _, p in games) or 1
    done_n = 0
    entries: list[dict] = []
    game_meta: dict[str, dict] = {}
    skipped: list[str] = []
    rendered = 0

    for folder, project in games:
        g = project.game
        gid = slug("_".join(x for x in (g.date, g.tag) if x)) or slug(folder.name)
        game_meta[gid] = {"id": gid, "season": season_of(g.date), "date": g.date, "week": g.week,
                          "matchup": g.matchup, "label": " ".join(x for x in (g.week, g.matchup) if x) or folder.name}
        media = out_dir / "media" / gid
        source_ok = Path(project.source).is_file()
        proxy = folder / "proxy.mp4"
        for clip in sorted(project.clips, key=lambda c: c.in_frame):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            done_n += 1
            if on_progress:
                on_progress(done_n / clips_total, {"game": gid, "clip": clip.name})
            dest = media / f"{clip.name}.mp4"
            have = next((Path(clip.rendered[k]) for k in WEB_FORMATS
                         if isinstance(clip.rendered.get(k), str) and Path(clip.rendered[k]).is_file()), None)
            try:
                if have is not None:
                    if not dest.exists() or dest.stat().st_size != have.stat().st_size:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(have, dest)
                elif not dest.exists():
                    if not render_missing or not (source_ok or proxy.is_file()):
                        skipped.append(f"{gid}/{clip.name}: no web MP4, and " +
                                       ("rendering is off" if not render_missing else "neither the recording nor a proxy is available"))
                        continue
                    spec = project.clip_spec(clip, "mp4", mp4_quality="review")
                    spec.name = clip.name
                    info = project.info if source_ok else probe(proxy)
                    if not source_ok:
                        spec.audio_tracks = None
                    render_clip(info, spec, media, overwrite=True, cancel=cancel)
                    rendered += 1
                fps = float(project.info.rate.fps)
                lo, hi = clip.padded_range(fps)
                mid = ((clip.in_frame + clip.out_frame) / 2 - max(0, lo)) / fps
                _thumb(dest, media / f"{clip.name}.jpg", mid)
            except (FfmpegError, OSError, ValueError) as e:
                skipped.append(f"{gid}/{clip.name}: {str(e).splitlines()[-1][:200]}")
                continue
            r = clip_row(project, clip)
            entries.append({
                "id": f"{gid}/{clip.id}", "game": gid, "season": game_meta[gid]["season"], "date": g.date,
                "name": clip.name, "shell": clip.shell, "camera": clip.camera, "engine": clip.engine,
                "segment": r["Segment"], "phase": r["Phase"], "whose": clip.whose, "source": r["Source"],
                "desc": clip.desc, "duration": r["Duration (s)"], "time": r["Time"], "zone": r["Zone"],
                "tc": r["In"], "in_frame": clip.in_frame,
                "video": f"media/{gid}/{clip.name}.mp4", "thumb": f"media/{gid}/{clip.name}.jpg",
            })

    # keep what an earlier build put here for games no longer in the work folder
    old = read_catalog(out_dir)
    kept_games = {k: v for k, v in (old.get("games") or {}).items() if k not in game_meta}
    kept = [c for c in old.get("clips") or [] if c.get("game") in kept_games]
    catalog = {"version": 1, "generated": datetime.now().isoformat(timespec="seconds"),
               "games": {**kept_games, **game_meta},
               "clips": sorted(kept + entries, key=lambda c: (c.get("date", ""), c.get("game", ""), c.get("in_frame", 0)))}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CATALOG).write_text(PREFIX + json.dumps(catalog, separators=(",", ":")) + ";\n", encoding="utf-8")
    shutil.copyfile(Path(__file__).parent / "reel_index.html", out_dir / "index.html")
    return {"out_dir": str(out_dir), "clips": len(catalog["clips"]), "games": len(catalog["games"]),
            "built_games": len(game_meta), "kept_games": len(kept_games), "rendered": rendered, "skipped": skipped}
