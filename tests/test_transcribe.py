import subprocess

import numpy as np
import pytest

from clipstudio import media
from clipstudio.transcribe import SAMPLE_RATE, clean_words, read_wav


def test_clean_words_tidies_whisper_output():
    raw = [{"w": " Hello", "s": 0.5, "e": 0.8, "p": 0.91}, {"w": ",", "s": 0.8, "e": 0.85, "p": 0.5},
           {"w": " नमस्ते", "s": 0.3, "e": 1.2, "p": 0.8}, {"w": "।", "s": 1.2, "e": 1.3, "p": 0.6},
           {"w": "   ", "s": 1.3, "e": 1.4}]
    assert clean_words(raw) == [
        {"w": "Hello,", "s": 0.5, "e": 0.85, "p": 0.91},
        {"w": "नमस्ते।", "s": 0.5, "e": 1.3, "p": 0.8},     # said to start before the word ahead of it: kept in order
    ]


def test_clean_words_joins_pieces_of_one_word():
    raw = [{"w": " a", "s": 9.0, "e": 9.04, "p": 0.9}, {"w": " 24", "s": 9.04, "e": 9.4, "p": 0.9},
           {"w": "-hour", "s": 9.4, "e": 9.86, "p": 0.6}, {"w": " cycle", "s": 9.86, "e": 10.2, "p": 0.95},
           {"w": "again", "s": 11.5, "e": 11.9, "p": 0.9}]       # no space, but after a pause: its own word
    assert [w["w"] for w in clean_words(raw)] == ["a", "24-hour", "cycle", "again"]
    joined = clean_words(raw)[1]
    assert (joined["s"], joined["e"], joined["p"]) == (9.04, 9.86, 0.6)


def test_read_wav_from_ffmpeg(tmp_path):
    media.require_tools()
    src, wav = tmp_path / "tone.mp4", tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5:sample_rate=44100",
                    "-c:a", "aac", str(src)], check=True)
    media.extract_audio(src, wav)
    audio = read_wav(wav)
    assert audio.dtype == np.float32
    assert abs(len(audio) / SAMPLE_RATE - 1.5) < 0.05
    assert 0.05 < float(np.abs(audio).max()) <= 1.0


def test_read_wav_rejects_other_formats(tmp_path):
    wav = tmp_path / "stereo.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.2", "-ac", "2", str(wav)],
                   check=True)
    with pytest.raises(ValueError, match="16 kHz mono"):
        read_wav(wav)
