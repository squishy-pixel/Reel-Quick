from reelquick.markers import ZONE_OFFSETS, import_log_csv
from reelquick.naming import GameInfo
from reelquick.probe import MediaInfo
from reelquick.project import Project

# The sheet as pasted from Google Sheets: tab separated, section rows, blanks, untimed halftime rows.
SHEET = """Time\tTime of Game\tType of Graphic\tCamera
PREGAME\t\t\t
18:03\t\tNFC Standings\tJib
\t\t\t
18:23\t\tCustom\tJib
18:57\t\tWho ya got?\tJib
\t\t\t
1ST HALF\t\t\t
19:08\t\tBooth\tBooth
19:10\t\tMatchup\tDrone
19:42\t\tQuarters\tDrone
20:23\t\tWingstop\tJib
20:35\t\tBooth\tBooth
HALF TIME\t\t\t
\t\tScore\tDrone
\t\tPrime Stories\tSkyCam
2ND HALF\t\t\t
21:37\tEnd of third\tQuarters\tDrone
\t\t\t
POSTGAME\t\t\t
"""


def game_info() -> MediaInfo:
    # TB at DAL return feed: 15:25:00;04 to 21:22:34;59 Pacific, 5.96 hours
    return MediaInfo(path="game.mxf", size_bytes=0, duration=5.96 * 3600, container="mxf", video_stream_index=0,
                     codec="h264", profile="High 4:2:2 Intra", width=1920, height=1080, pix_fmt="yuv422p10le",
                     bit_depth=10, rate_num=60000, rate_den=1001, is_intra=True, color_primaries="bt2020",
                     color_transfer="arib-std-b67", color_space="bt2020nc", start_timecode="15:25:00;04", audio=[])


def test_dallas_log_in_central_time():
    info = game_info()
    res = import_log_csv(SHEET, info, ZONE_OFFSETS["CT"])
    assert res.columns["shell"] == 2 and res.columns["camera"] == 3 and res.columns["gameclock"] == 1
    assert [m.shell for m in res.markers] == ["NFC Standings", "Custom", "Who ya got?", "Booth", "Matchup", "Quarters", "Wingstop", "Booth", "Quarters"]
    first = res.markers[0]
    assert first.tc == "16:03:00;04" and first.camera == "Jib" and first.kind == "AR" and first.section == "PREGAME"
    booth = res.markers[3]
    assert booth.tc == "17:08:00;04" and booth.kind == "booth" and booth.section == "1ST HALF"
    last = res.markers[-1]
    assert last.description == "End of third" and last.section == "2ND HALF" and last.tc == "19:37:00;04"
    assert [m.shell for m in res.untimed] == ["Score", "Prime Stories"]
    assert all(m.section == "HALF TIME" and m.frame is None for m in res.untimed)
    assert res.skipped == [] and not res.all_outside


def test_wrong_zone_is_flagged():
    info = game_info()
    res = import_log_csv(SHEET, info, ZONE_OFFSETS["ET"])     # read as Eastern, 18:03 lands before the file starts
    assert len(res.skipped) == 2 and "18:03" in res.skipped[0] and len(res.markers) == 7
    res_far = import_log_csv(SHEET, info, 6 * 3600)             # six hours out: everything misses
    assert res_far.all_outside and len(res_far.skipped) == 9


NEW_SHEET = """Time,Zone,Segment,Shell,Engine,Camera,Notes,Aired
18:03:12,CT,Pregame,NFC Standings,Vizrt,Jib,,Yes
19:08:40,,1st Half,Booth,,Booth,Al and Kirk open,Yes
19:30:00,,1st Half,Wingstop,viz,Jib,killed for a flag,No
,,Halftime,Score,Unreal,Drone,,
21:37:05,,2nd Half,Quarters,UE,Drone,End of third,Yes
"""


def test_new_sheet_layout_declares_zone_segment_and_aired():
    info = game_info()
    res = import_log_csv(NEW_SHEET, info, ZONE_OFFSETS["ET"])   # caller guesses ET, the sheet says CT and wins
    assert [m.shell for m in res.markers] == ["NFC Standings", "Booth", "Quarters"]
    assert res.markers[0].tc == "16:03:12;00" and res.markers[0].section == "PREGAME"
    assert res.markers[1].description == "Al and Kirk open" and res.markers[1].section == "1ST HALF"
    assert res.untimed[0].shell == "Score" and res.untimed[0].section == "HALFTIME" and res.untimed[0].engine == "UE"
    assert res.markers[0].engine == "Vizrt" and res.markers[2].engine == "UE" and res.markers[1].engine == ""
    assert any("not aired" in s for s in res.skipped)


def test_vocab_round_trip(tmp_path):
    from reelquick.vocab import DEFAULT_VOCAB, load_vocab, save_vocab
    v = load_vocab(tmp_path)
    assert v["shells"] == DEFAULT_VOCAB["shells"] and "UE" in v["engines"]
    saved = save_vocab(tmp_path, {"shells": [" Player Card", "player card", "New Shell "], "bogus": ["x"]})
    assert saved["shells"] == ["Player Card", "New Shell"] and "bogus" not in saved
    assert load_vocab(tmp_path)["shells"] == ["Player Card", "New Shell"]
    assert load_vocab(tmp_path)["cameras"] == DEFAULT_VOCAB["cameras"]


def test_engine_in_name_pattern():
    info = game_info()
    proj = Project(source="game.mxf", info=info, game=GameInfo("261008", "WK06", "TB", "DAL"))
    proj.settings.name_pattern = "{date}_{game}_{seq:02d}_{shell}_{engine}_{desc}"
    clip = proj.add_clip(10, 100, shell="Score", desc="late 3rd", engine="UE")
    assert clip.name == "261008_WK06-TBvDAL_01_Score_UE_late-3rd"


def test_clip_prefill_from_nearest_marker_and_camera_in_name():
    info = game_info()
    proj = Project(source="game.mxf", info=info, game=GameInfo("261008", "WK06", "TB", "DAL"))
    proj.markers = import_log_csv(SHEET, info, ZONE_OFFSETS["CT"]).markers
    fps = 60000 / 1001
    booth = next(m for m in proj.markers if m.shell == "Booth")
    m = proj.nearest_marker(booth.frame + int(20 * fps), booth.frame + int(30 * fps))
    assert m is booth
    assert proj.nearest_marker(booth.frame - int(120 * fps), booth.frame - int(100 * fps)) is None
    clip = proj.add_clip(booth.frame + 100, booth.frame + 700, shell=m.shell, desc=m.description, camera=m.camera)
    assert clip.name == "261008_WK06-TBvDAL_01_Booth_Booth"
    proj.settings.name_pattern = "{date}_{game}_{seq:02d}_{shell}_{desc}"
    proj.renumber()
    assert clip.name == "261008_WK06-TBvDAL_01_Booth"
