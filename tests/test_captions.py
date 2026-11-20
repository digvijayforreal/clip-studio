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


def test_pop_style_shows_one_word_at_a_time_each_popping():
    lines = events(build_ass([w("one", 0.0, 0.3), w("two", 0.3, 0.6), w("three", 0.6, 0.9)], 0.0, 2.0, "pop"))
    assert len(lines) == 3
    assert all("\\t(0,110" in line for line in lines)
    assert [re.sub(r"\{[^}]*\}", "", line.split(",", 9)[9]) for line in lines] == ["ONE", "TWO", "THREE"]


def test_boxed_style_draws_a_box():
    ass = build_ass([w("hi", 0.0, 0.3)], 0.0, 1.0, "boxed")
    cap_style = next(line for line in ass.splitlines() if line.startswith("Style: Cap,"))
    assert cap_style.split(",")[15] == "3"            # BorderStyle 3: an opaque box


def test_hook_is_wrapped_and_stays_up():
    ass = build_ass([], 0.0, 10.0, hook="THIS IS WHY NOBODY TELLS YOU THE TRUTH", hook_y=400)
    hook = next(line for line in events(ass) if ",Hook," in line)
    assert times(hook) == ("0:00:00.00", "0:00:10.00")
    text = hook.split(",", 9)[9]
    assert "\\pos(540,400)" in text and text.count("\\N") >= 1
    assert all(units(part) <= captions.HOOK["max_units"] for part in re.sub(r"\{[^}]*\}", "", text).split("\\N"))


def test_hook_can_leave_early():
    hook = next(line for line in events(build_ass([], 0.0, 10.0, hook="WAIT", hook_seconds=3)) if ",Hook," in line)
    assert times(hook)[1] == "0:00:03.00" and "\\fad(0,200)" in hook


def test_progress_bar_fills_over_the_clip():
    bar = next(line for line in events(build_ass([], 0.0, 12.5, progress=True)) if ",Bar," in line)
    assert times(bar) == ("0:00:00.00", "0:00:12.50")
    assert "\\fscx0\\t(0,12500,\\fscx100)" in bar and "\\p1" in bar


def test_captions_move_and_grow():
    lines = events(build_ass([w("hi", 0.0, 0.5)], 0.0, 1.0, "bold", y=500, x=300, scale=1.5))
    assert "\\pos(300,500)" in lines[0] and "\\fs192" in lines[0]


def test_free_texts():
    texts = [{"text": "BIG\nNEWS {x}", "start": 1.0, "end": 9.0, "px": 540, "py": 400, "size": 120, "font": "Anton",
              "color": "FF0000", "box": True, "back": "FFFFFF"},
             {"text": "   ", "start": 0, "end": 2, "px": 1, "py": 1, "size": 90, "font": "Anton", "color": "FFFFFF",
              "box": False, "back": "000000"}]
    lines = [line for line in events(build_ass([], 0.0, 5.0, texts=texts)) if ",Text" in line]
    assert len(lines) == 1                                         # the empty one is left out
    assert times(lines[0]) == ("0:00:01.00", "0:00:05.00")          # stops at the end of the clip
    assert ",TextBox," in lines[0] and "\\fnAnton\\fs120" in lines[0] and "\\c&H0000FF&" in lines[0]
    assert lines[0].endswith("BIG\\NNEWS x")


def test_build_ass_without_words_is_just_the_header():
    ass = build_ass([], 0.0, 5.0)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert events(ass) == []
