"""Render a lightweight MP4 of the whole program for scrubbing and for the iPad.

Defaults to SDR H.264 because that plays everywhere: the desktop app's own
player, Safari on an iPad, a phone someone shares it to. Keyframes every second
keep seeking snappy. The same file doubles as the plain reference copy of the
show, so it is worth making it look decent.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .audio import audio_graph, select_tracks
from .color import HLG_TAG_ARGS, SDR_TAG_ARGS, sdr_filter
from .ffmpeg import FfmpegError, ProgressFn, capabilities, encoder_works, run_ffmpeg
from .probe import MediaInfo


@dataclass
class ProxySettings:
    height: int = 1080
    bitrate: str = "8M"
    sdr: bool = True
    hevc: bool = False          # HEVC halves the size but plays in fewer places
    audio_tracks: tuple[int, ...] | None = None
    prefer_gpu: bool = True
    keyint_seconds: float = 1.0


def pick_encoder(settings: ProxySettings) -> tuple[str, list[str]]:
    caps = capabilities()
    if settings.hevc:
        if settings.prefer_gpu and encoder_works("hevc_nvenc"):
            return "hevc_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "26", "-b:v", settings.bitrate,
                                  "-maxrate", settings.bitrate, "-bf", "2", "-tag:v", "hvc1"]
        if caps.has_encoder("libx265"):
            return "libx265", ["-preset", "fast", "-crf", "24", "-tag:v", "hvc1"]
    if settings.prefer_gpu and encoder_works("h264_nvenc"):
        return "h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", settings.bitrate,
                              "-maxrate", settings.bitrate, "-bf", "2"]
    if caps.has_encoder("libx264"):
        return "libx264", ["-preset", "veryfast", "-crf", "21", "-maxrate", settings.bitrate,
                           "-bufsize", settings.bitrate]
    raise FfmpegError(["proxy"], -1, "no usable H.264 encoder in this ffmpeg build")


def build_proxy_args(info: MediaInfo, out_path: Path, settings: ProxySettings, hwdecode: bool) -> list[str]:
    caps = capabilities()
    encoder, enc_args = pick_encoder(settings)
    rate = info.rate
    keyint = max(1, round(settings.keyint_seconds * float(rate.fps)))

    vf_parts: list[str] = []
    if settings.height and settings.height < info.height:
        vf_parts.append(f"scale=-2:{settings.height}")
    if settings.sdr:
        vf_parts.append(sdr_filter(info, caps))
        tag_args = SDR_TAG_ARGS
    else:
        pix = "p010le" if encoder.endswith("nvenc") else "yuv420p10le"
        vf_parts.append(f"format={pix}")
        tag_args = HLG_TAG_ARGS if info.is_hlg else []

    tracks = select_tracks(info, settings.audio_tracks)
    a_graph, a_map = audio_graph(info, tracks)
    graph = f"[0:{info.video_stream_index}]{','.join(vf_parts)}[v]"
    if a_graph:
        graph += ";" + a_graph

    args: list[str] = []
    if hwdecode:
        args += ["-hwaccel", "cuda"]
    args += ["-i", info.path, "-filter_complex", graph, "-map", "[v]"]
    args += a_map
    args += ["-c:v", encoder, *enc_args, "-g", str(keyint), "-keyint_min", str(keyint), *tag_args]
    if tracks:
        args += ["-c:a", "aac", "-b:a", "192k"]
    if info.start_timecode:
        args += ["-timecode", info.start_timecode]
    args += ["-movflags", "+faststart", str(out_path)]
    return args


def build_proxy(
    info: MediaInfo,
    out_path: str | Path,
    settings: ProxySettings | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    settings = settings or ProxySettings()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    caps = capabilities()
    attempts = []
    if settings.prefer_gpu and caps.cuda_decode and info.codec in ("h264", "hevc"):
        attempts.append(True)
    attempts.append(False)
    last: FfmpegError | None = None
    for hwdecode in attempts:
        try:
            run_ffmpeg(build_proxy_args(info, out_path, settings, hwdecode), duration=info.duration,
                       on_progress=on_progress, cancel=cancel)
            return out_path
        except FfmpegError as e:
            last = e
            if cancel is not None and cancel.is_set():
                raise
            # A GPU decode failure (unsupported 4:2:2 on older cards) falls back to CPU.
            continue
    assert last is not None
    raise last
