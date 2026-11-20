from clipstudio.timeline import KEEP_AFTER, KEEP_BEFORE, KEEP_SILENT, Timeline, cut_pauses, cut_silences


def test_cut_silences_keeps_a_breath_each_side():
    assert cut_silences([(0.0, 10.0)], [(3.0, 4.0), (7.0, 7.9)]) == [
        (0.0, 3.0 + KEEP_SILENT), (round(4.0 - KEEP_SILENT, 3), round(7.0 + KEEP_SILENT, 3)),
        (round(7.9 - KEEP_SILENT, 3), 10.0)]


def test_cut_silences_at_the_edges_and_outside():
    # quiet at the very start and end of the segment is removed fully; quiet outside it is ignored
    assert cut_silences([(2.0, 8.0)], [(0.0, 2.6), (7.5, 9.0), (12.0, 13.0)]) == [
        (round(2.6 - KEEP_SILENT, 3), round(7.5 + KEEP_SILENT, 3))]


def test_cut_silences_ignores_tiny_cuts():
    assert cut_silences([(0.0, 5.0)], [(2.0, 2.4)]) == [(0.0, 5.0)]


def w(text, s, e):
    return {"w": text, "s": s, "e": e}


def test_one_segment_counts_from_its_start():
    t = Timeline([(10.0, 20.0)])
    assert t.length == 10.0
    assert t.to_clip(12.5) == 2.5
    assert t.to_clip(25.0) is None


def test_joined_parts_play_one_after_the_other():
    t = Timeline([(10.0, 14.0), (50.0, 53.0)])
    assert t.length == 7.0
    assert t.to_clip(13.0) == 3.0
    assert t.to_clip(51.0) == 5.0
    assert t.to_clip(30.0) is None
    assert t.cut_points() == [4.0]


def test_words_move_onto_clip_time_and_stop_at_their_piece():
    t = Timeline([(10.0, 14.0), (50.0, 53.0)])
    words = [w("in", 11.0, 11.5), w("gone", 30.0, 30.4), w("late", 13.8, 15.0), w("two", 50.5, 51.0)]
    assert [(x["w"], x["s"], x["e"]) for x in t.words(words)] == [
        ("in", 1.0, 1.5), ("late", 3.8, 4.0), ("two", 4.5, 5.0)]


def test_clips_play_in_list_order_and_may_repeat():
    t = Timeline([(50.0, 52.0, 0.5), (10.0, 12.0), (50.0, 52.0)])
    assert [p.at for p in t.pieces] == [0.0, 2.0, 4.0]
    assert [p.volume for p in t.pieces] == [0.5, 1.0, 1.0]
    got = t.words([w("again", 50.5, 51.0), w("first", 10.2, 10.6)])
    assert [(x["w"], x["s"]) for x in got] == [("again", 0.5), ("first", 2.2), ("again", 4.5)]


def test_cut_pauses_keeps_air_around_words():
    words = [w("one", 1.0, 1.5), w("two", 1.6, 2.0), w("three", 4.0, 4.5)]
    assert cut_pauses([(0.0, 6.0)], words) == [
        (round(1.0 - KEEP_BEFORE, 3), round(2.0 + KEEP_AFTER, 3)),
        (round(4.0 - KEEP_BEFORE, 3), 4.9)]


def test_short_pauses_stay():
    words = [w("one", 0.1, 0.5), w("two", 0.9, 1.3), w("three", 1.8, 2.2)]
    assert cut_pauses([(0.0, 2.4)], words) == [(0.0, 2.4)]


def test_segment_without_words_is_kept_whole():
    assert cut_pauses([(0.0, 3.0), (5.0, 6.0)], [w("x", 5.2, 5.4)]) == [(0.0, 3.0), (5.0, 6.0)]
