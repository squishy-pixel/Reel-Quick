import pytest

from reelquick.timecode import (FrameRate, day_frames, frames_to_tc, parse_time_of_day,
                                tc_to_frames, is_drop_frame_string)

R5994 = FrameRate(60000, 1001)
R2997 = FrameRate(30000, 1001)
R25 = FrameRate(25, 1)


def test_parse_rate():
    assert FrameRate.parse("60000/1001") == R5994
    assert FrameRate.parse("59.94") == R5994
    assert FrameRate.parse("29.97") == R2997
    assert FrameRate.parse("25") == R25
    assert R5994.nominal == 60 and R5994.drop_frame_capable
    assert not R25.drop_frame_capable


@pytest.mark.parametrize("tc,frames", [
    ("00:00:00;00", 0),
    ("00:00:59;59", 3599),
    ("00:01:00;04", 3600),          # frames 0-3 of minute 1 are dropped
    ("00:09:59;59", 35963),
    ("00:10:00;00", 35964),         # tenth minute drops nothing
    ("01:00:00;00", 215784),        # 6 frames/10min * 6 = 35964*6
])
def test_df_5994_known_values(tc, frames):
    assert tc_to_frames(tc, R5994) == frames
    assert frames_to_tc(frames, R5994, drop=True) == tc


def test_df_2997_known_values():
    assert tc_to_frames("00:01:00;02", R2997) == 1800
    assert frames_to_tc(1800, R2997, drop=True) == "00:01:00;02"
    assert tc_to_frames("00:10:00;00", R2997) == 17982


def test_df_round_trip_everywhere():
    for f in range(0, 36000 * 3, 97):
        assert tc_to_frames(frames_to_tc(f, R5994, True), R5994) == f


def test_dropped_frame_number_is_clamped():
    assert tc_to_frames("00:01:00;00", R5994) == tc_to_frames("00:01:00;04", R5994)
    with pytest.raises(ValueError):
        tc_to_frames("00:01:00;00", R5994, clamp=False)


def test_ndf():
    assert tc_to_frames("01:00:00:00", R25) == 90000
    assert frames_to_tc(90000, R25, drop=False) == "01:00:00:00"
    assert not is_drop_frame_string("01:00:00:00")
    assert is_drop_frame_string("01:00:00;00")


def test_real_file_span():
    start = tc_to_frames("15:25:00;04", R5994)
    end = tc_to_frames("21:25:49;59", R5994)
    hours = (end - start + 1) / float(R5994.fps) / 3600
    assert abs(hours - 6.0139) < 0.001


@pytest.mark.parametrize("text,expected_tc", [
    ("19:32:05", "19:32:05;00"),
    ("7:32:05 PM", "19:32:05;00"),
    ("7:32:05pm", "19:32:05;00"),
    ("12:05:00 AM", "00:05:00;00"),
    ("19:32", "19:32:00;04"),       # :00 of a non-ten minute does not exist in DF, clamps forward
    ("19:30", "19:30:00;00"),
    ("19:32:05.5", "19:32:05;30"),
    ("19:32:05;12", "19:32:05;12"),
])
def test_parse_time_of_day(text, expected_tc):
    frames = parse_time_of_day(text, R5994)
    assert frames == tc_to_frames(expected_tc, R5994)


def test_parse_time_of_day_rejects_garbage():
    with pytest.raises(ValueError):
        parse_time_of_day("halftime", R5994)


def test_day_frames():
    assert day_frames(R5994) == tc_to_frames("23:59:59;59", R5994) + 1
    assert day_frames(R25) == 25 * 86400
