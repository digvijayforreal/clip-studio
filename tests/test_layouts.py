"""Facecam layouts, templates and the logo: the plan, the drawings, and real renders checked pixel by pixel."""
import subprocess

import numpy as np
import pytest

from clipstudio import layouts, media, render
from clipstudio import project as projects

SRC = {"width": 1920, "height": 1080, "title": "Big Stream", "uploader": "Streamer"}


def frame(**kw):
    return {"layout": "blur", "x": 0.5, **layouts.frame_defaults(), **kw}


def inside(r):
    dx, dy, dw, dh = r["dest"]
    return 0 <= dx and 0 <= dy and dx + dw <= 1080 and dy + dh <= 1920


def test_cover_keeps_the_aspect_and_stays_in_the_box():
    box = layouts.cover({"x": 0, "y": 0, "w": 1, "h": 1}, 1920, 1080, 1080, 1920)
    assert box[1] == 0 and box[3] == 1                     # full height
    assert abs(box[2] * 1920 / (box[3] * 1080) - 1080 / 1920) < 0.001
    assert abs(box[0] - (1 - box[2]) / 2) < 1e-4           # centred
    left = layouts.cover({"x": 0, "y": 0, "w": 1, "h": 1}, 1920, 1080, 1080, 1920, ax=0)
    assert left[0] == 0


@pytest.mark.parametrize("layout", list(layouts.LAYOUTS))
def test_every_layout_fits_the_frame(layout):
    fp = layouts.plan(frame(layout=layout), None, "", SRC)
    assert fp["layout"] == layout and fp["regions"] and all(inside(r) for r in fp["regions"])
    assert 150 <= fp["cap_y"] <= 1800 and 150 <= fp["hook_y"] <= 1800
    assert fp["classic"] == (layout in ("blur", "crop"))


def test_stack_splits_the_screen():
    fp = layouts.plan(frame(layout="stack", split=0.4, face={"x": 0.7, "y": 0, "w": 0.3, "h": 0.3}), None, "", SRC)
    face, game = fp["regions"]
    assert face["dest"] == [0, 0, 1080, 768] and game["dest"] == [0, 768, 1080, 1152]
    assert face["box"][0] >= 0.7 - 1e-6                    # the facecam comes from the top-right corner


def test_corner_facecam_moves_the_captions_out_of_its_way():
    top = layouts.plan(frame(layout="corner", cam_corner="tr"), None, "", SRC)
    face = top["regions"][1]
    assert face["dest"][0] + face["dest"][2] == 1080 - 40 - 6 and face["border"] == 6
    assert top["hook_y"] > face["dest"][1] + face["dest"][3]
    bottom = layouts.plan(frame(layout="circle", cam_corner="bl"), None, "", SRC)
    face = bottom["regions"][1]
    assert face["shape"] == "circle" and face["dest"][2] == face["dest"][3]
    assert bottom["cap_y"] < face["dest"][1]


def test_x_post_uses_the_hook_as_its_text():
    fp = layouts.plan(frame(), {"id": "x_dark", "name": "Ajay", "handle": "@me"}, "WHY NOBODY TELLS YOU THIS", SRC)
    assert fp["hook"] is False and fp["background"] == {"t": "color", "color": "000000"}
    texts = [s["text"] for s in fp["shapes"] if s["t"] == "text"]
    assert "A" in texts and "Ajay" in texts and "@me" in texts and "WHY NOBODY TELLS YOU THIS" in texts
    video = fp["regions"][0]
    assert video["shape"] == "round" and video["dest"][2] == 1000 and inside(video)
    assert fp["cap_y"] > video["dest"][1] + video["dest"][3]


