# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["torch>=2.7", "torchvision", "numpy", "easyocr"]
#
# [tool.uv.sources]
# torch = { index = "pytorch-cu128" }
# torchvision = { index = "pytorch-cu128" }
#
# [[tool.uv.index]]
# name = "pytorch-cu128"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
# ///
"""Phase boundaries from the score bug: docs/DETECTION.md, "Phase from the score bug".

Run from engine/:

    uv run scripts/phase_eval.py scan --recording <game.mp4> --game 2025WK06-PHIvsNYG --cache <dir>
    uv run scripts/phase_eval.py phases --game 2025WK06-PHIvsNYG --cache <dir>   # writes <game>.phases.json
    uv run scripts/phase_eval.py dump --game ... --cache <dir> --from 300000 --to 320000   # the readings, for checking

scan:
1. Locate the bug. OCR the bottom of ~120 frames spread over the middle of the recording and keep the
   tokens that read as a quarter (1ST, 2ND, 3RD, 4TH, OT) with a game clock right of them on the same
   row. Their median boxes are the quarter and clock boxes for this recording (positions differ between
   1080p and 720p copies, and could between seasons).
2. Read every keyframe (about one per 1.1 s): recognition only, on those two boxes, with a quarter and a
   clock alphabet. A sample counts as "bug seen" only when both parse (a quarter word and a valid clock).
   Readings are cached in bug-<game>.json, so the rules can be changed without decoding again.

phases: boundaries from the readings, smoothed over neighbouring samples rather than trusted one by one,
since the bug drops out for replays, graphics and breaks, and OCR misreads single frames. See PHASE RULES
below; the method and its measured error are in DETECTION.md.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
from pathlib import Path

import numpy as np

FPS = 60000 / 1001
QUARTERS = {"1ST": 1, "2ND": 2, "3RD": 3, "4TH": 4, "OT": 5}
HALF, FINAL = 6, 7                  # the bug shows HALF / FINAL in place of quarter and clock
# Both boxes are read with letters too: HALF and FINAL sit centred across the row, not in either box.
QUARTER_ALPHA = "1234STNDRHOAFLIstndrhoafli"
CLOCK_ALPHA = "0123456789:.HALFINhalfin"


# ---------------------------------------------------------------------------------------- media

def probe(path: str) -> dict:
    d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height,start_time,nb_frames,duration",
                                   path], capture_output=True, text=True, check=True).stdout)["streams"][0]
    nb = int(d.get("nb_frames") or round(float(d["duration"]) * FPS))
    return {"width": int(d["width"]), "height": int(d["height"]),
            "start": float(d.get("start_time") or 0), "nb_frames": nb}


def frame_at_time(path: str, t: float, crop: str | None = None) -> np.ndarray:
    """One grey frame near time t (seconds from the start), optionally cropped (w:h:x:y)."""
    vf = f"crop={crop}," if crop else ""
    w, h = (int(v) for v in crop.split(":")[:2]) if crop else (None, None)
    info = probe(path) if not crop else None
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-map", "0:v:0",
                          "-vf", vf + "format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    if crop:
        return np.frombuffer(raw, np.uint8).reshape(h, w)
    return np.frombuffer(raw, np.uint8).reshape(info["height"], info["width"])


def keyframes(path: str, crop: tuple[int, int, int, int], p0: float, batch: int = 256):
    """Yield (frame indices, grey crops) for every keyframe. crop = (w, h, x, y)."""
    w, h, x, y = crop
    proc = subprocess.Popen(["ffmpeg", "-v", "info", "-nostats", "-skip_frame", "nokey", "-copyts", "-i", path,
                             "-map", "0:v:0", "-vf", f"crop={w}:{h}:{x}:{y},format=gray,showinfo",
                             "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pts: list[float] = []

    def read_err():
        for line in proc.stderr:
            m = re.search(rb"pts_time:\s*([-\d.]+)", line)
            if m:
                pts.append(float(m.group(1)))
    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    done, size = 0, w * h
    while True:
        buf = proc.stdout.read(size * batch)
        if not buf:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(-1, h, w)
        while len(pts) < done + len(fr) and t.is_alive():
            t.join(0.01)
        idx = np.round((np.array(pts[done:done + len(fr)]) - p0) * FPS).astype(np.int64)
        done += len(fr)
        yield idx, fr
    proc.wait()


# ---------------------------------------------------------------------------------------- reading

def parse_quarter(s: str) -> int:
    """Quarter word, also inside a longer reading: at 720p the box takes in an edge of the bug ("AIst")."""
    s = s.upper().replace(" ", "").replace("I", "1").replace("L", "1")
    if s in QUARTERS:
        return QUARTERS[s]
    m = re.search(r"1ST|2ND|3RD|4TH", s)
    if m:
        return QUARTERS[m.group(0)]
    # 720p misreads, consistent because the font is: 4TH as "414"/"41H", 1ST as "1S1"/"15T", 2ND as "2N0"
    m = re.match(r"^\D?([1-4])([A-Z0-9])", s)
    if m and m.group(2) in {"1": "S5", "2": "N", "3": "R", "4": "T17"}[m.group(1)]:
        return int(m.group(1))
    return 0


def parse_state(q: str, c: str) -> int:
    """HALF or FINAL, read in either box (allowing a lost letter), else 0."""
    s = re.sub(r"[^A-Z]", "", (q + c).upper().replace("1", "I"))
    # the word straddles both boxes, so it comes in pieces: "H" + "ALF", "FL" + "NAL" (720p)
    if any(k in s for k in ("HALF", "ALF", "HAL", "HLF")):
        return HALF
    if any(k in s for k in ("FINAL", "NAL", "FIN", "FNAL")):
        return FINAL
    return 0


def parse_clock(s: str) -> int:
    """Game clock text to seconds left in the quarter, or -1. Accepts 15:00, 4:25, 4.25, 425, 0:28, 28.3."""
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})\.(\d)", s)          # under a minute with tenths
    if m:
        return int(m.group(1)) if int(m.group(1)) < 60 else -1
    d = re.sub(r"\D", "", s)
    # a leading 1 read as 7 (720p: "70.11" for 10:11); no game clock has 70+ minutes
    if len(d) >= 4 and d[0] == "7":
        d = "1" + d[1:]
    # the colon is sometimes read as a digit (at 720p "14355" for 14:55): try without the digit where it sits
    colon = len(d) == 5 or (len(d) == 4 and d[-3] in "38")
    for cand in (d, d[:-3] + d[-2:] if colon else None):
        if cand is None or not 3 <= len(cand) <= 4:
            continue
        mins, secs = int(cand[:-2]), int(cand[-2:])
        if secs <= 59 and (mins < 15 or (mins == 15 and not secs)):
            return mins * 60 + secs
    return -1


class Reader:
    def __init__(self):
        import easyocr
        self.r = easyocr.Reader(["en"], gpu=True, verbose=False)

    def tokens(self, img: np.ndarray) -> list[tuple[list[int], str, float]]:
        """Full OCR (detect + read): [([x0, y0, x1, y1], text, conf)]."""
        out = []
        for b, s, c in self.r.readtext(img):
            xs, ys = [p[0] for p in b], [p[1] for p in b]
            out.append(([int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))], s, float(c)))
        return out

    def read(self, img: np.ndarray, box: list[int], alpha: str) -> tuple[str, float]:
        x0, y0, x1, y1 = box
        res = self.r.recognize(img, horizontal_list=[[x0, x1, y0, y1]], free_list=[], allowlist=alpha,
                               detail=1, paragraph=False)
        if not res:
            return "", 0.0
        return res[0][1], float(res[0][2])

    def read_many(self, imgs: np.ndarray, box: list[int], alpha: str, chunk: int = 128):
        """The same box in many crops, read in one recognition call per chunk: the crops are stacked into
        one tall image and each crop's box is passed as its own line, so the recogniser batches them on the
        GPU instead of being called once per box."""
        out = []
        x0, y0, x1, y1 = box
        h = imgs.shape[1]
        for i in range(0, len(imgs), chunk):
            part = imgs[i:i + chunk]
            tall = part.reshape(-1, part.shape[2])
            boxes = [[x0, x1, k * h + y0, k * h + y1] for k in range(len(part))]
            res = self.r.recognize(tall, horizontal_list=boxes, free_list=[], allowlist=alpha, detail=1,
                                   paragraph=False, batch_size=len(boxes))
            # one result per box, in box order (EasyOCR keeps the order of horizontal_list)
            got = {}
            for b, s, c in res:
                k = int(round(min(p[1] for p in b))) // h
                got[k] = (s, float(c))
            out += [got.get(k, ("", 0.0)) for k in range(len(part))]
        return out


def locate(path: str, info: dict, rd: Reader, n: int = 120) -> dict:
    """Find the quarter and clock boxes: median boxes of quarter tokens with a clock to their right."""
    W, H = info["width"], info["height"]
    y0 = int(H * 0.7)
    dur = info["nb_frames"] / FPS
    qs, cs = [], []
    for k in range(n):
        t = dur * (0.25 + 0.5 * k / (n - 1))
        img = frame_at_time(path, t, f"{W}:{H - y0}:0:{y0}")
        toks = rd.tokens(img)
        for qb, qt, qc in toks:
            if not parse_quarter(qt):
                continue
            qh = qb[3] - qb[1]
            right = [(cb, ct) for cb, ct, cc in toks
                     if cb[0] > qb[2] and cb[0] - qb[2] < 3 * qh and abs((cb[1] + cb[3]) - (qb[1] + qb[3])) < qh
                     and parse_clock(ct) >= 0]
            if right:
                cb = min(right, key=lambda r: r[0][0])[0]
                qs.append([qb[0], qb[1] + y0, qb[2], qb[3] + y0])
                cs.append([cb[0], cb[1] + y0, cb[2], cb[3] + y0])
    if len(qs) < 5:
        raise SystemExit(f"score bug not found ({len(qs)} sightings in {n} frames)")
    q, c = np.median(np.array(qs), 0), np.median(np.array(cs), 0)
    h = q[3] - q[1]
    # Pad sideways only: the quarter word changes width (1ST vs 2ND), the clock more (0:28 vs 15:00).
    # Not up or down: the token boxes already carry a margin, and taller boxes take in the band above
    # the row (WK06: "THURSDAY NIGHT FOOTBALL"), which halves the reading rate.
    qbox = [q[0] - 0.4 * h, q[1], q[2] + 0.4 * h, q[3]]
    cw = max(np.percentile(np.array(cs)[:, 2] - np.array(cs)[:, 0], 90), 3.2 * h)
    cmid = (c[0] + c[2]) / 2
    cbox = [cmid - 0.55 * cw, c[1], cmid + 0.55 * cw, c[3]]
    x0, y0_, x1, y1 = (int(min(qbox[0], cbox[0])) - 4, int(min(qbox[1], cbox[1])) - 4,
                       int(max(qbox[2], cbox[2])) + 4, int(max(qbox[3], cbox[3])) + 4)
    x0, y0_ = max(0, x0) & ~1, max(0, y0_) & ~1
    w, h_ = (min(W, x1) - x0 + 1) & ~1, (min(H, y1) - y0_ + 1) & ~1
    rel = lambda b: [int(round(b[0] - x0)), int(round(b[1] - y0_)), int(round(b[2] - x0)), int(round(b[3] - y0_))]
    return {"sightings": len(qs), "crop": [w, h_, x0, y0_], "quarter_box": rel(qbox), "clock_box": rel(cbox)}


def cmd_scan(a):
    cache = Path(a.cache)
    out = cache / f"bug-{a.out_game or a.game}.json"
    info = probe(a.recording)
    rd = Reader()
    loc = locate(a.recording, info, rd)
    print(f"bug located from {loc['sightings']} sightings: crop {loc['crop']}", flush=True)
    if a.slow:
        rows = []
        for idx, fr in keyframes(a.recording, tuple(loc["crop"]), info["start"]):
            for i, img in zip(idx, fr):
                qt, qc = rd.read(img, loc["quarter_box"], QUARTER_ALPHA)
                ct, cc = rd.read(img, loc["clock_box"], CLOCK_ALPHA)
                rows.append([int(i), qt, round(qc, 3), ct, round(cc, 3)])
            print(f"  {len(rows)} keyframes, at frame {int(idx[-1])}", flush=True)
        read_n = len(rows)
    else:
        rows, read_n = scan_fast(a.recording, info, loc, rd)
    out.write_text(json.dumps({"recording": a.recording, **info, "locate": loc,
                               "columns": ["frame", "quarter_text", "quarter_conf", "clock_text", "clock_conf"],
                               "mode": "slow" if a.slow else f"fast (every {SKIP}th keyframe, all near the bug)",
                               "ocr_read": read_n, "rows": rows}), encoding="utf-8")
    print(f"wrote {out} ({len(rows)} keyframes, {read_n} read by OCR)")


SKIP = 4            # fast scan: read every SKIPth keyframe first ...
FILL = 6            # ... then every keyframe within FILL keyframes of a first-pass reading that shows the bug


def scan_fast(path: str, info: dict, loc: dict, rd: Reader):
    """Decode every keyframe crop once (cheap), OCR every SKIPth, then every keyframe around the bug. Keyframes
    not read get empty text, which the rules treat as "no bug", as before for keyframes without one."""
    idx_all, crops = [], []
    for idx, fr in keyframes(path, tuple(loc["crop"]), info["start"]):
        idx_all.append(idx)
        crops.append(fr.copy())
    idx_all, crops = np.concatenate(idx_all), np.concatenate(crops)
    n = len(idx_all)
    print(f"  decoded {n} keyframe crops", flush=True)
    text = [None] * n

    def read(sel):
        sel = [k for k in sel if text[k] is None]
        if not sel:
            return
        qs = rd.read_many(crops[sel], loc["quarter_box"], QUARTER_ALPHA)
        cs = rd.read_many(crops[sel], loc["clock_box"], CLOCK_ALPHA)
        for k, (qt, qc), (ct, cc) in zip(sel, qs, cs):
            text[k] = (qt, qc, ct, cc)

    def bug_seen(k):
        qt, _, ct, _ = text[k]
        return (parse_quarter(qt) and parse_clock(ct) >= 0) or parse_state(qt, ct)
    read(list(range(0, n, SKIP)))
    print(f"  first pass: {sum(t is not None for t in text)} read", flush=True)
    # fill around every hit, and keep filling while new hits turn up at the edge of a filled stretch
    todo = [k for k in range(0, n, SKIP) if bug_seen(k)]
    while todo:
        near = sorted({j for k in todo for j in range(max(0, k - FILL), min(n, k + FILL + 1))})
        fresh = [j for j in near if text[j] is None]
        read(fresh)
        todo = [j for j in fresh if bug_seen(j)]
    read_n = sum(t is not None for t in text)
    print(f"  after filling: {read_n} read", flush=True)
    rows = []
    for k in range(n):
        qt, qc, ct, cc = text[k] or ("", 0.0, "", 0.0)
        rows.append([int(idx_all[k]), qt, round(qc, 3), ct, round(cc, 3)])
    return rows, read_n


# ---------------------------------------------------------------------------------------- phases

def load_readings(cache: Path, game: str):
    d = json.loads((cache / f"bug-{game}.json").read_text(encoding="utf-8"))
    rows = d["rows"]
    fr = np.array([r[0] for r in rows])
    q = np.array([parse_quarter(r[1]) for r in rows])
    c = np.array([parse_clock(r[3]) for r in rows])
    st = np.array([parse_state(r[1], r[3]) for r in rows])
    seen = (q > 0) & (c >= 0)
    q = np.where(seen, q, st)           # 1-5 quarter (with a clock), 6 HALF, 7 FINAL, 0 no bug
    c = np.where(seen, c, -1)
    return d, fr, q, c


# PHASE RULES
CORE_WINDOW = 300           # s: a sighting counts only with CORE_MIN sightings of the same state within +-this
CORE_MIN = 5                #    (drops single misreads, e.g. a "1ST" read off a replay graphic in the postgame)
RUN_GAP = 90                # s: sightings further apart than this belong to different stretches of the bug
KICK_LEAD = 3.5             # s: the bug wipes on and first reads "15:00" this long after the kick; set on WK06
                            #    (opening kick 3.5 s, second-half kick 4.2 s), tested on WK04 and WK11


STATE_WINDOW, STATE_MIN = 60, 2     # HALF / FINAL are up for seconds, not minutes: 2 sightings within 60 s
ZERO_AFTER = 6              # a 0:00 reading counts as the end of a quarter only if the next ZERO_AFTER readings
                            # of that quarter (within 10 min) mostly read 0:00 too: OCR turns a garbled clock into
                            # "0.7" now and then, while a real 0:00 stays up until the bug changes to HALF/FINAL


def core_mask(fr: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Sightings backed by enough sightings of the same state nearby (see CORE_* and STATE_*)."""
    t = fr / FPS
    keep = np.zeros(len(q), bool)
    for s in np.unique(q[q > 0]):
        win, need = (STATE_WINDOW, STATE_MIN) if s in (HALF, FINAL) else (CORE_WINDOW, CORE_MIN)
        idx = np.where(q == s)[0]
        ts = t[idx]
        lo = np.searchsorted(ts, ts - win)
        hi = np.searchsorted(ts, ts + win, side="right")
        keep[idx[(hi - lo) >= need]] = True
    return keep


