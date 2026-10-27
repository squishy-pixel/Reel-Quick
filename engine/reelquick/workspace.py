"""Where a game's working files live, and the project session the app edits."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from .filmstrip import INDEX_NAME, load_filmstrip
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
        return self.proxy_path.exists() and self.proxy_path.stat().st_size > 0

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
        if self.has_proxy():
            info = probe(self.proxy_path)
            return info, "proxy"
        raise FileNotFoundError("neither the original recording nor a proxy is available")

    def status(self) -> dict:
        return {
            "source": str(self.source),
            "source_online": self.recheck_source(),
            "root": str(self.root),
            "project_path": str(self.project_path),
            "has_filmstrip": self.has_filmstrip(),
            "has_proxy": self.has_proxy(),
            "proxy_url": "/media/proxy.mp4" if self.has_proxy() else None,
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

    def __init__(self, work_root: Path | None = None):
        self.work_root = work_root or default_work_root()
        self.workspace: Workspace | None = None
        self.lock = threading.RLock()

    def require(self) -> Workspace:
        if self.workspace is None:
            raise LookupError("no file is open")
        return self.workspace

    def open(self, source: str | Path) -> Workspace:
        with self.lock:
            self.workspace = Workspace.open(source, self.work_root)
            return self.workspace
