"""A project is one game: the source file, its markers, and the clip list.

Stored as JSON next to the user's work so the iPad companion can read and
write the same file. Frame numbers are the source of truth; timecode strings
are kept alongside for humans.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .markers import Marker
from .naming import DEFAULT_PATTERN, GameInfo, build_name
from .probe import MediaInfo
from .render import ClipSpec, OutputFormat
from .timecode import frames_to_tc

PROJECT_VERSION = 1


@dataclass
class ClipEntry:
    id: str
    in_frame: int
    out_frame: int
    name: str = ""
    shell: str = ""
    desc: str = ""
    fmt: str = "prores"
    head_seconds: float = 2.0
    tail_seconds: float = 2.0
    rendered: str | None = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Settings:
    audio_tracks: list[int] = field(default_factory=lambda: [1, 2])
    prores_profile: str = "standard"
    mp4_sdr: bool = True
    mp4_height: int | None = None
    copy_container: str = "mov"
    name_pattern: str = DEFAULT_PATTERN
    default_head_seconds: float = 2.0
    default_tail_seconds: float = 2.0
    log_offset_seconds: float = 0.0
    output_dir: str = ""


@dataclass
class Project:
    source: str
    info: MediaInfo
    game: GameInfo = field(default_factory=GameInfo)
    settings: Settings = field(default_factory=Settings)
    markers: list[Marker] = field(default_factory=list)
    clips: list[ClipEntry] = field(default_factory=list)
    version: int = PROJECT_VERSION

    # ---- clips -------------------------------------------------------------

    def add_clip(self, in_frame: int, out_frame: int, shell: str = "", desc: str = "",
                 fmt: str = "prores", name: str | None = None) -> ClipEntry:
        if out_frame <= in_frame:
            raise ValueError("out must be after in")
        seq = len(self.clips) + 1
        clip = ClipEntry(
            id=uuid.uuid4().hex[:8],
            in_frame=in_frame,
            out_frame=out_frame,
            shell=shell,
            desc=desc,
            fmt=fmt,
            head_seconds=self.settings.default_head_seconds,
            tail_seconds=self.settings.default_tail_seconds,
        )
        clip.name = name or build_name(self.game, seq, shell, desc, self.settings.name_pattern)
        self.clips.append(clip)
        return clip

    def renumber(self) -> None:
        """Sort clips by in point and rebuild names that follow the pattern."""
        self.clips.sort(key=lambda c: c.in_frame)
        for seq, c in enumerate(self.clips, start=1):
            c.name = build_name(self.game, seq, c.shell, c.desc, self.settings.name_pattern)

    def clip_spec(self, clip: ClipEntry) -> ClipSpec:
        fps = float(self.info.rate.fps)
        return ClipSpec(
            in_frame=clip.in_frame,
            out_frame=clip.out_frame,
            name=clip.name,
            fmt=OutputFormat(clip.fmt),
            head_frames=round(clip.head_seconds * fps),
            tail_frames=round(clip.tail_seconds * fps),
            audio_tracks=tuple(self.settings.audio_tracks) or None,
            prores_profile=self.settings.prores_profile,
            mp4_sdr=self.settings.mp4_sdr,
            mp4_height=self.settings.mp4_height,
            copy_container=self.settings.copy_container,
        )

    def tc(self, frame: int) -> str:
        return frames_to_tc(self.info.start_frames + frame, self.info.rate, self.info.drop_frame)

    # ---- persistence -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "source": self.source,
            "info": self.info.to_dict(),
            "game": asdict(self.game),
            "settings": asdict(self.settings),
            "markers": [m.to_dict() for m in self.markers],
            "clips": [c.to_dict() for c in self.clips],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            source=d["source"],
            info=MediaInfo.from_dict(d["info"]),
            game=GameInfo(**d.get("game", {})),
            settings=Settings(**{k: v for k, v in d.get("settings", {}).items() if k in Settings.__dataclass_fields__}),
            markers=[Marker(**m) for m in d.get("markers", [])],
            clips=[ClipEntry(**c) for c in d.get("clips", [])],
            version=d.get("version", PROJECT_VERSION),
        )
