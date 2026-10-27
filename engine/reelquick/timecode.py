"""SMPTE timecode math, including drop-frame at 29.97 and 59.94.

Broadcast recordings carry time-of-day timecode. The log the truck keeps is in
time of day too. Everything in the engine works in integer frame numbers from
the start of the file and converts to and from timecode only at the edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class FrameRate:
    num: int
    den: int

    @classmethod
    def parse(cls, text: str) -> "FrameRate":
        """Accept '60000/1001', '59.94', '29.97', '30', '25', '23.976'."""
        text = text.strip()
        if "/" in text:
            n, d = text.split("/", 1)
            return cls(int(n), int(d))
        value = float(text)
        for nominal in (24, 25, 30, 50, 60):
            if abs(value - nominal) < 0.01:
                return cls(nominal, 1)
            if abs(value - nominal * 1000 / 1001) < 0.01:
                return cls(nominal * 1000, 1001)
        frac = Fraction(text).limit_denominator(1001)
        return cls(frac.numerator, frac.denominator)

    @property
    def fps(self) -> Fraction:
        return Fraction(self.num, self.den)

    @property
    def nominal(self) -> int:
        """Integer frame count per timecode second (60 for 59.94)."""
        return round(self.fps)

    @property
    def is_fractional(self) -> bool:
        return self.den == 1001

    @property
    def drop_frame_capable(self) -> bool:
        return self.is_fractional and self.nominal in (30, 60)

    @property
    def frame_duration(self) -> Fraction:
        return Fraction(self.den, self.num)

    def __str__(self) -> str:
        return f"{self.num}/{self.den}"


_TC_RE = re.compile(
    r"^\s*(?P<h>\d{1,2})[:;.](?P<m>\d{2})[:;.](?P<s>\d{2})(?:(?P<sep>[:;.])(?P<f>\d{1,3}))?\s*$"
)


def is_drop_frame_string(tc: str) -> bool:
    """Drop-frame timecode is written with ';' before the frames field."""
    m = _TC_RE.match(tc)
    return bool(m and m.group("sep") == ";")


def _dropped_per_minute(rate: FrameRate) -> int:
    # 2 frames per minute at 29.97, 4 at 59.94.
    return 2 * (rate.nominal // 30)


def tc_to_frames(tc: str, rate: FrameRate, drop: bool | None = None, clamp: bool = True) -> int:
    """Convert a timecode string to a frame count since 00:00:00:00.

    drop=None infers drop-frame from the ';' separator. With clamp=True a
    frame number that does not exist in drop-frame (e.g. 00:01:00;02 at 59.94)
    is moved forward to the first real frame of that second, which is what a
    person means when they type a wall-clock time like 19:32:00.
    """
    m = _TC_RE.match(tc)
    if not m:
        raise ValueError(f"not a timecode: {tc!r}")
    hh, mm, ss = int(m.group("h")), int(m.group("m")), int(m.group("s"))
    ff = int(m.group("f") or 0)
    if drop is None:
        drop = m.group("sep") == ";"
    nominal = rate.nominal
    if mm > 59 or ss > 59 or ff >= nominal:
        raise ValueError(f"timecode field out of range: {tc!r}")
    if drop:
        if not rate.drop_frame_capable:
            raise ValueError(f"drop-frame timecode needs 29.97 or 59.94, got {rate}")
        d = _dropped_per_minute(rate)
        if ss == 0 and mm % 10 != 0 and ff < d:
            if not clamp:
                raise ValueError(f"{tc!r} is a dropped frame number")
            ff = d
        total_minutes = 60 * hh + mm
        return (
            nominal * 3600 * hh
            + nominal * 60 * mm
            + nominal * ss
            + ff
            - d * (total_minutes - total_minutes // 10)
        )
    return nominal * 3600 * hh + nominal * 60 * mm + nominal * ss + ff


def frames_to_tc(frames: int, rate: FrameRate, drop: bool) -> str:
    """Convert a frame count since 00:00:00:00 to a timecode string."""
    if frames < 0:
        raise ValueError("negative frame count")
    nominal = rate.nominal
    if drop:
        if not rate.drop_frame_capable:
            raise ValueError(f"drop-frame timecode needs 29.97 or 59.94, got {rate}")
        d = _dropped_per_minute(rate)
        frames_per_10min = nominal * 600 - d * 9
        frames_per_min = nominal * 60 - d
        tens = frames // frames_per_10min
        rem = frames % frames_per_10min
        if rem > d:
            frames += d * 9 * tens + d * ((rem - d) // frames_per_min)
        else:
            frames += d * 9 * tens
    ff = frames % nominal
    ss = (frames // nominal) % 60
    mm = (frames // (nominal * 60)) % 60
    hh = frames // (nominal * 3600)
    sep = ";" if drop else ":"
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def frames_to_seconds(frames: int, rate: FrameRate) -> Fraction:
    return Fraction(frames) * rate.frame_duration


def seconds_to_frames(seconds: float | Fraction, rate: FrameRate) -> int:
    return round(Fraction(seconds) * rate.fps)


_TOD_RE = re.compile(
    r"^\s*(?P<h>\d{1,2})(?::(?P<m>\d{2}))?(?::(?P<s>\d{2}))?(?:[.,](?P<frac>\d+))?(?:[;:](?P<f>\d{1,2}))?\s*(?P<ampm>[AaPp][Mm]?)?\s*$"
)


def parse_time_of_day(text: str, rate: FrameRate) -> int:
    """Parse a wall-clock time as typed in a log into a frame count since midnight.

    Accepts '19:32:05', '7:32:05 PM', '7:32 pm', '19:32', '19:32:05.5',
    '19:32:05;12'. Returns frames in the same drop-frame or non-drop sense the
    rate implies, so it can be compared with the file's own start timecode.
    """
    m = _TOD_RE.match(text)
    if not m or (m.group("m") is None and m.group("ampm") is None):
        raise ValueError(f"not a time of day: {text!r}")
    hh = int(m.group("h"))
    mm = int(m.group("m") or 0)
    ss = int(m.group("s") or 0)
    ampm = (m.group("ampm") or "").lower()
    if ampm.startswith("p") and hh < 12:
        hh += 12
    if ampm.startswith("a") and hh == 12:
        hh = 0
    nominal = rate.nominal
    if m.group("f") is not None:
        ff = int(m.group("f"))
    elif m.group("frac") is not None:
        ff = round(float("0." + m.group("frac")) * nominal)
        ff = min(ff, nominal - 1)
    else:
        ff = 0
    drop = rate.drop_frame_capable
    sep = ";" if drop else ":"
    return tc_to_frames(f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}", rate, drop=drop, clamp=True)


def day_frames(rate: FrameRate) -> int:
    """Frames in 24 hours of timecode at this rate, drop-frame aware."""
    if rate.drop_frame_capable:
        # 1440 minutes a day, minus the 144 that are multiples of ten and drop nothing.
        return rate.nominal * 86400 - _dropped_per_minute(rate) * (1440 - 144)
    return rate.nominal * 86400
