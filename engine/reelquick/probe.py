"""Describe a source file: codec, GOP structure, color, timecode, audio layout."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

from .ffmpeg import run_ffprobe
from .timecode import FrameRate, is_drop_frame_string, tc_to_frames

INTRA_CODECS = {"prores", "dnxhd", "mjpeg", "jpeg2000", "cfhd", "v210", "rawvideo", "ffv1"}
HLG_TRANSFER = "arib-std-b67"
PQ_TRANSFER = "smpte2084"


@dataclass
class AudioTrack:
    stream_index: int
    channels: int
    sample_rate: int
    codec: str
    bit_depth: int | None


@dataclass
class MediaInfo:
    path: str
    size_bytes: int
    duration: float
    container: str
    video_stream_index: int
    codec: str
    profile: str
    width: int
    height: int
    pix_fmt: str
    bit_depth: int
    rate_num: int
    rate_den: int
    is_intra: bool
    color_primaries: str | None
    color_transfer: str | None
    color_space: str | None
    start_timecode: str | None
    audio: list[AudioTrack] = field(default_factory=list)
    field_order: str = "progressive"

    @property
    def rate(self) -> FrameRate:
        return FrameRate(self.rate_num, self.rate_den)

    @property
    def drop_frame(self) -> bool:
        if self.start_timecode:
            return is_drop_frame_string(self.start_timecode)
        return self.rate.drop_frame_capable

    @property
    def start_frames(self) -> int:
        """Frame count of the first frame's timecode since 00:00:00:00."""
        if not self.start_timecode:
            return 0
        return tc_to_frames(self.start_timecode, self.rate, drop=self.drop_frame)

    @property
    def nb_frames(self) -> int:
        return round(Fraction(self.duration) * self.rate.fps)

    @property
    def interlaced(self) -> bool:
        return self.field_order not in ("progressive", "unknown", "", None)

    @property
    def is_hlg(self) -> bool:
        return self.color_transfer == HLG_TRANSFER

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in (HLG_TRANSFER, PQ_TRANSFER)

    @property
    def total_audio_channels(self) -> int:
        return sum(a.channels for a in self.audio)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rate"] = str(self.rate)
        d["fps"] = float(self.rate.fps)
        d["drop_frame"] = self.drop_frame
        d["nb_frames"] = self.nb_frames
        d["is_hlg"] = self.is_hlg
        d["is_hdr"] = self.is_hdr
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MediaInfo":
        fields = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        fields["audio"] = [AudioTrack(**a) for a in d.get("audio", [])]
        return cls(**fields)


def _bit_depth(stream: dict) -> int:
    if stream.get("bits_per_raw_sample", "").isdigit():
        return int(stream["bits_per_raw_sample"])
    pix = stream.get("pix_fmt", "")
    for depth in (16, 14, 12, 10):
        if f"p{depth}" in pix:
            return depth
    return 8


def _find_timecode(fmt: dict, streams: list[dict]) -> str | None:
    tc = (fmt.get("tags") or {}).get("timecode")
    if tc:
        return tc
    for s in streams:
        tc = (s.get("tags") or {}).get("timecode")
        if tc:
            return tc
    return None


def gop_is_intra(path: str | Path, stream_index: int, sample_frames: int = 240) -> bool:
    """Read the first packets of the video stream and check every one is a keyframe."""
    data = run_ffprobe(
        [
            "-select_streams", str(stream_index),
            "-read_intervals", f"%+#{sample_frames}",
            "-show_entries", "packet=flags",
            str(path),
        ]
    )
    packets = data.get("packets", [])
    return bool(packets) and all("K" in p.get("flags", "") for p in packets)


def probe(path: str | Path, check_gop: bool = True) -> MediaInfo:
    path = Path(path)
    data = run_ffprobe(["-show_format", "-show_streams", str(path)])
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ValueError(f"no video stream in {path}")
    rate = FrameRate.parse(video.get("r_frame_rate") or video.get("avg_frame_rate") or "30")
    profile = video.get("profile") or ""
    codec = video.get("codec_name") or ""
    is_intra = "intra" in profile.lower() or codec in INTRA_CODECS
    if not is_intra and check_gop and codec in ("h264", "hevc", "mpeg2video"):
        try:
            is_intra = gop_is_intra(path, video["index"])
        except Exception:
            is_intra = False
    audio = [
        AudioTrack(
            stream_index=s["index"],
            channels=int(s.get("channels") or 0),
            sample_rate=int(s.get("sample_rate") or 0),
            codec=s.get("codec_name") or "",
            bit_depth=int(s["bits_per_raw_sample"]) if str(s.get("bits_per_raw_sample", "")).isdigit() else None,
        )
        for s in streams
        if s.get("codec_type") == "audio"
    ]
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    return MediaInfo(
        path=str(path),
        size_bytes=int(fmt.get("size") or path.stat().st_size),
        duration=duration,
        container=fmt.get("format_name", ""),
        video_stream_index=video["index"],
        codec=codec,
        profile=profile,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        pix_fmt=video.get("pix_fmt") or "",
        bit_depth=_bit_depth(video),
        rate_num=rate.num,
        rate_den=rate.den,
        is_intra=is_intra,
        color_primaries=video.get("color_primaries"),
        color_transfer=video.get("color_transfer"),
        color_space=video.get("color_space"),
        start_timecode=_find_timecode(fmt, streams),
        audio=audio,
        field_order=video.get("field_order") or "progressive",
    )
