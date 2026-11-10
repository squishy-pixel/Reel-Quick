# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["torch>=2.7", "numpy", "transformers>=4.45"]
#
# [tool.uv.sources]
# torch = { index = "pytorch-cu128" }
#
# [[tool.uv.index]]
# name = "pytorch-cu128"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
# ///
"""Measure how well library shells are found in a recording whose answers are known.

Step 2 of docs/DETECTION.md. Run from engine/:

    uv run scripts/shell_eval.py score --library <work>/library/library.json \
        --test-game 2025WK04-SEAvsARI --test-recording <WK04.mp4> \
        --bg-game 2025WK05-SFvsLAR --bg-recording <WK05.mp4> --cache <dir>
    uv run scripts/shell_eval.py report --test-game 2025WK04-SEAvsARI --cache <dir>
    uv run scripts/shell_eval.py colour --library ... --cache <dir> \
        --pair 2025WK04-SEAvsARI=<WK04.mp4>@0 --pair 2025WK10-LVvsDEN=<WK10.mp4>@3380616

Answer key: the test game's library clips, placed by their timecode (the trailing number in the name,
see Findings in DETECTION.md) minus the recording's timecode base. References: frames from every other
game's clips, so the test game is unseen. Background: keyframes of another recording outside its
clips, used to standardise each shell's scores and to weight the patch matcher. Label: the shell of
the best-scoring reference. Threshold: fixed before the test game is scored, as the standardised
score at which LOO_RECALL of the library's frames are found in the other games (leave-one-game-out).

Samples are the recording's keyframes (about one per 1.1 s on WK04, decoded with -skip_frame nokey), and a
detection is a run of at least MIN_SPAN samples with one label. Shells in CAMERA are reported
apart: they run all game and only a few instances are cut, so the clip list under-counts them.

Needs a CUDA GPU (the inline metadata pulls the CUDA 12.8 PyTorch build); caches go in --cache.
The engine's own dependencies are not touched: uv builds this script's environment on its own.

Two matchers on the same DINOv2 features, each on three colour treatments (rgb, gray, eq):
- whole: cosine of the frame's CLS embedding, best reference of each shell.
- patch: cosine per patch location, weighted per shell by how consistently the shell looks there
  across its clips minus how much background footage already looks like it there.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import torch

FPS = 60000 / 1001
W, H = 448, 252                     # 32 x 18 DINOv2 patches of 14 px, 16:9
GW, GH = W // 14, H // 14
FRAME_BYTES = W * H * 3
REF_POSITIONS = (0.2, 0.35, 0.5, 0.65, 0.8)    # skip the handles at both ends of a clip
VARIANTS = ("rgb", "gray", "eq")
METHODS = ("whole", "patch")
MODEL = "facebook/dinov2-base"
MIN_SPAN = 2                        # samples (1.5 s apart on WK04) a detection must last
LOO_RECALL = 80                     # threshold: 80% of library frames are found in the other games
# Camera categories rather than graphics: they run all game and only a few instances are cut, so a clip
# list is not a complete answer key for them and their "false alarms" are mostly real but uncut shots.
CAMERA = {"Booth", "Scenic Comp", "Scenic Droneshow", "On Field"}
FLAT_T = 0.9                        # picture test: a frame is flat when this share of it sits within +-10 luma
                                    # of its median (black frames, plain ad cards). Set on WK04's false alarms:
                                    # catches all 37 black/flat ones, no real shell, 2 of 1420 library frames
REJECT_KEEP = 99                    # reject gate: at most 1% of library shell frames look this much like "not ours"
DEV = "cuda"


# ---------------------------------------------------------------------------------------- media

def probe_clip(path: str) -> tuple[str | None, int]:
    d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-select_streams", "v:0",
                                   "-show_entries", "stream=nb_frames:stream_tags=timecode", path],
                                  capture_output=True, text=True, check=True).stdout)
    s = d["streams"][0]
    return s.get("tags", {}).get("timecode"), int(s["nb_frames"])


def tc_frames(tc: str) -> int:
    """Timecode to a frame count at 59.94: drop-frame when written with ';'."""
    h, m, s, f = map(int, re.split(r"[:;.]", tc))
    n = (h * 3600 + m * 60 + s) * 60 + f
    if ";" in tc:
        mins = h * 60 + m
        n -= 4 * (mins - mins // 10)
    return n


def first_pts(path: str) -> float:
    d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-select_streams", "v:0",
                                   "-show_entries", "stream=start_time", path],
                                  capture_output=True, text=True, check=True).stdout)
    return float(d["streams"][0].get("start_time") or 0)


def clip_frames(path: str, indices: list[int]) -> np.ndarray:
    sel = "+".join(f"eq(n\\,{i})" for i in indices)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0",
                          "-vf", f"select='{sel}',scale={W}:{H}:flags=area", "-fps_mode", "passthrough",
                          "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(-1, H, W, 3)


def recording_frames(path: str, every: int = 1, batch: int = 128):
    """Yield (frame indices, uint8 frames) for the recording's keyframes, every `every`th one."""
    p0 = first_pts(path)
    vf = (f"select='not(mod(n\\,{every}))'," if every > 1 else "") + f"scale={W}:{H}:flags=area,showinfo"
    proc = subprocess.Popen(["ffmpeg", "-v", "info", "-nostats", "-skip_frame", "nokey", "-copyts", "-i", path,
                             "-map", "0:v:0", "-vf", vf, "-fps_mode", "passthrough", "-pix_fmt", "rgb24",
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pts: list[float] = []

    def read_err():
        for line in proc.stderr:
            m = re.search(rb"pts_time:\s*([-\d.]+)", line)
            if m:
                pts.append(float(m.group(1)))
    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    done = 0
    while True:
        buf = proc.stdout.read(FRAME_BYTES * batch)
        if not buf:
            break
        frames = np.frombuffer(buf, np.uint8).reshape(-1, H, W, 3)
        n = len(frames)
        while len(pts) < done + n and t.is_alive():
            t.join(0.01)
        idx = np.round((np.array(pts[done:done + n]) - p0) * FPS).astype(np.int64)
        done += n
        yield idx, frames
    proc.wait()


def frames_at(path: str, base_pts: float, indices: list[int]) -> np.ndarray:
    """Exact recording frames by index (decodes from a seek point before each)."""
    out = []
    for i in indices:
        t = base_pts + i / FPS
        raw = subprocess.run(["ffmpeg", "-v", "error", "-copyts", "-ss", f"{max(0, t - 3):.6f}", "-i", path,
                              "-map", "0:v:0", "-vf", f"select='gte(t\\,{t - 0.5 / FPS:.6f})',scale={W}:{H}:flags=area",
                              "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        out.append(np.frombuffer(raw, np.uint8).reshape(H, W, 3))
    return np.stack(out)


# ---------------------------------------------------------------------------------------- features

class Embedder:
    def __init__(self):
        from transformers import Dinov2Model
        self.model = Dinov2Model.from_pretrained(MODEL, torch_dtype=torch.float16).to(DEV).eval()
        self.mean = torch.tensor([0.485, 0.456, 0.406], device=DEV).view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225], device=DEV).view(1, 3, 1, 1)

    @staticmethod
    def treat(x: torch.Tensor, variant: str) -> torch.Tensor:
        """x: B,3,H,W float in 0..1. gray = BT.709 luma; eq = luma with per-frame histogram equalisation."""
        if variant == "rgb":
            return x
        y = (0.2126 * x[:, 0] + 0.7152 * x[:, 1] + 0.0722 * x[:, 2]).clamp(0, 1)
        if variant == "eq":
            q = (y * 255).round().long().flatten(1)
            out = torch.empty_like(y.flatten(1))
            for i in range(len(q)):
                cdf = torch.bincount(q[i], minlength=256).cumsum(0).float()
                cdf = (cdf - cdf[cdf > 0].min()) / (cdf[-1] - cdf[cdf > 0].min()).clamp(min=1)
                out[i] = cdf[q[i]]
            y = out.view_as(y)
        return y.unsqueeze(1).expand(-1, 3, -1, -1)

    @torch.no_grad()
    def __call__(self, frames: np.ndarray, variant: str) -> tuple[torch.Tensor, torch.Tensor]:
        """Returns unit CLS (B,768) and unit patch features (B, GH*GW, 768), fp16 on the GPU."""
        x = torch.from_numpy(np.ascontiguousarray(frames)).to(DEV).permute(0, 3, 1, 2).float() / 255
        x = (self.treat(x, variant) - self.mean) / self.std
        h = self.model(pixel_values=x.half()).last_hidden_state
        cls = torch.nn.functional.normalize(h[:, 0].float(), dim=-1).half()
        patches = torch.nn.functional.normalize(h[:, 1:].float(), dim=-1).half()
        return cls, patches


def embed_all(emb: Embedder, frames: np.ndarray, variant: str, bs: int = 64):
    cs, ps = [], []
    for i in range(0, len(frames), bs):
        c, p = emb(frames[i:i + bs], variant)
        cs.append(c)
        ps.append(p)
    return torch.cat(cs), torch.cat(ps)


# ---------------------------------------------------------------------------------------- matchers

class Matcher:
    """Per-shell scores for a batch of frames, for both methods, against one variant's references."""

    def __init__(self, ref_cls, ref_patch, ref_shell: np.ndarray, ref_clip: np.ndarray, bg_patch, shells):
        self.shells = shells
        self.S = len(shells)
        self.ref_cls, self.ref_patch = ref_cls, ref_patch
        self.ref_shell = torch.from_numpy(ref_shell).to(DEV)
        self.onehot = torch.nn.functional.one_hot(self.ref_shell, self.S).bool()       # R,S
        self.weights = self._weights(ref_patch, ref_shell, ref_clip, bg_patch)          # S,L
        w = self.weights[self.ref_shell]                                                # R,L
        self.ref_w = (w / w.sum(1, keepdim=True).clamp(min=1e-6)).half()

    def _weights(self, P, shell, clip, bg):
        """Consistency of each shell at each location across its clips, minus background look-alike."""
        S, L = self.S, P.shape[1]
        Pf = P.float()
        wts = torch.zeros(S, L, device=DEV)
        for s in range(S):
            rows = np.where(shell == s)[0]
            if len(rows) == 0:
                continue
            Ps = Pf[rows]
            total = Ps.sum(0)
            clips = clip[rows]
            if len(set(clips)) > 1:
                # leave the frame's own clip out, so one clip's background does not count as consistency
                cons = torch.zeros(L, device=DEV)
                for c in set(clips):
                    m = torch.from_numpy(clips == c).to(DEV)
                    mu = torch.nn.functional.normalize(total - Ps[m].sum(0), dim=-1)
                    cons += (Ps[m] * mu).sum(-1).sum(0)
                cons /= len(rows)
            else:
                mu = torch.nn.functional.normalize(total, dim=-1)
                cons = (Ps * mu).sum(-1).mean(0)
            mu = torch.nn.functional.normalize(total, dim=-1)
            bgsim = (bg.float() * mu).sum(-1).mean(0)
            wts[s] = (cons - bgsim).clamp(min=0)
        return wts

    @torch.no_grad()
    def scores(self, cls, patch, exclude: torch.Tensor | None = None) -> dict[str, torch.Tensor]:
        """B,S best-reference score per shell. exclude: B,R bool of references to ignore."""
        out = {}
        whole = cls.float() @ self.ref_cls.float().T                                    # B,R
        # patch: sum_l w[r,l] * <x_l, r_l>, as L batched matmuls
        sim = torch.bmm(patch.transpose(0, 1), self.ref_patch.permute(1, 2, 0))          # L,B,R
        pscore = (sim.float() * self.ref_w.T.float().unsqueeze(1)).sum(0)               # B,R
        for name, m in (("whole", whole), ("patch", pscore)):
            if exclude is not None:
                m = m.masked_fill(exclude, -1e4)
            per_shell = m.unsqueeze(2).masked_fill(~self.onehot.unsqueeze(0), -1e4).amax(1)   # B,S
            out[name] = per_shell
        return out


def flatness(frames: np.ndarray) -> np.ndarray:
    """Share of each frame within +-10 luma levels of its median: ~1 for black frames and plain cards."""
    out = []
    for i in range(0, len(frames), 256):
        f = frames[i:i + 256].astype(np.float32)
        y = (0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]).reshape(len(f), -1)
        out.append((np.abs(y - np.median(y, 1, keepdims=True)) <= 10).mean(1))
    return np.concatenate(out) if out else np.zeros(0)


@torch.no_grad()
def reject_similarity(patch: torch.Tensor, rej_patch: torch.Tensor) -> torch.Tensor:
    """Mean patch cosine, location by location, to the closest "not ours" reference frame."""
    L = patch.shape[1]
    return ((patch.flatten(1).float() @ rej_patch.flatten(1).float().T) / L).amax(1)


def reject_frames(manifest: str, recordings: str, cache: Path) -> np.ndarray:
    m = json.loads(Path(manifest).read_text(encoding="utf-8"))
    f = cache / f"reject-{Path(manifest).stem}.npz"
    if f.exists():
        return np.load(f)["frames"]
    rec = str(Path(recordings) / m["recording"])
    frames = frames_at(rec, first_pts(rec), [x["frame"] for x in m["frames"] if x["kind"] == "not_ours"])
    np.savez(f, frames=frames)
    return frames


def robust_z(bg: np.ndarray):
    med = np.median(bg, 0)
    mad = np.median(np.abs(bg - med), 0) * 1.4826
    return med, np.maximum(mad, 1e-4)


# ---------------------------------------------------------------------------------------- scoring

def spans(labels: np.ndarray, frames: np.ndarray, min_len: int, max_gap: int = 1):
    """Runs of one label across consecutive samples (a gap of max_gap samples is bridged)."""
    out = []
    cur = None                                   # [label, first sample, last sample, count]

    def close():
        if cur and cur[3] >= min_len:
            out.append((int(cur[0]), int(frames[cur[1]]), int(frames[cur[2]])))
    for i, lab in enumerate(labels):
        if lab < 0:
            continue
        if cur and cur[0] == lab and i - cur[2] <= max_gap + 1:
            cur[2], cur[3] = i, cur[3] + 1
        else:
            close()
            cur = [lab, i, i, 1]
    close()
    return out


def event_metrics(pred_spans, key, shells):
    per = defaultdict(lambda: {"found": 0, "missed": 0, "false_alarms": 0, "mislabelled": 0})
    for k in key:
        s = k["shell"]
        hit = [p for p in pred_spans if p[1] <= k["end"] and p[2] >= k["start"]]
        if any(shells[p[0]] == s for p in hit):
            per[s]["found"] += 1
        else:
            per[s]["missed"] += 1
            if hit:
                per[s]["mislabelled"] += 1
    for lab, a, b in pred_spans:
        s = shells[lab]
        if not any(k["shell"] == s and a <= k["end"] and b >= k["start"] for k in key):
            per[s]["false_alarms"] += 1
    return {s: dict(v) for s, v in sorted(per.items())}


def average_precision(score: np.ndarray, pos: np.ndarray) -> float:
    if pos.sum() == 0:
        return float("nan")
    order = np.argsort(-score)
    p = pos[order]
    tp = np.cumsum(p)
    prec = tp / np.arange(1, len(p) + 1)
    return float((prec * p).sum() / p.sum())


# ---------------------------------------------------------------------------------------- commands

def load_library(path):
    lib = json.load(open(path, encoding="utf-8"))
    return lib["clips"]


def build_key(clips, game, base):
    key = []
    for c in clips:
        if c["game"] != game:
            continue
        tc, n = probe_clip(c["path"])
        if tc is None:
            continue
        start = tc_frames(tc) - base
        key.append({"name": Path(c["path"]).name, "shell": c["shell"], "start": start, "end": start + n - 1})
    return key


def reference_frames(clips, exclude_game, cache: Path):
    f = cache / f"refs-minus-{exclude_game}.npz"
    if f.exists():
        z = np.load(f, allow_pickle=True)
        return z["frames"], z["shell"], z["clip"], z["game"]
    use = [c for c in clips if c["game"] != exclude_game]

    def one(c):
        _, n = probe_clip(c["path"])
        return c, clip_frames(c["path"], sorted({min(n - 1, int(p * n)) for p in REF_POSITIONS}))
    frames, shell, clip, game = [], [], [], []
    with ThreadPoolExecutor(6) as pool:
        for c, fr in pool.map(one, use):
            frames.append(fr)
            shell += [c["shell"]] * len(fr)
            clip += [Path(c["path"]).name] * len(fr)
            game += [c["game"]] * len(fr)
    frames = np.concatenate(frames)
    np.savez(f, frames=frames, shell=np.array(shell), clip=np.array(clip), game=np.array(game))
    return frames, np.array(shell), np.array(clip), np.array(game)


def background_frames(recording, game, clips, base, every, cache: Path):
    f = cache / f"bg-{game}.npz"
    if f.exists():
        z = np.load(f)
        return z["frames"], z["idx"]
    key = build_key(clips, game, base)
    keep_f, keep_i = [], []
    for idx, fr in recording_frames(recording, every=every):
        ok = np.array([not any(k["start"] - 600 <= i <= k["end"] + 600 for k in key) for i in idx])
        keep_f.append(fr[ok])
        keep_i.append(idx[ok])
    frames, idx = np.concatenate(keep_f), np.concatenate(keep_i)
    np.savez(f, frames=frames, idx=idx)
    return frames, idx


def cmd_score(a):
    """Raw per-shell scores, cached per colour variant: test samples, background, and leave-one-game-out."""
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)
    clips = load_library(a.library)
    print("answer key ...", flush=True)
    key = build_key(clips, a.test_game, a.test_base)
    key = [k for k in key if k["name"] not in set(a.not_in_feed)]
    print("reference frames ...", flush=True)
    rf, rshell, rclip, rgame = reference_frames(clips, a.test_game, cache)
    shells = sorted(set(rshell))
    sidx = {s: i for i, s in enumerate(shells)}
    rs = np.array([sidx[s] for s in rshell])
    print(f"  {len(rf)} frames from {len(set(rclip))} clips, {len(shells)} shells", flush=True)
    print("background frames ...", flush=True)
    bf, _ = background_frames(a.bg_recording, a.bg_game, clips, a.bg_base, a.bg_every, cache)
    print(f"  {len(bf)} frames", flush=True)
    (cache / f"key-{a.test_game}.json").write_text(json.dumps(
        {"test_game": a.test_game, "shells": shells, "ref_shell": rs.tolist(), "key": key}, indent=1), encoding="utf-8")
    emb = Embedder()
    games = torch.from_numpy(np.unique(rgame, return_inverse=True)[1]).to(DEV)
    gate = bool(a.reject_manifest)
    if gate:
        jf = reject_frames(a.reject_manifest, a.recordings, cache)
        rflat, bflat = flatness(rf), flatness(bf)
        keep = rflat < FLAT_T
        print(f"  reject set: {len(jf)} frames; flat library frames dropped: {int((~keep).sum())}", flush=True)
    for v in a.variants:
        out = cache / f"scores-{a.test_game}-{v}.npz"
        if out.exists():
            print(f"[{v}] cached", flush=True)
            continue
        print(f"[{v}] embedding references and background ...", flush=True)
        rc, rp = embed_all(emb, rf, v)
        bc, bp = embed_all(emb, bf, v)
        sub = torch.randperm(len(bp), generator=torch.Generator().manual_seed(0))[:600].to(DEV)
        m = Matcher(rc, rp, rs, rclip, bp[sub], shells)
        res = defaultdict(list)
        if gate:
            kt = torch.from_numpy(keep).to(DEV)
            mg = Matcher(rc[kt], rp[kt], rs[keep], rclip[keep], bp[sub], shells)
            jc, jp = embed_all(emb, jf, v)
            kgames = games[kt]
            for i in range(0, len(bc), 128):
                for name, sc in mg.scores(bc[i:i + 128], bp[i:i + 128]).items():
                    res[f"gbg_{name}"].append(sc.cpu().numpy())
                res["bg_rsim"].append(reject_similarity(bp[i:i + 128], jp).cpu().numpy())
            krc, krp = rc[kt], rp[kt]
            for i in range(0, len(krc), 128):
                excl = kgames[i:i + 128, None] == kgames[None, :]
                for name, sc in mg.scores(krc[i:i + 128], krp[i:i + 128], exclude=excl).items():
                    res[f"gloo_{name}"].append(sc.cpu().numpy())
            for i in range(0, len(rc), 128):
                res["ref_rsim"].append(reject_similarity(rp[i:i + 128], jp).cpu().numpy())
        for i in range(0, len(bc), 128):
            for name, sc in m.scores(bc[i:i + 128], bp[i:i + 128]).items():
                res[f"bg_{name}"].append(sc.cpu().numpy())
        # leave-one-game-out: each reference frame scored against the other games' references
        for i in range(0, len(rc), 128):
            excl = games[i:i + 128, None] == games[None, :]
            for name, sc in m.scores(rc[i:i + 128], rp[i:i + 128], exclude=excl).items():
                res[f"loo_{name}"].append(sc.cpu().numpy())
        print(f"[{v}] scoring {a.test_game} ...", flush=True)
        for idx, fr in recording_frames(a.test_recording):
            c, p = embed_all(emb, fr, v)
            for name, sc in m.scores(c, p).items():
                res[f"test_{name}"].append(sc.cpu().numpy())
            if gate:
                for name, sc in mg.scores(c, p).items():
                    res[f"gtest_{name}"].append(sc.cpu().numpy())
                res["test_rsim"].append(reject_similarity(p, jp).cpu().numpy())
                res["test_flat"].append(flatness(fr))
            res["test_idx"].append(idx)
        extra = {"ref_flat": rflat, "ref_keep": keep, "bg_flat": bflat} if gate else {}
        np.savez(out, weights=m.weights.cpu().numpy(), **extra, **{k: np.concatenate(x) for k, x in res.items()})
        print(f"[{v}] saved {out.name}", flush=True)
        del rc, rp, bc, bp, m
        if gate:
            del mg, jc, jp, krc, krp
        torch.cuda.empty_cache()


