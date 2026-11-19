"""Where ClipStudio keeps its files, and the few settings every part shares."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = Path(os.environ.get("CLIPSTUDIO_PROJECTS") or APP_DIR / "projects")
MODELS_DIR = Path(os.environ.get("CLIPSTUDIO_MODELS") or APP_DIR / "models")
FONTS_DIR = APP_DIR / "fonts"

# The finished clip: vertical 1080x1920 at 30 frames a second.
OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30

# Speech to text. large-v3-turbo handles Hindi and English well and runs on a CPU.
DEFAULT_MODEL = "large-v3-turbo"

# The languages this app is made for. When no language is given, the spoken one is picked from these,
# because Whisper on its own often hears Hindi as Urdu and writes it in Urdu script.
LANGUAGES = ("hi", "en")

# Seconds downloaded on each side of the chosen part, so its start and end can be moved later.
PAD_SECONDS = 3.0
