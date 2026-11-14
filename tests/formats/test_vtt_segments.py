"""
Tests for merging archived WebVTT livestream segments onto a program timeline.

"""

import warnings
from textwrap import dedent

import pytest

from pysubs2 import (
    SSAFile,
    VTTSegment,
    import_vtt_segments,
)
from pysubs2.warnings import QuarantinedSegmentWarning

WRAP_PERIOD_MS = 10_000


def vtt(start_ms: int, end_ms: int, text: str) -> str:
    def stamp(ms: int) -> str:
        s, ms = divmod(ms, 1000)
        m, s = divmod(s, 60)
        return f"00:{m:02d}:{s:02d}.{ms:03d}"

    return dedent(f"""\
    WEBVTT

    {stamp(start_ms)} --> {stamp(end_ms)}
    {text}
    """)


def placed_times(subs: SSAFile) -> list[tuple[int, int, str]]:
    return [(event.start, event.end, event.plaintext) for event in subs.events]


def test_segments_without_wraparound_join_naturally() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "opening")),
        VTTSegment(1, vtt(3000, 4000, "second")),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (1000, 2000, "opening"),
        (3000, 4000, "second"),
    ]
    assert result.quarantine == []


def test_clock_wraparound_lifts_later_segment() -> None:
    # Second segment's local clock restarts near zero after a wraparound.
    segments = [
        VTTSegment(0, vtt(1000, 2000, "before wrap")),
        VTTSegment(1, vtt(500, 1500, "after wrap")),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (1000, 2000, "before wrap"),
        (10_500, 11_500, "after wrap"),
    ]
    # Events are ordered on the program timeline, not the raw local clocks.
    assert [event.start for event in result.subs.events] == sorted(event.start for event in result.subs.events)


def test_time_map_handles_wraparound_exactly() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "before wrap")),
        VTTSegment(1, vtt(500, 1500, "after wrap"), time_map=(500, 9000)),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (1000, 2000, "before wrap"),
        (9000, 10_000, "after wrap"),
    ]


def test_time_map_can_anchor_first_segment_away_from_zero() -> None:
    segments = [
        VTTSegment(0, vtt(0, 1000, "late start"), time_map=(0, 60_000)),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [(60_000, 61_000, "late start")]


def test_duplicate_segment_is_imported_once() -> None:
    segment = VTTSegment(0, vtt(1000, 2000, "delivered twice"))
    result = import_vtt_segments([segment, segment], wrap_period_ms=WRAP_PERIOD_MS)

    assert len(result.subs) == 1
    assert placed_times(result.subs) == [(1000, 2000, "delivered twice")]


def test_duplicate_segment_among_others_is_imported_once() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "first")),
        VTTSegment(1, vtt(3000, 4000, "second")),
        VTTSegment(0, vtt(1000, 2000, "first")),  # redelivery out of order
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (1000, 2000, "first"),
        (3000, 4000, "second"),
    ]


def test_discontinuous_segment_without_map_is_quarantined() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "program one")),
        VTTSegment(1, vtt(100, 200, "new program"), discontinuous=True),
        VTTSegment(2, vtt(300, 400, "still new program")),
    ]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [(1000, 2000, "program one")]

    quarantined = [(cue.sequence, cue.start, cue.end, cue.text) for cue in result.quarantine]
    assert quarantined == [
        (1, 100, 200, "new program"),
        (2, 300, 400, "still new program"),
    ]
    assert any(issubclass(item.category, QuarantinedSegmentWarning) for item in caught)


def test_quarantined_segments_do_not_borrow_previous_program_time() -> None:
    segments = [
        VTTSegment(0, vtt(9000, 9500, "program one")),
        VTTSegment(1, vtt(100, 200, "program two"), discontinuous=True),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    # Local timestamps are preserved as-is, no wrap lifting against program one.
    assert [(cue.start, cue.end) for cue in result.quarantine] == [(100, 200)]


def test_new_time_map_re_anchors_after_source_switch() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "program one")),
        VTTSegment(1, vtt(100, 200, "program two"), discontinuous=True),
        VTTSegment(2, vtt(0, 500, "program two anchored"), time_map=(0, 120_000)),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (1000, 2000, "program one"),
        (120_000, 120_500, "program two anchored"),
    ]
    assert [cue.sequence for cue in result.quarantine] == [1]