def kickoff(fr, q, c, core, quarter: int):
    """The kick that starts `quarter` (1 or 3): back from the first running clock over the 15:00 stretch."""
    t = fr / FPS
    qi = np.where(core & (q == quarter))[0]
    if not len(qi):
        return None, 0.0, {"reason": f"no Q{quarter} sightings"}
    # running: below 15:00 and the next reading of the quarter agrees (below 15:00, not above this one)
    running = np.array([qi[k] for k in range(len(qi) - 1)
                        if c[qi[k]] < 900 and c[qi[k + 1]] < 900 and c[qi[k + 1]] <= c[qi[k]] + 2], int)
    if not len(running):
        return None, 0.0, {"reason": f"Q{quarter} clock never seen running"}
    r = running[0]
    # walk back over sightings of this quarter at 15:00 (or the running one itself), RUN_GAP apart at most
    first = r
    for i in qi[qi < r][::-1]:
        if c[i] != 900 or t[first] - t[i] > RUN_GAP:
            break
        first = i
    stretch = t[r] - t[first]
    # When the first reading already shows a running clock (the bug came up during the return), the time
    # that has run off goes back too: the clock starts when the returner touches the ball.
    frame = int(round(fr[first] - (KICK_LEAD + (900 - c[first])) * FPS))
    n_q = len(qi)
    # sure when the bug came up with the clock still at 15:00 and started running soon after; less sure when the
    # first sighting already shows a running clock (the bug was missed at the kick) or the 15:00 stretch is long
    conf = 0.95 if 900 - c[first] <= 5 else 0.6
    if stretch > 120:
        conf -= 0.25
    conf *= min(1.0, n_q / 50)
    return frame, round(conf, 2), {"first_sighting": int(fr[first]), "first_clock": int(c[first]),
                                    "first_running": int(fr[r]), "running_clock": int(c[r]),
                                    "stretch_s": round(stretch, 1), "quarter_sightings": n_q}


