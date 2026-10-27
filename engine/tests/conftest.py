"""Build a small stand-in for the real recording.

Same shape as the TNF return feed: H.264 High 4:2:2 Intra, 10-bit, 1920x1080,
59.94, BT.2020 HLG, several mono 24-bit PCM tracks, drop-frame time-of-day
timecode. Only much shorter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reelquick.ffmpeg import ffmpeg_bin
from reelquick.probe import probe

START_TC = "15:25:00;04"
SECONDS = 6
AUDIO_TRACKS = 4


@pytest.fixture(scope="session")
def synthetic_mxf(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("media") / "synthetic.mxf"
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60000/1001"]
    for n in range(AUDIO_TRACKS):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={220 * (n + 1)}:sample_rate=48000"]
    cmd += ["-t", str(SECONDS), "-map", "0:v"]
    for n in range(AUDIO_TRACKS):
        cmd += ["-map", f"{n + 1}:a"]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-pix_fmt", "yuv422p10le", "-profile:v", "high422", "-g", "1",
        "-x264-params", "keyint=1:min-keyint=1:scenecut=0:bframes=0:cabac=0",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "-color_range", "tv",
        "-timecode", START_TC,
        "-c:a", "pcm_s24le",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


@pytest.fixture(scope="session")
def info(synthetic_mxf):
    return probe(synthetic_mxf)


def decoded_frame_hashes(path: Path, first: int, count: int, stream: str = "v:0") -> list[str]:
    """MD5 of each decoded frame in [first, first+count), for frame-accuracy checks."""
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", f"0:{stream}",
           "-vf", f"select='between(n,{first},{first + count - 1})'", "-vsync", "0",
           "-f", "framemd5", "-"]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [line.split(",")[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]
