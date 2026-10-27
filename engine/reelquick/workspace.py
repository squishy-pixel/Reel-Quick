"""Where a game's working files live, and the project session the app edits."""

from __future__ import annotations

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

    def status(self) -> dict:
        return {
            "source": str(self.source),
            "root": str(self.root),
            "project_path": str(self.project_path),
            "has_filmstrip": self.has_filmstrip(),
            "has_proxy": self.has_proxy(),
            "proxy_url": "/media/proxy.mp4" if self.has_proxy() else None,
            "clips_dir": str(self.clips_dir),
        }

    @classmethod
    def open(cls, source: str | Path, work_root: Path | None = None) -> "Workspace":
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        root = (work_root or default_work_root()) / source.stem
        root.mkdir(parents=True, exist_ok=True)
        project_path = root / PROJECT_FILE
        if project_path.exists():
            project = Project.load(project_path)
            if Path(project.source) != source:
                project.source = str(source)
                project.info = probe(source)
        else:
            info = probe(source)
            project = Project(source=str(source), info=info, game=guess_game(source))
            project.save(project_path)
        return cls(source=source, root=root, project=project)


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
