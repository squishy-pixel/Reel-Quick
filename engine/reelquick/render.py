"""Cut a clip out of the source: stream copy, ProRes 422 keeping HDR, or share MP4."""

from __future__ import annotations

from fractions import Fraction

import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .audio import audio_streams
from .color import SDR_TAG_ARGS, SDR_TAG_FILTER, sdr_filter, source_tag_args, source_tag_filter
from .ffmpeg import FfmpegError, ProgressFn, capabilities, encoder_works, fmt_seconds, run_ffmpeg
from .probe import MediaInfo
from .timecode import frames_to_seconds, frames_to_tc


class OutputFormat(str, Enum):
    COPY = "copy"        # no re-encode of video; frame accurate only on intra codecs
    PRORES = "prores"    # ProRes 422, 10-bit, color tags preserved
    MP4 = "mp4"          # H.264 for sharing, tone-mapped to SDR by default


PRORES_PROFILES = {"proxy": 0, "lt": 1, "standard": 2, "hq": 3}

# Share MP4 quality. "review" is the everyday file; "slack" is small enough to drop into a chat
# (720p, about 5 MB for ten seconds, every frame kept so a glitch can be stepped through);
# "high" is for when the MP4 is the deliverable.
MP4_QUALITIES = ("slack", "review", "high")
SLACK_HEIGHT = 720


@dataclass
class ClipSpec:
    in_frame: int                     # first frame of the content, from file start
    out_frame: int                    # one past the last frame of the content
    name: str
    fmt: OutputFormat = OutputFormat.PRORES
    head_frames: int = 0              # extra frames before in
    tail_frames: int = 0              # extra frames after out
    audio_tracks: tuple[int, ...] | None = None   # 1-based track numbers of the stereo pair
    surround_tracks: tuple[int, ...] | None = None  # six 1-based mono tracks, L R C LFE Ls Rs
    include_surround: bool = False
    prores_profile: str = "standard"
    mp4_sdr: bool = True
    mp4_height: int | None = None     # None keeps source size
    mp4_quality: str = "review"       # one of MP4_QUALITIES
    copy_container: str = "mov"       # or 'mxf'
    extra: dict = field(default_factory=dict)

    def padded(self, nb_frames: int) -> tuple[int, int]:
        start = max(0, self.in_frame - self.head_frames)
        end = min(nb_frames, self.out_frame + self.tail_frames)
        if end <= start:
            raise ValueError(f"clip {self.name!r} has no frames (in={self.in_frame}, out={self.out_frame})")
        return start, end


def output_extension(spec: ClipSpec) -> str:
    if spec.fmt == OutputFormat.MP4:
        return ".mp4"
    if spec.fmt == OutputFormat.COPY:
        return "." + spec.copy_container
    return ".mov"


def build_render_args(info: MediaInfo, spec: ClipSpec, out_path: Path, allow_hw: bool = True) -> tuple[list[str], float]:
    """Return ffmpeg args and the clip duration in seconds. allow_hw=False forces the software encoders."""
    rate = info.rate
    start, end = spec.padded(info.nb_frames)
    n = end - start
    duration = frames_to_seconds(n, rate)
    start_sec = frames_to_seconds(start, rate) + Fraction(info.video_start).limit_denominator(1_000_000)
    clip_tc = frames_to_tc(info.start_frames + start, rate, info.drop_frame)
    caps = capabilities()

    streams = audio_streams(info, spec.audio_tracks, spec.surround_tracks, spec.include_surround)
    tracks = bool(streams)
    a_graph = ";".join(g for g, _m, _t in streams if g) or None
    a_map = [x for _g, m, _t in streams for x in m]
    a_titles = [x for i, (_g, _m, t) in enumerate(streams) for x in ("-metadata:s:a:%d" % i, f"title={t}")]
    a_default = ["-disposition:a:0", "default"] if len(streams) > 1 else []

    args: list[str] = []
    vmap = f"0:{info.video_stream_index}"

    if spec.fmt == OutputFormat.COPY:
        if not info.is_intra:
            raise ValueError("stream copy is only frame accurate on intra-frame codecs; use ProRes")
        args += ["-ss", fmt_seconds(start_sec), "-i", info.path]
        args += ["-map", vmap, "-c:v", "copy"]
        if a_graph:
            args += ["-filter_complex", a_graph]
        args += a_map
        if tracks:
            args += ["-c:a", "pcm_s24le", *a_titles, *a_default]
    else:
        args += ["-ss", fmt_seconds(start_sec), "-i", info.path]
        if spec.fmt == OutputFormat.PRORES:
            vf = ",".join(p for p in ("format=yuv422p10le", source_tag_filter(info)) if p)
            graph = f"[{vmap}]{vf}[v]" + (";" + a_graph if a_graph else "")
            args += ["-filter_complex", graph, "-map", "[v]", *a_map]
            profile = PRORES_PROFILES[spec.prores_profile]
            encoder, enc_args = _prores_encoder(profile, allow_hw)
            args += ["-c:v", encoder, *enc_args]
            args += source_tag_args(info)
            if tracks:
                args += ["-c:a", "pcm_s24le", *a_titles, *a_default]
        else:
            parts: list[str] = []
            height = spec.mp4_height
            if spec.mp4_quality == "slack":
                height = min(height or SLACK_HEIGHT, SLACK_HEIGHT)
            if height and height < info.height:
                parts.append(f"scale=-2:{height}")
            # a chat preview has no idea what HLG is, so the Slack file is always SDR
            if spec.mp4_sdr or spec.mp4_quality == "slack":
                parts += [sdr_filter(info, caps), SDR_TAG_FILTER]
                tags = SDR_TAG_ARGS
                encoder, enc_args = _h264_encoder(allow_hw, spec.mp4_quality)
            else:
                parts += ["format=yuv420p10le", source_tag_filter(info)]
                tags = source_tag_args(info)
                encoder, enc_args = _hevc10_encoder()
            graph = f"[{vmap}]{','.join(p for p in parts if p)}[v]" + (";" + a_graph if a_graph else "")
            args += ["-filter_complex", graph, "-map", "[v]", *a_map]
            args += ["-c:v", encoder, *enc_args, *tags]
            if tracks:
                args += ["-c:a", "aac", "-b:a:0", "192k", *a_titles, *a_default]
                if len(streams) > 1:
                    args += ["-b:a:1", "448k"]
            args += ["-movflags", "+faststart"]

    args += ["-t", fmt_seconds(duration), "-frames:v", str(n)]
    args += ["-timecode", clip_tc]
    args += ["-metadata", f"comment=source={Path(info.path).name} tc_in={clip_tc} frames={n}"]
    args += [str(out_path)]
    return args, float(duration)


