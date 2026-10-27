import json

from reelquick.filmstrip import build_filmstrip, load_filmstrip
from reelquick.markers import import_log_csv
from reelquick.naming import GameInfo, build_name, slug
from reelquick.project import Project
from reelquick.render import OutputFormat


def test_filmstrip(info, tmp_path):
    seen = []
    index = build_filmstrip(info, tmp_path / "strip", interval_seconds=1.0, width=160, workers=4,
                            on_progress=lambda f, d, t: seen.append((d, t)))
    assert index.count == 6
    assert index.frames[0]["tc"] == "15:25:00;04"
    assert index.frames[1]["frame"] == 60
    assert (tmp_path / "strip" / "000005.jpg").stat().st_size > 1000
    assert seen[-1] == (6, 6)
    assert load_filmstrip(tmp_path / "strip").count == 6


def test_markers_from_log(info):
    csv_text = (
        "Time,Type,Shell,Description\n"
        "15:25:02,AR,LowerThird,Herbie pick\n"
        "3:25:04 PM,booth,,Al and Kirk open\n"
        "15:24:59,scenic,,before the file starts\n"
        "15:25:01;30,drone,,half second in\n"
        ",,,\n"
        "soon,,,bad time\n"
    )
    res = import_log_csv(csv_text, info)
    assert [m.description for m in res.markers] == ["half second in", "Herbie pick", "Al and Kirk open"]
    assert res.markers[1].frame == 2 * 60 - 4       # 15:25:02;00 minus start 15:25:00;04
    assert res.markers[1].tc == "15:25:02;00"
    assert res.markers[1].kind == "AR" and res.markers[1].shell == "LowerThird"
    assert len(res.skipped) == 2


def test_markers_offset_and_headerless(info):
    res = import_log_csv("15:24:02,AR,Shell,late clock\n", info, offset_seconds=60)
    assert len(res.markers) == 1 and res.markers[0].tc == "15:25:02;00"


def test_naming():
    g = GameInfo(date="251023", week="WK08", away="car", home="gb")
    assert build_name(g, 7, "LowerThird", "Herbstreit pick") == "251023_WK08_CARvGB_07_LowerThird_Herbstreit-pick"
    assert build_name(g, 7, "", "Al / Kirk: open?") == "251023_WK08_CARvGB_07_Al-Kirk-open"
    assert slug('bad<>:"/\\|?*name') == "badname"


def test_project_round_trip(info, tmp_path):
    proj = Project(source=info.path, info=info, game=GameInfo("261022", "WK08", "NE", "CHI"))
    res = import_log_csv("Time,Type,Shell,Description\n15:25:02,AR,LowerThird,Herbie pick\n", info)
    proj.markers = res.markers
    clip = proj.add_clip(100, 200, shell="LowerThird", desc="Herbie pick")
    proj.add_clip(10, 50, desc="share this", fmt="mp4")
    proj.renumber()
    assert [c.name for c in proj.clips] == ["261022_WK08_NEvCHI_01_share-this", "261022_WK08_NEvCHI_02_LowerThird_Herbie-pick"]
    p = proj.save(tmp_path / "game.reelquick.json")
    again = Project.load(p)
    assert again.info == info
    assert again.clips[1].in_frame == 100 and again.markers[0].shell == "LowerThird"
    spec = again.clip_spec(again.clips[1])
    assert spec.fmt == OutputFormat.PRORES and spec.head_frames == 120 and spec.audio_tracks == (1, 2)
    assert json.loads(p.read_text())["version"] == 1
