from textwrap import dedent

import pytest

from pysubs2 import SSAEvent, SSAFile
from pysubs2.revision import (
    MergeConflict,
    Revision,
    TextEdit,
    UnresolvedChange,
    merge_revisions,
)


BASE_SRT = dedent("""\
    1
    00:00:00,000 --> 00:00:02,000
    今天天气很好我们出去玩吧

    2
    00:00:03,000 --> 00:00:05,000
    明天见

    """)


def load_srt(text: str) -> SSAFile:
    return SSAFile.from_string(text, format_="srt")


def base_subs() -> SSAFile:
    return load_srt(BASE_SRT)


def retimed_subs() -> SSAFile:
    subs = base_subs()
    subs.events[0].start = 1000
    subs.events[0].end = 3000
    subs.events[1].start = 4000
    subs.events[1].end = 6000
    return subs


def text_edited_subs() -> SSAFile:
    subs = base_subs()
    subs.events[0].text = "今天天汽很好我们出去玩吧"
    subs.events[1].text = "明天再见"
    return subs


def split_subs() -> SSAFile:
    subs = base_subs()
    subs.events[0:1] = [
        SSAEvent(start=0, end=1000, text="今天天气很好"),
        SSAEvent(start=1000, end=2000, text="我们出去玩吧"),
    ]
    return subs


def test_merge_keeps_independent_timing_and_text_edits() -> None:
    result = merge_revisions(base_subs(), {
        "甲": retimed_subs(),
        "乙": text_edited_subs(),
    })

    assert [(e.start, e.end, e.text) for e in result.events] == [
        (1000, 3000, "今天天汽很好我们出去玩吧"),
        (4000, 6000, "明天再见"),
    ]
    assert result.unresolved == []
    assert result.conflicts == []


def test_timing_only_revision_does_not_revert_text() -> None:
    result = merge_revisions(base_subs(), {
        "乙": text_edited_subs(),
        "甲": retimed_subs(),
    })

    assert result.events[0].text == "今天天汽很好我们出去玩吧"
    assert result.events[1].text == "明天再见"
    assert (result.events[0].start, result.events[0].end) == (1000, 3000)


def test_split_and_text_edit_merge_per_original_event_range() -> None:
    result = merge_revisions(base_subs(), {
        "甲": retimed_subs(),
        "乙": text_edited_subs(),
        "丙": split_subs(),
    })

    assert [(e.start, e.end, e.text) for e in result.events] == [
        (1000, 2000, "今天天汽很好"),
        (2000, 3000, "我们出去玩吧"),
        (4000, 6000, "明天再见"),
    ]
    assert len(result.groups) == 2
    assert result.groups[0].split_by == "丙"
    assert result.groups[0].base_start == 0
    assert result.groups[0].base_end == 2000
    assert result.groups[0].base_text == "今天天气很好我们出去玩吧"


def test_text_edit_uniquely_inside_split_segment_is_merged() -> None:
    edited = base_subs()
    edited.events[0].text = "今天天气很好我们出去逛吧"

    result = merge_revisions(base_subs(), {
        "乙": edited,
        "丙": split_subs(),
    })

    assert [e.text for e in result.events[:2]] == [
        "今天天气很好",
        "我们出去逛吧",
    ]
    assert result.unresolved == []


def test_text_edit_on_split_boundary_is_reported_separately() -> None:
    edited = base_subs()
    edited.events[0].text = "今天天气很好，我们出去玩吧"

    result = merge_revisions(base_subs(), {
        "乙": edited,
        "丙": split_subs(),
    })

    assert [e.text for e in result.events[:2]] == [
        "今天天气很好",
        "我们出去玩吧",
    ]
    assert len(result.unresolved) == 1
    change = result.unresolved[0]
    assert isinstance(change, UnresolvedChange)
    assert change.author == "乙"
    assert change.old == ""
    assert change.new == "，"
    assert {(c.segment, c.start, c.end) for c in change.candidates} == {
        (0, 6, 6),
        (1, 0, 0),
    }


