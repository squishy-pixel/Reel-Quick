import subprocess

import pytest

from reelquick.ffmpeg import ffprobe_bin
from reelquick.naming import GameInfo
from reelquick.project import ClipEntry, Project
from reelquick.stills import grab_still


def _img(path):
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_name,width,height,pix_fmt,color_transfer",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()
    return out.split(",")


def test_still_png_is_sdr_full_size(info, tmp_path):
    p = grab_still(info, 100, tmp_path, "261022_WK08_NEvCHI_01_LowerThird_Herbie-pick")
    assert p.name == "261022_WK08_NEvCHI_01_LowerThird_Herbie-pick.png"
    codec, w, h, pix, *_ = _img(p)
    assert codec == "png" and (w, h) == ("1920", "1080") and pix == "rgb24"


def test_still_jpg_scaled_and_hdr_png(info, tmp_path):
    p = grab_still(info, 100, tmp_path, "small", fmt="jpg", width=640)
    codec, w, h, *_ = _img(p)
    assert codec == "mjpeg" and w == "640" and h == "360"
    p = grab_still(info, 100, tmp_path, "hdr", hdr=True)
    codec, w, h, pix, *_ = _img(p)
    assert pix == "rgb48be" or pix == "rgb48le"


def test_still_refuses_bad_frame_and_overwrite(info, tmp_path):
    with pytest.raises(ValueError):
        grab_still(info, 10_000, tmp_path, "x")
    grab_still(info, 1, tmp_path, "x")
    with pytest.raises(FileExistsError):
        grab_still(info, 1, tmp_path, "x")


def test_clip_renders_every_format_and_a_still(info, tmp_path):
    proj = Project(source=info.path, info=info, game=GameInfo("261022", "WK08", "NE", "CHI"))
    proj.settings.default_formats = ["copy", "mp4"]
    clip = proj.add_clip(60, 90, shell="LowerThird", desc="Herbie pick")
    clip.head_seconds = clip.tail_seconds = 0
    clip.still_frame = 70
    done = proj.render_clip_outputs(clip, tmp_path / "out")
    assert set(done) == {"copy", "mp4", "still"}
    assert done["copy"].endswith("261022_WK08-NEvCHI_01_LowerThird_Herbie-pick.mov")
    assert done["mp4"].endswith("261022_WK08-NEvCHI_01_LowerThird_Herbie-pick.mp4")
    assert done["still"].endswith("stills/261022_WK08-NEvCHI_01_LowerThird_Herbie-pick.png")
    assert clip.rendered == done
    assert clip.representative_frame == 70


def test_copy_and_prores_do_not_collide(info):
    proj = Project(source=info.path, info=info)
    clip = proj.add_clip(0, 10, formats=["prores", "copy", "mp4"], name="x")
    assert proj.output_name(clip, "prores") == "x"
    assert proj.output_name(clip, "copy") == "x_src"
    assert proj.output_name(clip, "mp4") == "x"


def test_manual_stills_and_v1_migration(info, tmp_path):
    proj = Project(source=info.path, info=info)
    proj.add_still(5, "fireworks-wide")
    p = proj.save(tmp_path / "p.json")
    again = Project.load(p)
    assert again.stills[0].name == "fireworks-wide"
    path = again.render_still(again.stills[0], tmp_path / "out")
    assert path.name == "fireworks-wide.png" and again.stills[0].rendered == str(path)
    # a version 1 clip with a single fmt string still loads
    old = ClipEntry.from_dict({"id": "a", "in_frame": 1, "out_frame": 2, "fmt": "mp4", "rendered": "/x.mp4"})
    assert old.formats == ["mp4"] and old.rendered == {"mp4": "/x.mp4"}
    assert ClipEntry.from_dict({"id": "a", "in_frame": 1, "out_frame": 2}).formats == ["prores"]
