from reelquick.probe import probe


def test_probe_matches_real_feed_shape(info):
    assert info.codec == "h264"
    assert "Intra" in info.profile
    assert info.is_intra
    assert (info.width, info.height) == (1920, 1080)
    assert str(info.rate) == "60000/1001"
    assert info.pix_fmt == "yuv422p10le" and info.bit_depth == 10
    assert info.is_hlg and info.is_hdr
    assert info.start_timecode == "15:25:00;04"
    assert info.drop_frame
    assert len(info.audio) == 8 and all(a.channels == 1 for a in info.audio)
    assert info.nb_frames == round(6 * 60000 / 1001)


def test_round_trip_dict(info):
    from reelquick.probe import MediaInfo
    again = MediaInfo.from_dict(info.to_dict())
    assert again == info
