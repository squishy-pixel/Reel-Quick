"""Time the stages of the proxy pipeline on a short slice of the real file.

Each pipeline reads the same seconds of the source and throws the result
away, so the numbers compare like with like. Projected minutes are for the
whole file at that pipeline's speed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .color import active_lut, sdr_filter
from .ffmpeg import FfmpegError, capabilities, encoder_works, filter_options, fmt_seconds, run_ffmpeg
from .probe import MediaInfo


@dataclass
class BenchResult:
    name: str
    fps: float | None
    error: str | None = None

    def line(self, info: MediaInfo) -> str:
        if self.fps is None:
            lines = [l.strip() for l in (self.error or "failed").strip().splitlines() if l.strip()]
            telling = [l for l in lines if not l.startswith(("Last message", "frame=", "[out#"))
                       and "Nothing was written" not in l and "Conversion failed" not in l]
            why = " | ".join(telling[-3:]) if telling else (lines[-1] if lines else "failed")
            return f"{self.name:<34} failed: {why[:300]}"
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
        (f"CPU dec + {lut_name} + {enc_name} 1080p", [*seek, "-i", src, *dur, "-map", v, "-vf", sdr, *enc, *null]),
        (f"CPU dec + {lut_name} + {enc_name} 720p",
         [*seek, "-i", src, *dur, "-map", v, "-vf", f"scale=-2:720,{sdr}", *enc, *null]),
        (f"CPU dec + {lut_name} + libx264 1080p",
         [*seek, "-i", src, *dur, "-map", v, "-vf", sdr, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", *null]),
    ]
    if cuda:
        runs += [
            (f"CUDA dec + {lut_name} + {enc_name} 1080p", [*seek, *cuda, "-i", src, *dur, "-map", v, "-vf", sdr, *enc, *null]),
        ]
    if caps.has_filter("libplacebo"):
        placebo = ("libplacebo=w=1920:h=1080:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
                   ":range=tv:format=yuv420p:tonemapping=auto")
        runs.append((f"GPU tonemap (libplacebo) + {enc_name}",
                     ["-init_hw_device", "vulkan", *seek, "-i", src, *dur, "-map", v, "-vf", placebo, *enc, *null]))
        runs += gpu_lut_pipelines(info, seconds, at)
    return runs


def gpu_lut_filter(lut_path: str, out_format: str = "yuv420p") -> str:
    """libplacebo applying a full-range conversion LUT on the GPU, no colour mapping of its own."""
    from .color import filter_path
    return (f"libplacebo=lut={filter_path(lut_path)}:lut_type=conversion"
            f":colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:format={out_format}")


def gpu_lut_pipelines(info: MediaInfo, seconds: float, at: float) -> list[tuple[str, list[str]]]:
    """Experimental rows: the baked LUT applied by libplacebo. Needs a build whose libplacebo has `lut`."""
    lut = active_lut()
    if lut is None or "lut" not in filter_options("libplacebo"):
        return []
    from .cubelut import ensure_baked
    baked = str(ensure_baked(lut.path)) if lut.range == "narrow" else lut.path
    caps = capabilities()
    src = info.path
    v = f"0:{info.video_stream_index}"
    seek = ["-ss", fmt_seconds(at)]
    dur = ["-t", fmt_seconds(seconds)]
    null = ["-f", "null", "-"]
    nvenc = encoder_works("h264_nvenc")
    enc = NVENC if nvenc else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    enc_name = "NVENC" if nvenc else "libx264"
    rows = [
        (f"GPU LUT: CPU dec -> libplacebo -> {enc_name}",
         ["-init_hw_device", "vulkan", *seek, "-i", src, *dur, "-map", v, "-vf", gpu_lut_filter(baked), *enc, *null]),
    ]
    if caps.cuda_decode and nvenc:
        rows.append((
            "GPU LUT all on GPU: NVDEC -> vulkan -> libplacebo -> cuda -> NVENC",
            ["-init_hw_device", "cuda=cu", "-init_hw_device", "vulkan=vk@cu", "-filter_hw_device", "vk",
             *seek, "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", src, *dur, "-map", v,
             "-vf", f"hwupload,{gpu_lut_filter(baked, 'nv12')},hwmap=derive_device=cuda",
             *enc, *null]))
        rows.append((
            "GPU LUT: NVDEC -> vulkan -> libplacebo -> download -> NVENC",
            ["-init_hw_device", "cuda=cu", "-init_hw_device", "vulkan=vk@cu", "-filter_hw_device", "vk",
             *seek, "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", src, *dur, "-map", v,
             "-vf", f"hwupload,{gpu_lut_filter(baked, 'nv12')},hwdownload,format=nv12",
             *enc, *null]))
    return rows


def run_bench(info: MediaInfo, seconds: float = 30.0, at: float | None = None,
              only: str | None = None) -> list[BenchResult]:
    if at is None:
        at = min(3600.0, max(0.0, info.duration / 2))
    frames = round(seconds * float(info.rate.fps))
    results = []
    for name, args in pipelines(info, seconds, at):
        if only and only.lower() not in name.lower():
            continue
        samples: list[tuple[float, int]] = []

        def on_progress(frac: float, fields: dict) -> None:
            f = fields.get("frame", "")
            if f.isdigit():
                samples.append((time.time(), int(f)))

        try:
            run_ffmpeg(args, duration=seconds, on_progress=on_progress)
        except FfmpegError as e:
            results.append(BenchResult(name, None, e.stderr or str(e)))
            continue
        # Steady-state rate between the first and last progress reports, which
        # leaves out opening and seeking into a very large file.
        if len(samples) >= 3 and samples[-1][0] > samples[0][0]:
            (ta, fa), (tb, fb) = samples[0], samples[-1]
            results.append(BenchResult(name, (fb - fa) / (tb - ta)))
        elif samples:
            results.append(BenchResult(name, None, "slice too short to measure; raise --seconds"))
        else:
            results.append(BenchResult(name, None, "no progress reported"))
    return results


def run_parallel_bench(info: MediaInfo, jobs: int, seconds: float = 30.0, at: float | None = None,
                       height: int = 1080) -> BenchResult:
    """Run `jobs` copies of the CPU decode + SDR + encode pipeline on different slices at once.

    Reports the aggregate frame rate. If it scales with jobs, a single process
    is leaving cores idle and the proxy should be rendered in segments.
    """
    import subprocess
    from .ffmpeg import ffmpeg_bin, _popen_kwargs

    caps = capabilities()
    if at is None:
        at = min(3600.0, max(0.0, info.duration / 2))
    sdr = sdr_filter(info, caps)
    if height and height < info.height:
        sdr = f"scale=-2:{height},{sdr}"
    enc = NVENC if encoder_works("h264_nvenc") else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    v = f"0:{info.video_stream_index}"
    procs = []
    t0 = time.time()
    for j in range(jobs):
        start = at + j * (seconds + 5)
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-ss", fmt_seconds(start), "-i", info.path, "-t", fmt_seconds(seconds),
               "-map", v, "-vf", sdr, *enc, "-f", "null", "-"]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, **_popen_kwargs()))
    errors = []
    for p in procs:
        _, err = p.communicate()
        if p.returncode != 0:
            errors.append(err.strip().splitlines()[-1] if err.strip() else f"exit {p.returncode}")
    dt = time.time() - t0
    frames = round(seconds * float(info.rate.fps)) * jobs
    name = f"{jobs} parallel x (CPU dec + SDR + enc {height}p)"
    if errors:
        return BenchResult(name, None, "; ".join(errors))
    return BenchResult(name, frames / dt)
