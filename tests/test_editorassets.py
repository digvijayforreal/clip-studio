"""The editor's helper files, made by real ffmpeg from a short generated video."""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from clipstudio import ai, config, editorassets, media, server
from clipstudio import project as projects


@pytest.fixture
def folder(tmp_path):
    media.require_tools()
    d = tmp_path / "projects" / "gen_00m01s-00m05s"
    d.mkdir(parents=True)
    # 8 s: a tone with a 2 s silence in the middle, over a moving test picture
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=8",
                    "-af", "volume=enable='between(t,3,5)':volume=0",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(d / "source.mp4")], check=True)
    p = projects.new(name=d.name, segments=[(1.0, 7.0)], language="en",
                     source={"kind": "youtube", "url": "", "id": "gen", "title": "Gen", "uploader": "", "file": "source.mp4",
                             "offset": 0.0, "width": 640, "height": 360, "duration": 8.0},
                     words=[{"w": "one", "s": 1.2, "e": 2.0}, {"w": "money", "s": 5.5, "e": 6.2}])
    projects.save(p, d)
    return d


def test_prepare_makes_every_file(folder):
    p = projects.load(folder)
    assert editorassets.status(folder, p)["ready"] is False
    st = editorassets.prepare(folder, p, log=lambda *a: None)
    assert st["ready"] is True and st["offset"] == 0.0 and st["end"] == 8.0
    proxy = media.probe(folder / "proxy.mp4")
    assert proxy["height"] == 540 and abs(proxy["duration"] - 8.0) < 0.2
    strip = st["strip"]
    assert strip["h"] == 60 and strip["w"] == 106 and strip["count"] >= 8
    wave = json.loads((folder / "waveform.json").read_text())
    assert wave["rate"] == 50 and 380 <= len(wave["peaks"]) <= 410
    silent = wave["peaks"][160:240]                         # 3.2 s to 4.8 s
    assert max(silent) < 0.05 and max(wave["peaks"]) == 1.0
    # a clip moved outside the copied stretch makes the editor prepare again
    p["segments"][0]["end"] = 7.9
    assert editorassets.status(folder, p)["ready"] is True
    editorassets.ROOM, old = 0.5, editorassets.ROOM
    try:
        assert editorassets.window(p, 60.0) == (0.5, 8.4)
    finally:
        editorassets.ROOM = old


@pytest.fixture
def client(folder, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_DIR", folder.parent)
    monkeypatch.setattr(ai, "SETTINGS_FILE", folder.parent / "settings.json")
    return TestClient(server.app, base_url="http://127.0.0.1:8766")


def test_editor_endpoints(client, folder):
    name = folder.name
    data = client.get(f"/api/projects/{name}").json()
    assert data["fps"] == 30.0 and data["editor"]["ready"] is False
    job = client.post(f"/api/projects/{name}/prepare").json()
    for _ in range(300):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        import time
        time.sleep(0.05)
    assert job["status"] == "done", job["error"]
    assert client.get(f"/api/projects/{name}").json()["editor"]["ready"] is True
    assert client.get(f"/api/projects/{name}/media/proxy").status_code == 200
    assert client.get(f"/api/projects/{name}/media/filmstrip").headers["content-type"] == "image/jpeg"
    assert "peaks" in client.get(f"/api/projects/{name}/media/waveform").json()

    p = data["project"]
    cut = client.post(f"/api/projects/{name}/pauses", json={"project": p, "min_gap": 0.7}).json()
    assert cut["cuts"] == 1 and 1.5 < cut["removed"] < 2.0      # the 2 s silence, keeping a breath each side
    assert [s["id"] for s in cut["segments"]][0] == "c1" and len({s["id"] for s in cut["segments"]}) == 2

    fx = client.post(f"/api/projects/{name}/autofx", json=p).json()
    assert fx["length"] == 6.0 and [e["emoji"] for e in fx["emojis"]] == ["💰"]

    bad = {**p, "segments": [{"start": 4, "end": 2}]}
    assert client.post(f"/api/projects/{name}/autofx", json=bad).status_code == 422


def test_emoji_endpoint(client, monkeypatch, tmp_path):
    (tmp_path / "fire_3d.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(server.effects, "emoji_file", lambda e: tmp_path / "fire_3d.png")
    assert client.get("/api/emoji/1f525").content == b"\x89PNG fake"
    assert client.get("/api/emoji/1f984").status_code == 404           # 🦄: no picture
    assert client.get("/api/emoji/zz").status_code == 404
