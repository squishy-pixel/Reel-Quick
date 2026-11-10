"""Application preferences: keyboard bindings now, defaults like locations later.

Kept in <work root>/prefs.json, next to vocab.json, because the app window
cannot keep them itself: pywebview starts in private mode and the local
server picks a new port each launch, so browser storage is gone every time.
The engine does not interpret the values; the UI owns their meaning.
"""

from __future__ import annotations

import json
from pathlib import Path

PREFS_FILE = "prefs.json"

DEFAULT_PREFS: dict = {
    "shortcuts": {},        # action id -> key combination, only where it differs from the UI's default
    "ui": {},               # small UI conveniences (thumbnail size, last quick export, learned job rates)
    "output_root": "",      # where every game's clips go (one folder per game); "" keeps them in the work folder
}
MERGED = {"ui"}             # a partial update adds to these instead of replacing them


def load_prefs(work_root: Path) -> dict:
    prefs = json.loads(json.dumps(DEFAULT_PREFS))
    p = work_root / PREFS_FILE
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if k in prefs and isinstance(v, type(prefs[k])):
                    prefs[k] = v
    return prefs


def save_prefs(work_root: Path, changes: dict) -> dict:
    """Merge known keys into the stored prefs; anything unknown or of the wrong type is ignored."""
    prefs = load_prefs(work_root)
    for k, v in (changes or {}).items():
        if k in prefs and isinstance(v, type(prefs[k])):
            if k in MERGED:
                prefs[k] = {**prefs[k], **v}
            else:
                prefs[k] = v
    if isinstance(prefs.get("shortcuts"), dict):
        prefs["shortcuts"] = {str(a): str(b) for a, b in prefs["shortcuts"].items() if isinstance(b, str)}
    work_root.mkdir(parents=True, exist_ok=True)
    (work_root / PREFS_FILE).write_text(json.dumps(prefs, indent=1), encoding="utf-8")
    return prefs
