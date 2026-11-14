import pytest

from pysubs2 import DisplayChar, SSAEvent, make_time


def test_repr_dialogue() -> None:
    ev = SSAEvent(start=make_time(m=1, s=30), end=make_time(m=1, s=35), text="Hello\\Nworld!")
    ref = r"<SSAEvent type=Dialogue start=0:01:30 end=0:01:35 text='Hello\\Nworld!'>"
    assert repr(ev) == ref


def test_repr_comment() -> None:
    ev = SSAEvent(start=make_time(m=1, s=30), end=make_time(m=1, s=35), text="Hello\\Nworld!")
    ev.is_comment = True
    ref = r"<SSAEvent type=Comment start=0:01:30 end=0:01:35 text='Hello\\Nworld!'>"
    assert repr(ev) == ref


def test_duration() -> None:
    e = SSAEvent(start=0, end=10)
    assert e.duration == 10

    e.duration = 20
    assert e.start == 0 and e.end == 20

    e.duration = 5
    assert e.start == 0 and e.end == 5

    e.duration = 0
    assert e.start == 0 and e.end == 0

    with pytest.raises(ValueError):
        e.duration = -20


def test_plaintext() -> None:
    e = SSAEvent(text=r"First\NSecond\NThird\hline{with hidden text}")
    assert e.plaintext == "First\nSecond\nThird line"

    e.plaintext = "My\n Text "
    assert e.text == r"My\N Text "

    # SubStation has no way to escape braces, thus this wart
    text = "My text{with braces}"
    e.plaintext = text
    assert e.plaintext != text


def test_shift() -> None:
    e = SSAEvent(start=0, end=10)

    with pytest.raises(ValueError):
        e.shift(frames=5)

    with pytest.raises(ValueError):
        e.shift(fps=23.976)

    with pytest.raises(ValueError):
        e.shift(frames=5, fps=-1)

    e2 = e.copy()
    e2.shift(ms=5)
    assert e2 == SSAEvent(start=5, end=15)

    e2 = e.copy()
    e2.shift(ms=-5)
    assert e2 == SSAEvent(start=-5, end=5)

    e2 = e.copy()
    e2.shift(frames=1, fps=100.0)
    assert e2 == SSAEvent(start=10, end=20)

    e2 = e.copy()
    e2.shift(frames=-1, fps=100.0)
    assert e2 == SSAEvent(start=-10, end=0)

    e2 = e.copy()
    e2.shift(h=1, m=-60, s=2, ms=-2000)
    assert e2 == e


def test_fields() -> None:
    e = SSAEvent()
    with pytest.warns(DeprecationWarning):
        assert e.FIELDS == frozenset([
            "start", "end", "text", "marked", "layer", "style",
            "name", "marginl", "marginr", "marginv", "effect", "type"
        ])


def test_display_chars_basic() -> None:
    e = SSAEvent(text=r"Hello{\i1} world{\i0}!")
    assert [c.char for c in e.display_chars] == list("Hello world!")
    assert "".join(c.char for c in e.display_chars) == e.plaintext
    # "w" sits right after the 5-char "{\i1}" tag and a space
    assert e.display_chars[6] == DisplayChar("w", 11, 12)


def test_display_chars_escapes() -> None:
    e = SSAEvent(text=r"First\NSecond\nThird\hfourth")
    assert "".join(c.char for c in e.display_chars) == "First\nSecond\nThird fourth"
    # \N is one display position covering two raw characters
    newline_char = e.display_chars[5]
    assert newline_char.char == "\n"
    assert e.text[newline_char.start:newline_char.end] == r"\N"
    nbsp_char = e.display_chars[18]
    assert nbsp_char.char == " "
    assert e.text[nbsp_char.start:nbsp_char.end] == r"\h"


def test_display_chars_combining_accent() -> None:
    e = SSAEvent(text="Cafe\u0301{r}x")
    assert [c.char for c in e.display_chars] == ["C", "a", "f", "e\u0301", "x"]
    accented = e.display_chars[3]
    assert e.text[accented.start:accented.end] == "e\u0301"


def test_display_chars_zwj_and_unclosed_brace() -> None:
    e = SSAEvent(text="a\u200db{unclosed")
    assert [c.char for c in e.display_chars] == ["a\u200db", "{", "u", "n", "c", "l", "o", "s", "e", "d"]


def test_raw_spans_skips_override_tags() -> None:
    e = SSAEvent(text=r"你{\c&H0000FF&}好")
    assert e.raw_spans(0, 2) == [(0, 1), (14, 15)]
    # replacing the selected spans keeps the color tag intact
    text = e.text
    for (start, end), replacement in zip(e.raw_spans(0, 2), ["你", "们"]):
        text = text[:start] + replacement + text[end:]
    assert text == r"你{\c&H0000FF&}们"


def test_raw_spans_contiguous_selection_is_merged() -> None:
    e = SSAEvent(text=r"{\an8}Hello world")
    assert e.raw_spans(0, 5) == [(6, 11)]
    assert e.raw_spans(0, 11) == [(6, 17)]


def test_raw_spans_newline_escape() -> None:
    e = SSAEvent(text=r"Hello\Nworld")
    # selecting the line break maps to the whole \N escape
    assert e.raw_spans(5, 6) == [(5, 7)]
    assert e.raw_spans(4, 7) == [(4, 8)]


def test_raw_spans_combining_accent() -> None:
    e = SSAEvent(text="e\u0301")
    assert e.raw_spans(0, 1) == [(0, 2)]


def test_raw_spans_empty_selection_is_insertion_point() -> None:
    e = SSAEvent(text=r"你{\c&H0000FF&}好")
    assert e.raw_spans(0, 0) == [(0, 0)]
    # insertion between the two characters goes after the override tag
    assert e.raw_spans(1, 1) == [(14, 14)]
    assert e.raw_spans(2, 2) == [(15, 15)]

    empty = SSAEvent(text="")
    assert empty.raw_spans(0, 0) == [(0, 0)]


def test_raw_spans_out_of_bounds() -> None:
    e = SSAEvent(text="abc")
    with pytest.raises(IndexError):
        e.raw_spans(0, 4)
    with pytest.raises(IndexError):
        e.raw_spans(-1, 2)
    with pytest.raises(IndexError):
        e.raw_spans(2, 1)
