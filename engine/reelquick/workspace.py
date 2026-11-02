"""Where a game's working files live, and the project session the app edits."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .filmstrip import INDEX_NAME, load_filmstrip
from .proxy import hls_available_seconds, hls_complete, video_start_offset
from .naming import GameInfo
from .probe import probe
from .project import Project

PROJECT_FILE = "project.reelquick.json"
_GAME_RE = re.compile(r"NFL_(\d{6})_([A-Z]{2,3})_([A-Z]{2,3})_", re.I)


def default_work_root() -> Path:
    env = os.environ.get("REELQUICK_WORK")
    if env:
        return Path(env)
    videos = Path.home() / "Videos"
    base = videos if videos.is_dir() else Path.home()
    return base / "ReelQuick"


def guess_game(source: Path) -> GameInfo:
    """Read date and teams out of a return-feed file name like NFL_261008_TB_DAL_GAME_..."""
    m = _GAME_RE.search(source.name)
    if not m:
        return GameInfo()
    return GameInfo(date=m.group(1), away=m.group(2).upper(), home=m.group(3).upper())


@dataclass
class Workspace:
    source: Path
    root: Path                   # <work root>/<source stem>
    project: Project
    source_online: bool = True   # False when opened from the work folder with the original unavailable

    @property
    def project_path(self) -> Path:
        return self.root / PROJECT_FILE

    @property
    def strip_dir(self) -> Path:
        return self.root / "strip"

    @property
    def proxy_path(self) -> Path:
        return self.root / "proxy.mp4"

    @property
    def proxy_playlist(self) -> Path:
        return self.root / "proxy" / "index.m3u8"

    def has_hls(self) -> bool:
        return self.proxy_playlist.exists() and hls_available_seconds(self.proxy_playlist) > 0

    def has_mp4(self) -> bool:
        """A finished reference MP4 (builds write to temporary names, so an existing one is whole)."""
        return self.proxy_path.exists() and self.proxy_path.stat().st_size > 0

    def proxy_choice(self) -> str | None:
        """Which proxy the player gets: a finished playlist first, then a finished MP4, and only then a
        playlist still being written. So a rebuild leaves the old proxy in use until the new one is whole."""
        hls = self.has_hls()
        if hls and hls_complete(self.proxy_playlist):
            return "hls"
        if self.has_mp4():
            return "mp4"
        return "hls" if hls else None

    def proxy_url(self) -> str | None:
        choice = self.proxy_choice()
        return "/media/proxy/index.m3u8" if choice == "hls" else "/media/proxy.mp4" if choice == "mp4" else None

    _offset_cache: tuple[str, float, float] | None = None

    def proxy_start_offset(self) -> float:
        """First video frame's time in the file the player is given (cached per file and mtime)."""
        url = self.proxy_url()
        if not url:
            return 0.0
        path = self.proxy_playlist if url.endswith(".m3u8") else self.proxy_path
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return 0.0
        if self._offset_cache and self._offset_cache[0] == str(path) and self._offset_cache[1] == mtime:
            return self._offset_cache[2]
        off = video_start_offset(path)
        self._offset_cache = (str(path), mtime, off)
        return off

    @property
    def clips_dir(self) -> Path:
        return Path(self.project.settings.output_dir) if self.project.settings.output_dir else self.root / "clips"

    @property
    def frames_dir(self) -> Path:
        """Cache of exact-frame JPEGs for the viewer. Not a deliverable, hence hidden."""
        legacy = self.root / "frames"
        new = self.root / ".cache" / "frames"
        if legacy.is_dir() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(new)
        return new

    def has_filmstrip(self) -> bool:
        return (self.strip_dir / INDEX_NAME).exists()

    def has_proxy(self) -> bool:
        """A playable proxy exists: a finished MP4, or a playlist with at least one segment."""
        return (self.proxy_path.exists() and self.proxy_path.stat().st_size > 0) or self.has_hls()

    def filmstrip_index(self):
        return load_filmstrip(self.strip_dir)

    def save(self) -> None:
        self.project.save(self.project_path)

    def recheck_source(self) -> bool:
        self.source_online = self.source.is_file()
        return self.source_online

    def media_for_reading(self):
        """MediaInfo to cut from: the source when it is there, else the proxy."""
        if self.recheck_source():
            return self.project.info, "source"
        if self.proxy_path.exists() and self.proxy_path.stat().st_size > 0:
            return probe(self.proxy_path), "proxy"
        raise FileNotFoundError("neither the original recording nor a proxy is available")

    def status(self) -> dict:
        choice = self.proxy_choice()
        return {
            "source": str(self.source),
            "source_online": self.recheck_source(),
            "root": str(self.root),
            "project_path": str(self.project_path),
            "has_filmstrip": self.has_filmstrip(),
            "has_proxy": self.has_proxy(),
            "proxy_url": self.proxy_url(),
            "proxy_start_offset": self.proxy_start_offset(),
            # these describe the proxy the player is given, so the UI's "playable so far" matches what plays
            "proxy_complete": choice == "mp4" or (choice == "hls" and hls_complete(self.proxy_playlist)),
            "proxy_available_seconds": self.project.info.duration if choice == "mp4" else
                                       hls_available_seconds(self.proxy_playlist) if choice == "hls" else 0.0,
            "proxy_building": self.proxy_playlist.exists() and not hls_complete(self.proxy_playlist),
            "clips_dir": str(self.clips_dir),
        }

    @classmethod
    def open(cls, path: str | Path, work_root: Path | None = None) -> "Workspace":
        """Open a recording, or a work folder / project file when the recording itself is not at hand."""
        path = Path(path)
        work_root = work_root or default_work_root()
        if path.is_dir() and (path / PROJECT_FILE).exists():
            path = path / PROJECT_FILE
        if path.name == PROJECT_FILE and path.is_file():
            project = Project.load(path)
            source = Path(project.source)
            return cls(source=source, root=path.parent, project=project, source_online=source.is_file())
        source = path
        if not source.is_file():
            # Maybe the recording moved but its work folder is still here.
            root = work_root / source.stem
            if (root / PROJECT_FILE).exists():
                project = Project.load(root / PROJECT_FILE)
                return cls(source=source, root=root, project=project, source_online=False)
            raise FileNotFoundError(str(source))
        root = work_root / source.stem
        root.mkdir(parents=True, exist_ok=True)
        project_path = root / PROJECT_FILE
        if project_path.exists():
            project = Project.load(project_path)
            if Path(project.source) != source:
                project.source = str(source)
                project.info = probe(source)
                project.save(project_path)
        else:
            info = probe(source)
            project = Project(source=str(source), info=info, game=guess_game(source))
            from .audio import default_tracks
            project.settings.audio_tracks = default_tracks(info)
            project.settings.audio_layout_confirmed = True
            project.save(project_path)
        return cls(source=source, root=root, project=project)


