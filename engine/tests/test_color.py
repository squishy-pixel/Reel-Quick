from pathlib import Path

import pytest

from reelquick import color, lutcheck
from reelquick.color import Lut, filter_path, set_lut, sdr_filter
from reelquick.ffmpeg import capabilities


@pytest.fixture(autouse=True)
def _no_discovered_lut():
    set_lut(None)
    yield
    set_lut(None)


def _identity_cube(path: Path, n: int = 9) -> Path:
    lines = ['TITLE "identity"', f"LUT_3D_SIZE {n}"]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                lines.append(f"{r/(n-1):.6f} {g/(n-1):.6f} {b/(n-1):.6f}")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_filter_path_escapes_windows_drive_and_quotes():
    assert filter_path(r"D:\Dev\luts\NBC HLG.cube") == r"'D\:/Dev/luts/NBC HLG.cube'"
    assert filter_path("/a/it's.cube") == r"'/a/it'\''s.cube'"


def test_builtin_conversion_puts_reference_white_on_sdr_white():
    results = lutcheck.check(None)
    builtin = results[0]
    assert builtin.white >= 225, builtin.summary          # the first chain gave 91 here
    assert builtin.luma[1.00] >= 230
    assert 40 <= builtin.luma[0.25] <= 110
    assert builtin.luma[0.25] < builtin.luma[0.50] < builtin.luma[0.75]


def test_identity_lut_round_trip_and_colon_in_path(tmp_path):
    d = tmp_path / "with:colon"
    d.mkdir()
    cube = _identity_cube(d / "id.cube")
    results = lutcheck.check(str(cube))
    narrow = next(r for r in results if r.label == "lut narrow")
    full = next(r for r in results if r.label == "lut full")
    # identity LUT leaves HLG code values alone in either convention: 75% -> 180
    assert abs(narrow.white - 180) < 2 and abs(full.white - 180) < 2
    assert lutcheck.recommend(results) in ("narrow", "full")


def test_sdr_filter_uses_lut_when_set(info, tmp_path):
    cube = _identity_cube(tmp_path / "id.cube")
    set_lut(Lut(str(cube), "narrow"))
    vf = sdr_filter(info, capabilities())
    assert "lut3d=file=" in vf and "interp=tetrahedral" in vf and vf.endswith("format=yuv420p")
    set_lut(None)
    assert "lut3d" not in sdr_filter(info, capabilities())


def test_lut_validation(tmp_path):
    with pytest.raises(FileNotFoundError):
        Lut(str(tmp_path / "missing.cube"))
    with pytest.raises(ValueError):
        Lut(str(_identity_cube(tmp_path / "x.cube")), "legal")


def test_discovery_from_env(tmp_path, monkeypatch):
    cube = _identity_cube(tmp_path / "env.cube")
    monkeypatch.setenv("REELQUICK_SDR_LUT", str(cube))
    monkeypatch.setenv("REELQUICK_SDR_LUT_RANGE", "full")
    lut = color.discover_lut()
    assert lut and lut.path == str(cube) and lut.range == "full"


def test_pick_sdr_lut_prefers_hdr_to_sdr(tmp_path):
    names = ["NBCU_SDR2HLG_DL_v1.2.cube", "NBCU_HLG2SDR_DL_v1.2.cube", "NBCU_SDR2HLG_SL_v1.2.cube"]
    files = [tmp_path / n for n in names]
    assert color.pick_sdr_lut(files).name == "NBCU_HLG2SDR_DL_v1.2.cube"
    assert color.pick_sdr_lut([tmp_path / "NBCU_SDR2HLG_DL_v1.2.cube"]) is None
    assert color.pick_sdr_lut([tmp_path / "look.cube"]).name == "look.cube"


def test_narrow_and_full_paths_differ(info, tmp_path):
    cube = _identity_cube(tmp_path / "id.cube")
    narrow = color.lut_filter(info, Lut(str(cube), "narrow"))
    full = color.lut_filter(info, Lut(str(cube), "full"))
    assert narrow.count("colorlevels") == 2 and "colorlevels" not in full