def period_end(fr, q, c, core, quarters: tuple[int, ...], state: int, until: int | None = None):
    """End of a half: the first time the clock reads 0:00 in the half's last quarter (and stays there),
    confirmed by HALF/FINAL soon after; else the first HALF/FINAL; else the last sighting.
    until: index where the next half starts (the first 3rd-quarter sighting), so the bug in later replays and
    highlights cannot move the end of the first half."""
    t = fr / FPS
    limit = until if until is not None else len(q)
    qi = np.where(core & np.isin(q, quarters))[0]
    qi = qi[qi < limit]
    si = np.where(core & (q == state))[0]
    si = si[si < limit]
    if not len(qi) and not len(si):
        return None, 0.0, {"reason": "no sightings"}
    last_q = int(q[qi].max()) if len(qi) else None
    lq = qi[q[qi] == last_q] if last_q else np.array([], int)
    zero = lq[c[lq] == 0]

    def holds(z):
        nxt = lq[(lq > z) & (t[lq] - t[z] < 600)][:ZERO_AFTER]
        return not len(nxt) or np.mean(c[nxt] <= 1) >= 0.5
    rejected = [int(fr[z]) for z in zero if not holds(z)]
    zero = np.array([z for z in zero if holds(z)], int)
    ev = {"last_quarter": last_q, "zero_sightings": int(len(zero))}
    if rejected:
        ev["zero_readings_not_held"] = rejected[:10]
    if len(zero):
        z = zero[0]
        ev["first_zero"] = int(fr[z])
        after = si[(si > z) & (t[si] - t[z] < 600)]
        ev["state_sightings_after"] = int(len(after))
        if len(after):
            ev["first_state"] = int(fr[after[0]])
        return int(fr[z]), (0.95 if len(after) else 0.7), ev
    # No held 0:00 (the bug was off for the last play). HALF / FINAL then, but only after the quarter's clock
    # was last seen above 2:00: OCR finds "FINAL" in garbage ("Lnala", "inaFina") and the pregame shows other
    # games' finals, while the real one comes after the clock has run down.
    if len(lq):
        above = lq[c[lq] > 120]
        si = si[si > (above[-1] if len(above) else lq[0])]
    ev["state_sightings"] = int(len(si))
    if len(si):
        ev["first_state"] = int(fr[si[0]])
        return int(fr[si[0]]), 0.7, ev
    return int(fr[qi[-1]]), 0.4, ev


