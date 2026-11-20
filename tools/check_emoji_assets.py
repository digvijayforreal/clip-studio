"""Checks that every emoji picture ClipStudio knows about can still be downloaded.

Run with: uv run python tools/check_emoji_assets.py
"""
import sys
import urllib.parse
import urllib.request

from clipstudio import effects


def available(url: str) -> bool:
    """A GET that reads only the first bytes (GitHub's raw file host answers HEAD requests very slowly)."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "clipstudio", "Range": "bytes=0-7"})
        with urllib.request.urlopen(request, timeout=20) as resp:
            return resp.read(8).startswith(b"\x89PNG")
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False


missing = [f"{emoji} {path}" for emoji, path in effects.EMOJI.items()
           if not available(effects.EMOJI_BASE + urllib.parse.quote(path))]
unknown = sorted(set(effects.KEYWORDS) - set(effects.EMOJI))
print(f"{len(effects.EMOJI)} emoji, {len(missing)} missing, {len(unknown)} keyword groups without a picture")
for line in missing + unknown:
    print("  " + line)
sys.exit(1 if missing or unknown else 0)
