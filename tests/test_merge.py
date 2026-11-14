import pytest

from pysubs2 import SSAEvent, SSAFile, make_time


def make_subs(*events: SSAEvent) -> SSAFile:
    subs = SSAFile()
    for event in events:
        subs.append(event)
    return subs


def test_merge_same_sentence_twice_on_screen() -> None:
    # the same sentence typeset at the top and bottom of the screen
    subs = make_subs(
        SSAEvent(start=0, end=2000, text="Hello", style="Top"),
        SSAEvent(start=100, end=2100, text="Hello", style="Bottom"),
        SSAEvent(start=5000, end=6000, text="World"),
    )
    report = subs.merge_duplicate_events()

    assert [e.plaintext for e in subs] == ["Hello", "World"]
    assert report.kept[0].line == 0
    assert report.kept[0].folded == [1]
    assert report.kept[1].line == 2
    assert report.kept[1].folded == []
    assert report.review == []
    assert set(report.merged) == {0}


def test_merge_keeps_same_line_by_two_speakers() -> None:
    # two people answering "yes" at the same time - both lines stay
    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Yes", name="Alice"),
        SSAEvent(start=0, end=1000, text="Yes", name="Bob"),
    )
    report = subs.merge_duplicate_events()

    assert len(subs) == 2
    assert report.merged == {}
    assert report.review == []


def test_merge_respects_overlap_threshold() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hello"),
        SSAEvent(start=800, end=2000, text="Hello"),
    )
    report = subs.merge_duplicate_events(overlap_threshold=0.5)
    assert len(subs) == 2
    assert report.merged == {}

    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hello"),
        SSAEvent(start=800, end=2000, text="Hello"),
    )
    report = subs.merge_duplicate_events(overlap_threshold=0.2)
    assert len(subs) == 1
    assert report.kept[0].folded == [1]


def test_merge_requires_same_source() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hello", style="Top"),
        SSAEvent(start=0, end=1000, text="Hello", style="Bottom"),
    )
    report = subs.merge_duplicate_events(source="style")
    assert len(subs) == 2
    assert report.merged == {}

    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hello", style="Top"),
        SSAEvent(start=0, end=1000, text="Hello", style="Top"),
    )
    report = subs.merge_duplicate_events(source="style")
    assert len(subs) == 1
    assert report.kept[0].folded == [1]


def test_merge_unknown_speaker_folds_into_known_group() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=2000, text="Hello", name="Alice"),
        SSAEvent(start=100, end=2100, text="Hello"),
    )
    report = subs.merge_duplicate_events()

    assert len(subs) == 1
    assert report.kept[0].line == 0
    assert report.kept[0].folded == [1]
    assert report.review == []


def test_merge_unknown_speaker_does_not_bridge_known_speakers() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=2000, text="Yes", name="Alice"),
        SSAEvent(start=100, end=2100, text="Yes"),
        SSAEvent(start=200, end=2200, text="Yes", name="Bob"),
    )
    report = subs.merge_duplicate_events()

    assert len(subs) == 3
    assert report.merged == {}
    assert len(report.review) == 1
    assert report.review[0].line == 1
    assert report.review[0].candidates == [0, 2]


def test_merge_custom_callables() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hello", name="alice", layer=1),
        SSAEvent(start=0, end=1000, text="Hello", name="ALICE", layer=2),
    )
    report = subs.merge_duplicate_events(
        source=lambda e: e.layer,
        speaker=lambda e: e.name.lower() or None,
    )
    assert len(subs) == 2  # different layers = different sources

    report = subs.merge_duplicate_events(speaker=lambda e: e.name.lower() or None)
    assert len(subs) == 1  # same speaker, case-insensitive
    assert report.kept[0].folded == [1]


def test_merge_skips_comments_drawings_and_empty_lines() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hello", type="Comment"),
        SSAEvent(start=0, end=1000, text="Hello", type="Comment"),
        SSAEvent(start=0, end=1000, text=r"{\p1}m 0 0 l 1 1{\p0}"),
        SSAEvent(start=0, end=1000, text=r"{\p1}m 0 0 l 1 1{\p0}"),
        SSAEvent(start=0, end=1000, text=""),
        SSAEvent(start=0, end=1000, text=""),
    )
    report = subs.merge_duplicate_events()
    assert len(subs) == 6
    assert report.merged == {}


def test_merge_reprocessing_preserves_provenance() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hi"),
        SSAEvent(start=0, end=1000, text="Hi"),
        SSAEvent(start=900, end=2000, text="Hi"),
    )
    report1 = subs.merge_duplicate_events(overlap_threshold=0.5)
    assert len(subs) == 2
    assert report1.kept[0].line == 0
    assert report1.kept[0].folded == [1]
    assert report1.kept[1].line == 2

    # reprocess with a looser threshold; line numbers still refer to the original file
    report2 = subs.merge_duplicate_events(overlap_threshold=0.05, previous=report1)
    assert len(subs) == 1
    assert report2.kept[0].line == 0
    assert report2.kept[0].folded == [1, 2]

    # reprocessing without new merges keeps provenance intact
    report3 = subs.merge_duplicate_events(previous=report2)
    assert report3.kept[0].line == 0
    assert report3.kept[0].folded == [1, 2]


def test_merge_reprocessing_preserves_review_items() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=2000, text="Yes", name="Alice"),
        SSAEvent(start=100, end=2100, text="Yes"),
        SSAEvent(start=200, end=2200, text="Yes", name="Bob"),
    )
    report1 = subs.merge_duplicate_events()
    report2 = subs.merge_duplicate_events(previous=report1)

    assert len(report2.review) == 1
    assert report2.review[0].line == 1
    assert report2.review[0].candidates == [0, 2]


def test_merge_invalid_threshold() -> None:
    subs = make_subs(SSAEvent(start=0, end=1000, text="Hello"))
    with pytest.raises(ValueError):
        subs.merge_duplicate_events(overlap_threshold=1.5)


def test_merge_previous_report_mismatch() -> None:
    subs = make_subs(
        SSAEvent(start=0, end=1000, text="Hello"),
        SSAEvent(start=0, end=1000, text="Hello"),
    )
    report = subs.merge_duplicate_events()
    subs.append(SSAEvent(start=5000, end=6000, text="World"))
    with pytest.raises(ValueError):
        subs.merge_duplicate_events(previous=report)
