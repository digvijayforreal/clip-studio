import json

import pytest

from clipstudio import project as projects
from clipstudio.render import geometry, video_filter


def sample(**changes):
    p = projects.new(
        name="abc_01m23s-02m05s",
        source={"kind": "youtube", "url": "https://youtu.be/abc", "id": "abc", "title": "T", "uploader": "U",
                "file": "source.mp4", "offset": 80.0, "width": 1920, "height": 1080, "duration": 48.0},
        segment=(3.0, 45.0),
        words=[{"w": "नमस्ते", "s": 3.2, "e": 3.7, "p": 0.9}, {"w": "दोस्तों", "s": 3.7, "e": 4.3, "p": 0.8}],
        language="hi")
    p.update(changes)
    return p


def test_round_trip_keeps_hindi_readable(tmp_path):
    path = projects.save(sample(), tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "नमस्ते" in text                                     # not \u escapes
    assert '      {"w": "नमस्ते", "s": 3.2, "e": 3.7, "p": 0.9},' in text   # one word per line
    assert projects.load(tmp_path) == sample()
    assert json.loads(text) == sample()


@pytest.mark.parametrize("change, message", [
    ({"version": 2}, "version"),
    ({"segments": []}, "exactly one"),
    ({"segments": [{"start": 5, "end": 4}]}, "after its start"),
    ({"segments": [{"start": 5, "end": 60}]}, "only 48s"),
    ({"frame": {"layout": "zoom", "x": 0.5}}, "frame.layout"),
    ({"frame": {"layout": "crop", "x": 1.5}}, "frame.x"),
    ({"output": {"file": "..\\x.mp4"}}, "output.file"),
])
def test_validate_rejects(change, message):
    with pytest.raises(projects.ProjectError, match=message):
        projects.validate(sample(**change))


def test_validate_rejects_bad_word():
    p = sample()
    p["captions"]["words"].append({"w": "x", "s": 5.0, "e": 4.0})
    with pytest.raises(projects.ProjectError, match="ends before it starts"):
        projects.validate(p)


def test_load_explains_broken_json(tmp_path):
    (tmp_path / "project.json").write_text('{"version": 1,', encoding="utf-8")
    with pytest.raises(projects.ProjectError, match="isn't valid JSON"):
        projects.load(tmp_path)


def test_save_into_a_folder_that_does_not_exist_yet(tmp_path):
    written = projects.save(sample(), tmp_path / "new_clip")
    assert written == tmp_path / "new_clip" / "project.json"
    assert projects.load(tmp_path / "new_clip")["name"] == sample()["name"]
    assert projects.save(sample(), tmp_path / "other.json") == tmp_path / "other.json"


def test_source_path(tmp_path):
    assert projects.source_path(sample(), tmp_path) == tmp_path / "source.mp4"


def test_geometry():
    assert geometry(1920, 1080, "blur") == ("blur", 608, 1424)
    assert geometry(1920, 1080, "crop") == ("crop", 1920, 1344)
    assert geometry(1080, 1920, "blur")[0] == "crop"            # upright video: fill the screen


def test_video_filter_ends_in_v0():
    for layout in ("blur", "crop"):
        assert video_filter(layout, 608, 0.25).endswith("[v0]")
    assert "(iw-ow)*0.250" in video_filter("crop", 1920, 0.25)
