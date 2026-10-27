"""Locate and run ffmpeg/ffprobe, parse progress, and detect capabilities."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Sequence

ProgressFn = Callable[[float, dict], None]


class FfmpegError(RuntimeError):
    def __init__(self, args: Sequence[str], returncode: int, stderr: str):
        self.args_list = list(args)
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        super().__init__(f"ffmpeg exited {returncode}:\n{tail}")


def _candidate_dirs() -> Iterable[Path]:
    env = os.environ.get("REELQUICK_FFMPEG_DIR")
    if env:
        yield Path(env)
    # PyInstaller bundle or a 'bin' folder next to the package / executable.
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    yield base / "bin"
    yield base
    yield Path(__file__).resolve().parent.parent / "bin"


def find_binary(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for d in _candidate_dirs():
        p = d / exe
        if p.is_file():
            return str(p)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"{name} not found. Put it next to the app, on PATH, or set REELQUICK_FFMPEG_DIR."
    )


def ffmpeg_bin() -> str:
    return find_binary("ffmpeg")


def ffprobe_bin() -> str:
    return find_binary("ffprobe")


def _popen_kwargs() -> dict:
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw


def run_ffprobe(args: Sequence[str]) -> dict:
    cmd = [ffprobe_bin(), "-v", "error", "-print_format", "json", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, **_popen_kwargs())
    if proc.returncode != 0:
        raise FfmpegError(cmd, proc.returncode, proc.stderr)
    return json.loads(proc.stdout or "{}")


_PROGRESS_LINE = re.compile(r"^(\w+)=(.*)$")


def run_ffmpeg(
    args: Sequence[str],
    duration: float | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> str:
    """Run ffmpeg with the given arguments (everything after the binary).

    duration is the expected output duration in seconds and is used to turn
    ffmpeg's out_time into a 0..1 fraction for on_progress. Returns stderr.
    """
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if on_progress is not None:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE if on_progress is not None else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **_popen_kwargs(),
    )
    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()

    try:
        if on_progress is not None:
            assert proc.stdout is not None
            current: dict = {}
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.terminate()
                    break
                m = _PROGRESS_LINE.match(line.strip())
                if not m:
                    continue
                key, value = m.groups()
                current[key] = value
                if key == "progress":
                    out_us = current.get("out_time_us") or current.get("out_time_ms")
                    frac = 0.0
                    if duration and out_us and out_us.lstrip("-").isdigit():
                        frac = max(0.0, min(1.0, int(out_us) / 1_000_000 / duration))
                    if value == "end":
                        frac = 1.0
                    on_progress(frac, dict(current))
                    current = {}
        else:
            while proc.poll() is None:
                if cancel is not None and cancel.wait(0.25):
                    proc.terminate()
                    break
        proc.wait()
    finally:
        # Ctrl+C, an exception in a progress callback, or anything else that
        # leaves this frame must not leave an ffmpeg running at full tilt.
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    t.join(timeout=5)
    stderr = "".join(stderr_chunks)
    if cancel is not None and cancel.is_set():
        raise FfmpegError(cmd, proc.returncode, "cancelled")
    if proc.returncode != 0:
        raise FfmpegError(cmd, proc.returncode, stderr)
    return stderr


def _list_lines(flag: str) -> str:
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", flag], capture_output=True, text=True, **_popen_kwargs()
    )
    return proc.stdout


@dataclass
class Capabilities:
    ffmpeg: str
    version: str
    encoders: set[str] = field(default_factory=set)
    hwaccels: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    @property
    def nvenc(self) -> bool:
        return "h264_nvenc" in self.encoders

    @property
    def cuda_decode(self) -> bool:
        return "cuda" in self.hwaccels

    @property
    def tonemap(self) -> bool:
        return "zscale" in self.filters and "tonemap" in self.filters

    def summary(self) -> dict:
        return {
            "ffmpeg": self.ffmpeg,
            "version": self.version,
            "nvenc": self.nvenc,
            "cuda_decode": self.cuda_decode,
            "tonemap": self.tonemap,
            "prores": "prores_ks" in self.encoders,
            "libx264": "libx264" in self.encoders,
            "libx265": "libx265" in self.encoders,
            "hevc_nvenc": "hevc_nvenc" in self.encoders,
        }


@lru_cache(maxsize=1)
def capabilities() -> Capabilities:
    exe = ffmpeg_bin()
    version_out = subprocess.run(
        [exe, "-version"], capture_output=True, text=True, **_popen_kwargs()
    ).stdout
    version = version_out.splitlines()[0].replace("ffmpeg version ", "") if version_out else "?"
    caps = Capabilities(ffmpeg=exe, version=version)
    for line in _list_lines("-encoders").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1] in "VAS" and len(parts[0]) == 6:
            caps.encoders.add(parts[1])
    for line in _list_lines("-hwaccels").splitlines():
        name = line.strip()
        if name and not name.startswith("Hardware"):
            caps.hwaccels.add(name)
    for line in _list_lines("-filters").splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in line:
            caps.filters.add(parts[1])
    return caps


@lru_cache(maxsize=4)
def encoder_works(encoder: str) -> bool:
    """Encode two frames of test pattern with the encoder to prove it runs here.

    NVENC shows up in the encoder list on every build, but only works when a
    supported GPU and driver are present. This is the only reliable check.
    """
    if not capabilities().has_encoder(encoder):
        return False
    try:
        run_ffmpeg(
            [
                "-f", "lavfi", "-i", "testsrc2=size=256x144:rate=30",
                "-frames:v", "2", "-c:v", encoder, "-f", "null", "-",
            ]
        )
        return True
    except (FfmpegError, FileNotFoundError):
        return False


def fmt_seconds(x) -> str:
    """Format seconds for an ffmpeg -ss/-t argument, floored to the microsecond.

    ffmpeg keeps the first packet whose timestamp is at or after -ss, for stream
    copy and transcode alike. Flooring guarantees we never land a microsecond
    past the wanted frame and lose it; -frames:v pins the count at the far end.
    """
    from fractions import Fraction
    us = int(Fraction(x) * 1_000_000)
    return f"{us // 1_000_000}.{us % 1_000_000:06d}"
