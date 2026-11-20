"""Speech to text with a time for every word, done on this computer with faster-whisper."""
from __future__ import annotations

import os
import re
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import config, models

_PUNCTUATION_ONLY = re.compile(r"^[^\w]+$")
SAMPLE_RATE = 16000
HINDI_SHARE = 0.03       # Hindi + Urdu odds at or above this: the speech is Hindi or Hinglish


@lru_cache(maxsize=1)
def load_model(name: str = config.DEFAULT_MODEL):
    """The speech model, loaded once. The first use downloads it into the models folder."""
    from faster_whisper import WhisperModel

    folder = models.ensure(name, on_progress=models.progress_printer())
    threads = max(1, (os.cpu_count() or 2) // 2)    # one thread per physical core is fastest here
    return WhisperModel(str(folder), device="cpu", compute_type="int8", cpu_threads=threads)


def choose(odds: dict, allowed=config.LANGUAGES) -> str:
    """The language to write the captions in, from Whisper's odds.

    Hinglish (Hindi with English words mixed in) usually scores as English, and Whisper then writes the Hindi
    as broken Roman text. Pure English gets almost no Hindi odds (0.000 measured) while Hinglish still gets a
    clear share (0.10 measured), so any real Hindi share means Hindi: Hindi words in Devanagari, English words
    left in English. Urdu counts as Hindi, because Whisper can't tell the two apart by sound."""
    if "hi" in allowed and odds.get("hi", 0.0) + odds.get("ur", 0.0) >= HINDI_SHARE:
        return "hi"
    return max(allowed, key=lambda code: odds.get(code, 0.0))


def pick_language(model, audio, allowed=config.LANGUAGES) -> tuple[str, float]:
    """Which of the allowed languages to use, and Whisper's odds for it."""
    try:
        _, _, ranked = model.detect_language(audio=audio, vad_filter=True, language_detection_segments=3)
    except ValueError:                   # no speech found at all (music, silence): nothing to choose from
        return allowed[0], 0.0
    odds = dict(ranked)
    best = choose(odds, allowed)
    return best, float(odds.get(best, 0.0))


def clean_words(raw: list[dict]) -> list[dict]:
    """Whisper's words, tidied: no stray spaces, punctuation joined to the word before it, times in order.

    Whisper starts every new word with a space. A piece without one belongs to the word before it
    (" 24" + "-hour" is "24-hour"), unless a real pause separates them."""
    out: list[dict] = []
    for w in raw:
        raw_text = str(w["w"])
        text = raw_text.strip()
        if not text:
            continue
        start, end = float(w["s"]), float(w["e"])
        punctuation = bool(_PUNCTUATION_ONLY.match(text))
        piece = bool(out) and not raw_text[:1].isspace() and start - out[-1]["e"] < 0.5
        if out and (punctuation or piece):
            out[-1]["w"] += text
            out[-1]["e"] = round(max(out[-1]["e"], end), 3)
            if not punctuation and "p" in w and "p" in out[-1]:
                out[-1]["p"] = round(min(out[-1]["p"], float(w["p"])), 2)    # as sure as its least sure piece
            continue
        if out and start < out[-1]["s"]:
            start = out[-1]["s"]
        word = {"w": text, "s": round(start, 3), "e": round(max(start, end), 3)}
        if "p" in w:
            word["p"] = round(float(w["p"]), 2)
        out.append(word)
    return out


def read_wav(path) -> np.ndarray:
    """The 16 kHz mono WAV made by media.extract_audio, as the float samples Whisper takes.

    Read here rather than by faster-whisper, whose own reader breaks with recent PyAV versions."""
    with wave.open(str(path), "rb") as f:
        if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError(f"{Path(path).name} has to be 16 kHz mono 16-bit audio.")
        frames = f.readframes(f.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def transcribe(audio_path: Path, language: str | None = None, model_name: str = config.DEFAULT_MODEL,
               on_progress=None, accurate: bool = False) -> dict:
    """{"language": "hi" or "en", "words": [{"w", "s", "e", "p"}]} with times in seconds from the start of the file.

    `accurate` searches 5 ways through each sentence instead of 1: about 2x slower on a CPU, and on the
    tests so far only a word or two different."""
    model = load_model(model_name)
    audio = read_wav(audio_path)
    total = len(audio) / SAMPLE_RATE
    if total < 0.2:
        return {"language": language or config.LANGUAGES[0], "words": []}
    if not language:
        language, _ = pick_language(model, audio)
    segments, _info = model.transcribe(
        audio, language=language, task="transcribe", word_timestamps=True,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        beam_size=5 if accurate else 1, condition_on_previous_text=False,
    )
    raw = []
    for segment in segments:
        for w in segment.words or []:
            raw.append({"w": w.word, "s": w.start, "e": w.end, "p": w.probability})
        if on_progress:
            on_progress(min(segment.end, total), total)
    return {"language": language, "words": clean_words(raw)}
