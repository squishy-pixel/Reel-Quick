"""Time the stages of the proxy pipeline on a short slice of the real file.

Each pipeline reads the same seconds of the source and throws the result
away, so the numbers compare like with like. Projected minutes are for the
whole file at that pipeline's speed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .color import active_lut, sdr_filter
from .ffmpeg import FfmpegError, capabilities, encoder_works, fmt_seconds, run_ffmpeg
from .probe import MediaInfo


@dataclass
class BenchResult:
    name: str
    fps: float | None
    error: str | None = None

    def line(self, info: MediaInfo) -> str:
        if self.fps is None:
            first = (self.error or "failed").strip().splitlines()
            why = first[-1] if first else "failed"
            return f"{self.name:<34} failed: {why[:90]}"
        speed = self.fps / float(info.rate.fps)
        minutes = info.duration / speed / 60
        return f"{self.name:<34} {self.fps:7.1f} fps  {speed:5.1f}x realtime  whole file ~{minutes:5.0f} min"


NVENC = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "8M", "-maxrate", "8M", "-bf", "2"]


def pipelines(info: MediaInfo, seconds: float, at: float) -> list[tuple[str, list[str]]]:
    caps = capabilities()
    src = info.path
    v = f"0:{info.video_stream_index}"
    seek = ["-ss", fmt_seconds(at)]
    dur = ["-t", fmt_seconds(seconds)]
    null = ["-f", "null", "-"]
    sdr = sdr_filter(info, caps)
    lut = active_lut()
    lut_name = "LUT" if lut else "built-in SDR"
    cuda = ["-hwaccel", "cuda"] if caps.cuda_decode else []
    cuda_frames = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"] if caps.cuda_decode else []
    nvenc = encoder_works("h264_nvenc")
    enc = NVENC if nvenc else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    enc_name = "NVENC" if nvenc else "libx264"

    runs: list[tuple[str, list[str]]] = [
        ("decode only, CPU", [*seek, "-i", src, *dur, "-map", v, *null]),
    ]
    if caps.cuda_decode:
        runs.append(("decode only, CUDA", [*seek, *cuda, "-i", src, *dur, "-map", v, *null]))
        runs.append(("decode CUDA + scale_cuda + NVENC (ceiling)",
                     [*seek, *cuda_frames, "-i", src, *dur, "-map", v, "-vf", "scale_cuda=format=nv12", *enc, *null]))
    runs += [
        (f"CPU decode + {lut_name} 1080p", [*seek, "-i", src, *dur, "-map", v, "-vf", sdr, *null]),
        (f"CPU decode + {lut_name} 720p", [*seek, "-i", src, *dur, "-map", v, "-vf", f"scale=-2:720,{sdr}", *null]),
        (f"current proxy: {'CUDA' if cuda else 'CPU'} dec + {lut_name} + {enc_name}",
         [*seek, *cuda, "-i", src, *dur, "-map", v, "-vf", sdr, *enc, *null]),
        (f"proxy at 720p: {'CUDA' if cuda else 'CPU'} dec + {lut_name} + {enc_name}",
         [*seek, *cuda, "-i", src, *dur, "-map", v, "-vf", f"scale=-2:720,{sdr}", *enc, *null]),
    ]
    if caps.has_filter("libplacebo"):
        placebo = ("libplacebo=w=1920:h=1080:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
                   ":range=tv:format=yuv420p:tonemapping=auto")
        runs.append((f"GPU tonemap (libplacebo) + {enc_name}",
                     ["-init_hw_device", "vulkan", *seek, *cuda, "-i", src, *dur, "-map", v, "-vf", placebo, *enc, *null]))
    return runs


def run_bench(info: MediaInfo, seconds: float = 30.0, at: float | None = None,
              only: str | None = None) -> list[BenchResult]:
    if at is None:
        at = min(3600.0, max(0.0, info.duration / 2))
    frames = round(seconds * float(info.rate.fps))
    results = []
    for name, args in pipelines(info, seconds, at):
        if only and only.lower() not in name.lower():
            continue
        t0 = time.time()
        try:
            run_ffmpeg(args)
            dt = time.time() - t0
            results.append(BenchResult(name, frames / dt if dt > 0 else None))
        except FfmpegError as e:
            results.append(BenchResult(name, None, e.stderr or str(e)))
    return results
