import wave

import numpy as np
import pytest

from clipstudio import effects


def w(text, s, e):
    return {"w": text, "s": s, "e": e}


SPEECH = [w("Welcome", 0.0, 0.4), w("back.", 0.4, 0.8), w("Today", 1.3, 1.6), w("we", 1.6, 1.7),
          w("talk", 1.7, 2.0), w("money.", 2.0, 2.5), w("First", 3.0, 3.3), w("the", 3.3, 3.4),
          w("basics", 3.4, 4.0), w("and", 4.6, 4.7), w("then", 4.7, 5.0), w("more.", 5.0, 5.6)]


def test_phrases_join_short_bits():
    ps = effects.phrases(SPEECH, 6.0)
    assert ps[0] == (0.0, 2.5)                    # "Welcome back." was too short on its own
    assert all(b - a >= effects.MIN_PHRASE for a, b in ps)


def test_auto_zooms_every_other_phrase_and_inside_the_clip():
    zooms = effects.auto_zooms(SPEECH, 6.0)
    ps = effects.phrases(SPEECH, 6.0)
    assert zooms and zooms[0]["start"] == ps[1][0]
    assert all(0 <= z["start"] < z["end"] <= 6.0 for z in zooms)
    assert all(z["end"] - z["start"] >= effects.MIN_ZOOM for z in zooms)


def test_clean_zooms_orders_clips_and_trims_overlaps():
    assert effects.clean_zooms([{"start": 4, "end": 9}, {"start": 1, "end": 4.5}, {"start": 8.9, "end": 9.0}], 8.0) == [
        {"start": 1.0, "end": 4.5}, {"start": 4.5, "end": 8.0}]


@pytest.mark.parametrize("word, emoji", [
    ("money.", "💰"), ("Paisa", "💰"), ("पैसा", "💰"), ("funny!", "😂"), ("बढ़ाकर", "📈"), ("table", None)])
def test_emoji_for_words(word, emoji):
    assert effects.emoji_for(word) == emoji


def test_auto_emojis_are_spread_out():
    words = [w("money", 1.0, 1.3), w("cash", 2.0, 2.3), w("funny", 5.0, 5.3), w("money", 9.0, 9.3),
             w("love", 9.5, 9.8)]
    got = effects.auto_emojis(words, 12.0)
    assert [(e["t"], e["emoji"]) for e in got] == [(1.0, "💰"), (5.0, "😂"), (9.0, "💰")]


def test_auto_emojis_skip_the_very_end():
    assert effects.auto_emojis([w("money", 5.8, 6.0)], 6.0) == []


def test_every_keyword_has_a_picture():
    assert set(effects.KEYWORDS) <= set(effects.EMOJI)


def test_sfx_track_places_sounds(tmp_path):
    track = effects.sfx_track(3.0, whooshes=[1.0], pops=[2.0], rate=8000)
    assert len(track) == 3 * 8000 + 1
    assert float(np.abs(track[:4000]).max()) == 0.0               # quiet before the whoosh starts
    whoosh_peak = int(np.argmax(np.abs(track[:12000])))
    assert 0.6 * 8000 < whoosh_peak <= 1.0 * 8000                 # it builds up to the zoom
    assert float(np.abs(track[16000:16400]).max()) > 0.1          # the pop at 2.0 s
    path = effects.write_wav(track, tmp_path / "sfx.wav", rate=8000)
    with wave.open(str(path)) as f:
        assert (f.getnchannels(), f.getframerate(), f.getnframes()) == (1, 8000, len(track))


def test_emoji_file_uses_the_cache(tmp_path, monkeypatch):
    (tmp_path / "fire_3d.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(effects.urllib.request, "urlopen", lambda *a, **k: pytest.fail("should not download"))
    assert effects.emoji_file("🔥", tmp_path) == tmp_path / "fire_3d.png"


def test_emoji_file_unknown():
    with pytest.raises(effects.EmojiError):
        effects.emoji_file("🦄")