def _h264_encoder(allow_hw: bool = True, quality: str = "review") -> tuple[str, list[str]]:
    if quality not in MP4_QUALITIES:
        raise ValueError(f"mp4 quality must be one of {', '.join(MP4_QUALITIES)}")
    # slack caps the peak so the size is predictable; review and high are quality-led with no cap
    cap = ["-maxrate", "5M", "-bufsize", "10M"] if quality == "slack" else []
    if allow_hw and encoder_works("h264_nvenc"):
        cq = {"slack": "25", "review": "21", "high": "18"}[quality]
        return "h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", cq, "-b:v", "0", *cap, "-bf", "2"]
    if allow_hw and encoder_works("h264_videotoolbox"):
        rate = {"slack": ["-b:v", "4M", "-maxrate", "5M"], "review": ["-b:v", "12M", "-maxrate", "16M"],
                "high": ["-b:v", "24M", "-maxrate", "32M"]}[quality]
        return "h264_videotoolbox", [*rate, "-allow_sw", "1"]
    crf = {"slack": "23", "review": "19", "high": "16"}[quality]
    return "libx264", ["-preset", "medium", "-crf", crf, *cap]


def _prores_encoder(profile: int, allow_hw: bool = True) -> tuple[str, list[str]]:
    """prores_videotoolbox on Apple Silicon is many times faster than prores_ks; same profiles."""
    if allow_hw and encoder_works("prores_videotoolbox"):
        return "prores_videotoolbox", ["-profile:v", str(profile), "-allow_sw", "1"]
    return "prores_ks", ["-profile:v", str(profile), "-vendor", "apl0"]


def _hevc10_encoder() -> tuple[str, list[str]]:
    if encoder_works("hevc_nvenc"):
        return "hevc_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "22", "-b:v", "0", "-tag:v", "hvc1",
                              "-pix_fmt", "p010le"]
    if capabilities().has_encoder("libx265"):
        return "libx265", ["-preset", "medium", "-crf", "20", "-tag:v", "hvc1"]
    raise FfmpegError(["render"], -1, "no 10-bit HEVC encoder available for HDR MP4")


def render_clip(
    info: MediaInfo,
    spec: ClipSpec,
    out_dir: str | Path,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
    overwrite: bool = False,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (spec.name + output_extension(spec))
    if out_path.exists() and not overwrite:
        raise FileExistsError(str(out_path))
    tmp = out_path.with_name(out_path.stem + ".partial" + out_path.suffix)
    # A hardware encoder that refuses this picture (pixel format, size) is retried with the software one.
    for allow_hw in (True, False):
        args, duration = build_render_args(info, spec, out_path, allow_hw)
        args[-1] = str(tmp)
        try:
            run_ffmpeg(args, duration=duration, on_progress=on_progress, cancel=cancel)
            break
        except FfmpegError:
            tmp.unlink(missing_ok=True)
            if not allow_hw or (cancel is not None and cancel.is_set()) or not _used_hw(args):
                raise
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    tmp.replace(out_path)
    return out_path


def _used_hw(args: list[str]) -> bool:
    return any(a.endswith(("_nvenc", "_videotoolbox")) for a in args)
