"""The editor in a real browser (Microsoft Edge through Playwright), against a throwaway projects folder.

Clicks, drags and keys like a person would, then checks what was saved and rendered. Skipped when Edge or
Playwright isn't there. Takes about a minute.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from clipstudio import media
from clipstudio import project as projects

playwright = pytest.importorskip("playwright.sync_api")

NAME = "gen_00m01s-00m09s"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    media.require_tools()
    root = tmp_path_factory.mktemp("browser")
    folder = root / "projects" / NAME
    folder.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=10",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=10", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(folder / "source.mp4")], check=True)
    words = [{"w": w, "s": round(1.0 + i * 0.4, 2), "e": round(1.35 + i * 0.4, 2), "p": 0.9}
             for i, w in enumerate("this is all about the money and time you save today".split())]
    p = projects.new(name=NAME, segments=[(1.0, 9.0)], language="en", words=words,
                     source={"kind": "youtube", "url": "", "id": "gen", "title": "Generated", "uploader": "",
                             "file": "source.mp4", "offset": 0.0, "width": 640, "height": 360, "duration": 10.0})
    projects.save(p, folder)
    port = free_port()
    env = {**os.environ, "CLIPSTUDIO_PROJECTS": str(root / "projects"), "CLIPSTUDIO_SETTINGS": str(root / "settings.json"),
           "CLIPSTUDIO_UPLOADS": str(root / "uploads")}
    server = subprocess.Popen([sys.executable, "-m", "clipstudio", "app", "--no-browser", "--port", str(port)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/api/meta", timeout=1)
            break
        except OSError:
            time.sleep(0.5)
    else:
        server.kill()
        pytest.fail("The server didn't start.")
    yield base, folder
    server.kill()
    server.wait()


@pytest.fixture(scope="module")
def page(app):
    base, _ = app
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Edge isn't available: {e}")
        pg = browser.new_page(viewport={"width": 1500, "height": 1100})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
        pg.goto(f"{base}/#/edit/{NAME}")
        yield pg
        browser.close()


def saved(folder) -> dict:
    return json.loads((folder / "project.json").read_text(encoding="utf-8"))


def wait_saved(page):
    page.wait_for_timeout(50)
    page.wait_for_function("document.querySelector('.ed-status').textContent === 'All changes saved'", timeout=15000)


def test_editor_works_like_a_video_editor(page, app):
    base, folder = app
    page.wait_for_selector(".tl-item.clip", timeout=20000)
    # the old-style automatic effects become real items on the timeline, and are saved
    page.wait_for_function("document.querySelectorAll('.tl-item.sticker').length > 0", timeout=20000)   # "money" -> 💰
    wait_saved(page)
    assert isinstance(saved(folder)["effects"]["zooms"], list)
    assert page.locator(".tl-item.line").count() >= 3
    # the editing copy is made by itself and the filmstrip appears
    page.wait_for_function("!!document.querySelector('.pv-frame video').src.includes('/proxy')", timeout=60000)

    ruler = page.locator(".tl-ruler").bounding_box()
    pps = page.evaluate("document.querySelector('.tl-item.clip').getBoundingClientRect().width") / 8.0

    # click the ruler at 3 s, split with S
    page.mouse.click(ruler["x"] + 3.0 * pps, ruler["y"] + 10)
    page.keyboard.press("s")
    assert page.locator(".tl-item.clip").count() == 2

    # drag the last clip's right edge 1 s to the left: a trim
    right = page.locator(".tl-item.clip").nth(1)
    right.click()
    box = right.bounding_box()
    page.mouse.move(box["x"] + box["width"] - 3, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 3 - pps, box["y"] + box["height"] / 2, steps=8)
    page.mouse.up()
    wait_saved(page)
    segs = saved(folder)["segments"]
    assert len(segs) == 2 and segs[0]["end"] == 4.0 and abs(segs[1]["end"] - 8.0) < 0.05

    # undo the trim, redo it
    page.keyboard.press("Control+z")
    wait_saved(page)
    assert saved(folder)["segments"][1]["end"] == 9.0
    page.keyboard.press("Control+Shift+z")
    wait_saved(page)
    assert abs(saved(folder)["segments"][1]["end"] - 8.0) < 0.05

    # drag the first clip after the second: the clips swap
    first = page.locator(".tl-item.clip").nth(0).bounding_box()
    total_w = page.evaluate("[...document.querySelectorAll('.tl-item.clip')].reduce((n, e) => n + e.getBoundingClientRect().width, 0)")
    page.mouse.move(first["x"] + first["width"] / 2, first["y"] + 20)
    page.mouse.down()
    page.mouse.move(first["x"] + total_w - 10, first["y"] + 20, steps=10)
    page.mouse.up()
    wait_saved(page)
    assert [s["start"] for s in saved(folder)["segments"]] == [4.0, 1.0]

    # add a text, then drag it on the preview
    page.get_by_role("button", name="+ Text").first.click()
    overlay = page.locator(".ov-text")
    overlay.wait_for()
    ob = overlay.bounding_box()
    frame = page.locator(".pv-frame").bounding_box()
    page.mouse.move(ob["x"] + ob["width"] / 2, ob["y"] + ob["height"] / 2)
    page.mouse.down()
    page.mouse.move(ob["x"] + ob["width"] / 2, ob["y"] + ob["height"] / 2 + frame["height"] * 0.2, steps=8)
    page.mouse.up()
    page.locator(".ed-inspector textarea").fill("BIG NEWS")
    wait_saved(page)
    text = saved(folder)["texts"][0]
    assert text["text"] == "BIG NEWS" and abs(text["y"] - 0.6) < 0.03

    # move the captions on the preview: seek to a moment with words, then drag the caption line up
    pps = page.evaluate("document.querySelector('.tl-item.clip').getBoundingClientRect().width") / 4.0
    ruler = page.locator(".tl-ruler").bounding_box()
    page.mouse.click(ruler["x"] + 0.6 * pps, ruler["y"] + 10)
    cap = page.locator(".ov-captions")
    try:
        cap.wait_for(timeout=5000)
    except Exception:
        page.screenshot(path=os.environ.get("CLIPSTUDIO_SHOT", "editor_fail.png"))
        raise
    cb = cap.bounding_box()
    page.mouse.move(cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2)
    page.mouse.down()
    page.mouse.move(cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2 - frame["height"] * 0.3, steps=8)
    page.mouse.up()
    wait_saved(page)
    assert saved(folder)["captions"]["y"] < 0.5

    # playing moves the playhead
    page.locator(".ed-transport .play").click()
    page.wait_for_timeout(1200)
    page.locator(".ed-transport .play").click()
    t = page.evaluate("parseFloat(document.querySelector('.ed-time').textContent.split(':')[1])")
    assert t > 0.8

    # render, and the clip comes out as long as the edit
    page.locator(".ed-top .btn.go").click()
    page.wait_for_function("document.querySelector('.ed-top .btn.go').textContent === 'Render' && "
                           "!document.querySelector('.ed-top .btn.go').disabled && "
                           "!document.querySelector('a[download]').hidden", timeout=120000)
    clip = media.probe(folder / "clip.mp4")
    assert abs(clip["duration"] - 7.0) < 0.1 and (clip["width"], clip["height"]) == (1080, 1920)
    assert not page.errors, page.errors


def test_layouts_templates_and_logo(page, app, tmp_path):
    import re

    import numpy as np

    from clipstudio import layouts
    base, folder = app
    page.keyboard.press("Escape")
    page.get_by_role("button", name=re.compile("^Layout:")).click()
    page.get_by_role("radio", name="Round facecam in a corner").click()
    wait_saved(page)
    assert saved(folder)["frame"]["layout"] == "circle"

    # draw the facecam box: drag it right by a fifth of the picture
    page.get_by_role("button", name="Draw the boxes on the video").click()
    face = page.locator(".bx-box.face")
    face.wait_for()
    fb, stage = face.bounding_box(), page.locator(".bx-stage").bounding_box()
    page.mouse.move(fb["x"] + fb["width"] / 2, fb["y"] + fb["height"] / 2)
    page.mouse.down()
    page.mouse.move(fb["x"] + fb["width"] / 2 + stage["width"] * 0.2, fb["y"] + fb["height"] / 2, steps=6)
    page.mouse.up()
    page.get_by_role("button", name="Done").click()
    wait_saved(page)
    assert abs(saved(folder)["frame"]["face"]["x"] - 0.2) < 0.03

    # the X post template, with a name
    page.keyboard.press("Escape")
    page.get_by_role("button", name=re.compile("^Template:")).click()
    page.get_by_role("radio", name="X post (dark)").click()
    page.get_by_label("Name").fill("Gen Streamer")
    wait_saved(page)
    t = saved(folder)["template"]
    assert t["id"] == "x_dark" and t["name"] == "Gen Streamer"
    page.wait_for_timeout(600)                                        # the preview asks the server for the layout
    top_pixel = page.evaluate("Array.from(document.querySelector('.pv-canvas').getContext('2d').getImageData(500, 20, 1, 1).data)")
    assert max(top_pixel[:3]) < 30                                    # the dark page behind the post
    painted = page.evaluate("Array.from(document.querySelector('.pv-gfx').getContext('2d').getImageData(0, 0, 540, 960).data)"
                            ".filter((v, i) => i % 4 === 3 && v > 0).length")
    assert painted > 2000                                             # the avatar, name and post text are drawn

    # a logo
    logo = layouts.write_png(tmp_path / "brand.png", np.dstack([np.zeros((50, 50), np.uint8)] * 2 + [np.full((50, 50), 255, np.uint8)] * 2))
    page.keyboard.press("Escape")                                     # leaves the Name box
    page.keyboard.press("Escape")                                     # back to the project settings
    page.get_by_role("button", name=re.compile("^Logo and credit:")).click()
    page.locator(".ed-inspector input[type=file]").set_input_files(str(logo))
    page.wait_for_function("document.querySelector('.pv-logo') && !document.querySelector('.pv-logo').hidden", timeout=15000)
    page.get_by_role("textbox", name=re.compile("^Text")).fill("Clip: Gen")
    wait_saved(page)
    s = saved(folder)
    assert s["logo"]["file"].startswith("logo_") and s["credit"]["text"] == "Clip: Gen"

    # the render has the dark page, the logo's blue in the top-right corner, and no errors on the page
    page.locator(".ed-top .btn.go").click()
    page.wait_for_function("document.querySelector('.ed-top .btn.go').textContent === 'Render' && "
                           "!document.querySelector('.ed-top .btn.go').disabled", timeout=120000)
    page.wait_for_timeout(500)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(folder / "clip.mp4"), "-frames:v", "1",
                          "-vf", "format=rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3)
    assert img[20, 500].max() < 40
    r, g, b = img[70 + 40, 1080 - 40 - 40].tolist()
    assert b > 180 and r < 90, (r, g, b)
    assert not page.errors, page.errors