def phases(fr, q, c, shows: dict | None = None):
    """Boundaries from the readings. shows: from shows-<game>.json; None leaves half_start and game_end at
    the clock's 0:00 (used by the shows step itself to know where to search)."""
    core = core_mask(fr, q)
    q3 = np.where(core & (q == 3))[0]
    out, conf, ev = {}, {}, {}
    for name, fn in (("kickoff", lambda: kickoff(fr, q, c, core, 1)),
                     ("half_start", lambda: period_end(fr, q, c, core, (2,), HALF, q3[0] if len(q3) else None)),
                     ("second_half_kickoff", lambda: kickoff(fr, q, c, core, 3)),
                     ("game_end", lambda: period_end(fr, q, c, core, (4, 5), FINAL))):
        out[name], conf[name], ev[name] = fn()
    # halftime and the postgame start with their studio shows, after the break that follows 0:00 (STUDIO SHOWS)
    if shows is not None:
        for k in SHOW_WORDS:
            if out[k] is None:
                continue
            ev[k]["clock_zero"] = out[k]
            s = shows.get(k)
            if s:
                ev[k]["show"] = s
                out[k] = s["start"]
                conf[k] = round(min(conf[k], 0.95 if "black" in s["how"] else 0.8), 2)
            else:
                ev[k]["show"] = "not found: the clock's 0:00 is given instead"
                conf[k] = 0.3
    # program order must hold; a boundary out of order is dropped rather than guessed
    order = ["kickoff", "half_start", "second_half_kickoff", "game_end"]
    prev = -1
    for k in order:
        if out[k] is not None:
            if out[k] <= prev:
                ev[k]["dropped"] = f"out of order (at {out[k]})"
                out[k], conf[k] = None, 0.0
            else:
                prev = out[k]
    ev["sightings"] = {"total": int((q > 0).sum()), "core": int(core.sum()), "samples": int(len(q))}
    return out, conf, ev


