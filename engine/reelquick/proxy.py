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
from .color import HLG_TAG_ARGS, HLG_TAG_FILTER, SDR_TAG_ARGS, SDR_TAG_FILTER, sdr_filter
from .ffmpeg import FfmpegError, ProgressFn, capabilities, encoder_works, fmt_seconds, run_ffmpeg
from .probe import MediaInfo
from .timecode import frames_to_seconds


@dataclass
class ProxySettings:
    height: int = 720                # the colour conversion is CPU-bound and scales with pixels: 720p is ~1.7x faster
    bitrate: str = "8M"
    sdr: bool = True
    hevc: bool = False          # HEVC halves the size but plays in fewer places
    audio_tracks: tuple[int, ...] | None = None
    prefer_gpu: bool = True          # NVENC for encoding when it works
    hw_decode: bool = False          # CUDA decode; off by default, it measured slower than CPU on 4:2:2 intra
    keyint_seconds: float = 1.0
    jobs: int = 1                    # concurrent segment encoders
    hls: bool = True                 # write a segmented playlist the browser can play while it grows
    hls_segment_seconds: float = 4.0
    also_mp4: bool = True            # keep a single reference MP4 next to it (remuxed, no second encode)


def pick_encoder(settings: ProxySettings) -> tuple[str, list[str]]:
    caps = capabilities()
    if settings.hevc:
        if settings.prefer_gpu and encoder_works("hevc_nvenc"):
            return "hevc_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "26", "-b:v", settings.bitrate,
                                  "-maxrate", settings.bitrate, "-bf", "0", "-tag:v", "hvc1"]
        if settings.prefer_gpu and encoder_works("hevc_videotoolbox"):
            return "hevc_videotoolbox", ["-b:v", settings.bitrate, "-maxrate", settings.bitrate, "-tag:v", "hvc1", "-allow_sw", "1", "-bf", "0"]
        if caps.has_encoder("libx265"):
            return "libx265", ["-preset", "fast", "-crf", "24", "-tag:v", "hvc1", "-x265-params", "bframes=0"]
    if settings.prefer_gpu and encoder_works("h264_nvenc"):
        # No B-frames anywhere in the proxy: timestamps then start at zero in the HLS segments as well as
        # the MP4, so frame N of the proxy is frame N of the source, and seeks land faster.
        return "h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", settings.bitrate,
                              "-maxrate", settings.bitrate, "-bf", "0"]
    if settings.prefer_gpu and encoder_works("h264_videotoolbox"):
        # Apple Silicon media engine: fast, and the quality knob is a bitrate.
        return "h264_videotoolbox", ["-b:v", settings.bitrate, "-maxrate", settings.bitrate, "-allow_sw", "1", "-bf", "0"]
    if caps.has_encoder("libx264"):
        return "libx264", ["-preset", "veryfast", "-crf", "21", "-maxrate", settings.bitrate,
                           "-bufsize", settings.bitrate, "-bf", "0"]
    raise FfmpegError(["proxy"], -1, "no usable H.264 encoder in this ffmpeg build")


def _video_chain(info: MediaInfo, settings: ProxySettings, encoder: str) -> tuple[str, list[str]]:
    caps = capabilities()
    parts: list[str] = []
    if settings.sdr:
        parts += [sdr_filter(info, caps, height=settings.height), SDR_TAG_FILTER]
        tags = SDR_TAG_ARGS
    else:
        if settings.height and settings.height < info.height:
            parts.append(f"scale=-2:{settings.height}")
        pix = "p010le" if encoder.endswith(("nvenc", "videotoolbox")) else "yuv420p10le"
        parts.append(f"format={pix}")
        if info.is_hlg:
            parts.append(HLG_TAG_FILTER)
        tags = HLG_TAG_ARGS if info.is_hlg else []
    return ",".join(p for p in parts if p), tags


def _hls_output_args(settings: ProxySettings, playlist: Path) -> list[str]:
    """fMP4 HLS, event playlist (appended while encoding), every segment starting on a keyframe."""
    seg_dir = playlist.parent
    return [
        "-f", "hls",
        "-hls_time", fmt_seconds(settings.hls_segment_seconds),
        "-hls_playlist_type", "event",
        "-hls_list_size", "0",
        "-hls_segment_type", "fmp4",
        "-hls_flags", "independent_segments+temp_file",
        "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(seg_dir / "seg%05d.m4s"),
        str(playlist),
    ]


def _encode_args(info: MediaInfo, settings: ProxySettings, start_frame: int, n_frames: int,
                 out_path: Path, audio_codec: list[str], hwdecode: bool, final: bool,
                 hls_playlist: Path | None = None) -> list[str]:
    """ffmpeg args for one span of the file. final=True adds the whole-file trimmings.

    With hls_playlist set the output is a growing HLS playlist instead of out_path."""
    encoder, enc_args = pick_encoder(settings)
    rate = info.rate
    keyint = max(1, round(settings.keyint_seconds * float(rate.fps)))
    vf, tag_args = _video_chain(info, settings, encoder)
    tracks = select_tracks(info, settings.audio_tracks, fallback=True)
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
    if final and info.start_timecode:
        args += ["-timecode", info.start_timecode]
    if hls_playlist is not None:
        args += _hls_output_args(settings, hls_playlist)
        return args
    if final:
        args += ["-movflags", "+faststart"]
    args += [str(out_path)]
    return args