def test_news_has_a_moving_ticker():
    fp = layouts.plan(frame(), {"id": "news", "headline": "PF RULES CHANGED"}, "", SRC)
    ticker = next(s for s in fp["shapes"] if s["t"] == "ticker")
    assert "Big Stream" in ticker["text"] and fp["cap_y"] <= 1270
    events = layouts.shapes_to_ass(fp["shapes"], 30.0)
    moves = [e for e in events if "\\move(" in e]
    assert len(moves) >= 1 and all("\\clip(200," in e for e in moves)
    # it moves at a steady speed: each pass ends where the next one starts again
    assert all(e.split(",")[1] < e.split(",")[2] for e in moves)
    assert any("PF RULES CHANGED" in e for e in events) and any("BREAKING NEWS" in e for e in events)


def test_wrap_and_rounded_path():
    assert layouts.wrap("one two three four", 9, 5) == ["one two", "three", "four"]
    assert layouts.wrap("a b c d e f", 1, 2) == ["a", "b…"]
    assert layouts.rounded_path(100, 40, 0) == "m 0 0 l 100 0 l 100 40 l 0 40"
    assert layouts.rounded_path(100, 40, 10).count(" b ") == 4


def test_png_writer(tmp_path):
    media.require_tools()
    alpha = layouts.rounded_alpha(64, 32, 10)
    assert alpha[16, 32] == 255 and alpha[0, 0] == 0
    path = layouts.write_png(tmp_path / "m.png", alpha)
    assert media.probe(path)["width"] == 64
    rgba = layouts.write_png(tmp_path / "r.png", np.dstack([alpha] * 4))
    assert media.probe(rgba)["height"] == 32


def test_project_checks_the_new_settings():
    p = projects.new(name="x", segments=[(0, 2)], words=[], language="en",
                     source={"file": "s.mp4", "width": 1920, "height": 1080, "duration": 3})
    assert p["template"]["id"] == "none" and p["logo"]["file"] is None and p["frame"]["cam_corner"] == "tl"
    for change, message in (({"frame": {**p["frame"], "layout": "zoom"}}, "frame.layout"),
                            ({"frame": {**p["frame"], "face": {"x": 0.9, "y": 0, "w": 0.3, "h": 0.3}}}, "inside the picture"),
                            ({"template": {**p["template"], "id": "tiktok"}}, "template.id"),
                            ({"logo": {**p["logo"], "file": "..\\evil.png"}}, "logo.file"),
                            ({"logo": {**p["logo"], "corner": "middle"}}, "logo.corner")):
        with pytest.raises(projects.ProjectError, match=message):
            projects.validate({**p, **change})
    old = {k: v for k, v in p.items() if k not in ("template", "logo", "credit")}
    old["frame"] = {"layout": "crop", "x": 0.3}
    filled = projects.validate(old)                         # older projects get the defaults
    assert filled["template"]["id"] == "none" and filled["frame"]["split"] == 0.38 and filled["frame"]["x"] == 0.3


# ------------------------------------------------------------------ real renders

@pytest.fixture(scope="module")
def source(tmp_path_factory):
    """4 s at 640x360: red everywhere, with a green 'facecam' in the top-left quarter and a beep."""
    media.require_tools()
    d = tmp_path_factory.mktemp("lay")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=4",
                    "-f", "lavfi", "-i", "color=c=lime:s=192x108:r=30:d=4", "-f", "lavfi", "-i", "sine=duration=4",
                    "-filter_complex", "[0][1]overlay=0:0[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(d / "source.mp4")],
                   check=True)
    # a logo: a blue square
    layouts.write_png(d / "logo_brand.png", np.dstack([np.zeros((60, 60), np.uint8)] * 2 + [np.full((60, 60), 255, np.uint8)] * 2))
    return d


def make(folder, **changes):
    p = projects.new(name="lay", segments=[(0.5, 3.5)], language="en",
                     words=[{"w": "hello", "s": 1.0, "e": 1.4}, {"w": "there", "s": 1.4, "e": 1.9}],
                     source={"kind": "file", "file": "source.mp4", "width": 640, "height": 360, "duration": 4.0},
                     effects={"zooms": "off", "emojis": "off", "sfx": False, "progress": False})
    for key, value in changes.items():
        p[key] = {**p[key], **value}
    return p


