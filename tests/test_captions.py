import re

from clipstudio import captions
from clipstudio.captions import ass_time, build_ass, display_text, group_words, style_color, tag_color, units


def w(text, s, e):
    return {"w": text, "s": s, "e": e}


def events(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def times(line: str) -> tuple[str, str]:
    parts = line.split(",", 3)
    return parts[1], parts[2]


def test_ass_time():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(3723.456) == "1:02:03.46"
    assert ass_time(-1) == "0:00:00.00"


def test_colors_are_written_blue_green_red():
    assert style_color("FFE600") == "&H0000E6FF"
    assert style_color("#ffe600", "64") == "&H6400E6FF"
    assert tag_color("FFE600") == "&H00E6FF&"


def test_units_ignore_hindi_marks_drawn_above_or_below():
    assert units("HELLO") == 5
    assert units("हैं") == 1           # ह, then ै and ं are drawn above it
    assert units("किताब") == 5         # क ि त ा ब: the vowel signs ि and ा take room of their own


def test_display_text():
    assert display_text("hello,", upper=True) == "HELLO"
    assert display_text("नमस्ते।", upper=True) == "नमस्ते"        # uppercase doesn't touch Devanagari
    assert display_text("why?", upper=False) == "why?"
    assert display_text("{\\bad}", upper=False) == "bad"


def test_groups_break_on_count_pause_and_punctuation():
    words = [w("one", 0.0, 0.2), w("two", 0.2, 0.4), w("three", 0.4, 0.6), w("four", 0.6, 0.8),
             w("five.", 0.8, 1.0), w("six", 1.0, 1.2), w("seven", 2.0, 2.2)]
    groups = group_words(words, max_words=3, max_units=99)
    assert [[x["w"] for x in g] for g in groups] == [["one", "two", "three"], ["four", "five."], ["six"], ["seven"]]


def test_groups_break_when_too_wide():
    words = [w("aaaaaaaa", 0, 0.1), w("bbbbbbbb", 0.1, 0.2)]
    assert len(group_words(words, max_words=3, max_units=14)) == 2


def test_groups_do_not_end_on_a_small_word():
    words = [w("regularly", 0.0, 0.5), w("on", 0.5, 0.7), w("a", 0.7, 0.8), w("24-hour", 0.8, 1.2),
             w("cycle", 1.2, 1.5)]
    groups = group_words(words, max_words=3, max_units=13)
    assert [[x["w"] for x in g] for g in groups] == [["regularly", "on"], ["a", "24-hour", "cycle"]]


def test_small_word_stays_before_punctuation_breaks():
    words = [w("I", 0.0, 0.2), w("love", 0.2, 0.4), w("this.", 0.4, 0.6), w("Next", 0.6, 0.8)]
    groups = group_words(words, max_words=3, max_units=13)
    assert [[x["w"] for x in g] for g in groups] == [["I", "love", "this."], ["Next"]]


def test_hindi_conjunction_moves_to_the_next_line():
    words = [w("बहुत", 0.0, 0.3), w("अच्छा", 0.3, 0.6), w("और", 0.6, 0.8), w("सस्ता", 0.8, 1.1)]
    groups = group_words(words, max_words=3, max_units=13)
    assert [[x["w"] for x in g] for g in groups] == [["बहुत", "अच्छा"], ["और", "सस्ता"]]


def test_build_ass_highlights_each_word_in_turn():
    ass = build_ass([w("hello", 10.0, 10.4), w("big", 10.4, 10.7), w("world", 10.7, 11.2)], 10.0, 12.0, "bold")
    lines = events(ass)
    assert len(lines) == 3
    active = tag_color(captions.STYLES["bold"]["active"])
    for i, line in enumerate(lines):
        coloured = re.findall(r"\{\\c(&H[0-9A-F]{6}&)\}(\w+)", line)
        assert [c for c, _ in coloured].count(active) == 1
        assert coloured[i][0] == active                 # the i-th word is the spoken one
        assert [t for _, t in coloured] == ["HELLO", "BIG", "WORLD"]
    # one line runs straight into the next: no flicker, no overlap
    assert [times(x) for x in lines] == [("0:00:00.00", "0:00:00.40"), ("0:00:00.40", "0:00:00.70"),
                                         ("0:00:00.70", "0:00:01.55")]
    assert "\\t(0,110" in lines[0] and "\\t(" not in lines[1]     # the line pops in once


def test_build_ass_keeps_only_the_chosen_part_and_counts_from_its_start():
    words = [w("before", 1.0, 1.5), w("inside", 5.0, 5.5), w("after", 9.0, 9.5)]
    lines = events(build_ass(words, 4.0, 8.0))
    assert len(lines) == 1 and "INSIDE" in lines[0]
    assert times(lines[0]) == ("0:00:01.00", "0:00:01.85")


def test_build_ass_hindi_is_written_as_is():
    words = [w("यह", 0.0, 0.3), w("बहुत", 0.3, 0.7), w("अच्छा", 0.7, 1.1), w("है।", 1.1, 1.4)]
    text = " ".join(events(build_ass(words, 0.0, 3.0)))
    for word in ("यह", "बहुत", "अच्छा", "है"):
        assert word in text
    assert "।" not in text


def test_build_ass_shrinks_one_long_word():
    lines = events(build_ass([w("antidisestablishmentarianism", 0.0, 1.0)], 0.0, 2.0, "bold"))
    size = int(re.search(r"\\fs(\d+)", lines[0]).group(1))
    assert captions.MIN_SIZE <= size < captions.STYLES["bold"]["size"]


def test_build_ass_without_words_is_just_the_header():
    ass = build_ass([], 0.0, 5.0)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert events(ass) == []
