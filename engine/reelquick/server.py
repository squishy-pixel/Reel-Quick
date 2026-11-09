"""Local API the app window talks to. Everything runs on this machine; nothing listens beyond localhost."""

from __future__ import annotations

import mimetypes
import os
import re
import string
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .color import active_lut
from .ffmpeg import FfmpegError, capabilities, encoder_works
from .filmstrip import build_filmstrip, thumbnail_at
from .jobs import JobManager
from .markers import Marker, import_log_csv
from .naming import GameInfo, build_name
from .probe import probe
from .project import ClipEntry, Settings, StillEntry
from .proxy import ProxySettings, build_proxy
from .render import MP4_QUALITIES, OutputFormat, render_clip
from .stills import grab_still
from .timecode import frames_to_tc
from .vocab import load_vocab, save_vocab
from .workspace import Session, default_work_root, recent_projects

UI_DIR = Path(__file__).resolve().parent / "ui"


# ---- request bodies -----------------------------------------------------------

class OpenBody(BaseModel):
    path: str


class GameBody(BaseModel):
    date: str = ""
    week: str = ""
    away: str = ""
    home: str = ""


class ClipBody(BaseModel):
    in_frame: int
    out_frame: int
    shell: str = ""
    desc: str = ""
    camera: str = ""
    engine: str = ""
    formats: list[str] | None = None
    name: str | None = None
    prefill: bool = True            # take shell/camera/engine/description from the nearest log marker


class ClipPatch(BaseModel):
    in_frame: int | None = None
    out_frame: int | None = None
    name: str | None = None
    shell: str | None = None
    desc: str | None = None
    camera: str | None = None
    engine: str | None = None
    formats: list[str] | None = None
    head_seconds: float | None = None
    tail_seconds: float | None = None
    still_frame: int | None = None
    extra_stills: list[int] | None = None
    notes: str | None = None


class StillBody(BaseModel):
    frame: int
    name: str


class RevealBody(BaseModel):
    path: str


class AudioPreviewBody(BaseModel):
    at_frame: int
    seconds: float = 15.0


class MarkerBody(BaseModel):
    frame: int
    kind: str = ""
    shell: str = ""
    description: str = ""
    camera: str = ""
    engine: str = ""


class MarkersImportBody(BaseModel):
    csv: str
    offset_seconds: float | None = None
    replace: bool = True


class FilmstripBody(BaseModel):
    interval: float = 2.0
    width: int = 320
    workers: int = 8
    force: bool = False


class ProxyBody(BaseModel):
    height: int = 1080
    bitrate: str | None = None       # None: scaled to the height (proxy.default_bitrate)
    jobs: int = 1


class RenderBody(BaseModel):
    clip_ids: list[str] | None = None
    overwrite: bool = False
    stills: bool = True
    from_proxy: bool = False        # cut from the proxy when the original is not available
    # A one-off export: these formats and this MP4 quality for this run only; the clips keep their own.
    formats: list[str] | None = None
    mp4_quality: str | None = None


# ---- helpers ------------------------------------------------------------------

def _list_dir(path: str | None) -> dict:
    """Minimal file browser for the in-app open dialog."""
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
            return {"path": "", "parent": None, "entries": [{"name": d, "path": d, "dir": True} for d in drives]}
        roots = [{"name": "Home", "path": str(Path.home()), "dir": True}]
        if Path("/Volumes").is_dir():
            roots += [{"name": v.name, "path": str(v), "dir": True} for v in sorted(Path("/Volumes").iterdir()) if v.is_dir()]
        return {"path": "", "parent": None, "entries": roots}
    p = Path(path)
    if not p.is_dir():
        raise HTTPException(404, f"not a folder: {path}")
    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith((".", "$")):
                continue
            try:
                is_dir = child.is_dir()
                size = 0 if is_dir else child.stat().st_size
            except OSError:
                continue
            if not is_dir and child.suffix.lower() not in (".mxf", ".mov", ".mp4", ".mkv", ".mts", ".m2ts", ".avi", ".csv"):
                continue
            entries.append({"name": child.name, "path": str(child), "dir": is_dir, "size": size})
    except PermissionError:
        raise HTTPException(403, f"no access to {path}")
    parent = str(p.parent) if p.parent not in (p, Path("/Volumes"), Path.home().parent) else ""
    return {"path": str(p), "parent": parent, "entries": entries}