def detect(raw: np.ndarray, med: np.ndarray, mad: np.ndarray):
    """Label = shell of the best-scoring reference (raw scores share one scale across shells);
    strength = that shell's score standardised against background footage."""
    lab = raw.argmax(1)
    z = (raw[np.arange(len(raw)), lab] - med[lab]) / mad[lab]
    return lab, z


def evaluate(bg, loo, lrs, test, drop, tidx, truth, key, shells, sidx):
    """Labels, spans and counts for one configuration. drop: samples the reject gate removes (or None)."""
    med, mad = robust_z(bg)
    # Threshold fixed before the test game is looked at: the strength at which LOO_RECALL of the
    # library's frames are found, labelled right, in the other games. Not from background footage:
    # booth and drone shots run all game but only a few are cut, so "background" is full of them.
    llab, lz = detect(loo, med, mad)
    thr = float(np.percentile(lz[llab == lrs], 100 - LOO_RECALL))
    lab, tz = detect(test, med, mad)
    if drop is not None:
        tz = np.where(drop, -np.inf, tz)
    labels = np.where(tz > thr, lab, -1)
    ps = spans(labels, tidx, MIN_SPAN)
    zs = (test - med) / mad
    if drop is not None:
        zs = np.where(drop[:, None], -np.inf, zs)
    aps = {s: average_precision(zs[:, sidx[s]], truth == sidx[s])
           for s in sorted({k["shell"] for k in key}) if s in sidx}
    in_key = truth >= 0
    res = {
        "threshold_z": thr,
        "loo_top1": float((loo.argmax(1) == lrs).mean()),
        "sample_label_acc": float((lab[in_key] == truth[in_key]).mean()),
        "frame_ap": aps,
        "mean_ap": float(np.nanmean(list(aps.values()))),
        "events": event_metrics(ps, key, shells),
        "spans": len(ps),
        "flagged_samples": int((labels >= 0).sum()),
        "pred_spans": [[shells[l], s, e] for l, s, e in ps],
        "sweep": [],
    }
    # the same counts over a range of thresholds, to show the trade-off rather than one point on it
    for q in (99, 95, 90, 80, 70, 50):
        t_ = float(np.percentile(lz[llab == lrs], 100 - q))
        ev_ = event_metrics(spans(np.where(tz > t_, lab, -1), tidx, MIN_SPAN), key, shells)
        res["sweep"].append({"loo_recall": q, "threshold_z": t_, **{
            g: {x: sum(e[x] for s, e in ev_.items() if (s in CAMERA) == (g == "camera"))
                for x in ("found", "missed", "false_alarms")} for g in ("graphics", "camera")}})
    return res


