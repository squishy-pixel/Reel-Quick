import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reelquick.server import create_app
from reelquick.workspace import Session


@pytest.fixture
def client(tmp_path, synthetic_mxf):
    app = create_app(Session(work_root=tmp_path / "work"))
    with TestClient(app) as c:
        r = c.post("/api/open", json={"path": str(synthetic_mxf)})
        assert r.status_code == 200, r.text
        yield c


def _wait(client, job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = next(j for j in client.get("/api/jobs").json() if j["id"] == job_id)
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise TimeoutError(job_id)


def test_open_and_state(client):
    s = client.get("/api/state").json()
    assert s["project"]["info"]["is_intra"] is True
    assert s["workspace"]["has_proxy"] is False and s["workspace"]["has_filmstrip"] is False
    assert s["project"]["settings"]["log_offset_seconds"] == -10800
    assert s["project"]["settings"]["audio_tracks"] == [7, 8]      # eight mono tracks in the synthetic file
    assert client.post("/api/open", json={"path": "/nope.mxf"}).status_code == 404


def test_requires_open_file(tmp_path):
    with TestClient(create_app(Session(work_root=tmp_path))) as c:
        assert c.get("/api/state").json()["project"] is None
        assert c.post("/api/clips", json={"in_frame": 0, "out_frame": 10}).status_code == 409


def test_game_clips_and_naming(client):
    client.put("/api/game", json={"date": "261008", "week": "WK06", "away": "TB", "home": "DAL"})
    r = client.post("/api/clips", json={"in_frame": 100, "out_frame": 160, "shell": "LowerThird", "desc": "Herbie pick"})
    assert r.status_code == 200
    clip = r.json()["clip"]
    assert clip["name"] == "261008_WK06-TBvDAL_01_LowerThird_Herbie-pick"
    assert clip["formats"] == ["prores", "mp4"]
    r2 = client.post("/api/clips", json={"in_frame": 10, "out_frame": 40, "desc": "earlier"})
    names = [c["name"] for c in r2.json()["project"]["clips"]]
    assert names[0].endswith("_01_earlier") and names[1].endswith("_02_LowerThird_Herbie-pick")
    cid = clip["id"]
    r3 = client.patch(f"/api/clips/{cid}", json={"still_frame": 120, "formats": ["copy"], "name": "custom-name"})
    assert r3.json()["clip"]["name"] == "custom-name" and r3.json()["clip"]["still_frame"] == 120
    assert client.patch(f"/api/clips/{cid}", json={"out_frame": 50}).status_code == 400
    assert client.delete(f"/api/clips/{cid}").status_code == 200
    assert len(client.get("/api/state").json()["project"]["clips"]) == 1


def test_undo_redo_walks_project_edits(client):
    assert client.get("/api/state").json()["history"] == {"undo": None, "redo": None, "seq": 0}
    assert client.post("/api/undo").status_code == 409
    cid = client.post("/api/clips", json={"in_frame": 100, "out_frame": 160, "desc": "one"}).json()["clip"]["id"]
    client.patch(f"/api/clips/{cid}", json={"in_frame": 90})
    client.post("/api/markers", json={"frame": 30, "description": "by hand"})
    s = client.get("/api/state").json()
    assert s["history"]["undo"] == "add marker" and s["project"]["clips"][0]["in_frame"] == 90
    r = client.post("/api/undo").json()
    assert r["label"] == "add marker" and r["project"]["markers"] == []
    r = client.post("/api/undo").json()
    assert r["label"] == "clip in point" and r["project"]["clips"][0]["in_frame"] == 100
    # seq counts edits only: three so far, and undo did not move it, so the UI can tell edits from undos
    assert r["history"] == {"undo": "add clip", "redo": "clip in point", "seq": 3}
    r = client.post("/api/redo").json()
    assert r["project"]["clips"][0]["in_frame"] == 90
    r = client.post("/api/undo").json(); r = client.post("/api/undo").json()
    assert r["label"] == "add clip" and r["project"]["clips"] == [] and r["history"]["undo"] is None
    # a fresh edit forgets the redo branch, and the project file on disk follows every step
    client.post("/api/clips", json={"in_frame": 5, "out_frame": 50})
    assert client.get("/api/state").json()["history"]["redo"] is None
    assert client.post("/api/redo").status_code == 409
    # an out point before the in point is refused before anything is remembered
    cid = client.get("/api/state").json()["project"]["clips"][0]["id"]
    assert client.patch(f"/api/clips/{cid}", json={"in_frame": 70}).status_code == 400
    assert client.get("/api/state").json()["history"]["undo"] == "add clip"


def test_finished_proxy_outranks_one_still_building(client):
    root = Path(client.get("/api/state").json()["workspace"]["root"])
    (root / "proxy").mkdir()
    playlist = root / "proxy" / "index.m3u8"
    (root / "proxy" / "seg0.m4s").write_bytes(b"x")
    playlist.write_text("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg0.m4s\n")
    ws = client.get("/api/state").json()["workspace"]
    assert ws["proxy_url"] == "/media/proxy/index.m3u8" and ws["proxy_building"] and not ws["proxy_complete"]
    assert ws["proxy_available_seconds"] == 4.0
    # an older finished MP4 beside it keeps playing until the playlist is whole
    (root / "proxy.mp4").write_bytes(b"old proxy")
    ws = client.get("/api/state").json()["workspace"]
    assert ws["proxy_url"] == "/media/proxy.mp4" and ws["proxy_complete"] and ws["proxy_building"]
    assert ws["proxy_available_seconds"] == pytest.approx(client.get("/api/state").json()["project"]["info"]["duration"])
    playlist.write_text(playlist.read_text() + "#EXT-X-ENDLIST\n")
    ws = client.get("/api/state").json()["workspace"]
    assert ws["proxy_url"] == "/media/proxy/index.m3u8" and ws["proxy_complete"] and not ws["proxy_building"]


def test_markers_import_uses_project_offset(client):
    csv = "Time,Type,Shell,Description\n6:25:02 PM,AR,LowerThird,eastern log\n15:25:02,booth,,already pacific\n"
    r = client.post("/api/markers/import", json={"csv": csv})
    assert r.json()["added"] == 1 and len(r.json()["skipped"]) == 1
    assert r.json()["project"]["markers"][0]["tc"] == "15:25:02;00"
    r = client.post("/api/markers/import", json={"csv": csv, "offset_seconds": 0})
    assert r.json()["added"] == 1 and r.json()["project"]["markers"][0]["description"] == "already pacific"
    r = client.post("/api/markers", json={"frame": 30, "description": "by hand"})
    assert r.json()["marker"]["source"] == "user"
    assert len(r.json()["project"]["markers"]) == 2


def test_filmstrip_proxy_render_jobs(client, tmp_path):
    job = client.post("/api/jobs/filmstrip", json={"interval": 2, "width": 160, "workers": 2}).json()
    assert _wait(client, job["id"])["status"] == "done"
    idx = client.get("/api/filmstrip").json()
    assert idx["count"] == 3
    assert client.get(f"/strip/{idx['frames'][0]['file']}").status_code == 200

    job = client.post("/api/jobs/proxy", json={"height": 360}).json()
    done = _wait(client, job["id"])
    assert done["status"] == "done", done
    wsd = client.get("/api/state").json()["workspace"]
    assert wsd["has_proxy"] is True and wsd["proxy_url"] == "/media/proxy/index.m3u8" and wsd["proxy_complete"] is True
    assert abs(wsd["proxy_available_seconds"] - 6.0) < 0.6
    assert 0.0 <= wsd["proxy_start_offset"] < 0.05
    pl = client.get("/media/proxy/index.m3u8")
    assert pl.status_code == 200 and pl.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    seg = next(l for l in pl.text.splitlines() if l.endswith(".m4s"))
    assert client.get(f"/media/proxy/{seg}").status_code == 200
    assert client.get("/media/proxy/init.mp4").headers["content-type"].startswith("video/mp4")
    r = client.get("/media/proxy.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["Content-Range"].startswith("bytes 0-99/")
    assert client.get("/media/proxy.mp4").status_code == 200

    client.post("/api/clips", json={"in_frame": 60, "out_frame": 90, "desc": "one", "formats": ["copy", "mp4"]})
    client.post("/api/clips", json={"in_frame": 200, "out_frame": 230, "desc": "two", "formats": ["copy"]})
    seen = []
    job = client.post("/api/jobs/render", json={}).json()
    while True:
        j = next(x for x in client.get("/api/jobs").json() if x["id"] == job["id"])
        seen.append(j["progress"])
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    assert j["result"]["rendered"] == 2 and j["result"]["failed"] == []
    rows = {r["name"]: r for r in j["detail"]["clips"]}
    assert all(r["status"] == "done" and r["progress"] == 1.0 for r in rows.values())
    assert any(r["outputs"].get("still", "").endswith(".png") for r in rows.values())
    assert seen == sorted(seen) and seen[-1] == 1.0
    project = client.get("/api/state").json()["project"]
    assert all(c["rendered"] for c in project["clips"])
    name = project["clips"][0]["name"]
    # filed by content type: a clip with no shell or camera goes in Unsorted/
    assert client.get(f"/clips/Unsorted/{name}.mov").status_code == 200
    # an encoded traversal reaches the clips route unnormalised and must be refused
    r = client.get("/clips/..%2Fproject.reelquick.json")
    assert r.status_code == 404
    r = client.get("/clips/%2E%2E/project.reelquick.json")
    assert r.status_code == 404 or "application/json" not in r.headers.get("content-type", "")


def test_manual_still_and_frame_image(client):
    r = client.post("/api/stills", json={"frame": 5, "name": "fireworks-wide"})
    assert r.status_code == 200 and r.json()["path"].endswith("fireworks-wide.png")
    assert client.post("/api/stills", json={"frame": 99999, "name": "x"}).status_code == 400
    r = client.get("/api/frame/10?width=320")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get("/api/frame/99999").status_code == 404


def test_fs_listing(client, tmp_path, synthetic_mxf):
    r = client.get("/api/fs", params={"path": str(synthetic_mxf.parent)})
    names = [e["name"] for e in r.json()["entries"]]
    assert synthetic_mxf.name in names
    assert client.get("/api/fs", params={"path": str(tmp_path / "missing")}).status_code == 404


def test_extra_stills_and_audio_preview(client):
    r = client.post("/api/clips", json={"in_frame": 60, "out_frame": 120, "desc": "multi", "formats": ["copy"]})
    cid = r.json()["clip"]["id"]
    r = client.patch(f"/api/clips/{cid}", json={"still_frame": 70, "extra_stills": [90, 70, 500]})
    assert r.json()["clip"]["extra_stills"] == [90]               # duplicate of the primary dropped, 500 is outside the clip
    job = client.post("/api/jobs/render", json={"clip_ids": [cid]}).json()
    done = _wait(client, job["id"])
    row = done["detail"]["clips"][0]
    assert row["outputs"]["still"].endswith(".png") and len(row["outputs"]["extra_stills"]) == 1
    assert row["outputs"]["extra_stills"][0].endswith("_02.png")
    r = client.post("/api/audio-preview", json={"at_frame": 30, "seconds": 1})
    assert r.status_code == 200 and len(r.json()["tracks"]) == 8
    assert client.get(r.json()["tracks"][0]["url"]).status_code == 200
    assert client.get(r.json()["tracks"][0]["wave"]).headers["content-type"] == "image/png"
    state = client.get("/api/state").json()
    assert "/.cache/" not in state["workspace"]["clips_dir"]


def test_offline_source_opens_from_work_folder_and_renders_from_proxy(tmp_path, synthetic_mxf):
    import shutil
    work = tmp_path / "work"
    copy = tmp_path / "media" / synthetic_mxf.name
    copy.parent.mkdir()
    shutil.copy(synthetic_mxf, copy)
    with TestClient(create_app(Session(work_root=work))) as c:
        assert c.post("/api/open", json={"path": str(copy)}).status_code == 200
        job = c.post("/api/jobs/proxy", json={"height": 360}).json()
        assert _wait(c, job["id"])["status"] == "done"
        c.post("/api/clips", json={"in_frame": 60, "out_frame": 90, "desc": "offline", "formats": ["copy", "mp4"]})
        c.patch(f"/api/clips/{c.get('/api/state').json()['project']['clips'][0]['id']}", json={"still_frame": 70})
    copy.unlink()                                   # the drive went home
    with TestClient(create_app(Session(work_root=work))) as c:
        recent = c.get("/api/recent").json()
        assert len(recent) == 1 and recent[0]["source_online"] is False and recent[0]["has_proxy"] is True
        r = c.post("/api/open", json={"path": recent[0]["root"]})
        assert r.status_code == 200 and r.json()["workspace"]["source_online"] is False
        assert c.get("/api/frame/10?width=160").status_code == 200          # served from the proxy
        assert c.post("/api/jobs/render", json={}).status_code == 409        # refuses silently falling back
        job = c.post("/api/jobs/render", json={"from_proxy": True}).json()
        done = _wait(c, job["id"])
        assert done["status"] == "done" and done["result"]["origin"] == "proxy", done
        outs = done["detail"]["clips"][0]["outputs"]
        assert outs["copy"].endswith("_fromproxy.mov") and outs["mp4"].endswith("_fromproxy.mp4") and outs["still"].endswith("_fromproxy.png")
        # the 'copy' request became ProRes because the proxy is long-GOP
        from reelquick.probe import probe as _probe
        assert _probe(outs["copy"]).codec == "prores" and _probe(outs["copy"]).color_transfer == "bt709"
        assert c.post("/api/audio-preview", json={"at_frame": 1, "seconds": 1}).status_code == 409


def test_one_off_export_takes_its_own_formats_and_quality(client):
    from reelquick.probe import probe
    r = client.post("/api/clips", json={"in_frame": 60, "out_frame": 90, "desc": "mishap", "formats": ["prores", "mp4"]})
    cid = r.json()["clip"]["id"]
    # just a Slack MP4 for the engineers: no ProRes, no still
    job = client.post("/api/jobs/render", json={"clip_ids": [cid], "formats": ["mp4"], "mp4_quality": "slack",
                                                "stills": False}).json()
    done = _wait(client, job["id"])
    assert done["status"] == "done", done
    outs = done["detail"]["clips"][0]["outputs"]
    assert set(outs) == {"mp4_slack", "origin"}, outs
    assert outs["mp4_slack"].endswith("_slack.mp4")
    slack = probe(outs["mp4_slack"])
    assert slack.height == 720 and slack.color_transfer == "bt709"
    clip = next(c for c in client.get("/api/state").json()["project"]["clips"] if c["id"] == cid)
    assert clip["formats"] == ["prores", "mp4"]                  # the clip keeps its own formats
    # the full render afterwards still makes the review MP4: the Slack file does not stand in for it
    done = _wait(client, client.post("/api/jobs/render", json={"clip_ids": [cid]}).json()["id"])
    outs = done["detail"]["clips"][0]["outputs"]
    assert outs["mp4"].endswith("mishap.mp4") and outs["mp4"] != clip["rendered"]["mp4_slack"]
    assert probe(outs["mp4"]).height == 1080 and outs["prores"].endswith(".mov") and "still" in outs
    # nonsense is refused before anything runs
    assert client.post("/api/jobs/render", json={"formats": ["gif"]}).status_code == 400
    assert client.post("/api/jobs/render", json={"formats": []}).status_code == 400
    assert client.post("/api/jobs/render", json={"mp4_quality": "huge"}).status_code == 400


def test_prefs_persist_in_the_work_root(client):
    assert client.get("/api/state").json()["prefs"] == {"shortcuts": {}, "ui": {}, "output_root": ""}
    r = client.put("/api/prefs", json={"shortcuts": {"clear_marks": "Ctrl+Shift+X"}, "nonsense": 1})
    assert r.json() == {"shortcuts": {"clear_marks": "Ctrl+Shift+X"}, "ui": {}, "output_root": ""}
    # ui settings merge key by key, so the window can save one without resending the rest
    client.put("/api/prefs", json={"ui": {"thumbW": 200}})
    assert client.put("/api/prefs", json={"ui": {"quickExport": {"formats": ["mp4"]}}}).json()["ui"] == {
        "thumbW": 200, "quickExport": {"formats": ["mp4"]}}
    root = Path(client.get("/api/state").json()["work_root"])
    assert (root / "prefs.json").is_file()
    assert client.get("/api/state").json()["prefs"]["shortcuts"] == {"clear_marks": "Ctrl+Shift+X"}
    # a bad type is ignored rather than stored
    assert client.put("/api/prefs", json={"shortcuts": ["x"]}).json()["shortcuts"] == {"clear_marks": "Ctrl+Shift+X"}


def test_phases_set_by_hand_name_the_clips_and_survive_an_import(client):
    info = client.get("/api/state").json()["project"]["info"]
    cid = client.post("/api/clips", json={"in_frame": 30, "out_frame": 60, "shell": "Match-Up"}).json()["clip"]["id"]
    later = client.post("/api/clips", json={"in_frame": 300, "out_frame": 330, "shell": "Booth"}).json()["clip"]["id"]
    clips = {c["id"]: c for c in client.get("/api/state").json()["project"]["clips"]}
    assert "_Pre_" not in clips[cid]["name"] and "__" not in clips[cid]["name"]   # no boundaries yet: no phase
    # kickoff at frame 200: the first clip is pregame, the second is game
    r = client.put("/api/phases", json={"values": {"kickoff": 200}})
    assert r.status_code == 200
    clips = {c["id"]: c for c in r.json()["project"]["clips"]}
    assert clips[cid]["name"].endswith("01_Pre_Match-Up") and clips[later]["name"].endswith("02_Game_Booth")
    assert r.json()["project"]["phases"]["kickoff"] == {"frame": 200, "source": "user"}
    # a clip can override its phase by hand
    r = client.patch(f"/api/clips/{later}", json={"phase": "half"})
    assert r.json()["clip"]["name"].endswith("02_Half_Booth")
    assert client.patch(f"/api/clips/{later}", json={"phase": "lunch"}).status_code == 400
    # the detector's file fills the gaps but never moves what was set by hand
    data = {"version": 1, "source": "scorebug", "nb_frames": info["nb_frames"], "kickoff": 150, "half_start": 250,
            "second_half_kickoff": 280, "game_end": None}
    r = client.post("/api/phases/import", json={"data": data}).json()
    assert r["kept"] == ["kickoff"] and set(r["changed"]) == {"half_start", "second_half_kickoff"}
    ph = r["project"]["phases"]
    assert ph["kickoff"] == {"frame": 200, "source": "user"} and ph["half_start"] == {"frame": 250, "source": "scorebug"}
    # a file for another recording is refused; one undo takes the import back
    bad = {**data, "nb_frames": info["nb_frames"] * 3}
    assert client.post("/api/phases/import", json={"data": bad}).status_code == 409
    assert client.post("/api/undo").json()["label"] == "import phases"
    assert "half_start" not in client.get("/api/state").json()["project"]["phases"] or \
        client.get("/api/state").json()["project"]["phases"]["half_start"]["frame"] is None
    assert client.put("/api/phases", json={"values": {"kickoff": 10 ** 9}}).status_code == 400
    assert client.put("/api/phases", json={"values": {"lunch": 5}}).status_code == 400


def test_outputs_are_filed_by_type_with_other_content_apart(client, tmp_path):
    a = client.post("/api/clips", json={"in_frame": 30, "out_frame": 60, "shell": "Player Card", "formats": ["copy"]}).json()["clip"]["id"]
    b = client.post("/api/clips", json={"in_frame": 100, "out_frame": 130, "shell": "Standings", "formats": ["copy"]}).json()["clip"]["id"]
    client.patch(f"/api/clips/{b}", json={"whose": "nbc", "desc": "network standings"})
    assert client.patch(f"/api/clips/{b}", json={"whose": "fox"}).status_code == 400
    # the app-wide output root: this game gets a folder of its own under it
    root = tmp_path / "deliveries"
    client.put("/api/game", json={"date": "261008", "week": "WK06", "away": "TB", "home": "DAL"})
    client.put("/api/prefs", json={"output_root": str(root)})
    est = client.post("/api/render/estimate", json={}).json()
    assert est["out_dir"] == str(root / "261008_WK06-TBvDAL") and est["bytes"] > 0 and est["fits"]
    done = _wait(client, client.post("/api/jobs/render", json={}).json()["id"])
    outs = {r["id"]: r["outputs"] for r in done["detail"]["clips"]}
    game = root / "261008_WK06-TBvDAL"
    assert Path(outs[a]["copy"]).parent == game / "Player Card"
    assert Path(outs[a]["still"]).parent == game / "Player Card" / "stills"
    assert Path(outs[b]["copy"]).parent == game / "Other Content"
    # the estimate is in the right range: a 30-frame copy plus a still is a few MB, not GB
    assert 1e5 < est["bytes"] / 2 < 5e7


def test_render_refuses_when_the_destination_is_too_full(client, monkeypatch):
    import reelquick.server as srv
    client.post("/api/clips", json={"in_frame": 30, "out_frame": 60, "formats": ["prores"]})
    monkeypatch.setattr(srv, "free_space", lambda p: (Path(p), 1000))
    est = client.post("/api/render/estimate", json={}).json()
    assert est["free"] == 1000 and not est["fits"]
    r = client.post("/api/jobs/render", json={})
    assert r.status_code == 507 and "not enough space" in r.json()["detail"]
    # the user can still insist
    job = client.post("/api/jobs/render", json={"force": True}).json()
    assert _wait(client, job["id"])["status"] == "done"


def test_a_downloaded_game_plays_without_a_proxy(tmp_path):
    """An H.264 MP4 (a game downloaded to study, say) goes to the player as it is, so marking can start at
    once; the broadcast MXF still needs its proxy. Stream copy is flagged as unavailable on long-GOP files."""
    from reelquick.ffmpeg import ffmpeg_bin
    from reelquick.server import create_app
    from reelquick.workspace import Session
    mp4 = tmp_path / "mnf.mp4"
    subprocess.run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60000/1001",
                    "-f", "lavfi", "-i", "sine=r=48000", "-t", "3", "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                    "-g", "60", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(mp4)], check=True)
    c = TestClient(create_app(Session(tmp_path / "work")))
    ws = c.post("/api/open", json={"path": str(mp4)}).json()["workspace"]
    assert ws["proxy_url"] == "/media/source" and ws["playing_source"] and ws["proxy_complete"]
    assert ws["stream_copy_ok"] is False and abs(ws["proxy_available_seconds"] - 3.0) < 0.1
    r = c.get("/media/source", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    # a proxy, once built, takes over from the source
    job = c.post("/api/jobs/proxy", json={"height": 180}).json()
    assert _wait(c, job["id"])["status"] == "done"
    ws = c.get("/api/state").json()["workspace"]
    assert ws["proxy_url"] == "/media/proxy/index.m3u8" and not ws["playing_source"]


def test_the_broadcast_mxf_still_needs_a_proxy(client):
    ws = client.get("/api/state").json()["workspace"]
    assert ws["proxy_url"] is None and not ws["playing_source"] and ws["stream_copy_ok"] is True
    assert client.get("/media/source").status_code == 404


def _cands(*items):
    return {"version": 1, "source": "shell_eval", "recording": "g.mxf", "items": [
        {"in_frame": a, "out_frame": b, "what": w, "label": l, "alternatives": alt, "whose": "ours", "strength": s, "samples": 3}
        for a, b, w, l, alt, s in items]}


def test_candidates_import_review_and_undo(client):
    data = _cands((30, 90, "shell", "Next Week Promo", ["Match-Up", "Promo"], 9.7),
                  (120, 180, "booth", "Booth", [], 12.0),
                  (200, 260, "scenic", "Scenic Comp", [], 6.1))
    seq0 = client.get("/api/state").json()["history"]["seq"]
    # a bad file is refused before anything is remembered
    assert client.post("/api/candidates/import", json={"data": {"version": 1, "items": [{"in_frame": 5}]}}).status_code == 400
    assert client.post("/api/candidates/import", json={"data": _cands((10, 10 ** 9, "shell", "X", [], 1))}).status_code == 400
    assert client.get("/api/state").json()["history"]["seq"] == seq0
    r = client.post("/api/candidates/import", json={"data": data}).json()
    assert r["added"] == 3 and [c["status"] for c in r["project"]["candidates"]] == ["pending"] * 3
    promo, booth, scenic = r["project"]["candidates"]
    # accept the promo as its look-alike Match-Up; accept the booth shot; dismiss the scenic
    r = client.post(f"/api/candidates/{promo['id']}/accept", json={"label": "Match-Up"}).json()
    assert r["clip"]["shell"] == "Match-Up" and r["clip"]["in_frame"] == 30 and r["clip"]["out_frame"] == 90
    assert r["clip"]["whose"] == "ours"
    r = client.post(f"/api/candidates/{booth['id']}/accept", json={}).json()
    assert r["clip"]["shell"] == "Booth" and r["clip"]["camera"] == "Booth"
    assert client.post(f"/api/candidates/{booth['id']}/accept", json={}).status_code == 409
    client.patch(f"/api/candidates/{scenic['id']}", json={"status": "dismissed"})
    st = client.get("/api/state").json()["project"]
    assert {c["id"]: c["status"] for c in st["candidates"]} == {promo["id"]: "accepted", booth["id"]: "accepted", scenic["id"]: "dismissed"}
    assert len(st["clips"]) == 2
    # importing the same file again keeps every decision and adds nothing
    r = client.post("/api/candidates/import", json={"data": data}).json()
    assert r["added"] == 0 and r["already_decided"] == 3 and len(r["project"]["candidates"]) == 3
    # undo takes back the import, the dismissal, then the booth acceptance with its clip
    for label in ("import candidates", "dismiss candidate", "accept candidate"):
        assert client.post("/api/undo").json()["label"] == label
    st = client.get("/api/state").json()["project"]
    assert len(st["clips"]) == 1 and next(c for c in st["candidates"] if c["id"] == booth["id"])["status"] == "pending"
    # deleting the clip made from a candidate puts the candidate back to pending
    clip_id = st["clips"][0]["id"]
    st = client.delete(f"/api/clips/{clip_id}").json()["project"]
    assert next(c for c in st["candidates"] if c["id"] == promo["id"])["status"] == "pending"