def recent_projects(work_root: Path) -> list[dict]:
    """Every game that has a work folder, newest first, with what it has on disk."""
    out = []
    if not work_root.is_dir():
        return out
    for pf in work_root.glob(f"*/{PROJECT_FILE}"):
        try:
            d = json.loads(pf.read_text(encoding="utf-8"))
            src = Path(d.get("source", ""))
            out.append({
                "root": str(pf.parent),
                "project_path": str(pf),
                "source": str(src),
                "source_online": src.is_file(),
                "game": d.get("game", {}),
                "clips": len(d.get("clips", [])),
                "has_proxy": (pf.parent / "proxy.mp4").exists(),
                "has_filmstrip": (pf.parent / "strip" / INDEX_NAME).exists(),
                "modified": pf.stat().st_mtime,
            })
        except (OSError, ValueError):
            continue
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


class Session:
    """The one open workspace the app edits, guarded for the API's threads."""

    HISTORY_LIMIT = 200

    def __init__(self, work_root: Path | None = None):
        self.work_root = work_root or default_work_root()
        self.workspace: Workspace | None = None
        self.lock = threading.RLock()
        # Undo keeps whole project snapshots: they are a few hundred KB at most and every edit is small.
        self.undo_stack: list[tuple[str, dict]] = []
        self.redo_stack: list[tuple[str, dict]] = []

    def require(self) -> Workspace:
        if self.workspace is None:
            raise LookupError("no file is open")
        return self.workspace

    def open(self, source: str | Path) -> Workspace:
        with self.lock:
            self.workspace = Workspace.open(source, self.work_root)
            self.undo_stack.clear()
            self.redo_stack.clear()
            return self.workspace

    # ---- undo ----------------------------------------------------------------

    def remember(self, label: str) -> None:
        """Call before changing the project: the state as it is now becomes the undo point for `label`."""
        w = self.require()
        self.undo_stack.append((label, w.project.to_dict()))
        del self.undo_stack[:-self.HISTORY_LIMIT]
        self.redo_stack.clear()

    def history(self) -> dict:
        return {"undo": self.undo_stack[-1][0] if self.undo_stack else None,
                "redo": self.redo_stack[-1][0] if self.redo_stack else None}

    def undo(self) -> str | None:
        return self._swap(self.undo_stack, self.redo_stack)

    def redo(self) -> str | None:
        return self._swap(self.redo_stack, self.undo_stack)

    def _swap(self, take: list, give: list) -> str | None:
        with self.lock:
            w = self.require()
            if not take:
                return None
            label, snapshot = take.pop()
            give.append((label, w.project.to_dict()))
            old = {c.id: c for c in w.project.clips}
            w.project = Project.from_dict(snapshot)
            # Rendering is not an edit: a clip's record of what has been rendered survives the undo.
            for c in w.project.clips:
                prev = old.get(c.id)
                if prev is not None and prev.rendered and not c.rendered:
                    c.rendered = dict(prev.rendered)
            w.save()
            return label
