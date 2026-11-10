"""The season report: every game in the work folder, counted.

How many elements were ours against everyone else's, which shells and in which part of the broadcast, how
much air time, and how that moved week to week. On-air time is each clip's content (in to out, handles
left out), which is what went to air.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from .exports import WHOSE_NAMES, clip_row, write_xlsx
from .project import Project
from .workspace import PROJECT_FILE

SEGMENTS = ["Pregame", "1st Half", "Halftime", "2nd Half", "Postgame"]


def season_of(yymmdd: str) -> str:
    """The NFL season a game date belongs to: January and February games count with the year before."""
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return ""
    year, month = 2000 + int(yymmdd[:2]), int(yymmdd[2:4])
    return str(year - 1 if month <= 6 else year)


def season_rows(work_root: Path) -> list[dict]:
    """One row per clip, across every game in the work folder, with its game and season."""
    rows: list[dict] = []
    for pf in sorted(Path(work_root).glob(f"*/{PROJECT_FILE}")):
        try:
            project = Project.load(pf)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        g = project.game
        game = "-".join(x for x in (g.week, g.matchup) if x) or pf.parent.name
        for clip in project.clips:
            r = clip_row(project, clip)
            rows.append({"Season": season_of(g.date), "Date": g.date, "Week": g.week, "Game": game,
                         "Folder": pf.parent.name, "Ours": clip.whose == "ours", **r})
    rows.sort(key=lambda r: (r["Date"], r["Folder"], r["In"]))
    return rows


def _sum(rows, key="Duration (s)"):
    return round(sum(r[key] for r in rows), 1)


def season_report(work_root: Path, out_path: Path) -> dict:
    rows = season_rows(work_root)
    by_season: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_season[r["Season"] or "unknown"].append(r)

    # Summary: one row per season, then all seasons together
    summary = []
    for season, rs in sorted(by_season.items()) + ([("All", rows)] if len(by_season) > 1 else []):
        ours = [r for r in rs if r["Ours"]]
        other = [r for r in rs if not r["Ours"]]
        games = len({r["Folder"] for r in rs})
        summary.append([season, games, len(ours), len(other),
                        round(100 * len(ours) / len(rs), 1) if rs else 0.0,
                        round(_sum(ours) / 60, 1), round(_sum(other) / 60, 1),
                        round(len(ours) / games, 1) if games else 0.0])

    # By game, in date order: the week-to-week view
    games: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        games[r["Folder"]].append(r)
    by_game = []
    for folder, rs in sorted(games.items(), key=lambda kv: (kv[1][0]["Date"], kv[0])):
        ours = [r for r in rs if r["Ours"]]
        other = [r for r in rs if not r["Ours"]]
        first = rs[0]
        by_game.append([first["Season"], first["Date"], first["Week"], first["Game"], len(ours), len(other),
                        _sum(ours), _sum(other)] + [sum(1 for r in ours if r["Segment"] == s) for s in SEGMENTS])

    # By shell (and whose), most used first
    shells: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        shells[(r["Season"], r["Shell"] or "(none)", r["Whose"])].append(r)
    by_shell = sorted(([s, sh, w, len(rs), _sum(rs), len({r["Folder"] for r in rs})] for (s, sh, w), rs in shells.items()),
                      key=lambda x: (x[0], -x[3], x[1]))

    # By segment: where in the broadcast
    by_segment = []
    for season, rs in sorted(by_season.items()):
        for seg in SEGMENTS + [""]:
            ss = [r for r in rs if r["Segment"] == seg]
            if not ss:
                continue
            ours = [r for r in ss if r["Ours"]]
            other = [r for r in ss if not r["Ours"]]
            by_segment.append([season, seg or "(no phases set)", len(ours), len(other), _sum(ours), _sum(other)])

    clip_cols = ["Season", "Date", "Week", "Game", "File name", "Shell", "Camera", "Engine", "Segment", "Phase",
                 "Whose", "Source", "Duration (s)", "Time", "Zone", "In", "Out", "Notes"]
    write_xlsx(out_path, {
        "Summary": (["Season", "Games", "Ours", "Others'", "Ours %", "Ours on air (min)", "Others' on air (min)",
                     "Ours per game"], summary),
        "By game": (["Season", "Date", "Week", "Game", "Ours", "Others'", "Ours on air (s)", "Others' on air (s)"]
                    + [f"Ours {s}" for s in SEGMENTS], by_game),
        "By shell": (["Season", "Shell", "Whose", "Clips", "On air (s)", "Games"], by_shell),
        "By segment": (["Season", "Segment", "Ours", "Others'", "Ours on air (s)", "Others' on air (s)"], by_segment),
        "All clips": (clip_cols, [[r[c] for c in clip_cols] for r in rows]),
    })
    ours_total = sum(1 for r in rows if r["Ours"])
    return {"path": str(out_path), "games": len(games), "clips": len(rows), "ours": ours_total,
            "others": len(rows) - ours_total, "seasons": sorted(by_season)}


def default_report_path(base: Path) -> Path:
    return Path(base) / f"Season report {date.today().isoformat()}.xlsx"


__all__ = ["season_of", "season_rows", "season_report", "default_report_path", "WHOSE_NAMES"]
