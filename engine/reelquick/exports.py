"""What leaves the app as records: a game's log, per owner, and the rows the season report and the web reel
are built from.

A game's log is what was confirmed in the app, which is the tech's log plus everything it missed. It keeps the
log template's columns (docs/reel-quick-log-template.xlsx) so the two read alike, and adds what only the app
knows: the file name, the duration, the source timecodes, and where the entry came from.
"""

from __future__ import annotations

from pathlib import Path

from .markers import ZONE_OFFSETS
from .naming import PHASE_NAMES, folder_name
from .project import ClipEntry, Project

LOG_COLUMNS = ["Time", "Zone", "Segment", "Shell", "Engine", "Camera", "Notes", "Aired",
               "File name", "Duration (s)", "In", "Out", "Phase", "Whose", "Source"]
SEGMENT_NAMES = {"pre": "Pregame", "half": "Halftime", "post": "Postgame"}
WHOSE_NAMES = {"ours": "Girraphic", "nbc": "NBC design", "other": "Other contributor"}


def zone_name(offset_seconds: float) -> str:
    return next((z for z, o in ZONE_OFFSETS.items() if o == offset_seconds), "")


def time_of_day(project: Project, frame: int) -> str:
    """The truck clock's time of day at this frame, in the log's zone; "" when the recording has no timecode."""
    if not project.info.start_timecode:
        return ""
    hh, mm, ss = (int(x) for x in project.tc(frame)[:8].split(":"))
    secs = (hh * 3600 + mm * 60 + ss - int(project.settings.log_offset_seconds)) % 86400
    return f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}"


def segment_of(project: Project, clip: ClipEntry) -> str:
    """Pregame, 1st Half, Halftime, 2nd Half or Postgame, as the log template has it; "" if unknown."""
    phase = project.clip_phase(clip)
    if phase != "game":
        return SEGMENT_NAMES.get(phase, "")
    second = project.boundary("second_half_kickoff")
    return "2nd Half" if second is not None and clip.in_frame >= second else "1st Half"


def source_of(project: Project, clip: ClipEntry) -> str:
    """Where a clip came from: an accepted detector suggestion, a tech's-log entry, or the operator."""
    if any(c.clip_id == clip.id for c in project.candidates):
        return "detector"
    # a log entry counts only if it falls inside the clip, handles included (not the 45 s look-back that
    # prefilling a new clip uses)
    lo, hi = clip.padded_range(float(project.info.rate.fps))
    hit = any(m.source == "log" and m.frame is not None and lo <= m.frame < hi for m in project.markers)
    return "log" if hit else "user"


def clip_row(project: Project, clip: ClipEntry) -> dict:
    fps = float(project.info.rate.fps)
    phase = project.clip_phase(clip)
    return {
        "Time": time_of_day(project, clip.in_frame),
        "Zone": zone_name(project.settings.log_offset_seconds) if project.info.start_timecode else "",
        "Segment": segment_of(project, clip),
        "Shell": clip.shell,
        "Engine": clip.engine,
        "Camera": clip.camera,
        "Notes": clip.desc,
        "Aired": "Yes",
        "File name": clip.name,
        "Duration (s)": round((clip.out_frame - clip.in_frame) / fps, 2),
        "In": project.tc(clip.in_frame),
        "Out": project.tc(clip.out_frame),
        "Phase": PHASE_NAMES.get(phase, ""),
        "Whose": WHOSE_NAMES.get(clip.whose, clip.whose),
        "Source": source_of(project, clip),
    }


def log_rows(project: Project, ours: bool) -> list[dict]:
    """Every clip that is ours (or every one that is not), in program order."""
    clips = sorted((c for c in project.clips if (c.whose == "ours") == ours), key=lambda c: c.in_frame)
    return [clip_row(project, c) for c in clips]


def write_xlsx(path: Path, sheets: dict[str, tuple[list[str], list[list]]], widths: dict[str, int] | None = None) -> Path:
    """Sheets of rows with a bold header, frozen, filterable; numbers stay numbers so they sort and chart."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    for title, (head, rows) in sheets.items():
        ws = wb.create_sheet(title[:31])
        ws.append(head)
        for c in ws[1]:
            c.font = Font(bold=True, color="1E1E1E")
            c.fill = PatternFill("solid", fgColor="FEB404")
        for r in rows:
            ws.append(r)
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        for i, h in enumerate(head, start=1):
            longest = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:500] if i - 1 < len(r)])
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(8, (widths or {}).get(h, longest + 2)))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def game_label(project: Project, fallback: str = "game") -> str:
    g = project.game
    return folder_name("_".join(x for x in (g.date, g.tag) if x)) or fallback


def export_game_log(project: Project, out_dir: str | Path, fallback_label: str = "game") -> dict:
    """Two logs for a game: Girraphic's work, and everyone else's. Returns their paths and row counts."""
    out_dir = Path(out_dir)
    label = game_label(project, fallback_label)
    done = {}
    for ours, name in ((True, "Girraphic"), (False, "Other Content")):
        rows = log_rows(project, ours)
        path = out_dir / f"{label} log - {name}.xlsx"
        write_xlsx(path, {"Log": (LOG_COLUMNS, [[r[c] for c in LOG_COLUMNS] for r in rows])})
        done[name] = {"path": str(path), "rows": len(rows)}
    return done
