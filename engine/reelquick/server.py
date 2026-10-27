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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .color import active_lut
from .ffmpeg import FfmpegError, capabilities, encoder_works
from .filmstrip import build_filmstrip, thumbnail_at
from .jobs import JobManager
from .markers import Marker, import_log_csv
from .naming import GameInfo, build_name
from .project import ClipEntry, Settings, StillEntry
from .proxy import ProxySettings, build_proxy
from .render import render_clip
from .stills import grab_still
from .timecode import frames_to_tc
from .vocab import load_vocab, save_vocab
from .workspace import Session, default_work_root

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
    notes: str | None = None


class StillBody(BaseModel):
    frame: int
    name: str


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
    bitrate: str = "8M"
    jobs: int = 1


class RenderBody(BaseModel):
    clip_ids: list[str] | None = None
    overwrite: bool = False
    stills: bool = True


# ---- helpers ------------------------------------------------------------------

def _range_response(path: Path, request: Request) -> Response:
    """Serve a file with HTTP range support so <video> can seek."""
    size = path.stat().st_size
    ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    rng = request.headers.get("range")
    if not rng:
        return FileResponse(path, media_type=ctype, headers={"Accept-Ranges": "bytes"})
    m = re.match(r"bytes=(\d*)-(\d*)", rng)
    if not m:
        raise HTTPException(416)
    start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
    end = int(m.group(2)) if m.group(2) and m.group(1) else size - 1
    end = min(end, size - 1)
    if start > end:
        raise HTTPException(416)
    length = end - start + 1

    def body():
        with path.open("rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = {"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
               "Content-Length": str(length)}
    return StreamingResponse(body(), status_code=206, media_type=ctype, headers=headers)


def _list_dir(path: str | None) -> dict:
    """Minimal file browser for the in-app open dialog."""
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
            return {"path": "", "parent": None, "entries": [{"name": d, "path": d, "dir": True} for d in drives]}
        path = str(Path.home())
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
    parent = str(p.parent) if p.parent != p else ("" if os.name == "nt" else None)
    return {"path": str(p), "parent": parent, "entries": entries}


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

    @app.get("/api/fs")
    def fs(path: str | None = None):
        return _list_dir(path)

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
            w.project.game = GameInfo(body.date, body.week, body.away, body.home)
            w.project.renumber()
            w.save()
        return state()

    @app.put("/api/settings")
    def put_settings(body: dict):
        w = ws()
        with session.lock:
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
            try:
                clip = w.project.add_clip(body.in_frame, body.out_frame, shell, desc, body.formats, body.name, camera, engine)
            except ValueError as e:
                raise HTTPException(400, str(e))
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
            for k, v in body.model_dump(exclude_none=True).items():
                setattr(clip, k, v)
            if clip.out_frame <= clip.in_frame:
                raise HTTPException(400, "out must be after in")
            if clip.still_frame is not None:
                clip.still_frame = max(clip.in_frame, min(clip.out_frame - 1, clip.still_frame))
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
            before = len(w.project.clips)
            w.project.clips = [c for c in w.project.clips if c.id != clip_id]
            if len(w.project.clips) == before:
                raise HTTPException(404, "no such clip")
            w.project.renumber()
            w.save()
        return {"project": w.project.to_dict()}

    @app.post("/api/clips/renumber")
    def renumber():
        w = ws()
        with session.lock:
            w.project.renumber()
            w.save()
        return {"project": w.project.to_dict()}

    # ---- stills ----------------------------------------------------------

    @app.post("/api/stills")
    def add_still(body: StillBody):
        """A hand-named still: recorded in the project and rendered right away."""
        w = ws()
        with session.lock:
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
            w.project.stills = [s for s in w.project.stills if s.id != still_id]
            w.save()
        return {"project": w.project.to_dict()}

    # ---- markers ---------------------------------------------------------

    @app.post("/api/markers/import")
    def import_markers(body: MarkersImportBody):
        w = ws()
        offset = body.offset_seconds if body.offset_seconds is not None else w.project.settings.log_offset_seconds
        res = import_log_csv(body.csv, w.project.info, offset)
        with session.lock:
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
            w.project.markers.remove(untimed[index])
            w.save()
        return {"project": w.project.to_dict()}

    # ---- media -----------------------------------------------------------

    @app.get("/media/proxy.mp4")
    def proxy_media(request: Request):
        w = ws()
        if not w.has_proxy():
            raise HTTPException(404, "no proxy yet")
        return _range_response(w.proxy_path, request)

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
                thumbnail_at(info, frame, p, width)
            except FfmpegError as e:
                raise HTTPException(500, str(e))
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
            # Write under a temporary name so a half-built proxy never counts as ready.
            tmp = w.proxy_path.with_name("proxy.partial.mp4")
            try:
                build_proxy(info, tmp, settings, on_progress=lambda f, d: progress(f, d), cancel=cancel)
                tmp.replace(w.proxy_path)
            finally:
                tmp.unlink(missing_ok=True)
            return {"path": str(w.proxy_path), "size": w.proxy_path.stat().st_size}

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
        fps = float(project.info.rate.fps)
        weights = {"prores": 1.0, "mp4": 1.0, "copy": 0.08}

        def work_of(c: ClipEntry) -> float:
            n = (c.out_frame - c.in_frame) + (c.head_seconds + c.tail_seconds) * fps
            return sum(weights.get(f, 1.0) * n for f in c.formats) + 2.0

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
                fmt_count = len(c.formats) or 1

                def fmt_progress(frac, _d, _i=[0]):
                    row["progress"] = (_i[0] + frac) / fmt_count
                    progress((done_work + weight * row["progress"]) / total_work,
                             {"clips": list(rows.values()), "current": c.id})

                try:
                    outputs: dict = {}
                    for i, fmt in enumerate(c.formats):
                        spec = project.clip_spec(c, fmt)
                        spec.name = project.output_name(c, fmt)
                        fmt_progress.__defaults__[0][0] = i
                        try:
                            path = render_clip(project.info, spec, out_dir, on_progress=fmt_progress,
                                               cancel=cancel, overwrite=body.overwrite)
                        except FileExistsError as e:
                            path = Path(str(e))
                        outputs[fmt] = str(path)
                    if body.stills:
                        try:
                            still = grab_still(project.info, c.representative_frame, out_dir / "stills", c.name,
                                               project.settings.still_format, project.settings.still_hdr,
                                               overwrite=body.overwrite)
                        except FileExistsError as e:
                            still = Path(str(e))
                        outputs["still"] = str(still)
                    with session.lock:
                        c.rendered.update(outputs)
                        w.save()
                    row["outputs"] = outputs
                    row["status"] = "done"
                    row["progress"] = 1.0
                except FfmpegError as e:
                    if cancel.is_set():
                        row["status"] = "cancelled"
                        break
                    row["status"] = "failed"
                    row["error"] = str(e).splitlines()[-1][:300]
                    failures.append(c.name)
                done_work += weight
                progress(done_work / total_work, {"clips": list(rows.values()), "current": None})
            return {"rendered": sum(1 for r in rows.values() if r["status"] == "done"), "failed": failures,
                    "out_dir": str(out_dir)}

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