def test_repeated_phrase_deletion_across_split_is_not_guessed() -> None:
    base = SSAFile()
    base.events = [SSAEvent(start=0, end=2000, text="走吧走吧")]
    split = SSAFile()
    split.events = [
        SSAEvent(start=0, end=1000, text="走吧"),
        SSAEvent(start=1000, end=2000, text="走吧"),
    ]
    edited = SSAFile()
    edited.events = [SSAEvent(start=0, end=2000, text="走吧")]

    result = merge_revisions(base, {"乙": edited, "丙": split})

    assert [e.text for e in result.events] == ["走吧", "走吧"]
    assert len(result.unresolved) == 1
    assert result.unresolved[0].old == "走吧"
    assert {c.segment for c in result.unresolved[0].candidates} == {0, 1}


def test_substitution_in_one_of_two_identical_split_events() -> None:
    base = SSAFile()
    base.events = [SSAEvent(start=0, end=2000, text="走吧走吧")]
    split = SSAFile()
    split.events = [
        SSAEvent(start=0, end=1000, text="走吧"),
        SSAEvent(start=1000, end=2000, text="走吧"),
    ]
    edited = SSAFile()
    edited.events = [SSAEvent(start=0, end=2000, text="走啊走吧")]

    result = merge_revisions(base, {"乙": edited, "丙": split})

    assert [e.text for e in result.events] == ["走啊", "走吧"]
    assert result.unresolved == []


def test_conflicting_text_edits_are_not_applied() -> None:
    left = base_subs()
    left.events[0].text = "今天天气很号我们出去玩吧"
    right = base_subs()
    right.events[0].text = "今天天气很妙我们出去玩吧"

    result = merge_revisions(base_subs(), {"乙": left, "丁": right})

    assert result.events[0].text == "今天天气很好我们出去玩吧"
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert isinstance(conflict, MergeConflict)
    assert conflict.field == "text"
    assert set(conflict.authors) == {"乙", "丁"}


def test_identical_text_edit_from_two_reviewers_counts_once() -> None:
    left = base_subs()
    left.events[0].text = "今天天汽很好我们出去玩吧"
    right = base_subs()
    right.events[0].text = "今天天汽很好我们出去玩吧"

    result = merge_revisions(base_subs(), {"乙": left, "丁": right})

    assert result.events[0].text == "今天天汽很好我们出去玩吧"
    assert result.conflicts == []
    assert result.groups[0].edits[0].authors == ("乙", "丁")


def test_provenance_records_field_level_authors() -> None:
    result = merge_revisions(base_subs(), {
        "甲": retimed_subs(),
        "乙": text_edited_subs(),
        "丙": split_subs(),
    })

    first = result.groups[0].provenance[0]
    assert first.authors == ("甲", "乙", "丙")
    assert first.field_authors["text"] == ("乙", "丙")
    assert first.field_authors["start"] == ("甲",)
    assert first.field_authors["end"] == ("甲",)
    assert first.source_events["丙"] == (0,)

    second = result.groups[0].provenance[1]
    assert second.field_authors["text"] == ("丙",)


def test_deletion_across_split_boundary_is_decomposed() -> None:
    base = SSAFile()
    base.events = [SSAEvent(start=0, end=2000, text="abcdefghij")]
    split = SSAFile()
    split.events = [
        SSAEvent(start=0, end=1000, text="abcde"),
        SSAEvent(start=1000, end=2000, text="fghij"),
    ]
    edited = SSAFile()
    edited.events = [SSAEvent(start=0, end=2000, text="abchij")]

    result = merge_revisions(base, {"乙": edited, "丙": split})

    assert [e.text for e in result.events] == ["abc", "hij"]
    assert result.unresolved == []
    assert {edit.segment for edit in result.groups[0].edits} == {0, 1}


def test_replacement_across_split_boundary_is_unresolved() -> None:
    base = SSAFile()
    base.events = [SSAEvent(start=0, end=2000, text="abcdefghij")]
    split = SSAFile()
    split.events = [
        SSAEvent(start=0, end=1000, text="abcde"),
        SSAEvent(start=1000, end=2000, text="fghij"),
    ]
    edited = SSAFile()
    edited.events = [SSAEvent(start=0, end=2000, text="abcdXYghij")]

    result = merge_revisions(base, {"乙": edited, "丙": split})

    assert [e.text for e in result.events] == ["abcde", "fghij"]
    assert len(result.unresolved) == 1
    assert result.unresolved[0].old == "ef"
    assert result.unresolved[0].new == "XY"