def hls_playlist_for(mp4_path: Path) -> Path:
    """The playlist that sits beside a proxy MP4: <dir>/proxy/index.m3u8 for <dir>/proxy.mp4."""
    return mp4_path.with_suffix("") / "index.m3u8"


def remux_hls_to_mp4(playlist: Path, out_path: Path, on_progress: ProgressFn | None = None,
                     cancel: threading.Event | None = None, duration: float | None = None) -> Path:
    """The reference MP4: same packets as the playlist, timestamps pulled back so video starts at zero
    (the MP4 muxer then hides the AAC priming in an edit list, as a directly written MP4 would)."""
    tmp = out_path.with_name(out_path.stem + ".partial" + out_path.suffix)
    off = video_start_offset(playlist)
    args = ["-allowed_extensions", "ALL", "-i", str(playlist)]
    if off > 0:
        args += ["-output_ts_offset", f"-{off:.6f}"]
    args += ["-map", "0", "-c", "copy", "-movflags", "+faststart", str(tmp)]
    run_ffmpeg(args, duration=duration, on_progress=on_progress, cancel=cancel)
    tmp.replace(out_path)
    return out_path


def remux_mp4_to_hls(mp4_path: Path, playlist: Path, settings: ProxySettings, on_progress: ProgressFn | None = None,
                     cancel: threading.Event | None = None, duration: float | None = None) -> Path:
    playlist.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(mp4_path), "-map", "0:v", "-map", "0:a?", "-c", "copy", *_hls_output_args(settings, playlist)],
               duration=duration, on_progress=on_progress, cancel=cancel)
    return playlist


def hls_available_seconds(playlist: Path) -> float:
    """Seconds of program the playlist already covers; grows while the encode runs."""
    try:
        total = 0.0
        for line in playlist.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("#EXTINF:"):
                total += float(line[8:].split(",")[0])
        return total
    except (OSError, ValueError):
        return 0.0


def video_start_offset(path: Path) -> float:
    """Seconds between the container's zero and its first video frame.

    AAC priming puts the first audio packet at negative time; a plain MP4 hides
    that with an edit list, a segmented playlist shifts everything instead, so
    its first frame sits ~0.021 s in. The player subtracts this so frame N of the
    proxy is frame N of the source whichever container it is playing.
    """
    from .ffmpeg import run_ffprobe
    try:
        data = run_ffprobe(["-allowed_extensions", "ALL", "-select_streams", "v:0", "-show_entries",
                            "stream=start_time", str(path)],
                           # a playlist without #EXT-X-ENDLIST is live to ffprobe, and some builds
                           # wait on it for new segments; the status poll must never block on that
                           timeout=10)
        st = (data.get("streams") or [{}])[0].get("start_time")
        return max(0.0, float(st)) if st not in (None, "N/A") else 0.0
    except Exception:
        return 0.0


def hls_complete(playlist: Path) -> bool:
    try:
        return "#EXT-X-ENDLIST" in playlist.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


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

    aac = ["-c:a", "aac", "-b:a", "192k"]
    playlist = hls_playlist_for(out_path) if settings.hls else None
    encode_share = 0.94 if (settings.hls and settings.also_mp4) else 1.0

    def enc_progress(frac: float, d: dict) -> None:
        if on_progress:
            on_progress(frac * encode_share, {**d, "stage": "encode"})

    if settings.jobs <= 1:
        if playlist is not None:
            playlist.parent.mkdir(parents=True, exist_ok=True)
            for old in playlist.parent.glob("*"):
                old.unlink()
        attempts = [hwdecode, False] if hwdecode else [False]
        last: FfmpegError | None = None
        for hw in attempts:
            try:
                run_ffmpeg(_encode_args(info, settings, 0, info.nb_frames, out_path, aac, hw, final=True,
                                        hls_playlist=playlist),
                           duration=info.duration, on_progress=enc_progress, cancel=cancel)
                break
            except FfmpegError as e:
                last = e
                if cancel is not None and cancel.is_set():
                    raise
        else:
            assert last is not None
            raise last
        if playlist is not None and settings.also_mp4:
            remux_hls_to_mp4(playlist, out_path, duration=info.duration, cancel=cancel,
                             on_progress=lambda f, d: on_progress(encode_share + (1 - encode_share) * f, {"stage": "mp4"}) if on_progress else None)
        return playlist if playlist is not None else out_path

    _build_segmented(info, out_path, settings, hwdecode, enc_progress, cancel)
    if playlist is not None:
        remux_mp4_to_hls(out_path, playlist, settings, duration=info.duration, cancel=cancel,
                         on_progress=lambda f, d: on_progress(encode_share + (1 - encode_share) * f, {"stage": "hls"}) if on_progress else None)
        return playlist
    return out_path


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
