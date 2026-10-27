"""Editable lists: shells, cameras, engines, segments.

Seeded with the TNF set and kept in <work root>/vocab.json so the user can
change them through the season from inside the app. The same lists are what
the log sheet's dropdowns should hold.
"""

from __future__ import annotations

import json
from pathlib import Path

VOCAB_FILE = "vocab.json"

DEFAULT_VOCAB: dict[str, list[str]] = {
    "shells": [
        "Player Card", "Booth", "In The Know Classic", "In The Know 2026v1", "Match-Up", "NBA Promo",
        "Player List", "Playoff Bracket", "Playoff Picture", "Prime Stories", "Single Page Story",
        "QB Comparison", "Repurposed Shell", "Scenic Comp", "Scenic Droneshow", "Schedules", "Score",
        "Standings", "Takeover", "TNF Talk", "Who Ya Got", "Wingstop", "Video 2-Box", "Score with Video", "Custom",
    ],
    "cameras": ["Jib", "SkyCam", "Drone", "Booth", "Scenic"],
    "engines": ["Vizrt", "UE"],
    "segments": ["Pregame", "1st Half", "Halftime", "2nd Half", "OT", "Postgame"],
}


def _clean(items) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items or []:
        s = str(x).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def load_vocab(work_root: Path) -> dict[str, list[str]]:
    p = work_root / VOCAB_FILE
    vocab = {k: list(v) for k, v in DEFAULT_VOCAB.items()}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        for k in vocab:
            if isinstance(data.get(k), list):
                vocab[k] = _clean(data[k])
    return vocab


def save_vocab(work_root: Path, vocab: dict) -> dict[str, list[str]]:
    current = load_vocab(work_root)
    for k in current:
        if k in vocab and isinstance(vocab[k], list):
            current[k] = _clean(vocab[k])
    work_root.mkdir(parents=True, exist_ok=True)
    (work_root / VOCAB_FILE).write_text(json.dumps(current, indent=1), encoding="utf-8")
    return current