def pixel(clip, x, y, t=1.2):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(clip), "-frames:v", "1",
                          "-vf", f"format=rgb24,crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


def is_(rgb, name):
    r, g, b = rgb
    return {"red": r > 180 and g < 90 and b < 90, "green": g > 180 and r < 90 and b < 110,
            "white": min(rgb) > 200, "black": max(rgb) < 50, "blue": b > 180 and r < 90 and g < 90}[name]


@pytest.mark.parametrize("layout, face_at, game_at", [
    ("stack", (540, 300), (540, 1500)),          # facecam (green) fills the top, game (red) below
    ("stack_bottom", (540, 1700), (540, 400)),
    ("corner", (200, 200), (540, 1200)),          # facecam in the top-left corner over the game
    ("circle", (270, 300), (540, 1200)),
])
def test_render_facecam_layouts(source, layout, face_at, game_at):
    p = make(source, frame={"layout": layout, "face": {"x": 0, "y": 0, "w": 0.3, "h": 0.3}, "cam_corner": "tl"})
    p["output"]["file"] = f"{layout}.mp4"
    clip = render.render(source, p, log=lambda *a: None)
    info = media.probe(clip)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["duration"] - 3.0) < 0.1
    assert is_(pixel(clip, *face_at), "green"), pixel(clip, *face_at)
    assert is_(pixel(clip, *game_at), "red"), pixel(clip, *game_at)


def test_render_circle_has_a_white_ring_and_round_corners(source):
    p = make(source, frame={"layout": "circle", "face": {"x": 0, "y": 0, "w": 0.3, "h": 0.3}, "cam_corner": "tl"})
    p["output"]["file"] = "ring.mp4"
    clip = render.render(source, p, log=lambda *a: None)
    fp = layouts.plan(p["frame"], None, "", {"width": 640, "height": 360})
    dx, dy, dw, _ = fp["regions"][1]["dest"]
    assert is_(pixel(clip, dx + dw // 2, dy - 4), "white")            # the ring above the circle
    assert is_(pixel(clip, dx + 6, dy + 6), "red")                     # the square's corner shows the game


@pytest.mark.parametrize("tid, top_colour", [("x_light", "white"), ("x_dark", "black"), ("news", None), ("card", None)])
def test_render_templates(source, tid, top_colour):
    p = make(source, template={"id": tid, "name": "Ajay", "handle": "me", "headline": "BIG NEWS"},
             hook={"text": "WATCH THIS"})
    p["output"]["file"] = f"{tid}.mp4"
    clip = render.render(source, p, log=lambda *a: None)
    assert media.probe(clip)["height"] == 1920
    fp = layouts.plan(p["frame"], p["template"], "WATCH THIS", {"width": 640, "height": 360})
    vx, vy, vw, vh = fp["regions"][0]["dest"]
    assert is_(pixel(clip, vx + vw - 40, vy + vh // 2), "red")        # the video is where the plan says
    if top_colour:
        assert is_(pixel(clip, 1000, 30), top_colour)
    if tid == "news":
        assert is_(pixel(clip, 60, 1600), "white")                     # the headline bar
        assert is_(pixel(clip, 20, 1710), "red") is False
    if tid == "x_light":
        assert is_(pixel(clip, vx + 2, vy + 2), "white")               # rounded corner shows the page


def test_render_logo_and_credit(source):
    p = make(source, logo={"file": "logo_brand.png", "corner": "tr", "size": 0.2, "opacity": 1.0},
             credit={"text": "Clip: Streamer"})
    p["output"]["file"] = "logo.mp4"
    clip = render.render(source, p, log=lambda *a: None)
    assert is_(pixel(clip, 1080 - 40 - 100, 70 + 100), "blue")         # a 216 px logo in the top-right corner
    assert "Clip: Streamer" in (source / "captions.ass").read_text(encoding="utf-8")
