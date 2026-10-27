"""Turn the tech's log (CSV from Google Sheets) into markers on the file."""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from pathlib import Path

from .probe import MediaInfo
from .timecode import day_frames, frames_to_tc, parse_time_of_day

# The truck log is kept in Eastern time. The recorder's timecode clock is Pacific.
DEFAULT_LOG_OFFSET_SECONDS = -3 * 3600

TIME_HEADERS = {"time", "tod", "time of day", "timecode", "tc", "start", "time (local)"}
TYPE_HEADERS = {"type", "kind", "category"}
SHELL_HEADERS = {"shell", "viz shell", "vizrt shell", "template", "graphic"}
DESC_HEADERS = {"description", "desc", "notes", "note", "title", "content"}


@dataclass
class Marker:
    frame: int
    tc: str
    kind: str = ""
    shell: str = ""
    description: str = ""
    source: str = "log"       # log | detect | user
    confidence: float = 1.0
    end_frame: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImportResult:
    markers: list[Marker]
    skipped: list[str]
    columns: dict


def _pick(header: list[str], names: set[str]) -> int | None:
    low = [h.strip().lower() for h in header]
    for i, h in enumerate(low):
        if h in names:
            return i
    for i, h in enumerate(low):
        if any(n in h for n in names):
            return i
    return None


def import_log_csv(
    text_or_path: str | Path,
    info: MediaInfo,
    offset_seconds: float = DEFAULT_LOG_OFFSET_SECONDS,
) -> ImportResult:
    """Map each log row to a frame in the file.

    The log clock and the file's timecode are both time of day, in different
    zones: offset_seconds is added to each log time to get file time. The
    default of -10800 turns an Eastern log entry into the Pacific timecode the
    recorder writes (19:32 ET -> 16:32 in the file). Add a few seconds either
    way to calibrate against one known moment. Rows before the file starts or
    after it ends are reported in skipped.
    """
    if isinstance(text_or_path, Path) or (isinstance(text_or_path, str) and "\n" not in text_or_path and Path(text_or_path).exists()):
        text = Path(text_or_path).read_text(encoding="utf-8-sig")
    else:
        text = str(text_or_path)
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ImportResult([], ["empty file"], {})

    rate = info.rate
    header = rows[0]
    t_i = _pick(header, TIME_HEADERS)
    has_header = t_i is not None
    if has_header:
        type_i = _pick(header, TYPE_HEADERS)
        shell_i = _pick(header, SHELL_HEADERS)
        desc_i = _pick(header, DESC_HEADERS)
        body = rows[1:]
    else:
        # No recognisable header: assume Time, Type, Shell, Description in order.
        t_i, type_i, shell_i, desc_i = 0, 1, 2, 3
        body = rows
    columns = {"time": t_i, "type": type_i, "shell": shell_i, "description": desc_i, "header": has_header}

    def cell(row: list[str], i: int | None) -> str:
        return row[i].strip() if i is not None and i < len(row) else ""

    offset_frames = round(offset_seconds * float(rate.fps))
    start = info.start_frames
    total = info.nb_frames
    day = day_frames(rate)
    markers: list[Marker] = []
    skipped: list[str] = []
    for n, row in enumerate(body, start=2 if has_header else 1):
        t = cell(row, t_i)
        if not t:
            skipped.append(f"row {n}: no time")
            continue
        try:
            tod = parse_time_of_day(t, rate)
        except ValueError as e:
            skipped.append(f"row {n}: {e}")
            continue
        frame = tod - start + offset_frames
        if frame < -day // 2:
            frame += day          # log time after midnight, file started the evening before
        if frame < 0 or frame >= total:
            skipped.append(f"row {n}: {t} is outside the recording")
            continue
        markers.append(
            Marker(
                frame=frame,
                tc=frames_to_tc(start + frame, rate, info.drop_frame),
                kind=cell(row, type_i),
                shell=cell(row, shell_i),
                description=cell(row, desc_i),
                source="log",
            )
        )
    markers.sort(key=lambda m: m.frame)
    return ImportResult(markers, skipped, columns)