def _patch_label(patch: dict) -> str:
    """What an undo of this clip edit will say it put back."""
    names = {"in_frame": "in point", "out_frame": "out point", "still_frame": "still", "extra_stills": "extra stills",
             "formats": "formats", "name": "file name", "shell": "shell", "desc": "description", "camera": "camera",
             "engine": "engine", "head_seconds": "head", "tail_seconds": "tail", "notes": "notes"}
    keys = [names.get(k, k) for k in patch]
    return "clip " + (", ".join(keys) if keys else "edit")


def _nearest_strip(w, frame: int):
    idx = w.filmstrip_index()
    if idx is None or not idx.frames:
        return None
    best = min(idx.frames, key=lambda t: abs(t["frame"] - frame))
    p = w.strip_dir / best["file"]
    return p if p.is_file() else None


def create_app(session: Session | None = None) -> FastAPI:
    session = session or Session()
    jobs = JobManager()
    app = FastAPI(title="Reel Quick", version=__version__)
    app.state.session = session
    app.state.jobs = jobs

    def ws():
        try:
            return session.require()
        except LookupError as e:
            raise HTTPException(409, str(e))

    def state() -> dict:
        w = session.workspace
        caps = capabilities()
        lut = active_lut()
        return {
            "version": __version__,
            "workspace": w.status() if w else None,
            "project": w.project.to_dict() if w else None,
            "jobs": [j.to_dict() for j in jobs.list()],
            "work_root": str(session.work_root),
            "vocab": load_vocab(session.work_root),
            "history": session.history() if w else {"undo": None, "redo": None},
            "engine": {
                "ffmpeg": caps.version.split()[0],
                "nvenc": encoder_works("h264_nvenc") if caps.nvenc else False,
                "lut": {"name": Path(lut.path).name, "range": lut.range} if lut else None,
            },
        }

    # ---- state and files -------------------------------------------------

    @app.get("/api/state")
    def get_state():
        return state()

    @app.get("/api/vocab")
    def get_vocab():
        return load_vocab(session.work_root)

    @app.put("/api/vocab")
    def put_vocab(body: dict):
        return save_vocab(session.work_root, body)

    @app.get("/api/fonts")
    def fonts():
        """Font files the user dropped in <work root>/fonts, so licensed fonts never enter the repo."""
        d = session.work_root / "fonts"
        out = []
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in (".ttf", ".otf", ".woff", ".woff2"):
                    stem = f.stem
                    fam = re.split(r"[-_ ]", stem)[0]
                    low = stem.lower()
                    weight = 700 if "bold" in low or "black" in low or "heavy" in low else 300 if "light" in low or "thin" in low else 500 if "medium" in low or "semi" in low else 400
                    out.append({"file": f.name, "url": f"/userfonts/{f.name}", "family": fam, "weight": weight,
                                "italic": "italic" in low or "oblique" in low, "playoff": "playoff" in low})
        return out

    @app.get("/userfonts/{name}")
    def userfont(name: str):
        p = session.work_root / "fonts" / Path(name).name
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/recent")
    def recent():
        return recent_projects(session.work_root)

    @app.get("/api/fs")
    def fs(path: str | None = None):
        return _list_dir(path)

    @app.post("/api/reveal")
    def reveal(body: RevealBody):
        """Open a folder (or a file's folder) in Explorer / Finder."""
        import subprocess
        import sys as _sys
        p = Path(body.path)
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        target = p if p.is_dir() else p.parent
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return {"ok": True, "path": str(target)}

    @app.post("/api/open")
    def open_file(body: OpenBody):
        try:
            session.open(body.path)
        except FileNotFoundError as e:
            raise HTTPException(404, f"file not found: {e}")
        except (FfmpegError, ValueError) as e:
            raise HTTPException(400, f"could not read file: {e}")
        return state()

    @app.put("/api/game")
    def put_game(body: GameBody):
        w = ws()
        with session.lock:
            session.remember("game details")
            w.project.game = GameInfo(body.date, body.week, body.away, body.home)
            w.project.renumber()
            w.save()
        return state()

    @app.put("/api/settings")
    def put_settings(body: dict):
        w = ws()
        with session.lock:
            session.remember("settings")
            s = w.project.settings
            for k, v in body.items():
                if k in Settings.__dataclass_fields__:
                    setattr(s, k, v)
            w.save()
        return state()

    # ---- clips -----------------------------------------------------------

    @app.post("/api/clips")
    def add_clip(body: ClipBody):
        w = ws()
        with session.lock:
            shell, desc, camera, engine = body.shell, body.desc, body.camera, body.engine
            if body.prefill and not (shell or desc or camera or engine):
                m = w.project.nearest_marker(body.in_frame, body.out_frame)
                if m is not None:
                    shell, desc, camera, engine = m.shell, m.description, m.camera, m.engine
            if body.out_frame <= body.in_frame:
                raise HTTPException(400, "out must be after in")
            session.remember("add clip")
            clip = w.project.add_clip(body.in_frame, body.out_frame, shell, desc, body.formats, body.name, camera, engine)
            w.project.renumber()
            w.save()
        return {"clip": clip.to_dict(), "project": w.project.to_dict()}

    @app.patch("/api/clips/{clip_id}")
    def patch_clip(clip_id: str, body: ClipPatch):
        w = ws()
        with session.lock:
            clip = next((c for c in w.project.clips if c.id == clip_id), None)
            if clip is None:
                raise HTTPException(404, "no such clip")
            custom_name = body.name is not None and body.name != clip.name
            patch = body.model_dump(exclude_none=True)
            new_in = patch.get("in_frame", clip.in_frame)
            new_out = patch.get("out_frame", clip.out_frame)
            if new_out <= new_in:
                raise HTTPException(400, f"out must be after in ({w.project.tc(new_in)} to {w.project.tc(new_out)})")
            session.remember(_patch_label(patch))
            for k, v in patch.items():
                setattr(clip, k, v)
            lo, hi = clip.padded_range(float(w.project.info.rate.fps))
            lo, hi = max(0, lo), min(w.project.info.nb_frames, hi)
            if clip.still_frame is not None:
                clip.still_frame = max(lo, min(hi - 1, clip.still_frame))
            # Stills may sit anywhere the rendered clip reaches, handles included; one of each frame, nothing clamped
            # on top of another.
            clip.extra_stills = sorted({f for f in clip.extra_stills if lo <= f < hi and f != clip.still_frame})
            if custom_name:
                clip.notes = clip.notes or ""
                clip.name = body.name            # a typed name is kept as typed
            else:
                w.project.renumber()
            w.save()
        return {"clip": clip.to_dict(), "project": w.project.to_dict()}

    @app.delete("/api/clips/{clip_id}")
    def delete_clip(clip_id: str):
        w = ws()
        with session.lock:
            if not any(c.id == clip_id for c in w.project.clips):
                raise HTTPException(404, "no such clip")
            session.remember("delete clip")
            w.project.clips = [c for c in w.project.clips if c.id != clip_id]
            w.project.renumber()
            w.save()
        return {"project": w.project.to_dict()}

    @app.post("/api/clips/renumber")
    def renumber():
        w = ws()
        with session.lock:
            session.remember("renumber clips")
            w.project.renumber()
            w.save()
        return {"project": w.project.to_dict()}

    # ---- stills ----------------------------------------------------------

    @app.post("/api/stills")
    def add_still(body: StillBody):
        """A hand-named still: recorded in the project and rendered right away."""
        w = ws()
        with session.lock:
            session.remember("save still")
            try:
                still = w.project.add_still(body.frame, body.name)
                path = w.project.render_still(still, w.clips_dir, overwrite=True)
            except (ValueError, FfmpegError) as e:
                raise HTTPException(400, str(e))
            w.save()
        return {"still": still.to_dict(), "path": str(path), "project": w.project.to_dict()}

    @app.delete("/api/stills/{still_id}")
    def delete_still(still_id: str):
        w = ws()
        with session.lock:
            session.remember("delete still")
            w.project.stills = [s for s in w.project.stills if s.id != still_id]
            w.save()
        return {"project": w.project.to_dict()}

    @app.post("/api/audio-preview")
    def audio_preview(body: AudioPreviewBody):
        """One short MP3 per source audio track, so the right pair can be found by ear."""
        w = ws()
        info = w.project.info
        out_dir = w.root / ".cache" / "audio"
        out_dir.mkdir(parents=True, exist_ok=True)
        start = max(0, min(info.nb_frames - 1, body.at_frame))
        from .timecode import frames_to_seconds
        from .ffmpeg import fmt_seconds, run_ffmpeg
        read_info, _origin = w.media_for_reading()
        if _origin == "proxy":
            raise HTTPException(409, "track preview needs the original recording; the proxy only carries the chosen pair")
        files = []
        for n, a in enumerate(info.audio, start=1):
            out = out_dir / f"track{n:02d}_{start}.mp3"
            wave = out_dir / f"track{n:02d}_{start}.png"
            if not out.exists() or not wave.exists():
                try:
                    run_ffmpeg(["-ss", fmt_seconds(frames_to_seconds(start, info.rate)), "-i", info.path,
                                "-t", fmt_seconds(body.seconds), "-map", f"0:{a.stream_index}", "-vn",
                                "-c:a", "libmp3lame", "-b:a", "128k", str(out),
                                "-filter_complex", f"[0:{a.stream_index}]showwavespic=s=720x48:colors=#FEB404:scale=sqrt[w]",
                                "-map", "[w]", "-frames:v", "1", str(wave)])
                except FfmpegError as e:
                    raise HTTPException(500, f"track {n}: {e}")
            files.append({"track": n, "url": f"/cache/audio/{out.name}", "wave": f"/cache/audio/{wave.name}", "channels": a.channels})
        return {"tracks": files, "seconds": body.seconds}

    @app.get("/cache/audio/{name}")
    def cache_audio(name: str):
        w = ws()
        p = w.root / ".cache" / "audio" / Path(name).name
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png" if p.suffix == ".png" else "audio/mpeg")

    # ---- markers ---------------------------------------------------------

    @app.post("/api/markers/import")
    def import_markers(body: MarkersImportBody):
        w = ws()
        offset = body.offset_seconds if body.offset_seconds is not None else w.project.settings.log_offset_seconds
        res = import_log_csv(body.csv, w.project.info, offset)
        with session.lock:
            session.remember("import log")
            if body.replace:
                w.project.markers = [m for m in w.project.markers if m.source != "log"] + res.markers + res.untimed
            else:
                w.project.markers += res.markers + res.untimed
            w.project.markers.sort(key=lambda m: (m.frame is None, m.frame or 0))
            w.save()
        return {"added": len(res.markers), "untimed": len(res.untimed), "skipped": res.skipped,
                "all_outside": res.all_outside, "columns": res.columns, "project": w.project.to_dict()}

    @app.post("/api/markers")
    def add_marker(body: MarkerBody):
        w = ws()
        with session.lock:
            session.remember("add marker")
            m = Marker(frame=body.frame, tc=w.project.tc(body.frame), kind=body.kind, shell=body.shell,
                       description=body.description, camera=body.camera, engine=body.engine, source="user")
            w.project.markers.append(m)
            w.project.markers.sort(key=lambda x: (x.frame is None, x.frame or 0))
            w.save()
        return {"marker": m.to_dict(), "project": w.project.to_dict()}

    @app.delete("/api/markers/{frame}")
    def delete_marker(frame: int):
        w = ws()
        with session.lock:
            session.remember("delete marker")
            w.project.markers = [m for m in w.project.markers if m.frame != frame]
            w.save()
        return {"project": w.project.to_dict()}

    @app.delete("/api/markers/untimed/{index}")
    def delete_untimed(index: int):
        w = ws()
        with session.lock:
            untimed = [m for m in w.project.markers if m.frame is None]
            if not 0 <= index < len(untimed):
                raise HTTPException(404)
            session.remember("delete marker")
            w.project.markers.remove(untimed[index])
            w.save()
        return {"project": w.project.to_dict()}

    @app.post("/api/undo")
    def undo():
        ws()
        label = session.undo()
        if label is None:
            raise HTTPException(409, "nothing to undo")
        return {"label": label, **state()}

    @app.post("/api/redo")
    def redo():
        ws()
        label = session.redo()
        if label is None:
            raise HTTPException(409, "nothing to redo")
        return {"label": label, **state()}

    # ---- media -----------------------------------------------------------

    @app.get("/media/proxy.mp4")
    def proxy_media():
        """Starlette's FileResponse answers Range requests itself (206, Content-Range, Accept-Ranges),
        which Safari in particular insists on for video; it also streams via sendfile."""
        w = ws()
        if not w.has_proxy():
            raise HTTPException(404, "no proxy yet")
        return FileResponse(w.proxy_path, media_type="video/mp4", headers={"Cache-Control": "no-cache"})

    @app.get("/media/proxy/{name}")
    def proxy_hls(name: str):
        """Playlist and segments. The playlist changes while the encode runs, so it is never cached."""
        w = ws()
        p = w.root / "proxy" / Path(name).name
        if not p.is_file():
            raise HTTPException(404)
        if p.suffix == ".m3u8":
            return FileResponse(p, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
        mt = "video/mp4" if p.suffix == ".mp4" else "video/iso.segment"
        return FileResponse(p, media_type=mt, headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/filmstrip")
    def filmstrip_index():
        w = ws()
        idx = w.filmstrip_index()
        if idx is None:
            raise HTTPException(404, "no filmstrip yet")
        return idx.to_dict()

    @app.get("/strip/{name}")
    def strip_file(name: str):
        w = ws()
        p = w.strip_dir / Path(name).name
        if not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/frame/{frame}")
    def frame_image(frame: int, width: int = 960):
        """One source frame as JPEG, cached. Exact frame, through the LUT."""
        w = ws()
        info = w.project.info
        if not 0 <= frame < info.nb_frames:
            raise HTTPException(404, "frame outside the recording")
        w.frames_dir.mkdir(parents=True, exist_ok=True)
        p = w.frames_dir / f"{frame:08d}_{width}.jpg"
        if not p.exists():
            try:
                read_info, _origin = w.media_for_reading()     # falls back to the proxy when the source is away
                thumbnail_at(read_info, frame, p, width)
            except (FileNotFoundError, FfmpegError) as e:
                # Nothing can decode that frame right now (original away, proxy not there yet): the nearest
                # filmstrip thumbnail still shows roughly what is there, and is not cached as the exact frame.
                near = _nearest_strip(w, frame)
                if near is None:
                    raise HTTPException(409 if isinstance(e, FileNotFoundError) else 500, str(e))
                return FileResponse(near, headers={"Cache-Control": "no-store", "X-Reelquick-Approx": "filmstrip"})
        return FileResponse(p, headers={"Cache-Control": "max-age=86400"})

    @app.get("/clips/{name:path}")
    def clip_file(name: str):
        w = ws()
        p = (w.clips_dir / name).resolve()
        if w.clips_dir.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    # ---- jobs ------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs():
        return [j.to_dict() for j in jobs.list()]

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        if not jobs.cancel(job_id):
            raise HTTPException(404, "no running job with that id")
        return {"ok": True}

    @app.post("/api/jobs/filmstrip")
    def job_filmstrip(body: FilmstripBody):
        w = ws()
        info = w.project.info

        def run(progress, cancel):
            idx = build_filmstrip(info, w.strip_dir, body.interval, body.width, body.workers,
                                  on_progress=lambda f, d, t: progress(f, {"done": d, "total": t}),
                                  cancel=cancel, force=body.force)
            return {"count": idx.count}

        try:
            job = jobs.start("filmstrip", "Filmstrip", run)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return job.to_dict()

    @app.post("/api/jobs/proxy")
    def job_proxy(body: ProxyBody):
        w = ws()
        info = w.project.info
        settings = ProxySettings(height=body.height, bitrate=body.bitrate, jobs=max(1, body.jobs),
                                 audio_tracks=tuple(w.project.settings.audio_tracks) or None)

        def run(progress, cancel):
            # The playlist grows in place so the first hour can be scrubbed while the sixth renders.
            # A rebuild over an existing proxy builds beside it and swaps in at the end.
            existing = w.has_proxy()
            target = w.proxy_path.with_name("proxy.new.mp4") if existing else w.proxy_path
            try:
                build_proxy(info, target, settings, on_progress=lambda f, d: progress(f, d), cancel=cancel)
            except Exception:
                if existing:
                    import shutil
                    shutil.rmtree(target.with_suffix(""), ignore_errors=True)
                    target.unlink(missing_ok=True)
                raise
            if existing:
                import shutil
                shutil.rmtree(w.proxy_path.with_suffix(""), ignore_errors=True)
                w.proxy_path.unlink(missing_ok=True)
                if target.with_suffix("").is_dir():
                    target.with_suffix("").rename(w.proxy_path.with_suffix(""))
                if target.exists():
                    target.replace(w.proxy_path)
            size = w.proxy_path.stat().st_size if w.proxy_path.exists() else 0
            return {"path": str(w.proxy_path), "size": size, "playlist": str(w.proxy_playlist) if w.proxy_playlist.exists() else None}

        try:
            job = jobs.start("proxy", "Proxy", run)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return job.to_dict()

    @app.post("/api/jobs/render")
    def job_render(body: RenderBody):
        w = ws()
        project = w.project
        clips = [c for c in project.clips if body.clip_ids is None or c.id in body.clip_ids]
        if not clips:
            raise HTTPException(400, "nothing to render")
        if body.formats is not None:
            if not body.formats or any(f not in {x.value for x in OutputFormat} for f in body.formats):
                raise HTTPException(400, "formats must be one or more of copy, prores, mp4")
        if body.mp4_quality is not None and body.mp4_quality not in MP4_QUALITIES:
            raise HTTPException(400, f"mp4_quality must be one of {', '.join(MP4_QUALITIES)}")
        quality = body.mp4_quality or project.settings.mp4_quality

        def formats_of(c: ClipEntry) -> list[str]:
            return list(dict.fromkeys(body.formats)) if body.formats is not None else c.formats

        if w.recheck_source():
            read_info, origin = project.info, "source"
        elif body.from_proxy and w.has_proxy():
            read_info, origin = probe(w.proxy_path), "proxy"
        else:
            raise HTTPException(409, "the original recording is not available; render from the proxy or reconnect it")
        fps = float(project.info.rate.fps)
        weights = {"prores": 1.0, "mp4": 1.0, "copy": 0.08}

        def work_of(c: ClipEntry) -> float:
            n = (c.out_frame - c.in_frame) + (c.head_seconds + c.tail_seconds) * fps
            return sum(weights.get(f, 1.0) * n for f in formats_of(c)) + 2.0

        total_work = sum(work_of(c) for c in clips)
        out_dir = w.clips_dir

        def run(progress, cancel):
            done_work = 0.0
            rows = {c.id: {"id": c.id, "name": c.name, "status": "queued", "progress": 0.0, "outputs": {}} for c in clips}
            progress(0.0, {"clips": list(rows.values()), "current": None})
            failures = []
            for c in clips:
                if cancel.is_set():
                    break
                row = rows[c.id]
                row["status"] = "running"
                weight = work_of(c)
                fmts = formats_of(c)
                fmt_count = len(fmts) or 1

                def fmt_progress(frac, _d, _i=[0]):
                    row["progress"] = (_i[0] + frac) / fmt_count
                    progress((done_work + weight * row["progress"]) / total_work,
                             {"clips": list(rows.values()), "current": c.id})

                try:
                    outputs: dict = {}
                    for i, fmt in enumerate(fmts):
                        spec = project.clip_spec(c, fmt, mp4_quality=quality)
                        spec.name = project.output_name(c, fmt, also=fmts, mp4_quality=quality)
                        if origin == "proxy":
                            spec.name += "_fromproxy"
                            spec.audio_tracks = None                # the proxy carries one stereo track already
                            if spec.fmt == OutputFormat.COPY:
                                spec.fmt = OutputFormat.PRORES      # the proxy is long-GOP; copy would not be frame accurate
                        fmt_progress.__defaults__[0][0] = i
                        try:
                            path = render_clip(read_info, spec, out_dir, on_progress=fmt_progress,
                                               cancel=cancel, overwrite=body.overwrite)
                        except FileExistsError as e:
                            path = Path(str(e))
                        # a Slack or High MP4 sits beside the review one, under its own key
                        outputs[fmt if fmt != "mp4" or quality == "review" else f"mp4_{quality}"] = str(path)
                    if body.stills:
                        if origin == "proxy":
                            frames = [c.representative_frame] + [f for f in c.extra_stills if f != c.representative_frame]
                            stills = []
                            for k, f in enumerate(frames):
                                nm = (c.name if k == 0 else f"{c.name}_{k + 1:02d}") + "_fromproxy"
                                try:
                                    stills.append(grab_still(read_info, f, out_dir / "stills", nm, project.settings.still_format,
                                                             False, overwrite=body.overwrite))
                                except FileExistsError as e:
                                    stills.append(Path(str(e)))
                        else:
                            stills = project.render_clip_stills(c, out_dir, body.overwrite)
                        outputs["still"] = str(stills[0])
                        if len(stills) > 1:
                            outputs["extra_stills"] = [str(x) for x in stills[1:]]
                    outputs["origin"] = origin
                    with session.lock:
                        c.rendered.update(outputs)
                        w.save()
                    row["outputs"] = outputs
                    row["status"] = "done"
                    row["progress"] = 1.0
                except (FfmpegError, ValueError, OSError) as e:
                    if cancel.is_set():
                        row["status"] = "cancelled"
                        break
                    row["status"] = "failed"
                    row["error"] = str(e).splitlines()[-1][:300]
                    failures.append(c.name)
                done_work += weight
                progress(done_work / total_work, {"clips": list(rows.values()), "current": None})
            return {"rendered": sum(1 for r in rows.values() if r["status"] == "done"), "failed": failures,
                    "out_dir": str(out_dir), "origin": origin}

        try:
            job = jobs.start("render", f"Render {len(clips)} clip(s)", run)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return job.to_dict()

    # ---- UI --------------------------------------------------------------

    if UI_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=UI_DIR / "assets"), name="assets") if (UI_DIR / "assets").is_dir() else None

        @app.get("/")
        def index():
            return FileResponse(UI_DIR / "index.html")

        @app.get("/{path:path}")
        def spa(path: str):
            p = UI_DIR / path
            if p.is_file():
                return FileResponse(p)
            return FileResponse(UI_DIR / "index.html")

    return app