def cmd_report(a):
    cache = Path(a.cache)
    meta = json.loads((cache / f"key-{a.test_game}.json").read_text(encoding="utf-8"))
    shells, key, rs = meta["shells"], meta["key"], np.array(meta["ref_shell"])
    sidx = {s: i for i, s in enumerate(shells)}
    results = {"test_game": a.test_game, "shells": shells, "key": key,
               "no_reference": sorted({k["shell"] for k in key if k["shell"] not in sidx}), "variants": {}}
    for v in a.variants:
        z_ = np.load(cache / f"scores-{a.test_game}-{v}.npz")
        tidx = z_["test_idx"]
        truth = np.full(len(tidx), -1)
        for k in key:
            if k["shell"] in sidx:
                truth[(tidx >= k["start"]) & (tidx <= k["end"])] = sidx[k["shell"]]
        vres = {}
        configs = [(name, name, None) for name in METHODS]
        if "test_rsim" in z_.files:
            keep = z_["ref_keep"]
            tau = float(np.percentile(z_["ref_rsim"][keep], REJECT_KEEP))
            flat = z_["test_flat"] >= FLAT_T
            rejected = flat | (z_["test_rsim"] > tau)
            results.setdefault("gate", {})[v] = {
                "reject_tau": tau, "flat_samples": int(flat.sum()), "rejected_samples": int(rejected.sum()),
                "key_samples_rejected": int(rejected[truth >= 0].sum()), "key_samples": int((truth >= 0).sum()),
                "bg_rejected_share": float(((z_["bg_flat"] >= FLAT_T) | (z_["bg_rsim"] > tau)).mean())}
            for name in METHODS:
                configs += [(f"{name}+flat", f"g:{name}", flat), (f"{name}+flat+reject", f"g:{name}", rejected)]
        for label, src, drop in configs:
            g = src.startswith("g:")
            name = src[2:] if g else src
            pre = "g" if g else ""
            bg, loo, test = z_[f"{pre}bg_{name}"], z_[f"{pre}loo_{name}"], z_[f"{pre}test_{name}"]
            lrs = rs[z_["ref_keep"]] if g else rs
            vres[label] = evaluate(bg, loo, lrs, test, drop, tidx, truth, key, shells, sidx)
            ev = vres[label]["events"]
            for grp in ("graphics", "camera"):
                f_, m_, fa = (sum(e[x] for s, e in ev.items() if (s in CAMERA) == (grp == "camera"))
                              for x in ("found", "missed", "false_alarms"))
                print(f"{v:4s} {label:20s} {grp:8s} found={f_} missed={m_} false-alarms={fa}", flush=True)
            print(f"{v:4s} {label:20s} loo-top1={vres[label]['loo_top1']:.2f} "
                  f"label-acc={vres[label]['sample_label_acc']:.2f} "
                  f"meanAP={vres[label]['mean_ap']:.2f} (thr z={vres[label]['threshold_z']:.1f})", flush=True)
        results["variants"][v] = vres
        results["samples"] = int(len(tidx))
    out = cache / f"eval-{a.test_game}.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"results: {out}")