def test_timing_attribution_distinguishes_start_and_end() -> None:
    end_only = base_subs()
    end_only.events[0].end = 3500

    result = merge_revisions(base_subs(), {"甲": end_only})

    provenance = result.groups[0].provenance[0]
    assert provenance.field_authors == {"end": ("甲",)}
    assert result.groups[0].timing_authors == ("甲",)


def test_group_authors_summarize_contributions() -> None:
    result = merge_revisions(base_subs(), {
        "甲": retimed_subs(),
        "乙": text_edited_subs(),
        "丙": split_subs(),
    })

    assert result.groups[0].authors == ("甲", "乙", "丙")
    assert result.groups[0].timing_authors == ("甲",)
    assert result.groups[0].text_authors == ("乙", "丙")


def test_ssafile_merge_revisions_uses_self_as_base() -> None:
    base = base_subs()
    result = base.merge_revisions({
        "甲": retimed_subs(),
        "乙": text_edited_subs(),
    })

    assert result.events[0].text == "今天天汽很好我们出去玩吧"
    assert (result.events[0].start, result.events[0].end) == (1000, 3000)


def test_applied_text_edits_carry_attribution_and_spans() -> None:
    result = merge_revisions(base_subs(), {
        "甲": retimed_subs(),
        "乙": text_edited_subs(),
        "丙": split_subs(),
    })

    edit = result.groups[0].edits[0]
    assert isinstance(edit, TextEdit)
    assert edit.authors == ("乙",)
    assert edit.base_index == 0
    assert edit.old == "气"
    assert edit.new == "汽"
    assert edit.segment == 0


def test_split_and_retime_from_same_reviewer() -> None:
    split = base_subs()
    split.events[0:1] = [
        SSAEvent(start=500, end=1200, text="今天天气很好"),
        SSAEvent(start=1200, end=2200, text="我们出去玩吧"),
    ]

    result = merge_revisions(base_subs(), {"丙": split})

    assert [(e.start, e.end, e.text) for e in result.events[:2]] == [
        (500, 1200, "今天天气很好"),
        (1200, 2200, "我们出去玩吧"),
    ]
    assert result.conflicts == []


def test_merge_does_not_mutate_inputs() -> None:
    base = base_subs()
    revision = retimed_subs()
    original_base = base.to_string("srt")
    original_revision = revision.to_string("srt")

    merge_revisions(base, {"甲": revision})

    assert base.to_string("srt") == original_base
    assert revision.to_string("srt") == original_revision


def test_merge_preserves_file_metadata() -> None:
    base = base_subs()
    base.info["Title"] = "Example"
    base.styles["Default"].fontsize = 42

    result = merge_revisions(base, {"甲": retimed_subs()})

    assert result.subs.info["Title"] == "Example"
    assert result.subs.styles["Default"].fontsize == 42


def test_revisions_can_be_passed_as_revision_objects_or_pairs() -> None:
    as_objects = merge_revisions(base_subs(), [
        Revision("甲", retimed_subs()),
        Revision("乙", text_edited_subs()),
    ])
    as_pairs = merge_revisions(base_subs(), [
        ("甲", retimed_subs()),
        ("乙", text_edited_subs()),
    ])

    assert as_objects.to_dict() == as_pairs.to_dict()


def test_result_is_json_serializable() -> None:
    import json

    result = merge_revisions(base_subs(), {
        "甲": retimed_subs(),
        "乙": text_edited_subs(),
        "丙": split_subs(),
    })

    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["authors"] == ["甲", "乙", "丙"]
    assert payload["groups"][0]["events"][0]["provenance"]["field_authors"]["text"] == ["乙", "丙"]


def test_no_revisions_returns_copy_of_base() -> None:
    base = base_subs()
    result = merge_revisions(base, {})

    assert result.subs.to_string("srt") == base.to_string("srt")
    assert result.subs.events is not base.events
    assert result.conflicts == []


def test_invalid_inputs() -> None:
    with pytest.raises(TypeError):
        merge_revisions(object(), {})  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        merge_revisions(base_subs(), {"甲": object()})  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        merge_revisions(base_subs(), {"": base_subs()})

    with pytest.raises(ValueError):
        merge_revisions(base_subs(), [
            ("甲", base_subs()),
            ("甲", base_subs()),
        ])

    with pytest.raises(ValueError):
        merge_revisions(base_subs(), {}, max_split_events=0)
