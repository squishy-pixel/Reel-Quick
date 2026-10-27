"""Turn the tech's log (exported or pasted from Google Sheets) into markers on the file.

The real sheet looks like this, tab separated when pasted, comma separated
when exported:

    Time    Time of Game    Type of Graphic    Camera
    PREGAME
    18:03                   NFC Standings      Jib
    1ST HALF
    19:08                   Booth              Booth
    HALF TIME
                            Score              Drone        <- no time: listed as still to find
    2ND HALF
    21:37   End of third    Quarters           Drone

Times are the venue's local clock. The recorder's timecode clock is Pacific,
so the caller passes the offset for that game's venue.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from pathlib import Path

from .probe import MediaInfo
from .timecode import day_frames, frames_to_tc, parse_time_of_day

# Offsets from a venue clock to the recorder's Pacific timecode clock.
ZONE_OFFSETS = {"PT": 0, "MT": -3600, "CT": -7200, "ET": -10800}
DEFAULT_LOG_OFFSET_SECONDS = ZONE_OFFSETS["ET"]

TIME_HEADERS = {"time", "tod", "time of day", "timecode", "tc", "start", "time (local)", "air time", "on-air time"}
GAMECLOCK_HEADERS = {"time of game", "game time", "game clock", "quarter", "period"}
TYPE_HEADERS = {"type", "kind", "category"}
SHELL_HEADERS = {"shell", "viz shell", "vizrt shell", "template", "graphic", "type of graphic", "name"}
CAMERA_HEADERS = {"camera", "cam", "source"}
SEGMENT_HEADERS = {"segment", "section", "part", "show segment", "show"}
ZONE_HEADERS = {"zone", "time zone", "timezone", "tz", "log zone", "clock"}
AIRED_HEADERS = {"aired", "on air", "air", "played", "status"}
DESC_HEADERS = {"description", "desc", "notes", "note", "title", "content", "comment"}
SECTION_WORDS = {"pregame", "pre-game", "pre game", "1st half", "first half", "half time", "halftime",
                 "2nd half", "second half", "postgame", "post-game", "post game", "ot", "overtime"}
CAMERA_KIND = {"booth": "booth", "drone": "drone", "jib": "AR", "skycam": "AR", "scenic": "scenic"}


@dataclass
class Marker:
    frame: int | None               # None: the log has the entry but no time; still to be found
    tc: str
    kind: str = ""
    shell: str = ""
    description: str = ""
    camera: str = ""
    section: str = ""
    source: str = "log"             # log | detect | user
    confidence: float = 1.0
    end_frame: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        return " · ".join(x for x in (self.shell, self.camera, self.description) if x)


@dataclass
class ImportResult:
    markers: list[Marker]
    untimed: list[Marker]
    skipped: list[str]
    columns: dict

    @property
    def all_outside(self) -> bool:
        """Every timed row missed the recording: almost always a wrong time zone."""
        return not self.markers and any("outside the recording" in s for s in self.skipped)


def _pick(header: list[str], names: set[str]) -> int | None:
    low = [h.strip().lower() for h in header]
    for i, h in enumerate(low):
        if h in names:
            return i
    for i, h in enumerate(low):
        if any(n in h for n in names):
            return i
    return None


def _rows(text: str) -> list[list[str]]:
    sample = text[:4096]
    delim = "\t" if sample.count("\t") >= sample.count(",") and "\t" in sample else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return [[c.strip() for c in r] for r in rows]


def _is_section(row: list[str]) -> str | None:
    filled = [c for c in row if c]
    if len(filled) == 1 and (filled[0].lower() in SECTION_WORDS or (filled[0].isupper() and len(filled[0]) <= 24)):
        return filled[0].upper()
    return None


def import_log_csv(
    text_or_path: str | Path,
    info: MediaInfo,
    offset_seconds: float = DEFAULT_LOG_OFFSET_SECONDS,
) -> ImportResult:
    """Map each log row to a frame in the file.

    offset_seconds is added to each log time to reach file timecode: -7200 for
    a Central venue against Pacific timecode, -10800 for Eastern. Rows without
    a time come back in `untimed` with their section so they can be hunted
    down by hand. Rows before the file starts or after it ends are in skipped.
    """
    if isinstance(text_or_path, Path) or (isinstance(text_or_path, str) and "\n" not in text_or_path and Path(text_or_path).exists()):
        text = Path(text_or_path).read_text(encoding="utf-8-sig")
    else:
        text = str(text_or_path)
    rows = [r for r in _rows(text) if any(r)]
    if not rows:
        return ImportResult([], [], ["empty file"], {})

    rate = info.rate
    header = rows[0]
    t_i = _pick(header, TIME_HEADERS)
    has_header = t_i is not None
    if has_header:
        cols = {
            "time": t_i,
            "gameclock": _pick(header, GAMECLOCK_HEADERS),
            "type": _pick(header, TYPE_HEADERS - {"type of graphic"}) if _pick(header, SHELL_HEADERS) != _pick(header, TYPE_HEADERS) else None,
            "shell": _pick(header, SHELL_HEADERS),
            "camera": _pick(header, CAMERA_HEADERS),
            "description": _pick(header, DESC_HEADERS),
            "segment": _pick(header, SEGMENT_HEADERS),
            "zone": _pick(header, ZONE_HEADERS),
            "aired": _pick(header, AIRED_HEADERS),
        }
        body = rows[1:]
    else:
        # No recognisable header: assume Time, Type, Shell, Description in order.
        cols = {"time": 0, "gameclock": None, "type": 1, "shell": 2, "camera": None, "description": 3,
                "segment": None, "zone": None, "aired": None}
        body = rows
    columns = {**cols, "header": has_header}

    def cell(row: list[str], i: int | None) -> str:
        return row[i] if i is not None and i < len(row) else ""

    start = info.start_frames
    total = info.nb_frames
    day = day_frames(rate)
    markers: list[Marker] = []
    untimed: list[Marker] = []
    skipped: list[str] = []
    section = ""
    zone_offset: float | None = None      # a Zone column in the sheet wins over the caller's offset
    for n, row in enumerate(body, start=2 if has_header else 1):
        sec = _is_section(row)
        if sec:
            section = sec
            continue
        z = cell(row, cols["zone"]).upper().replace("S", "").replace("D", "")   # EST/EDT -> ET
        if z in ZONE_OFFSETS:
            zone_offset = ZONE_OFFSETS[z]
        if cell(row, cols["segment"]):
            section = cell(row, cols["segment"]).upper()
        aired = cell(row, cols["aired"]).strip().lower()
        if aired in ("no", "n", "killed", "did not air", "dna", "false", "0"):
            skipped.append(f"row {n}: marked as not aired")
            continue
        shell = cell(row, cols["shell"])
        camera = cell(row, cols["camera"])
        kind = cell(row, cols["type"]) or CAMERA_KIND.get(camera.lower(), "")
        desc = " ".join(x for x in (cell(row, cols["gameclock"]), cell(row, cols["description"])) if x)
        t = cell(row, cols["time"])
        if not t:
            if shell or desc or camera:
                untimed.append(Marker(None, "", kind, shell, desc, camera, section, confidence=0.0))
            continue
        try:
            tod = parse_time_of_day(t, rate, zone_offset if zone_offset is not None else offset_seconds)
        except ValueError as e:
            skipped.append(f"row {n}: {e}")
            continue
        frame = tod - start
        if frame < -day // 2:
            frame += day          # log time after midnight, file started the evening before
        if frame < 0 or frame >= total:
            skipped.append(f"row {n}: {t} is outside the recording")
            continue
        markers.append(Marker(frame, frames_to_tc(start + frame, rate, info.drop_frame), kind, shell, desc, camera, section))
    markers.sort(key=lambda m: m.frame or 0)
    return ImportResult(markers, untimed, skipped, columns)
