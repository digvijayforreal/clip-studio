"""Effects added on top of the cut: zoom punch-ins, emoji, sound effects.

Everything here works in clip time (seconds from the start of the finished clip), on words that have already
been moved onto the clip's own timeline. "auto" in the project means these functions choose; a list there
means the person chose.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np

from . import config

# ---------------------------------------------------------------- zoom punch-ins

ZOOM_SCALE = 1.15
PHRASE_GAP = 0.35        # a pause this long ends a phrase
MIN_PHRASE = 1.2         # shorter phrases are joined to the next one
MIN_ZOOM = 0.8           # a zoom shorter than this flickers; it's dropped
PHRASE_END = (".", "?", "!", "।", "॥", ",", ";", ":")


def phrases(words: list[dict], length: float) -> list[tuple[float, float]]:
    """(start, end) of each spoken phrase, joined up so none is shorter than MIN_PHRASE."""
    out: list[list[float]] = []
    for i, w in enumerate(words):
        if not out:
            out.append([w["s"], w["e"]])
            continue
        prev = words[i - 1]
        if w["s"] - prev["e"] >= PHRASE_GAP or prev["w"].rstrip().endswith(PHRASE_END):
            out.append([w["s"], w["e"]])
        else:
            out[-1][1] = w["e"]
    joined: list[list[float]] = []
    for p in out:
        if joined and joined[-1][1] - joined[-1][0] < MIN_PHRASE:
            joined[-1][1] = p[1]
        else:
            joined.append(p)
    if len(joined) > 1 and joined[-1][1] - joined[-1][0] < MIN_PHRASE:
        last = joined.pop()
        joined[-1][1] = last[1]
    return [(round(max(0.0, a), 3), round(min(length, b), 3)) for a, b in joined]


def auto_zooms(words: list[dict], length: float) -> list[dict]:
    """Jump-cut punch-ins: every other phrase is shown zoomed in, from its first word to the next phrase."""
    ps = phrases(words, length)
    zooms = []
    for i in range(1, len(ps), 2):
        start = ps[i][0]
        end = ps[i + 1][0] if i + 1 < len(ps) else length
        if end - start >= MIN_ZOOM:
            zooms.append({"start": start, "end": round(end, 3)})
    return zooms


def clean_zooms(zooms: list[dict], length: float) -> list[dict]:
    """In order, inside the clip, not overlapping, none too short to see."""
    out = []
    for z in sorted(zooms, key=lambda z: z["start"]):
        a, b = max(0.0, float(z["start"])), min(length, float(z["end"]))
        if out and a < out[-1]["end"]:
            a = out[-1]["end"]
        if b - a >= 0.2:
            out.append({"start": round(a, 3), "end": round(b, 3)})
    return out


# ---------------------------------------------------------------- emoji

# Microsoft Fluent Emoji (MIT licence), the 3D set: 256x256 pictures with a clear background.
EMOJI_BASE = "https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/"
EMOJI = {
    "🔥": "Fire/3D/fire_3d.png",
    "💰": "Money bag/3D/money_bag_3d.png",
    "😂": "Face with tears of joy/3D/face_with_tears_of_joy_3d.png",
    "❤️": "Red heart/3D/red_heart_3d.png",
    "🤯": "Exploding head/3D/exploding_head_3d.png",
    "🤔": "Thinking face/3D/thinking_face_3d.png",
    "💡": "Light bulb/3D/light_bulb_3d.png",
    "🏆": "Trophy/3D/trophy_3d.png",
    "⏰": "Alarm clock/3D/alarm_clock_3d.png",
    "📱": "Mobile phone/3D/mobile_phone_3d.png",
    "🚀": "Rocket/3D/rocket_3d.png",
    "📈": "Chart increasing/3D/chart_increasing_3d.png",
    "📉": "Chart decreasing/3D/chart_decreasing_3d.png",
    "😢": "Crying face/3D/crying_face_3d.png",
    "😱": "Face screaming in fear/3D/face_screaming_in_fear_3d.png",
    "📚": "Books/3D/books_3d.png",
    "💼": "Briefcase/3D/briefcase_3d.png",
    "🎵": "Musical note/3D/musical_note_3d.png",
    "🎮": "Video game/3D/video_game_3d.png",
    "🚗": "Automobile/3D/automobile_3d.png",
    "🏠": "House/3D/house_3d.png",
    "⭐": "Star/3D/star_3d.png",
    "🎉": "Party popper/3D/party_popper_3d.png",
    "💯": "Hundred points/3D/hundred_points_3d.png",
    "⚠️": "Warning/3D/warning_3d.png",
    "✅": "Check mark button/3D/check_mark_button_3d.png",
    "❌": "Cross mark/3D/cross_mark_3d.png",
    "👀": "Eyes/3D/eyes_3d.png",
    "🧠": "Brain/3D/brain_3d.png",
    "🎯": "Bullseye/3D/bullseye_3d.png",
    "😎": "Smiling face with sunglasses/3D/smiling_face_with_sunglasses_3d.png",
    "💎": "Gem stone/3D/gem_stone_3d.png",
    "👑": "Crown/3D/crown_3d.png",
    "🌍": "Globe showing europe-africa/3D/globe_showing_europe-africa_3d.png",
}

# Spoken word -> emoji. English, Hindi in Devanagari, and Hindi typed in Roman letters.
KEYWORDS = {
    "🔥": "fire hot crazy insane आग धमाका",
    "💰": "money cash salary paisa paise rupees rupee rupay dollars dollar price rich पैसा पैसे रुपये रुपए रुपे सैलरी कमाई",
    "😂": "funny laugh lol haha joke hilarious मज़ाक मजाक हंसी",
    "❤️": "love heart pyaar pyar प्यार दिल मोहब्बत",
    "🤯": "mindblowing unbelievable shocking कमाल",
    "🤔": "why think thinking wonder kyun kyon क्यों सोचो सोचिए",
    "💡": "idea tip trick secret hack आइडिया तरीका",
    "🏆": "win winner won champion best जीत जीता विजेता",
    "⏰": "time minutes hours deadline समय वक्त टाइम",
    "📱": "phone mobile app iphone फोन मोबाइल",
    "🚀": "growth launch fast rocket boost तेज़ी",
    "📈": "increase increased grow growing profit gain बढ़ाकर बढ़ा बढ़ेगा मुनाफा फायदा",
    "📉": "loss drop fall crash decrease नुकसान गिरावट",
    "😢": "sad cry crying दुख रोना",
    "😱": "scary shocked shock danger डर खतरा",
    "📚": "study school exam learn education books पढ़ाई स्कूल परीक्षा",
    "💼": "job work office business employees employee career नौकरी काम बिजनेस",
    "🎵": "music song songs गाना संगीत",
    "🎮": "game games gaming गेम",
    "🚗": "car cars drive गाड़ी कार",
    "🏠": "home house घर मकान",
    "⭐": "star stars famous स्टार",
    "🎉": "party celebrate congratulations badhai बधाई जश्न",
    "💯": "perfect hundred absolutely बिल्कुल",
    "⚠️": "warning careful beware सावधान चेतावनी",
    "✅": "yes correct right done हाँ सही",
    "❌": "wrong mistake never गलत",
    "👀": "look watch see देखो देखिए",
    "🧠": "brain smart genius mind दिमाग",
    "🎯": "goal target focus लक्ष्य",
    "😎": "cool swag",
    "💎": "valuable diamond premium",
    "👑": "king queen raja रानी राजा",
    "🌍": "world global country countries दुनिया देश",
}
_KEY = re.compile(r"[^\w\u0900-\u097f]+")
WORD_TO_EMOJI = {word: emoji for emoji, words in KEYWORDS.items() for word in words.split()}
EMOJI_SECONDS = 1.4
EMOJI_GAP = 3.0          # seconds between two emoji at least
EMOJI_EVERY = 4.0        # at most one emoji per this many seconds of clip


def key(word: str) -> str:
    return _KEY.sub("", word.lower())


def emoji_for(word: str) -> str | None:
    return WORD_TO_EMOJI.get(key(word))


def auto_emojis(words: list[dict], length: float) -> list[dict]:
    """An emoji on words that call for one ("money", "पैसा", "funny"...), spread out so the screen stays calm."""
    out: list[dict] = []
    limit = max(1, int(length / EMOJI_EVERY))
    for w in words:
        emoji = emoji_for(w["w"])
        if not emoji or w["s"] < 0.3 or w["s"] + 0.5 > length:
            continue
        if out and (w["s"] - out[-1]["t"] < EMOJI_GAP or emoji == out[-1]["emoji"] and w["s"] - out[-1]["t"] < 8):
            continue
        out.append({"t": round(w["s"], 3), "emoji": emoji, "seconds": EMOJI_SECONDS})
        if len(out) >= limit:
            break
    return out


def clean_emojis(emojis: list[dict], length: float) -> list[dict]:
    out = []
    for e in sorted(emojis, key=lambda e: e["t"]):
        t = max(0.0, float(e["t"]))
        seconds = min(float(e.get("seconds", EMOJI_SECONDS)), length - t)
        if seconds >= 0.3 and e.get("emoji") in EMOJI:
            item = {"t": round(t, 3), "emoji": e["emoji"], "seconds": round(seconds, 3)}
            for k in ("x", "y", "size"):                 # where it sits and how big, when chosen in the editor
                if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool):
                    item[k] = float(e[k])
            out.append(item)
    return out


class EmojiError(RuntimeError):
    pass


def emoji_file(emoji: str, folder: Path | None = None) -> Path:
    """The picture for an emoji, downloaded once into the assets folder."""
    if emoji not in EMOJI:
        raise EmojiError(f"No picture for {emoji}.")
    folder = Path(folder or config.ASSETS_DIR / "emoji")
    asset = EMOJI[emoji]
    dest = folder / Path(asset).name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    folder.mkdir(parents=True, exist_ok=True)
    url = EMOJI_BASE + urllib.parse.quote(asset)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "clipstudio"})
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise EmojiError(f"Couldn't download the {emoji} picture ({e}).") from None
    if not data.startswith(b"\x89PNG"):
        raise EmojiError(f"The {emoji} picture that came back isn't a PNG.")
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------- sound effects

SFX_RATE = 48000


def whoosh(rate: int = SFX_RATE) -> np.ndarray:
    """Air rushing past: noise through a filter that opens up, swelling to a peak just before the end."""
    n = int(0.42 * rate)
    x = np.linspace(0.0, 1.0, n, endpoint=False)
    noise = np.random.default_rng(7).standard_normal(n)
    cutoff = 300.0 + 5200.0 * x ** 1.6
    alpha = 1.0 - np.exp(-2.0 * np.pi * cutoff / rate)
    out = np.empty(n)
    low = 0.0
    for i in range(n):                      # a one-pole low-pass whose cut-off moves with time
        low += alpha[i] * (noise[i] - low)
        out[i] = low
    envelope = np.where(x < 0.75, (x / 0.75) ** 2, np.cos((x - 0.75) / 0.25 * np.pi / 2) ** 2)
    out *= envelope
    return (out / (np.abs(out).max() or 1.0) * 0.28).astype(np.float32)


def pop(rate: int = SFX_RATE) -> np.ndarray:
    """A short bubble pop: a falling tone that dies away quickly."""
    n = int(0.12 * rate)
    t = np.arange(n) / rate
    freq = 250.0 + 1100.0 * np.exp(-t / 0.02)
    tone = np.sin(2.0 * np.pi * np.cumsum(freq) / rate)
    return (tone * np.exp(-t / 0.03) * 0.30).astype(np.float32)


def sfx_track(length: float, whooshes: list[float], pops: list[float], rate: int = SFX_RATE) -> np.ndarray:
    """A track as long as the clip with each sound at its moment. A whoosh peaks where the zoom starts."""
    track = np.zeros(int(length * rate) + 1, dtype=np.float32)
    w, p = whoosh(rate), pop(rate)

    def place(sound: np.ndarray, at: float) -> None:
        i = int(round(at * rate))
        a, b = max(0, i), min(len(track), i + len(sound))
        if b > a:
            track[a:b] += sound[a - i:b - i]
    for t in whooshes:
        place(w, t - len(w) / rate * 0.8)
    for t in pops:
        place(p, t)
    return np.clip(track, -1.0, 1.0)


def write_wav(samples: np.ndarray, path: Path, rate: int = SFX_RATE) -> Path:
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(data.tobytes())
    return Path(path)