# Candidates for the app (docs/DETECTION.md, "Interfaces between detection and the app")
CAMERA_WHAT = {"Booth": "booth", "Scenic Comp": "scenic", "Scenic Droneshow": "scenic",
               "Kelce Close Up": "closeup", "On Field": "other"}
# shells that look alike and were confused on WK04 / WK06: a candidate labelled with one lists the others
LOOKALIKES = [{"Promo", "NBA Promo", "Next Week Promo"}, {"Match-Up", "Next Week Promo"}]
RUNNER_UP = 0.97        # also list a shell whose mean raw score over the span is within 3% of the label's


def cmd_candidates(a):
    """<game>.candidates.json from saved scores: patch matcher, the given variant, with the reject gate."""
    cache = Path(a.cache)
    meta = json.loads((cache / f"key-{a.test_game}.json").read_text(encoding="utf-8"))
    shells, rs = meta["shells"], np.array(meta["ref_shell"])
    z_ = np.load(cache / f"scores-{a.test_game}-{a.variant}.npz")
    if "test_rsim" not in z_.files:
        raise SystemExit("scores were made without --reject-manifest; rerun score with the gate")
    tidx = z_["test_idx"]
    keep = z_["ref_keep"]
    tau = float(np.percentile(z_["ref_rsim"][keep], REJECT_KEEP))
    rejected = (z_["test_flat"] >= FLAT_T) | (z_["test_rsim"] > tau)
    bg, loo, test = z_["gbg_patch"], z_["gloo_patch"], z_["gtest_patch"]
    med, mad = robust_z(bg)
    llab, lz = detect(loo, med, mad)
    thr = float(np.percentile(lz[llab == rs[keep]], 100 - LOO_RECALL))
    lab, tz = detect(test, med, mad)
    tz = np.where(rejected, -np.inf, tz)
    labels = np.where(tz > thr, lab, -1)
    items = []
    for l, s, e in spans(labels, tidx, MIN_SPAN):
        m = (tidx >= s) & (tidx <= e) & (labels == l)
        name = shells[l]
        mean_raw = test[m].mean(0)
        alts = {x for g in LOOKALIKES if name in g for x in g} - {name}
        alts |= {shells[j] for j in np.where(mean_raw >= RUNNER_UP * mean_raw[l])[0] if j != l}
        items.append({"in_frame": int(s), "out_frame": int(e), "what": CAMERA_WHAT.get(name, "shell"),
                      "label": name, "alternatives": sorted(alts), "whose": "ours",
                      "strength": round(float(tz[m].max()), 2), "samples": int(m.sum())})
    out = {"version": 1, "source": "shell_eval", "recording": a.recording or meta.get("recording", ""),
           "items": items,
           "evidence": {"matcher": "patch", "variant": a.variant, "gate": "flat + reject set",
                        "threshold_z": round(thr, 2), "reject_tau": round(tau, 3), "min_span_samples": MIN_SPAN,
                        "note": "in/out are the first and last keyframe samples of the span (about 1.1 s apart "
                                "on the 1080p copies), not refined to the cut"}}
    path = cache / f"{a.test_game}.candidates.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    whats = defaultdict(int)
    for it in items:
        whats[it["what"]] += 1
    print(f"{len(items)} candidates ({dict(whats)}), threshold z {thr:.1f}: {path}")


