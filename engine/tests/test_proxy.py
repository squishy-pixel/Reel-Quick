import subprocess

import re

from reelquick.ffmpeg import ffmpeg_bin, ffprobe_bin
from reelquick.probe import probe
from reelquick.proxy import ProxySettings, _segments, build_proxy


def test_segments_cover_everything_on_keyframe_boundaries():
    spans = _segments(1000, 4, 60)
    assert spans[0][0] == 0
    assert sum(n for _, n in spans) == 1000
    assert all(s % 60 == 0 for s, _ in spans)
    assert [s + n for s, n in spans][:-1] == [s for s, _ in spans][1:]
    assert _segments(100, 8, 60) == [(0, 60), (60, 40)]


def _audio_samples(path) -> int:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=duration_ts", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return int(out)


def test_single_and_segmented_proxy_agree(info, tmp_path):
    seen = []
    one_pl = build_proxy(info, tmp_path / "one.mp4", ProxySettings(height=360, prefer_gpu=False),
                         on_progress=lambda f, _: seen.append(f))
    many_pl = build_proxy(info, tmp_path / "many.mp4", ProxySettings(height=360, prefer_gpu=False, jobs=3),
                          on_progress=lambda f, _: seen.append(f))
    one, many = tmp_path / "one.mp4", tmp_path / "many.mp4"
    # both forms exist: a growing playlist with fMP4 segments, and the single reference MP4
    from reelquick.proxy import hls_available_seconds, hls_complete
    for pl in (one_pl, many_pl):
        assert pl.name == "index.m3u8" and (pl.parent / "init.mp4").exists()
        assert len(list(pl.parent.glob("seg*.m4s"))) >= 1
        assert hls_complete(pl) and abs(hls_available_seconds(pl) - info.duration) < 0.5
    # the playlist's first frame sits at the AAC priming offset, which the engine measures for the player
    from reelquick.proxy import video_start_offset
    off = video_start_offset(one_pl)
    assert 0.0 <= off < 0.05 and abs(video_start_offset(many_pl) - off) < 0.002
    # the reference MP4 is pulled back to a zero start, like a directly written MP4
    assert video_start_offset(one) < 0.001 and probe(one).video_start < 0.001
    assert abs(probe(one_pl).video_start - off) < 0.002
    assert seen and seen[-1] >= 0.99 and all(0 <= f <= 1 for f in seen)
    a, b = probe(one), probe(many)
    assert a.nb_frames == b.nb_frames == info.nb_frames
    assert b.codec == "h264" and b.height == 360 and b.color_transfer == "bt709"
    assert b.start_timecode == info.start_timecode
    assert b.audio[0].codec == "aac" and b.audio[0].channels == 2
    # audio length matches to within a few milliseconds of the video
    samples = _audio_samples(many)
    assert abs(samples / 48000 - info.duration) < 0.05
    # every frame lines up with the single-pass render: a frame off at a seam would
    # crater the minimum PSNR, since the test pattern moves every frame
    out = subprocess.run([ffmpeg_bin(), "-v", "info", "-i", str(many), "-i", str(one), "-lavfi", "psnr",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"average:([\d.]+) min:([\d.]+)", out)
    assert m, out[-500:]
    assert float(m.group(1)) > 35 and float(m.group(2)) > 30, m.group(0)
    assert not list(tmp_path.glob("reelquick-proxy-*"))      # temp segments cleaned up
