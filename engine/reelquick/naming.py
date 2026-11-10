"""File names for rendered clips."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_PATTERN = "{date}_{game}_{seq:02d}_{phase}_{shell}_{camera}_{desc}"
# Earlier defaults: a project still on one of these moves to the current default when it is opened.
OLD_PATTERNS = {"{date}_{week}_{matchup}_{seq:02d}_{shell}_{desc}", "{date}_{game}_{seq:02d}_{shell}_{camera}_{desc}"}

# How each broadcast phase reads in a file name. The studio shows (pre, half, post) are made by a different
# group from the game, so the name says which one a clip is for.
PHASE_NAMES = {"pre": "Pre", "game": "Game", "half": "Half", "post": "Post"}

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class GameInfo:
    date: str = ""        # YYMMDD
    week: str = ""        # e.g. WK08
    away: str = ""        # tri-code
    home: str = ""        # tri-code

    @property
    def matchup(self) -> str:
        if self.away and self.home:
            return f"{self.away.upper()}v{self.home.upper()}"
        return (self.away or self.home).upper()

    @property
    def tag(self) -> str:
        """Week and matchup as one unit, the way the office refers to a game: WK05-TBvDAL."""
        return "-".join(x for x in (slug(self.week), self.matchup) if x)


def folder_name(text: str) -> str:
    """A folder name people read: spaces kept ("Player Card"), only what file systems refuse removed."""
    text = _ILLEGAL.sub("", text.strip())
    return re.sub(r"\s+", " ", text).strip(" .")


def slug(text: str) -> str:
    """Make a chunk safe for Windows, macOS and iPadOS file systems."""
    text = _ILLEGAL.sub("", text.strip())
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-._")


def build_name(game: GameInfo, seq: int, shell: str = "", desc: str = "", pattern: str = DEFAULT_PATTERN,
               camera: str = "", engine: str = "", phase: str = "") -> str:
    fields = {
        "phase": PHASE_NAMES.get(phase, slug(phase)),
        "camera": slug(camera),
        "engine": slug(engine),
        "game": slug(game.tag),
        "date": slug(game.date),
        "week": slug(game.week),
        "matchup": slug(game.matchup),
        "seq": seq,
        "shell": slug(shell),
        "desc": slug(desc),
        "away": slug(game.away.upper()),
        "home": slug(game.home.upper()),
    }
    name = pattern.format(**fields)
    # Drop empty segments so a clip without a shell does not get a double underscore.
    name = re.sub(r"_{2,}", "_", name).strip("_")
    return name or f"clip_{seq:02d}"
