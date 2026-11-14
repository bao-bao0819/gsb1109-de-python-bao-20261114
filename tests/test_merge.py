import pytest

from pysubs2 import SSAEvent, SSAFile, SSAStyle, Color
from pysubs2.formats.substation import parse_tags, replace_named_style_resets


ASS_TEMPLATE = """\
[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{events}
"""


def _style_line(name: str, style: SSAStyle) -> str:
    bold = "-1" if style.bold else "0"
    italic = "-1" if style.italic else "0"
    primary = f"&H00{style.primarycolor.b:02X}{style.primarycolor.g:02X}{style.primarycolor.r:02X}"
    return (f"Style: {name},{style.fontname},{int(style.fontsize)},{primary},"
            f"&H000000FF,&H00000000,&H00000000,{bold},{italic},0,0,100,100,0,0,1,2,2,2,10,10,10,1")


def _dialogue(style: str, text: str) -> str:
    return f"Dialogue: 0,0:00:00.00,0:00:01.00,{style},,0,0,0,,{text}"


def _build_ass(styles: dict[str, SSAStyle], events: list[SSAEvent]) -> str:
    style_lines = "\n".join(_style_line(name, sty) for name, sty in styles.items())
    event_lines = "\n".join(_dialogue(ev.style, ev.text) for ev in events)
    return ASS_TEMPLATE.format(styles=style_lines, events=event_lines)


def _fragment_styles(subs: SSAFile) -> list[list[tuple[str, SSAStyle]]]:
    """Effective (fragment, style) pairs per event, the same view across separate and merged reads."""
    result = []
    for event in subs:
        line_style = subs.styles.get(event.style, SSAStyle.DEFAULT_STYLE)
        fragments = [(fragment, sty)
                     for fragment, sty in parse_tags(event.text, line_style, subs.styles)
                     if fragment]
        result.append(fragments)
    return result


def _vendor_files() -> tuple[SSAFile, SSAFile]:
    interviewer_style = SSAStyle(fontname="Interviewer", fontsize=40,
                                 primarycolor=Color(0, 0, 255), italic=True)
    interviewee_style = SSAStyle(fontname="Interviewee", fontsize=40,
                                 primarycolor=Color(255, 0, 0), bold=True)
    shared_style = SSAStyle(fontname="Shared", fontsize=12)

    interviewer = SSAFile.from_string(_build_ass(
        {"Speaker": interviewer_style, "Shared": shared_style},
        [SSAEvent(style="Speaker", text=r"Question {\rShared}aside{\r}more question"),
         SSAEvent(style="Shared", text="shared line")]))

    interviewee = SSAFile.from_string(_build_ass(
        {"Speaker": interviewee_style, "Shared": shared_style.copy()},
        [SSAEvent(style="Speaker", text=r"Answer {\rSpeaker}named reset{\r} tail")]))

    return interviewer, interviewee


def test_same_name_different_style_kept_and_renamed() -> None:
    interviewer, interviewee = _vendor_files()

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer, interviewee)

    assert set(corpus.styles) == {"Speaker", "Shared", "Speaker 2"}
    assert corpus.styles["Speaker"] == interviewer.styles["Speaker"]
    assert corpus.styles["Speaker 2"] == interviewee.styles["Speaker"]

    assert [event.style for event in corpus.events] == ["Speaker", "Shared", "Speaker 2"]


def test_equivalent_styles_stored_once() -> None:
    interviewer, interviewee = _vendor_files()

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer, interviewee)

    assert len(corpus.styles) == 3
    assert corpus.styles["Shared"] is corpus.styles["Shared"]
    # the second file's Shared, equal by definition, resolves to the single existing one
    assert corpus.events[1].style == "Shared"


def test_named_reset_rewritten_bare_reset_preserved() -> None:
    interviewer, interviewee = _vendor_files()

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer, interviewee)

    assert corpus.events[0].text == r"Question {\rShared}aside{\r}more question"
    assert corpus.events[2].text == r"Answer {\rSpeaker 2}named reset{\r} tail"


def test_roundtrip_preserves_per_fragment_styles() -> None:
    interviewer, interviewee = _vendor_files()
    expected = _fragment_styles(interviewer) + _fragment_styles(interviewee)

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer, interviewee)

    reread = SSAFile.from_string(corpus.to_string("ass"), "ass")
    assert _fragment_styles(reread) == expected


def test_merge_is_idempotent() -> None:
    interviewer, interviewee = _vendor_files()

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer, interviewee)
    names_after_first = set(corpus.styles)
    style_defs_after_first = list(corpus.styles.values())
    events_after_first = len(corpus.events)

    # merging the same material again must not introduce another set of names
    corpus.merge(corpus)
    assert set(corpus.styles) == names_after_first
    assert list(corpus.styles.values()) == style_defs_after_first
    assert len(corpus.events) == events_after_first * 2
    for event in corpus.events[events_after_first:]:
        assert event.style in names_after_first


def test_repeated_merge_of_same_source_dedupes() -> None:
    interviewer, _ = _vendor_files()

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer)
    corpus.merge(interviewer)

    assert set(corpus.styles) == {"Speaker", "Shared"}


def test_generated_name_avoids_existing_suffix() -> None:
    interviewer, interviewee = _vendor_files()
    # pre-occupy the generated name, forcing a higher suffix
    interviewer.styles["Speaker 2"] = SSAStyle(fontname="Occupant")

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer, interviewee)

    assert "Speaker 3" in corpus.styles
    assert corpus.styles["Speaker 3"] == interviewee.styles["Speaker"]
    assert corpus.events[-1].style == "Speaker 3"
    assert r"\rSpeaker 3" in corpus.events[-1].text


def test_merge_does_not_mutate_sources() -> None:
    interviewer, interviewee = _vendor_files()
    original_styles = dict(interviewee.styles)
    original_texts = [event.text for event in interviewee.events]

    corpus = SSAFile()
    corpus.styles = {}
    corpus.merge(interviewer, interviewee)

    assert interviewee.styles == original_styles
    assert [event.text for event in interviewee.events] == original_texts
    assert corpus.events[2] is not interviewee.events[0]


def test_merge_rejects_non_ssafile() -> None:
    corpus = SSAFile()
    with pytest.raises(TypeError):
        corpus.merge(42)  # type: ignore[arg-type]


def test_replace_named_style_resets_only_override_sequences() -> None:
    text = "{\\rSpeaker\\i1}keep {\\rSpeaker} literal \\rSpeaker outside {\\r}"
    renamed = replace_named_style_resets(text, {"Speaker": "Speaker 2"})

    assert renamed == "{\\rSpeaker 2\\i1}keep {\\rSpeaker 2} literal \\rSpeaker outside {\\r}"
    # bare reset and prose outside override sequences are untouched
    assert "{\\r}" in renamed
    assert "literal \\rSpeaker outside" in renamed


def test_replace_named_style_resets_no_mapping_is_identity() -> None:
    text = r"{\rSpeaker}x{\r}y"
    assert replace_named_style_resets(text, {}) == text
