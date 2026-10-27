import time

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
    assert client.get("/api/state").json()["workspace"]["has_proxy"] is True
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
    assert client.get(f"/clips/{name}.mov").status_code == 200
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