def test_discontinuous_segment_with_map_is_placed() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "program one")),
        VTTSegment(1, vtt(0, 500, "program two"), discontinuous=True, time_map=(0, 30_000)),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert result.quarantine == []
    assert placed_times(result.subs) == [
        (1000, 2000, "program one"),
        (30_000, 30_500, "program two"),
    ]


def test_redelivered_quarantined_segment_stays_single_entry() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "program one")),
        VTTSegment(1, vtt(100, 200, "new program"), discontinuous=True),
        VTTSegment(1, vtt(100, 200, "new program"), discontinuous=True),
    ]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert len(result.quarantine) == 1


def test_segments_are_processed_in_sequence_order() -> None:
    segments = [
        VTTSegment(1, vtt(3000, 4000, "second")),
        VTTSegment(0, vtt(1000, 2000, "first")),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (1000, 2000, "first"),
        (3000, 4000, "second"),
    ]


def test_invalid_wrap_period_raises() -> None:
    with pytest.raises(ValueError):
        import_vtt_segments([VTTSegment(0, vtt(0, 1000, "x"))], wrap_period_ms=0)


def test_empty_segments() -> None:
    result = import_vtt_segments([], wrap_period_ms=WRAP_PERIOD_MS)
    assert len(result.subs) == 0
    assert result.quarantine == []


def test_empty_segment_does_not_reset_anchor() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "first")),
        VTTSegment(1, "WEBVTT\n"),
        VTTSegment(2, vtt(500, 1500, "after wrap")),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (1000, 2000, "first"),
        (WRAP_PERIOD_MS + 500, WRAP_PERIOD_MS + 1500, "after wrap"),
    ]


def test_wraparound_spanning_multiple_periods() -> None:
    # The local clock ran well past one wrap period before jumping backwards.
    segments = [
        VTTSegment(0, vtt(24_000, 25_000, "long running clock")),
        VTTSegment(1, vtt(300, 400, "after big wrap")),
    ]

    result = import_vtt_segments(segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert placed_times(result.subs) == [
        (24_000, 25_000, "long running clock"),
        (30_300, 30_400, "after big wrap"),
    ]


def test_reader_integration_via_from_string() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "program one")),
        VTTSegment(1, vtt(100, 200, "program two"), discontinuous=True),
        VTTSegment(2, vtt(500, 1500, "program two, no map yet")),
        VTTSegment(3, vtt(0, 500, "program two anchored"), time_map=(0, 120_000)),
    ]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        subs = SSAFile.from_string("WEBVTT\n", "vtt", segments=segments)

    assert [(event.start, event.end, event.plaintext) for event in subs.events] == [
        (1000, 2000, "program one"),
        (120_000, 120_500, "program two anchored"),
    ]
    assert [(cue.sequence, cue.start, cue.end) for cue in subs.quarantine] == [
        (1, 100, 200),
        (2, 500, 1500),
    ]


def test_reader_without_segments_parses_plain_vtt() -> None:
    text = vtt(1000, 2000, "plain file")
    subs = SSAFile.from_string(text, "vtt")

    assert len(subs) == 1
    assert subs.events[0].start == 1000
    assert subs.quarantine == []


def test_reader_passes_through_wrap_period() -> None:
    segments = [
        VTTSegment(0, vtt(1000, 2000, "before wrap")),
        VTTSegment(1, vtt(500, 1500, "after wrap")),
    ]

    subs = SSAFile.from_string("WEBVTT\n", "vtt", segments=segments, wrap_period_ms=WRAP_PERIOD_MS)

    assert [(event.start, event.end) for event in subs.events] == [
        (1000, 2000),
        (WRAP_PERIOD_MS + 500, WRAP_PERIOD_MS + 1500),
    ]
