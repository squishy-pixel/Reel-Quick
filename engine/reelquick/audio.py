"""Build the audio side of an ffmpeg command from a list of source tracks."""

from __future__ import annotations

from .probe import MediaInfo

LAYOUTS = {1: "mono", 2: "stereo", 4: "quad", 6: "5.1", 8: "7.1"}


def select_tracks(info: MediaInfo, tracks: tuple[int, ...] | None) -> list[int]:
    """Resolve user track numbers (1-based) to stream indexes.

    Default is the first two mono tracks when the file has mono tracks, or the
    first track when it is already stereo. Broadcast MXFs carry 16 mono tracks
    with the program stereo pair on 1 and 2.
    """
    if not info.audio:
        return []
    if tracks is None:
        if info.audio[0].channels == 1:
            return [a.stream_index for a in info.audio[:2]]
        return [info.audio[0].stream_index]
    by_number = {n + 1: a.stream_index for n, a in enumerate(info.audio)}
    missing = [t for t in tracks if t not in by_number]
    if missing:
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
