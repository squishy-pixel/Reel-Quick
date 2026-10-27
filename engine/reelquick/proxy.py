"""Render a lightweight MP4 of the whole program for scrubbing and for the iPad.

Defaults to SDR H.264 because that plays everywhere: the desktop app's own
player, Safari on an iPad, a phone someone shares it to. Keyframes every second
keep seeking snappy. The same file doubles as the plain reference copy of the
show, so it is worth making it look decent.

One ffmpeg process does not spread the LUT work across a big CPU, so the
render can be split into N frame-exact segments encoded at once and joined
afterwards. Segments carry PCM audio so the join is sample exact; the audio
is encoded to AAC once, in the join step, which reads only the segments.
"""

from __future__ import annotations

import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .audio import audio_graph, select_tracks
from .color import HLG_TAG_ARGS, SDR_TAG_ARGS, sdr_filter
from .ffmpeg import FfmpegError, ProgressFn, capabilities, encoder_works, fmt_seconds, run_ffmpeg
from .probe import MediaInfo
from .timecode import frames_to_seconds


@dataclass
class ProxySettings:
    height: int = 1080
    bitrate: str = "8M"
    sdr: bool = True
    hevc: bool = False          # HEVC halves the size but plays in fewer places
    audio_tracks: tuple[int, ...] | None = None
    prefer_gpu: bool = True          # NVENC for encoding when it works
    hw_decode: bool = False          # CUDA decode; off by default, it measured slower than CPU on 4:2:2 intra
    keyint_seconds: float = 1.0
    jobs: int = 1                    # concurrent segment encoders


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


def _video_chain(info: MediaInfo, settings: ProxySettings, encoder: str) -> tuple[str, list[str]]:
    caps = capabilities()
    parts: list[str] = []
    if settings.height and settings.height < info.height:
        parts.append(f"scale=-2:{settings.height}")
    if settings.sdr:
        parts.append(sdr_filter(info, caps))
        tags = SDR_TAG_ARGS
    else:
        pix = "p010le" if encoder.endswith("nvenc") else "yuv420p10le"
        parts.append(f"format={pix}")
        tags = HLG_TAG_ARGS if info.is_hlg else []
    return ",".join(parts), tags


def _encode_args(info: MediaInfo, settings: ProxySettings, start_frame: int, n_frames: int,
                 out_path: Path, audio_codec: list[str], hwdecode: bool, final: bool) -> list[str]:
    """ffmpeg args for one span of the file. final=True adds the whole-file MP4 trimmings."""
    encoder, enc_args = pick_encoder(settings)
    rate = info.rate
    keyint = max(1, round(settings.keyint_seconds * float(rate.fps)))
    vf, tag_args = _video_chain(info, settings, encoder)
    tracks = select_tracks(info, settings.audio_tracks)
    a_graph, a_map = audio_graph(info, tracks)
    graph = f"[0:{info.video_stream_index}]{vf}[v]" + (";" + a_graph if a_graph else "")

    args: list[str] = []
    if hwdecode:
        args += ["-hwaccel", "cuda"]
    if start_frame:
        args += ["-ss", fmt_seconds(frames_to_seconds(start_frame, rate))]
    args += ["-i", info.path]
    if n_frames < info.nb_frames - start_frame:
        args += ["-t", fmt_seconds(frames_to_seconds(n_frames, rate))]
    args += ["-filter_complex", graph, "-map", "[v]", *a_map]
    args += ["-frames:v", str(n_frames)]
    args += ["-c:v", encoder, *enc_args, "-g", str(keyint), "-keyint_min", str(keyint), *tag_args]
    args += ["-video_track_timescale", str(rate.num)]
    if tracks:
        args += audio_codec
    if final:
        if info.start_timecode:
            args += ["-timecode", info.start_timecode]
        args += ["-movflags", "+faststart"]
    args += [str(out_path)]
    return args


def _segments(nb_frames: int, jobs: int, keyint: int) -> list[tuple[int, int]]:
    """Split frames into `jobs` spans whose starts fall on keyframe multiples."""
    per = -(-nb_frames // jobs)
    per = max(keyint, -(-per // keyint) * keyint)
    spans = []
    start = 0
    while start < nb_frames:
        n = min(per, nb_frames - start)
        spans.append((start, n))
        start += n
    return spans


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
    hwdecode = bool(settings.hw_decode and caps.cuda_decode and info.codec in ("h264", "hevc"))

    if settings.jobs <= 1:
        aac = ["-c:a", "aac", "-b:a", "192k"]
        attempts = [hwdecode, False] if hwdecode else [False]
        last: FfmpegError | None = None
        for hw in attempts:
            try:
                run_ffmpeg(_encode_args(info, settings, 0, info.nb_frames, out_path, aac, hw, final=True),
                           duration=info.duration, on_progress=on_progress, cancel=cancel)
                return out_path
            except FfmpegError as e:
                last = e
                if cancel is not None and cancel.is_set():
                    raise
        assert last is not None
        raise last

    return _build_segmented(info, out_path, settings, hwdecode, on_progress, cancel)


def _build_segmented(info: MediaInfo, out_path: Path, settings: ProxySettings, hwdecode: bool,
                     on_progress: ProgressFn | None, cancel: threading.Event | None) -> Path:
    keyint = max(1, round(settings.keyint_seconds * float(info.rate.fps)))
    spans = _segments(info.nb_frames, settings.jobs, keyint)
    cancel = cancel or threading.Event()        # so Ctrl+C can stop every worker
    total = info.nb_frames
    done = [0.0] * len(spans)
    lock = threading.Lock()
    join_share = 0.04           # the join step is a few percent of the whole

    def report() -> None:
        if on_progress:
            frac = sum(d * n for d, (_, n) in zip(done, spans)) / total * (1 - join_share)
            on_progress(frac, {"stage": "segments"})

    with tempfile.TemporaryDirectory(prefix="reelquick-proxy-", dir=out_path.parent) as td:
        tmp = Path(td)
        seg_paths = [tmp / f"seg{i:03d}.mov" for i in range(len(spans))]
        pcm = ["-c:a", "pcm_s16le"]

        def work(i: int) -> None:
            start, n = spans[i]
            dur = float(frames_to_seconds(n, info.rate))

            def prog(frac: float, _fields: dict) -> None:
                with lock:
                    done[i] = frac
                    report()

            run_ffmpeg(_encode_args(info, settings, start, n, seg_paths[i], pcm, hwdecode, final=False),
                       duration=dur, on_progress=prog, cancel=cancel)
            with lock:
                done[i] = 1.0
                report()

        pool = ThreadPoolExecutor(max_workers=settings.jobs)
        try:
            list(pool.map(work, range(len(spans))))     # re-raises the first failure
        except BaseException:
            cancel.set()                                 # workers' ffmpeg processes exit on the next progress tick
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        listing = tmp / "segments.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_paths), encoding="utf-8")
        join = ["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0:v", "-c:v", "copy"]
        if info.audio and (settings.audio_tracks is None or settings.audio_tracks):
            join += ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]
        if info.start_timecode:
            join += ["-timecode", info.start_timecode]
        join += ["-movflags", "+faststart", str(out_path)]

        def join_prog(frac: float, _fields: dict) -> None:
            if on_progress:
                on_progress((1 - join_share) + join_share * frac, {"stage": "join"})

        run_ffmpeg(join, duration=info.duration, on_progress=join_prog, cancel=cancel)
    return out_path
