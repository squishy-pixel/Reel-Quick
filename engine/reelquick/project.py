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
from .render import ClipSpec, OutputFormat, output_extension
from .stills import grab_still
from .timecode import frames_to_tc

PROJECT_VERSION = 2


@dataclass
class ClipEntry:
    id: str
    in_frame: int
    out_frame: int
    name: str = ""
    shell: str = ""
    desc: str = ""
    camera: str = ""
    engine: str = ""
    formats: list[str] = field(default_factory=lambda: ["prores"])
    head_seconds: float = 2.0
    tail_seconds: float = 2.0
    still_frame: int | None = None          # representative frame; None means the middle of the content
    rendered: dict = field(default_factory=dict)   # format -> path, plus "still" -> path
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def representative_frame(self) -> int:
        if self.still_frame is not None:
            return self.still_frame
        return (self.in_frame + self.out_frame) // 2

    @classmethod
    def from_dict(cls, d: dict) -> "ClipEntry":
        d = dict(d)
        if "fmt" in d:                        # version 1 projects had a single format
            d["formats"] = [d.pop("fmt")]
        if isinstance(d.get("rendered"), str) or d.get("rendered") is None:
            d["rendered"] = {d["formats"][0]: d["rendered"]} if d.get("rendered") else {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StillEntry:
    id: str
    frame: int
    name: str
    rendered: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Settings:
    audio_tracks: list[int] = field(default_factory=lambda: [1, 2])
    prores_profile: str = "standard"
    mp4_sdr: bool = True
    mp4_height: int | None = None
    copy_container: str = "mov"
    still_format: str = "png"          # png | jpg | tiff
    still_hdr: bool = False
    default_formats: list[str] = field(default_factory=lambda: ["prores", "mp4"])
    name_pattern: str = DEFAULT_PATTERN
    default_head_seconds: float = 2.0
    default_tail_seconds: float = 2.0
    log_offset_seconds: float = -3 * 3600     # Eastern log, Pacific timecode
    output_dir: str = ""


@dataclass
class Project:
    source: str
    info: MediaInfo
    game: GameInfo = field(default_factory=GameInfo)
    settings: Settings = field(default_factory=Settings)
    markers: list[Marker] = field(default_factory=list)
    clips: list[ClipEntry] = field(default_factory=list)
    stills: list[StillEntry] = field(default_factory=list)
    version: int = PROJECT_VERSION

    # ---- clips -------------------------------------------------------------

    def add_clip(self, in_frame: int, out_frame: int, shell: str = "", desc: str = "",
                 formats: list[str] | None = None, name: str | None = None, camera: str = "",
                 engine: str = "") -> ClipEntry:
        if out_frame <= in_frame:
            raise ValueError("out must be after in")
        seq = len(self.clips) + 1
        clip = ClipEntry(
            id=uuid.uuid4().hex[:8],
            in_frame=in_frame,
            out_frame=out_frame,
            shell=shell,
            desc=desc,
            camera=camera,
            engine=engine,
            formats=list(formats or self.settings.default_formats),
            head_seconds=self.settings.default_head_seconds,
            tail_seconds=self.settings.default_tail_seconds,
        )
        clip.name = name or build_name(self.game, seq, shell, desc, self.settings.name_pattern, camera, engine)
        self.clips.append(clip)
        return clip

    def nearest_marker(self, in_frame: int, out_frame: int, before_seconds: float = 45.0) -> Marker | None:
        """The log entry this range most likely belongs to: inside it, or shortly before the in point."""
        fps = float(self.info.rate.fps)
        lo = in_frame - before_seconds * fps
        candidates = [m for m in self.markers if m.frame is not None and lo <= m.frame <= out_frame]
        if not candidates:
            return None
        return min(candidates, key=lambda m: abs(m.frame - in_frame))

    def renumber(self) -> None:
        """Sort clips by in point and rebuild names that follow the pattern."""
        self.clips.sort(key=lambda c: c.in_frame)
        for seq, c in enumerate(self.clips, start=1):
            c.name = build_name(self.game, seq, c.shell, c.desc, self.settings.name_pattern, c.camera, c.engine)

    def add_still(self, frame: int, name: str) -> StillEntry:
        """A still with a name typed by hand, not tied to a clip."""
        if not 0 <= frame < self.info.nb_frames:
            raise ValueError("frame outside the recording")
        still = StillEntry(id=uuid.uuid4().hex[:8], frame=frame, name=name)
        self.stills.append(still)
        return still

    def output_name(self, clip: ClipEntry, fmt: str) -> str:
        """Clip name for one output. Formats that would share an extension get a suffix."""
        exts = {f: output_extension(self.clip_spec(clip, f)) for f in clip.formats}
        mine = exts[fmt]
        clash = [f for f, e in exts.items() if e == mine and f != fmt]
        if clash and fmt == "copy":
            return clip.name + "_src"
        return clip.name

    def clip_spec(self, clip: ClipEntry, fmt: str | None = None) -> ClipSpec:
        fps = float(self.info.rate.fps)
        fmt = fmt or clip.formats[0]
        return ClipSpec(
            in_frame=clip.in_frame,
            out_frame=clip.out_frame,
            name=clip.name,
            fmt=OutputFormat(fmt),
            head_frames=round(clip.head_seconds * fps),
            tail_frames=round(clip.tail_seconds * fps),
            audio_tracks=tuple(self.settings.audio_tracks) or None,
            prores_profile=self.settings.prores_profile,
            mp4_sdr=self.settings.mp4_sdr,
            mp4_height=self.settings.mp4_height,
            copy_container=self.settings.copy_container,
        )

    def render_clip_outputs(self, clip: ClipEntry, out_dir: str | Path, on_progress=None,
                            overwrite: bool = False, render=None) -> dict:
        """Render every format of a clip plus its still. Returns format -> path."""
        from .render import render_clip as _render_clip
        render = render or _render_clip
        done: dict = {}
        for fmt in clip.formats:
            spec = self.clip_spec(clip, fmt)
            spec.name = self.output_name(clip, fmt)
            path = render(self.info, spec, out_dir, on_progress=on_progress, overwrite=overwrite)
            done[fmt] = str(path)
        still = grab_still(self.info, clip.representative_frame, Path(out_dir) / "stills", clip.name,
                           self.settings.still_format, self.settings.still_hdr, overwrite=overwrite)
        done["still"] = str(still)
        clip.rendered.update(done)
        return done

    def render_still(self, still: StillEntry, out_dir: str | Path, overwrite: bool = False) -> Path:
        path = grab_still(self.info, still.frame, Path(out_dir) / "stills", still.name,
                          self.settings.still_format, self.settings.still_hdr, overwrite=overwrite)
        still.rendered = str(path)
        return path

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
            "stills": [s.to_dict() for s in self.stills],
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
            markers=[Marker(**{k: v for k, v in m.items() if k in Marker.__dataclass_fields__}) for m in d.get("markers", [])],
            clips=[ClipEntry.from_dict(c) for c in d.get("clips", [])],
            stills=[StillEntry(**s) for s in d.get("stills", [])],
            version=d.get("version", PROJECT_VERSION),
        )
