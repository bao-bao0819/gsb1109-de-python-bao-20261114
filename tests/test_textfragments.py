import pytest

from pysubs2 import SSAEvent, SSAFile, SSAStyle, split_fragments
from pysubs2.textfragments import readable_text


def test_mixed_background_and_name() -> None:
    text = r"{\p1}m 0 0 l 100 0 100 100 0 100{\p0}人民广场"
    event = SSAEvent(text=text)

    fragments = split_fragments(text)
    assert [(f.drawing, f.text) for f in fragments] == [
        (True, "m 0 0 l 100 0 100 100 0 100"),
        (False, "人民广场"),
    ]
    assert event.station_name == "人民广场"
    assert event.readable_text == "人民广场"
    # plaintext is the legacy view and intentionally still includes the drawing
    # payload; use readable_text/station_name when indexing names.
    assert event.plaintext == "m 0 0 l 100 0 100 100 0 100人民广场"


def test_fragment_offsets_point_at_original_text() -> None:
    text = r"{\p1}m 0 0 l 10 0{\p0}站名"
    drawing, name = split_fragments(text)

    assert text[drawing.start:drawing.end] == drawing.raw == "m 0 0 l 10 0"
    assert text[name.start:name.end] == name.raw == "站名"


def test_inline_drawing_open_and_close() -> None:
    text = r"{\p1}draw{\p0}hello{\p1}draw2{\p0}tail"
    assert [(f.drawing, f.text) for f in split_fragments(text)] == [
        (True, "draw"),
        (False, "hello"),
        (True, "draw2"),
        (False, "tail"),
    ]
    assert readable_text(text) == "hellotail"


def test_style_reset_leaves_drawing_mode() -> None:
    text = r"{\p1}m 0 0{\r}站名"
    assert [(f.drawing, f.text) for f in split_fragments(text)] == [
        (True, "m 0 0"),
        (False, "站名"),
    ]


def test_named_style_reset_leaves_drawing_mode() -> None:
    text = r"{\p1}m 0 0{\rAlt}站名"
    styles = {"Alt": SSAStyle(italic=True)}
    assert [(f.drawing, f.text) for f in split_fragments(text, styles=styles)] == [
        (True, "m 0 0"),
        (False, "站名"),
    ]


def test_two_switches_with_only_drawings_make_no_name() -> None:
    text = r"{\p1}m 0 0 l 10 0{\p0}{\p1}m 5 5 l 20 20{\p0}"
    event = SSAEvent(text=text)

    fragments = split_fragments(text)
    assert fragments
    assert all(fragment.drawing for fragment in fragments)
    assert event.station_name == ""
    assert event.station_name_spans == []
    assert not event.has_text

    subs = SSAFile()
    subs.append(event)
    assert subs.extract_station_names() == []


def test_numeric_coordinates_in_dialogue_stay_text() -> None:
    text = "坐标 120,300 站"
    event = SSAEvent(text=text)

    assert [(f.drawing, f.text) for f in split_fragments(text)] == [(False, text)]
    assert event.station_name == text
    assert event.station_name_spans == [(0, len(text))]


def test_position_tag_is_not_a_drawing_scale() -> None:
    text = r"{\pos(100,200)\pbo5}站名"
    assert [(f.drawing, f.text) for f in split_fragments(text)] == [(False, "站名")]


def test_drawing_scale_must_be_positive() -> None:
    text = r"{\p0}站名"
    assert [(f.drawing, f.text) for f in split_fragments(text)] == [(False, "站名")]

    text = r"{\p2}m 0 0{\p0}站名"
    assert split_fragments(text)[0].drawing_scale == 2
    assert [(f.drawing, f.text) for f in split_fragments(text)] == [
        (True, "m 0 0"),
        (False, "站名"),
    ]


def test_escapes_are_decoded_and_keep_source_offsets() -> None:
    text = r"第一行\N第二行\h结尾"
    fragment, = split_fragments(text)

    assert fragment.text == "第一行\n第二行 结尾"
    assert fragment.raw == text
    assert text[fragment.start:fragment.end] == text


def test_station_name_spans_split_by_inline_tags() -> None:
    text = r"站名{\p1}m 0 0 l 10 0{\r}换乘"
    event = SSAEvent(text=text)

    assert event.station_name == "站名换乘"
    assert event.station_name_spans == [(0, 2), (23, 25)]
    assert [(start, end, text[start:end]) for start, end in event.station_name_spans] == [
        (0, 2, "站名"),
        (23, 25, "换乘"),
    ]


