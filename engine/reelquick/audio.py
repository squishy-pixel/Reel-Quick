"""Build the audio side of an ffmpeg command from a list of source tracks."""

from __future__ import annotations

from .probe import MediaInfo

LAYOUTS = {1: "mono", 2: "stereo", 4: "quad", 6: "5.1", 8: "7.1"}


def default_tracks(info: MediaInfo) -> list[int]:
    """1-based default stereo choice for a file: 7,8 on a 16-track return feed, else the first tracks."""
    if not info.audio:
        return []
    if len(info.audio) >= 8 and all(a.channels == 1 for a in info.audio[:8]):
        return [7, 8]
    if info.audio[0].channels == 1:
        return [n + 1 for n in range(min(2, len(info.audio)))]
    return [1]


def select_tracks(info: MediaInfo, tracks: tuple[int, ...] | None, fallback: bool = False) -> list[int]:
    """Resolve user track numbers (1-based) to stream indexes.

    tracks=None picks the file's default pair. With fallback=True a request the
    file cannot satisfy (a 16-track preset applied to a two-track MP4) quietly
    becomes the default instead of an error.
    """
    if not info.audio:
        return []
    by_number = {n + 1: a.stream_index for n, a in enumerate(info.audio)}
    if tracks is None:
        return [by_number[t] for t in default_tracks(info)]
    missing = [t for t in tracks if t not in by_number]
    if missing:
        if fallback:
            return [by_number[t] for t in default_tracks(info)]
        raise ValueError(f"audio track(s) {missing} not in file, which has {len(info.audio)}")
    return [by_number[t] for t in tracks]


def audio_graph(info: MediaInfo, stream_indexes: list[int], label: str = "a") -> tuple[str | None, list[str]]:
    """Return (filter_complex fragment or None, map args) for the chosen tracks.

    Several mono tracks are joined into one multichannel stream. PCM in, PCM
    out is lossless, so joining never costs quality. One track maps directly.
    """
    if not stream_indexes:
        return None, []
    if len(stream_indexes) == 1:
        return None, ["-map", f"0:{stream_indexes[0]}"]
    inputs = "".join(f"[0:{i}]" for i in stream_indexes)
    channels = sum(a.channels for a in info.audio if a.stream_index in stream_indexes)
    layout = LAYOUTS.get(channels)
    layout_arg = f":channel_layout={layout}" if layout else ""
    graph = f"{inputs}join=inputs={len(stream_indexes)}{layout_arg}[{label}]"
    return graph, ["-map", f"[{label}]"]


SURROUND_ORDER = "5.1"   # ffmpeg 5.1 = FL FR FC LFE BL BR, the SMPTE order broadcast feeds use on tracks 1-6


def audio_streams(info: MediaInfo, stereo: tuple[int, ...] | None, surround: tuple[int, ...] | None,
                  include_surround: bool) -> list[tuple[str | None, list[str], str]]:
    """Output audio streams as (filter fragment or None, map args, title).

    The stereo pair comes first so every player picks it by default; the 5.1 is
    a second track for systems that can use it. Surround is skipped quietly when
    the file does not carry all six tracks.
    """
    streams: list[tuple[str | None, list[str], str]] = []
    st = select_tracks(info, stereo, fallback=True)
    if st:
        g, m = audio_graph(info, st, "ast")
        streams.append((g, m, "Stereo"))
    if include_surround and surround and len(surround) == 6:
        try:
            sidx = select_tracks(info, surround)
        except ValueError:
            sidx = []
        if len(sidx) == 6 and all(a.channels == 1 for a in info.audio if a.stream_index in sidx):
            inputs = "".join(f"[0:{i}]" for i in sidx)
            streams.append((f"{inputs}join=inputs=6:channel_layout={SURROUND_ORDER}[asur]", ["-map", "[asur]"], "5.1"))
    return streams