def cmd_colour(a):
    """Cosine between clip frames and the exact recording frames they were cut from, per variant."""
    clips = load_library(a.library)
    emb = Embedder()
    res = {}
    for pair in a.pair:
        game, rest = pair.split("=", 1)
        rec, base = rest.rsplit("@", 1)
        base = int(base)
        p0 = first_pts(rec)
        cs, rs_ = [], []
        for c in clips:
            if c["game"] != game or Path(c["path"]).name in set(a.not_in_feed):
                continue
            tc, n = probe_clip(c["path"])
            if tc is None or (";" not in tc and base) or (";" in tc and not base):
                continue                    # only clips on this recording's timecode base
            js = [int(n * p) for p in (0.25, 0.5, 0.75)]
            cs.append(clip_frames(c["path"], js))
            rs_.append(frames_at(rec, p0, [tc_frames(tc) - base + j for j in js]))
        cf, rf = np.concatenate(cs), np.concatenate(rs_)
        # distractors: other moments of the same recording
        distract = []
        for idx, fr in recording_frames(rec, every=40):
            distract.append(fr)
        df = np.concatenate(distract)[:400]
        res[game] = {"pairs": len(cf)}
        for v in VARIANTS:
            cc, cp = embed_all(emb, cf, v)
            rc, rp = embed_all(emb, rf, v)
            dc, dp = embed_all(emb, df, v)
            whole = (cc.float() * rc.float()).sum(-1)
            patch = (cp.float() * rp.float()).sum(-1).mean(-1)
            # retrieval: is the true frame the best match among itself and 400 other moments?
            wd = cc.float() @ dc.float().T
            pd = torch.einsum("bld,rld->br", cp.float(), dp.float()) / cp.shape[1]
            res[game][v] = {
                "whole_cos_median": float(whole.median()), "patch_cos_median": float(patch.median()),
                "whole_top1": float((whole > wd.max(1).values).float().mean()),
                "patch_top1": float((patch > pd.max(1).values).float().mean()),
                "whole_margin_median": float((whole - wd.max(1).values).median()),
                "patch_margin_median": float((patch - pd.max(1).values).median()),
            }
            print(game, v, json.dumps(res[game][v]), flush=True)
    out = Path(a.cache) / "colour.json"
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"results: {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("score")
    e.add_argument("--library", required=True)
    e.add_argument("--test-game", required=True)
    e.add_argument("--test-recording", required=True)
    e.add_argument("--test-base", type=int, default=0, help="recording's timecode base in frames")
    e.add_argument("--bg-game", required=True)
    e.add_argument("--bg-recording", required=True)
    e.add_argument("--bg-base", type=int, default=0)
    e.add_argument("--bg-every", type=int, default=5, help="use every Nth background keyframe")
    e.add_argument("--not-in-feed", nargs="*", default=[], help="test clips not cut from the recording")
    e.add_argument("--cache", required=True)
    e.add_argument("--variants", nargs="*", default=list(VARIANTS))
    e.add_argument("--reject-manifest", help="reject set manifest (e.g. scripts/reject_wk04.json): adds the gate")
    e.add_argument("--recordings", help="folder holding the manifest's recording")
    r = sub.add_parser("report")
    r.add_argument("--test-game", required=True)
    r.add_argument("--cache", required=True)
    r.add_argument("--variants", nargs="*", default=list(VARIANTS))
    c = sub.add_parser("colour")
    c.add_argument("--library", required=True)
    c.add_argument("--pair", action="append", required=True, help="GAME=RECORDING@BASE")
    c.add_argument("--not-in-feed", nargs="*", default=[])
    c.add_argument("--cache", required=True)
    k = sub.add_parser("candidates", help="write <game>.candidates.json (patch matcher with the reject gate)")
    k.add_argument("--test-game", required=True)
    k.add_argument("--cache", required=True)
    k.add_argument("--variant", default="eq")
    k.add_argument("--recording", help="recording name to put in the file")
    a = ap.parse_args()
    {"score": cmd_score, "report": cmd_report, "colour": cmd_colour, "candidates": cmd_candidates}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