def test_file_level_extraction_skips_comments_and_drawings() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(start=0, end=1, text=r"{\p1}m 0 0 l 1 1{\p0}甲站"))
    subs.append(SSAEvent(start=1, end=2, text=r"{\p1}m 0 0 l 1 1"))
    subs.append(SSAEvent(start=2, end=3, text="乙站", type="Comment"))
    subs.append(SSAEvent(start=3, end=4, text="丙站"))

    names = subs.extract_station_names()
    assert [name.text for name in names] == ["甲站", "丙站"]
    assert [name.start for name in names] == [0, 3]
    assert names[0].raw_spans == ("甲站",)


def test_plaintext_keeps_legacy_behavior() -> None:
    text = r"{\p1}m 0 0 l 10 0{\p0}站名{\i1}x{\i0}"
    event = SSAEvent(text=text)

    assert event.plaintext == "m 0 0 l 10 0站名x"
    assert event.readable_text == "站名x"
    assert event.station_name == "站名x"


def test_parse_tags_reset_leaves_drawing() -> None:
    from pysubs2.formats.substation import parse_tags

    text = r"{\p1}m 0 0{\r}名字"
    assert [(fragment, style.drawing) for fragment, style in parse_tags(text)] == [
        ("", False),
        ("m 0 0", True),
        ("名字", False),
    ]


def test_parse_tags_named_reset_leaves_drawing() -> None:
    from pysubs2 import SSAStyle
    from pysubs2.formats.substation import parse_tags

    text = r"{\p1}m 0 0{\rAlt}名字"
    fragments = parse_tags(text, styles={"Alt": SSAStyle(italic=True)})
    assert [(fragment, style.drawing, style.italic) for fragment, style in fragments] == [
        ("", False, False),
        ("m 0 0", True, False),
        ("名字", False, True),
    ]


def test_extraction_tracks_comments_and_empty_names() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(text="   ", start=0, end=1))
    subs.append(SSAEvent(text=r"{\p1}m 0 0", start=1, end=2))

    assert subs.extract_station_names() == []


def test_explicit_styles_are_used_for_resets() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(text=r"{\p1}m 0 0{\rAlt}名字", style="Default", start=0, end=1))

    names = subs.extract_station_names(styles={"Default": SSAStyle(), "Alt": SSAStyle(italic=True)})
    assert [name.text for name in names] == ["名字"]


@pytest.mark.parametrize("text", [
    r"{\p1}m 0 0 l 10 0{\p0}",
    r"{\p1}m 0 0 l 10 0{\p0}{\p1}m 1 1",
    r"{\p1}m 0 0 l 10 0{\p0}   ",
])
def test_pure_drawing_lines_never_yield_names(text: str) -> None:
    event = SSAEvent(text=text)
    assert event.station_name == ""
    assert event.station_name_spans == []

    subs = SSAFile()
    subs.append(event)
    assert subs.extract_station_names() == []


@pytest.mark.parametrize("text", [
    "站台 3",
    "12:30 到站",
    "100,200",
])
def test_plain_dialogue_numbers_remain_text(text: str) -> None:
    event = SSAEvent(text=text)
    assert event.station_name == text
    assert event.readable_fragments


def test_fragment_raw_is_exact_source_substring() -> None:
    text = r"前{\i1}中\N文{\i0}后"
    for fragment in split_fragments(text):
        assert text[fragment.start:fragment.end] == fragment.raw


def test_no_empty_fragments_from_adjacent_overrides() -> None:
    text = r"{\i1}{\b1}{\i0}{\b0}"
    assert split_fragments(text) == []


def test_readable_text_joins_across_tags() -> None:
    text = r"{\i1}甲{\i0}{\b1}乙{\b0}"
    event = SSAEvent(text=text)
    assert event.readable_text == "甲乙"
    assert event.station_name_spans == [(5, 6), (16, 17)]


def test_extraction_works_with_default_style_on_event() -> None:
    # A different event style must not break name extraction.
    subs = SSAFile()
    event = SSAEvent(text=r"{\p1}m 0 0{\p0}站名", style="Missing")
    subs.append(event)

    names = subs.extract_station_names()
    assert [name.text for name in names] == ["站名"]
