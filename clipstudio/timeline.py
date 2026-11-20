"""The finished clip's timeline: which stretches of the source are played, one after the other.

Segments are (start, end) in source seconds. Joined parts are several segments; cutting pauses splits a segment
into smaller ones. Source times (caption words) are moved onto clip time with `to_clip`.
"""
from __future__ import annotations

from dataclasses import dataclass

# Pause cutting. Whisper tends to put word starts a little late and word ends a little late too
# (measured: starts up to 0.25 s after the voice), so more air is kept before a word than after one.
MIN_GAP = 0.7          # only pauses longer than this are cut
KEEP_BEFORE = 0.30     # seconds kept ahead of the next word
KEEP_AFTER = 0.15      # seconds kept after the last word
KEEP_END = 0.40        # after the very last word of a segment


@dataclass(frozen=True)
class Piece:
    start: float        # source seconds
    end: float
    at: float           # where it starts in the clip
    volume: float = 1.0

    @property
    def length(self) -> float:
        return self.end - self.start


class Timeline:
    """Segments are (start, end) or (start, end, volume), played in the order given. They may repeat."""

    def __init__(self, segments):
        self.pieces: list[Piece] = []
        at = 0.0
        for seg in segments:
            start, end = seg[0], seg[1]
            volume = float(seg[2]) if len(seg) > 2 else 1.0
            if end - start > 0.01:
                self.pieces.append(Piece(round(start, 4), round(end, 4), round(at, 4), volume))
                at += end - start
        self.length = round(at, 4)

    def segments(self) -> list[tuple[float, float]]:
        return [(p.start, p.end) for p in self.pieces]

    def to_clip(self, t: float) -> float | None:
        """Source time -> clip time, or None when that moment was cut out."""
        for p in self.pieces:
            if p.start - 0.05 <= t < p.end:
                return round(p.at + max(0.0, t - p.start), 3)
        return None

    def cut_points(self) -> list[float]:
        """Clip times where one piece ends and the next begins."""
        return [p.at for p in self.pieces[1:]]

    def words(self, words: list[dict]) -> list[dict]:
        """Caption words on clip time. Words in cut-out parts are dropped; a word never runs past its piece.
        A word in a stretch that's played twice is shown both times."""
        out = []
        for w in words:
            for p in self.pieces:
                if p.start - 0.05 <= w["s"] < p.end:
                    s = p.at + max(0.0, w["s"] - p.start)
                    e = p.at + min(p.length, max(w["e"], w["s"]) - p.start)
                    out.append({**w, "s": round(s, 3), "e": round(max(s, e), 3)})
        return sorted(out, key=lambda w: w["s"])


KEEP_SILENT = 0.12     # air kept on each side when cutting a measured silence


def cut_silences(segments, silences) -> list[tuple[float, float]]:
    """Each segment with the measured silences taken out, keeping a breath on each side.

    Measured silence is more exact than the gaps between Whisper's words, whose ends run into the pauses."""
    out = []
    for start, end in segments:
        a = start
        for s0, s1 in sorted(silences):
            if s1 <= start or s0 >= end:
                continue
            cut_from = max(start, s0 + KEEP_SILENT) if s0 > start else start
            cut_to = min(end, s1 - KEEP_SILENT) if s1 < end else end
            if cut_to - cut_from < 0.2:
                continue
            if cut_from > a:
                out.append((a, cut_from))
            a = cut_to
        if end - a > 0.05:
            out.append((a, end))
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a > 0.05]


def cut_pauses(segments, words: list[dict], min_gap: float = MIN_GAP) -> list[tuple[float, float]]:
    """Each segment split around the long pauses between words, for when the sound can't be measured."""
    out = []
    for start, end in segments:
        inside = sorted((w for w in words if start <= w["s"] < end), key=lambda w: w["s"])
        if not inside:
            out.append((start, end))
            continue
        a = start
        if inside[0]["s"] - start > min_gap:
            a = inside[0]["s"] - KEEP_BEFORE
        for prev, nxt in zip(inside, inside[1:]):
            if nxt["s"] - prev["e"] > min_gap:
                out.append((a, prev["e"] + KEEP_AFTER))
                a = nxt["s"] - KEEP_BEFORE
        last_end = max(w["e"] for w in inside)
        b = min(end, last_end + KEEP_END) if end - last_end > min_gap else end
        out.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a > 0.05]
