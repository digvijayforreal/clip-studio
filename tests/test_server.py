"""The web app's API, against a throwaway projects folder. No downloads, speech model or ffmpeg rendering."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from clipstudio import ai, config, server
from clipstudio import project as projects


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(server, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(ai, "SETTINGS_FILE", tmp_path / "settings.json")
    return TestClient(server.app, base_url="http://127.0.0.1:8766")


def make_project(name="vid_00m10s-00m20s"):
    p = projects.new(
        name=name, segments=[(3.0, 13.0)], language="en",
        source={"kind": "youtube", "url": "https://youtu.be/vid", "id": "vid", "title": "<b>Title</b>", "uploader": "U",
                "file": "source.mp4", "offset": 7.0, "width": 1920, "height": 1080, "duration": 16.0},
        words=[{"w": "hello", "s": 3.2, "e": 3.6, "p": 0.9}, {"w": "money", "s": 4.0, "e": 4.4, "p": 0.4}])
    projects.save(p, config.PROJECTS_DIR / name)
    return p


def test_page_and_meta(client):
    assert "ClipStudio" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/fonts/Anton-Regular.ttf").status_code == 200
    assert client.get("/capfonts/Poppins-ExtraBold.ttf").status_code == 200
    meta = client.get("/api/meta").json()
    assert {s["id"] for s in meta["styles"]} == {"bold", "clean", "pop", "boxed"}
    assert "💰" in meta["emojis"] and meta["ai_ready"] is False


def test_other_hosts_and_origins_are_refused(client):
    assert client.get("/api/meta", headers={"host": "evil.example"}).status_code == 403
    r = client.post("/api/make", json={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/make", json={}, headers={"origin": "http://localhost:8766"}).status_code == 422


def test_list_get_and_save(client):
    make_project()
    listed = client.get("/api/projects").json()
    assert [p["name"] for p in listed] == ["vid_00m10s-00m20s"] and listed[0]["has_clip"] is False
    data = client.get("/api/projects/vid_00m10s-00m20s").json()
    p = data["project"]
    p["captions"]["words"][1]["w"] = "Money!"
    p["hook"]["text"] = "BIG NEWS"
    p["source"]["file"] = "C:/Windows/win.ini"            # can't be changed from the page
    saved = client.put("/api/projects/vid_00m10s-00m20s", json=p).json()["project"]
    assert saved["captions"]["words"][1]["w"] == "Money!" and saved["hook"]["text"] == "BIG NEWS"
    assert saved["source"]["file"] == "source.mp4"


def test_save_rejects_a_broken_project(client):
    make_project()
    p = client.get("/api/projects/vid_00m10s-00m20s").json()["project"]
    p["segments"] = [{"start": 9, "end": 2}]
    r = client.put("/api/projects/vid_00m10s-00m20s", json=p)
    assert r.status_code == 422 and "after the start" in r.json()["detail"]


def test_names_cannot_escape_the_projects_folder(client):
    for bad in ("..", "..%2F..%2Fsecret", "a b", "x" * 200):
        assert client.get(f"/api/projects/{bad}").status_code == 404
    assert client.get("/api/projects/nope/media/clip").status_code == 404


def test_media_and_music_upload(client):
    make_project()
    folder = config.PROJECTS_DIR / "vid_00m10s-00m20s"
    (folder / "source.mp4").write_bytes(b"\x00" * 2000)
    r = client.get("/api/projects/vid_00m10s-00m20s/media/source", headers={"range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100              # video seeking works
    assert client.get("/api/projects/vid_00m10s-00m20s/media/clip").status_code == 404
    up = client.put("/api/projects/vid_00m10s-00m20s/music?filename=My Beat!.mp3", content=b"ID3fake")
    assert up.status_code == 200 and up.json()["project"]["music"]["file"] == "music_My-Beat.mp3"
    assert (folder / "music_My-Beat.mp3").read_bytes() == b"ID3fake"
    assert client.put("/api/projects/vid_00m10s-00m20s/music?filename=x.exe", content=b"MZ").status_code == 415
    assert client.delete("/api/projects/vid_00m10s-00m20s/music").json()["project"]["music"]["file"] is None


def test_video_upload_keeps_a_safe_name(client):
    r = client.put("/api/upload?filename=..\\..\\my stream.mp4", content=b"data")
    assert r.status_code == 200
    path = r.json()["path"]
    assert path.endswith("_my-stream.mp4") and str(server.UPLOADS_DIR) in path


def test_make_checks_its_input(client):
    assert "Paste a YouTube link" in client.post("/api/make", json={"source": ""}).json()["detail"]
    assert "isn't a link" in client.post("/api/make", json={"source": "C:/nope.mp4", "ranges": "1-2"}).json()["detail"]
    r = client.post("/api/make", json={"source": "https://youtu.be/x", "ranges": "2:00-1:00"})
    assert r.status_code == 422 and "after the start" in r.json()["detail"]
    r = client.post("/api/make", json={"source": "https://youtu.be/x", "ranges": "1-5", "style": "comic"})
    assert r.status_code == 422


def test_make_runs_as_a_job(client, monkeypatch):
    seen = {}

    def fake_make(source, ranges, **kw):
        seen.update(source=source, ranges=ranges, **kw)
        kw["log"]("working")
        kw["progress"]("Speech to text", 0.5)
        return [config.PROJECTS_DIR / "vid_00m10s-00m20s" / "clip.mp4"]
    monkeypatch.setattr(server.pipeline, "make", fake_make)
    job = client.post("/api/make", json={"source": "https://youtu.be/x", "ranges": "0:10-0:20, 1:00-1:05",
                                         "join": True, "cut_pauses": True, "hook": "HI", "emoji": False}).json()
    for _ in range(100):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert job["status"] == "done" and job["result"] == ["vid_00m10s-00m20s"] and "working" in job["log"]
    assert seen["ranges"] == [(10.0, 20.0), (60.0, 65.0)] and seen["join"] is True
    assert seen["settings"]["edit"]["cut_pauses"] is True and seen["settings"]["effects"]["emojis"] == "off"
    assert seen["settings"]["hook"]["text"] == "HI"


def test_settings_never_show_the_key(client):
    r = client.put("/api/settings", json={"ai": {"key": "gsk_abcdefghijklmnop1234", "model": "openai/gpt-oss-120b"}}).json()
    assert r["ai_ready"] is True and r["ai"]["has_key"] is True and r["ai"]["key_hint"] == "1234"
    assert "gsk_" not in json.dumps(client.get("/api/settings").json())
    assert client.put("/api/settings", json={"ai": {"model": "bad model!"}}).status_code == 422


def test_ai_buttons_need_a_key(client):
    make_project()
    assert "AI key" in client.post("/api/projects/vid_00m10s-00m20s/ai/fix").json()["detail"]
    assert client.post("/api/projects/vid_00m10s-00m20s/ai/hook").status_code == 422


def test_delete(client):
    make_project()
    assert client.delete("/api/projects/vid_00m10s-00m20s").status_code == 200
    assert client.get("/api/projects").json() == []