def cmd_phases(a):
    cache = Path(a.cache)
    d, fr, q, c = load_readings(cache, a.game)
    sp = cache / f"shows-{a.game}.json"
    shows = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    out, conf, ev = phases(fr, q, c, shows)
    res = {"version": 1, "source": "scorebug", "recording": Path(d["recording"]).name,
           "nb_frames": d["nb_frames"], **out, "confidence": conf,
           "evidence": {"method": "phase_eval.py: OCR of quarter and game clock on every keyframe; half_start "
                                  "and game_end are the starts of the halftime and postgame shows after the "
                                  "break that follows the clock's 0:00", **ev}}
    path = cache / f"{a.game}.phases.json"
    path.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("kickoff", "half_start", "second_half_kickoff", "game_end", "confidence")}))
    if a.truth:
        tr = json.loads(Path(a.truth).read_text(encoding="utf-8")).get(a.game)
        if tr:
            rows = [("kickoff", res["kickoff"], "kickoff"),
                    ("half 0:00", ev["half_start"].get("clock_zero", res["half_start"]), "half_clock0"),
                    ("half_start (show)", res["half_start"], "half_show"),
                    ("second_half_kickoff", res["second_half_kickoff"], "second_half_kickoff"),
                    ("end 0:00", ev["game_end"].get("clock_zero", res["game_end"]), "end_clock0"),
                    ("game_end (show)", res["game_end"], "post_show")]
            for k, v, tk in rows:
                if v is not None and tk in tr:
                    print(f"  {k:20s} {v:8d}  truth {tr[tk]:8d}  off {(v - tr[tk]) / FPS:+7.1f} s")


