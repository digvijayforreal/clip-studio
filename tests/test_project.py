import json

import pytest

from clipstudio import project as projects
from clipstudio.render import geometry, hook_y, video_filter


def sample(**changes):
    p = projects.new(
        name="abc_01m23s-02m05s",
        source={"kind": "youtube", "url": "https://youtu.be/abc", "id": "abc", "title": "T", "uploader": "U",
                "file": "source.mp4", "offset": 80.0, "width": 1920, "height": 1080, "duration": 48.0},
        segments=[(3.0, 45.0)],
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


def test_new_project_has_effects_on_by_default():
    p = sample()
    assert p["version"] == 3
    assert p["segments"] == [{"id": "c1", "start": 3.0, "end": 45.0, "volume": 1.0}]
    assert p["texts"] == [] and p["captions"]["x"] == 0.5 and p["captions"]["scale"] == 1.0
    assert p["effects"]["zooms"] == "auto" and p["effects"]["emojis"] == "auto"
    assert p["effects"]["sfx"] is True and p["effects"]["progress"] is True
    assert p["edit"]["cut_pauses"] is False and p["hook"]["text"] == ""


def test_settings_merge_over_the_defaults():
    p = projects.new(name="x", source=sample()["source"], segments=[(1, 2)], words=[], language="en",
                     effects={"zooms": "off"}, hook={"text": "WOW"})
    assert p["effects"]["zooms"] == "off" and p["effects"]["emojis"] == "auto"
    assert p["hook"] == {"text": "WOW", "seconds": None, "y": None}


def test_version_1_projects_upgrade_and_look_the_same():
    old = sample()
    old["version"] = 1
    for key in ("edit", "hook", "effects", "music"):
        del old[key]
    for key in ("x", "scale"):
        del old["captions"][key]
    del old["texts"]
    old["segments"] = [{"start": 3.0, "end": 45.0}]
    p = projects.validate(old)
    assert p["version"] == 3
    assert p["effects"]["zooms"] == "off" and p["effects"]["emojis"] == "off"
    assert p["effects"]["sfx"] is False and p["effects"]["progress"] is False
    assert p["segments"] == [{"start": 3.0, "end": 45.0, "id": "c1", "volume": 1.0}]
    assert p["texts"] == [] and p["hook"]["y"] is None and p["captions"]["scale"] == 1.0


def good_text(**changes):
    t = {"id": "t1", "text": "HELLO", "start": 1.0, "end": 3.0, "x": 0.5, "y": 0.3, "size": 90,
         "font": "Anton", "color": "FFFFFF", "box": False, "back": "000000"}
    t.update(changes)
    return t


def test_texts_validate():
    p = sample(texts=[good_text()])
    assert projects.validate(p)["texts"][0]["font"] == "Anton"
    for change, message in (({"font": "Comic Sans"}, "font"), ({"end": 0.5}, "end has to be after"),
                            ({"color": "red"}, "colour"), ({"size": 5}, "size"), ({"x": 2}, "x")):
        with pytest.raises(projects.ProjectError, match=message):
            projects.validate(sample(texts=[good_text(**change)]))


@pytest.mark.parametrize("change, message", [
    ({"version": 7}, "isn't a ClipStudio project"),
    ({"segments": []}, "at least one"),
    ({"segments": [{"start": 5, "end": 4}]}, "after the start"),
    ({"segments": [{"start": 5, "end": 60}]}, "only 48s"),
    ({"segments": [{"start": 5, "end": 10, "volume": 3}]}, "volume"),
    ({"frame": {"layout": "zoom", "x": 0.5}}, "frame.layout"),
    ({"frame": {"layout": "crop", "x": 1.5}}, "frame.x"),
    ({"output": {"file": "..\\x.mp4"}}, "output.file"),
])
def test_validate_rejects(change, message):
    with pytest.raises(projects.ProjectError, match=message):
        projects.validate(sample(**change))


@pytest.mark.parametrize("effects_change, message", [
    ({"zooms": "sometimes"}, '"auto", "off" or a list'),
    ({"zooms": [{"start": 3, "end": 2}]}, "ends before it starts"),
    ({"emojis": [{"t": 1, "emoji": "🦄"}]}, "has pictures for"),
    ({"zoom_scale": 3}, "zoom_scale"),
    ({"sfx": "yes"}, "effects.sfx"),
])
def test_validate_rejects_effects(effects_change, message):
    p = sample()
    p["effects"] = {**p["effects"], **effects_change}
    with pytest.raises(projects.ProjectError, match=message):
        projects.validate(p)


def test_clips_may_repeat_and_come_in_any_order():
    p = sample(segments=[{"start": 20, "end": 25}, {"start": 5, "end": 10}, {"start": 20, "end": 25}])
    assert len(projects.validate(p)["segments"]) == 3


def test_validate_accepts_listed_effects():
    p = sample()
    p["effects"]["zooms"] = [{"start": 1.0, "end": 2.5}]
    p["effects"]["emojis"] = [{"t": 1.0, "emoji": "🔥", "seconds": 1.2}]
    assert projects.validate(p) is p


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


def test_source_and_music_paths(tmp_path):
    p = sample()
    assert projects.source_path(p, tmp_path) == tmp_path / "source.mp4"
    assert projects.music_path(p, tmp_path) is None
    p["music"]["file"] = "beat.mp3"
    assert projects.music_path(p, tmp_path) == tmp_path / "beat.mp3"


def test_geometry():
    assert geometry(1920, 1080, "blur") == ("blur", 608, 1424)
    assert geometry(1920, 1080, "crop") == ("crop", 1920, 1344)
    assert geometry(1080, 1920, "blur")[0] == "crop"            # upright video: fill the screen
    assert hook_y("blur", 608) == 466 and hook_y("crop", 1920) == 300


def test_video_filter_ends_in_v0():
    for layout in ("blur", "crop"):
        assert video_filter(layout, 608, 0.25).endswith("[v0]")
    assert "(iw-ow)*0.250" in video_filter("crop", 1920, 0.25)
    assert video_filter("crop", 1920, src="[cv]").startswith("[cv]fps=")
