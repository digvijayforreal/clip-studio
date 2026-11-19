import http.client
import io
import urllib.error

import pytest

from clipstudio import models

DATA = bytes(range(256)) * 40          # 10,240 bytes


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int, total: int, cut_after: int | None = None):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body))}
        self.cut_after = cut_after

    def read(self, n=-1):
        if self.cut_after is not None and self.tell() >= self.cut_after:
            raise http.client.IncompleteRead(b"")       # the connection dropped mid-file
        if self.cut_after is not None:
            n = min(n, self.cut_after - self.tell())
        return super().read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_server(monkeypatch, plan):
    """Each call to models._get takes the next step of the plan: ("cut", bytes) or ("ok",)."""
    calls = []

    def get(url, headers=None, timeout=60):
        start = int((headers or {}).get("Range", "bytes=0-")[6:-1] or 0)
        calls.append(start)
        step = plan[len(calls) - 1]
        body = DATA[start:]
        if step[0] == "status":
            raise urllib.error.HTTPError(url, step[1], "nope", {}, None)
        return FakeResponse(body, 206 if start else 200, len(DATA), cut_after=step[1] if step[0] == "cut" else None)

    monkeypatch.setattr(models, "_get", get)
    return calls


def test_fetch_resumes_after_a_dropped_connection(tmp_path, monkeypatch):
    calls = fake_server(monkeypatch, [("cut", 4000), ("cut", 3000), ("ok",)])
    seen = []
    dest = models.fetch("https://x/model.bin", tmp_path / "model.bin", len(DATA),
                        on_progress=lambda name, done, total: seen.append(done), wait=lambda s: None)
    assert dest.read_bytes() == DATA
    assert calls == [0, 4000, 7000]                 # each try asks only for what's still missing
    assert seen[-1] == len(DATA)
    assert not (tmp_path / "model.bin.part").exists()


def test_fetch_continues_a_part_left_from_last_time(tmp_path, monkeypatch):
    (tmp_path / "model.bin.part").write_bytes(DATA[:5000])
    calls = fake_server(monkeypatch, [("ok",)])
    models.fetch("https://x/model.bin", tmp_path / "model.bin", len(DATA), wait=lambda s: None)
    assert calls == [5000]
    assert (tmp_path / "model.bin").read_bytes() == DATA


def test_fetch_gives_up_at_once_on_not_found(tmp_path, monkeypatch):
    fake_server(monkeypatch, [("status", 404)])
    with pytest.raises(models.ModelError, match="HTTP 404"):
        models.fetch("https://x/model.bin", tmp_path / "model.bin", len(DATA), wait=lambda s: None)


def test_fetch_stops_after_too_many_failures(tmp_path, monkeypatch):
    fake_server(monkeypatch, [("status", 503)] * models.ATTEMPTS)
    with pytest.raises(models.ModelError, match="Run the same command again"):
        models.fetch("https://x/model.bin", tmp_path / "model.bin", len(DATA), wait=lambda s: None)


def test_repo_for():
    assert models.repo_for("large-v3-turbo") == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    assert models.repo_for("someone/their-model") == "someone/their-model"
    with pytest.raises(models.ModelError, match="Unknown speech model"):
        models.repo_for("not-a-model")
