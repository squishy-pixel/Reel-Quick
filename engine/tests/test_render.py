import pytest

from conftest import decoded_frame_hashes
from reelquick.probe import probe
from reelquick.render import ClipSpec, OutputFormat, render_clip
from reelquick.timecode import frames_to_tc


@pytest.mark.parametrize("in_frame,n", [(0, 10), (1, 12), (7, 30), (61, 25), (301, 40)])
def test_stream_copy_is_frame_accurate(info, tmp_path, in_frame, n):
    spec = ClipSpec(in_frame=in_frame, out_frame=in_frame + n, name=f"copy_{in_frame}", fmt=OutputFormat.COPY,
                    audio_tracks=(1, 2))
    out = render_clip(info, spec, tmp_path)
    got = probe(out)
    assert got.nb_frames == n
    assert got.codec == "h264" and "Intra" in got.profile
    assert got.is_hlg
    assert got.start_timecode == frames_to_tc(info.start_frames + in_frame, info.rate, True)
    assert len(got.audio) == 1 and got.audio[0].channels == 2 and got.audio[0].codec == "pcm_s24le"
    assert decoded_frame_hashes(out, 0, n) == decoded_frame_hashes(info.path, in_frame, n)


def test_head_and_tail_are_applied_and_clamped(info, tmp_path):
    spec = ClipSpec(in_frame=5, out_frame=20, name="padded", fmt=OutputFormat.COPY,
                    head_frames=10, tail_frames=7)
    out = render_clip(info, spec, tmp_path)
    got = probe(out)
    assert got.nb_frames == 20 + 7           # head clamped to frame 0
    assert got.start_timecode == info.start_timecode


def test_prores_keeps_hlg_and_timecode(info, tmp_path):
    in_frame, n = 120, 30
    spec = ClipSpec(in_frame=in_frame, out_frame=in_frame + n, name="prores", fmt=OutputFormat.PRORES)
    out = render_clip(info, spec, tmp_path)
    got = probe(out)
    assert out.suffix == ".mov"
    assert got.codec == "prores" and got.profile.lower().startswith("standard")
    assert got.pix_fmt == "yuv422p10le"
    assert got.is_hlg and got.color_primaries == "bt2020"
    assert got.nb_frames == n
    assert got.start_timecode == frames_to_tc(info.start_frames + in_frame, info.rate, True)
    assert got.audio[0].channels == 2 and got.audio[0].codec == "pcm_s24le"


def test_mp4_share_is_sdr_709(info, tmp_path):
    spec = ClipSpec(in_frame=30, out_frame=60, name="share", fmt=OutputFormat.MP4, mp4_height=720)
    out = render_clip(info, spec, tmp_path)
    got = probe(out)
    assert out.suffix == ".mp4"
    assert got.codec == "h264" and got.pix_fmt == "yuv420p"
    assert got.color_transfer == "bt709" and got.color_primaries == "bt709"
    assert got.height == 720
    assert got.nb_frames == 30
    assert got.audio[0].codec == "aac"


def test_refuses_to_overwrite(info, tmp_path):
    spec = ClipSpec(in_frame=0, out_frame=5, name="dup", fmt=OutputFormat.COPY)
    render_clip(info, spec, tmp_path)
    with pytest.raises(FileExistsError):
        render_clip(info, spec, tmp_path)
    render_clip(info, spec, tmp_path, overwrite=True)


def test_single_track_and_bad_track(info, tmp_path):
    spec = ClipSpec(in_frame=0, out_frame=5, name="mono", fmt=OutputFormat.COPY, audio_tracks=(3,))
    got = probe(render_clip(info, spec, tmp_path))
    assert got.audio[0].channels == 1
    # a pair the file does not have falls back to the file's default instead of failing
    got = probe(render_clip(info, ClipSpec(in_frame=0, out_frame=5, name="bad", fmt=OutputFormat.COPY, audio_tracks=(19, 20)), tmp_path))
    assert got.audio[0].channels == 2


def test_stereo_pair_plus_surround_tracks(info, tmp_path):
    spec = ClipSpec(in_frame=0, out_frame=12, name="layers", fmt=OutputFormat.COPY, audio_tracks=(7, 8),
                    surround_tracks=(1, 2, 3, 4, 5, 6), include_surround=True)
    got = probe(render_clip(info, spec, tmp_path))
    assert [a.channels for a in got.audio] == [2, 6]
    spec = ClipSpec(in_frame=0, out_frame=12, name="layers_mp4", fmt=OutputFormat.MP4, audio_tracks=(7, 8),
                    surround_tracks=(1, 2, 3, 4, 5, 6), include_surround=True)
    got = probe(render_clip(info, spec, tmp_path))
    assert [a.channels for a in got.audio] == [2, 6] and all(a.codec == "aac" for a in got.audio)
    # a file without six surround tracks just gets the stereo
    spec = ClipSpec(in_frame=0, out_frame=12, name="nosur", fmt=OutputFormat.COPY, audio_tracks=(7, 8),
                    surround_tracks=(1, 2, 3, 4, 5, 60), include_surround=True)
    assert [a.channels for a in probe(render_clip(info, spec, tmp_path)).audio] == [2]