# STUDIO SHOWS (TJ, November 2026): the game crew hands over to the pre/half/post crew in a commercial break,
# so halftime starts when the halftime show starts after the first break following the 2nd quarter's 0:00,
# and the postgame likewise. Every show checked opens the same way: a black frame ends the break, then a
# ~6 s animated open with a large "HALFTIME SHOW" / "POSTGAME SHOW" title card.
SHOW_WORDS = {"half_start": "HALFTIME", "game_end": "POSTGAME"}
SHOW_SEARCH = 20 * 60       # s after 0:00 to look for the show's title card
SHOW_FPS = 2                # frames per second read in that window
TITLE_MIN_H = 0.08          # title text at least this share of the frame height (lower thirds and the desk
                            # graphic that also say HALFTIME SHOW are 2-4%)
BLACK_BACK = 15             # s before the title to look for the black frame that ends the break
OPEN_LEAD = 6.0             # s from the start of the open to the title card, when no black frame is found
SW, SH = 640, 360


def window_frames(path: str, t0: float, dur: float, fps: float = SHOW_FPS, w: int = SW, h: int = SH):
    """Grey w x h frames at fps from t0 for dur seconds: (times, frames)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", path,
                          "-map", "0:v:0", "-vf", f"fps={fps},scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
    return t0 + np.arange(len(fr)) / fps, fr


def find_show(path: str, rd: Reader, zero_frame: int, word: str) -> dict | None:
    """Start of the studio show after the first break following zero_frame (see STUDIO SHOWS)."""
    t0 = zero_frame / FPS
    step = 120.0                                       # read the window in 2-minute pieces, stop at the title
    for k in range(int(SHOW_SEARCH / step)):
        ts, frs = window_frames(path, t0 + k * step, step)
        for t, img in zip(ts, frs):
            for b, s, cf in rd.tokens(img):
                if word in s.upper().replace(" ", "") and (b[3] - b[1]) >= TITLE_MIN_H * SH:
                    # back to the black frame that ends the break, at 30 fps since it can be a few frames
                    bt, bf = window_frames(path, t - BLACK_BACK, BLACK_BACK, fps=30, w=160, h=90)
                    y = bf.reshape(len(bf), -1).astype(np.float32)
                    black = np.where((np.median(y, 1) < 20) & (y.std(1) < 8))[0]
                    if len(black):
                        start, how = bt[black[-1]] + 1 / 30, "after the black frame ending the break"
                    else:
                        start, how = t - OPEN_LEAD, f"title card minus {OPEN_LEAD} s (no black frame found)"
                    return {"start": int(round(start * FPS)), "title": int(round(t * FPS)), "how": how,
                            "text": s}
    return None


def cmd_shows(a):
    """Find the halftime and postgame shows' starts; writes shows-<game>.json, which phases then uses."""
    cache = Path(a.cache)
    d, fr, q, c = load_readings(cache, a.game)
    _, _, ev = phases(fr, q, c, shows=None)
    rd = Reader()
    out = {}
    for k, word in SHOW_WORDS.items():
        z = ev[k].get("first_zero") or ev[k].get("first_state")
        if z is None:
            out[k] = None
            continue
        out[k] = find_show(d["recording"], rd, z, word)
        print(k, out[k], flush=True)
    (cache / f"shows-{a.game}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


def cmd_dump(a):
    d, fr, q, c = load_readings(Path(a.cache), a.game)
    for r, qq, cc in zip(d["rows"], q, c):
        if a.frm <= r[0] <= a.to:
            print(f"{r[0]:8d} {r[0] / FPS / 60:7.2f}m  q={qq} clock={cc if cc >= 0 else '-':>4}   "
                  f"[{r[1]!r} {r[2]:.2f} | {r[3]!r} {r[4]:.2f}]")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--recording", required=True)
    s.add_argument("--game", required=True)
    s.add_argument("--cache", required=True)
    s.add_argument("--slow", action="store_true", help="read every keyframe, two OCR calls each (the old way)")
    s.add_argument("--out-game", help="write the readings under this name instead of --game (for comparisons)")
    d = sub.add_parser("dump")
    d.add_argument("--game", required=True)
    d.add_argument("--cache", required=True)
    d.add_argument("--from", dest="frm", type=int, default=0)
    d.add_argument("--to", type=int, default=10**9)
    w = sub.add_parser("shows", help="find the halftime / postgame show starts (after scan; before phases)")
    w.add_argument("--game", required=True)
    w.add_argument("--cache", required=True)
    f = sub.add_parser("phases")
    f.add_argument("--game", required=True)
    f.add_argument("--cache", required=True)
    f.add_argument("--truth", help="JSON of boundaries checked by eye, to print how far off each one is")
    a = p.parse_args()
    {"scan": cmd_scan, "dump": cmd_dump, "phases": cmd_phases, "shows": cmd_shows}[a.cmd](a)


if __name__ == "__main__":
    main()
