import pytest

from clipstudio.timestamps import TimestampError, clock, parse_range, parse_ranges, parse_time, slug


@pytest.mark.parametrize("text, seconds", [
    ("83", 83), ("1:23", 83), ("01:23", 83), ("1:23.5", 83.5), ("1:02:03", 3723), ("0:00", 0), ("12.25", 12.25),
])
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "1:75", "1::2", "1:2:3:4", "-5", "1:2.5:3", ":30", "1.5:20"])
def test_parse_time_rejects(text):
    with pytest.raises(TimestampError):
        parse_time(text)


@pytest.mark.parametrize("text", ["1:23-2:05", " 1:23 - 2:05 ", "1:23–2:05", "1:23 — 2:05", "1:23 to 2:05", "83-125"])
def test_parse_range_separators(text):
    assert parse_range(text) == (83, 125)


@pytest.mark.parametrize("text", ["2:05-1:23", "1:23-1:23", "1:23-1:23.5", "0:00-10:01", "1:23", "1:23-", "a-b"])
def test_parse_range_rejects(text):
    with pytest.raises(TimestampError):
        parse_range(text)


def test_parse_ranges_spaces_and_commas():
    assert parse_ranges(["1:00-1:30, 2:00-2:20", "3:00-3:10"]) == [(60, 90), (120, 140), (180, 190)]


def test_parse_ranges_needs_one():
    with pytest.raises(TimestampError):
        parse_ranges([" , "])


def test_clock_and_slug():
    assert clock(83) == "1:23"
    assert clock(83.5) == "1:23.5"
    assert clock(3723) == "1:02:03"
    assert slug(83.9) == "01m23s"
    assert slug(3723) == "1h02m03s"
