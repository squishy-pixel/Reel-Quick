"""Read .cube files and bake a narrow-range LUT into a full-range one.

A broadcast LUT built on 10-bit code values expects legal black at 64/1023
and white at 940/1023. Applying it to the full-range RGB ffmpeg hands over
needs a compress pass before and an expand pass after, which costs about a
third of the whole conversion. Baking those passes into a resampled copy of
the LUT gives the same picture with two fewer filters per frame.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

LEGAL_BLACK = 64 / 1023
LEGAL_WHITE = 940 / 1023

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_ROW = re.compile(rf"^\s*({_NUM})\s+({_NUM})\s+({_NUM})\s*$")


class Cube:
    def __init__(self, size: int, table: list[tuple[float, float, float]], title: str = "",
                 domain_min=(0.0, 0.0, 0.0), domain_max=(1.0, 1.0, 1.0)):
        if len(table) != size ** 3:
            raise ValueError(f"cube says size {size} but has {len(table)} entries")
        self.size = size
        self.table = table          # red fastest, then green, then blue
        self.title = title
        self.domain_min = domain_min
        self.domain_max = domain_max

    @classmethod
    def load(cls, path: str | Path) -> "Cube":
        size = 0
        title = ""
        dmin, dmax = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        table: list[tuple[float, float, float]] = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = _ROW.match(s)
            if m:
                table.append((float(m.group(1)), float(m.group(2)), float(m.group(3))))
                continue
            key, _, rest = s.partition(" ")
            key = key.upper()
            if key == "LUT_3D_SIZE":
                size = int(rest.strip())
            elif key == "TITLE":
                title = rest.strip().strip('"')
            elif key == "DOMAIN_MIN":
                dmin = tuple(float(x) for x in rest.split())
            elif key == "DOMAIN_MAX":
                dmax = tuple(float(x) for x in rest.split())
            elif key == "LUT_1D_SIZE":
                raise ValueError("1D LUTs are not supported")
        if not size:
            raise ValueError(f"{path}: no LUT_3D_SIZE")
        return cls(size, table, title, dmin, dmax)

    def _at(self, r: int, g: int, b: int) -> tuple[float, float, float]:
        n = self.size
        return self.table[r + n * (g + n * b)]

    def sample(self, r: float, g: float, b: float) -> tuple[float, float, float]:
        """Tetrahedral interpolation, the same scheme ffmpeg's lut3d uses by default."""
        n = self.size - 1
        r = min(max(r, 0.0), 1.0) * n
        g = min(max(g, 0.0), 1.0) * n
        b = min(max(b, 0.0), 1.0) * n
        r0, g0, b0 = int(r), int(g), int(b)
        r1, g1, b1 = min(r0 + 1, n), min(g0 + 1, n), min(b0 + 1, n)
        dr, dg, db = r - r0, g - g0, b - b0
        c000 = self._at(r0, g0, b0)
        c111 = self._at(r1, g1, b1)
        if dr > dg:
            if dg > db:      # r > g > b
                c100, c110 = self._at(r1, g0, b0), self._at(r1, g1, b0)
                w = ((1 - dr, c000), (dr - dg, c100), (dg - db, c110), (db, c111))
            elif dr > db:    # r > b >= g
                c100, c101 = self._at(r1, g0, b0), self._at(r1, g0, b1)
                w = ((1 - dr, c000), (dr - db, c100), (db - dg, c101), (dg, c111))
            else:            # b >= r > g
                c001, c101 = self._at(r0, g0, b1), self._at(r1, g0, b1)
                w = ((1 - db, c000), (db - dr, c001), (dr - dg, c101), (dg, c111))
        else:
            if db > dg:      # b > g >= r
                c001, c011 = self._at(r0, g0, b1), self._at(r0, g1, b1)
                w = ((1 - db, c000), (db - dg, c001), (dg - dr, c011), (dr, c111))
            elif db > dr:    # g >= b > r
                c010, c011 = self._at(r0, g1, b0), self._at(r0, g1, b1)
                w = ((1 - dg, c000), (dg - db, c010), (db - dr, c011), (dr, c111))
            else:            # g >= r >= b
                c010, c110 = self._at(r0, g1, b0), self._at(r1, g1, b0)
                w = ((1 - dg, c000), (dg - dr, c010), (dr - db, c110), (db, c111))
        return tuple(sum(wt * c[i] for wt, c in w) for i in range(3))  # type: ignore[return-value]

    def write(self, path: str | Path, title: str | None = None) -> Path:
        path = Path(path)
        lines = [f'TITLE "{title or self.title}"', f"LUT_3D_SIZE {self.size}"]
        lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in self.table]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def bake_full_range(src: Cube) -> Cube:
    """Resample a code-value LUT so it takes and gives full-range RGB directly.

    out(x) = expand(lut(compress(x))), where compress maps 0..1 onto legal
    64..940 and expand does the reverse. Values the LUT pushes beyond legal
    white (super-white) come out above 1.0, as the two-pass version did.
    """
    n = src.size
    span = LEGAL_WHITE - LEGAL_BLACK
    table = []
    for b in range(n):
        for g in range(n):
            for r in range(n):
                rr, gg, bb = (LEGAL_BLACK + span * (v / (n - 1)) for v in (r, g, b))
                o = src.sample(rr, gg, bb)
                table.append(tuple((c - LEGAL_BLACK) / span for c in o))
    return Cube(n, table, src.title + " (baked full range)")


def baked_path(lut_path: str | Path, cache_dir: Path | None = None) -> Path:
    lut_path = Path(lut_path)
    digest = hashlib.sha1(lut_path.read_bytes()).hexdigest()[:10]
    d = cache_dir or (lut_path.parent / ".baked")
    return d / f"{lut_path.stem}.full.{digest}.cube"


def ensure_baked(lut_path: str | Path, cache_dir: Path | None = None) -> Path:
    """Return a full-range copy of a narrow-range LUT, building and caching it on first use."""
    out = baked_path(lut_path, cache_dir)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    bake_full_range(Cube.load(lut_path)).write(tmp)
    tmp.replace(out)
    return out
