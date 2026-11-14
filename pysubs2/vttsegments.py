"""
Import of archived WebVTT livestream segments onto a single program timeline.

When a livestream is archived as a sequence of WebVTT segments, simply concatenating
the files is not enough to reconstruct the program timeline: the media clock may
wrap around (eg. a 32-bit millisecond clock reaching its maximum), the stream may
switch to a different program source whose clock restarts near zero, and segments
may be delivered more than once.

This module implements a small state machine that places cues from individual
segments onto the program timeline, using the segment sequence numbers,
discontinuity markers and optional per-segment time maps supplied by the caller.
"""

import dataclasses
import warnings
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from .ssaevent import SSAEvent
from .warnings import QuarantinedSegmentWarning

if TYPE_CHECKING:
    from .ssafile import SSAFile

#: Wrap period of a signed 32-bit millisecond media clock (2^31 ms, ~24.86 days).
DEFAULT_WRAP_PERIOD_MS = 1 << 31


@dataclasses.dataclass
class VTTSegment:
    """
    A single archived WebVTT segment together with its timeline metadata.

    Attributes:
        sequence: Segment sequence number (eg. the HLS media sequence number).
            Segments are always processed in ascending sequence order.
        text: Contents of the WebVTT segment (a WebVTT file or fragment).
        discontinuous: True when the segment is marked as a discontinuity,
            ie. a program source boundary rather than a continuation of the
            same stream (eg. HLS ``EXT-X-DISCONTINUITY``).
        time_map: Optional time map as a ``(local_time_ms, program_time_ms)``
            pair, anchoring the segment's local media clock onto the program
            timeline. When provided, it always takes precedence over any
            inferred placement. Corresponds to data such as the WebVTT
            ``X-TIMESTAMP-MAP`` header.
    """
    sequence: int
    text: str
    discontinuous: bool = False
    time_map: tuple[int, int] | None = None


@dataclasses.dataclass
class QuarantinedVTTCue:
    """
    A cue that could not be placed on the program timeline.

    This happens for segments after a program source switch which lack a time
    map providing a new anchor - their timestamps cannot be safely borrowed
    from the previous program.

    Attributes:
        sequence: Sequence number of the segment the cue comes from.
        start: Cue start time in segment-local milliseconds.
        end: Cue end time in segment-local milliseconds.
        text: Cue text (with SubStation override tags, as in :class:`SSAEvent`).
    """
    sequence: int
    start: int
    end: int
    text: str


@dataclasses.dataclass
class SegmentedVTTImportResult:
    """
    Result of :func:`import_vtt_segments`.

    Attributes:
        subs: Subtitle file with all cues that were placed on the program
            timeline, sorted by time.
        quarantine: Cues from segments without a usable timeline anchor.
            They keep their segment-local timestamps and are not mixed into
            ``subs``. Re-delivering a quarantined segment does not add
            duplicate entries.
    """
    subs: "SSAFile"
    quarantine: list[QuarantinedVTTCue]


def _parse_segment_cues(text: str) -> list[SSAEvent]:
    from .ssafile import SSAFile
    segment_subs = SSAFile.from_string(text, "vtt")
    return [event.copy() for event in segment_subs.events]


def import_vtt_segments(
        segments: Iterable[VTTSegment],
        wrap_period_ms: int = DEFAULT_WRAP_PERIOD_MS
) -> SegmentedVTTImportResult:
    """
    Merge archived WebVTT livestream segments onto a single program timeline.

    Segments are processed in ascending ``sequence`` order. Each cue is placed
    on the program timeline according to these rules:

    - A segment with a :attr:`VTTSegment.time_map` is anchored by the map,
      ``program_time = local_time + (program_time_in_map - local_time_in_map)``.
      This handles clock wraparounds and program source switches precisely.
    - The very first segment without a time map is anchored at zero
      (its local clock is assumed to be the program clock).
    - A non-discontinuous segment without a time map continues the anchor
      of the preceding segments. When its local clock runs backwards
      (first cue starts before the previous segment ended), the media clock
      is assumed to have wrapped around and the anchor is lifted by whole
      wrap periods so that the segments join up.
    - A discontinuous segment without a time map is treated as a program
      source switch: its cues are quarantined and the timeline anchor is
      reset, so following segments cannot borrow time from the previous
      program. Segments remain quarantined until a segment with a time map
      establishes a new anchor.
    - Re-delivered segments (same sequence and identical cues) are imported
      only once, whether their cues end up on the timeline or in quarantine.

    Arguments:
        segments: WebVTT segments with their timeline metadata.
        wrap_period_ms: Media clock wrap period in milliseconds, used when
            inferring a clock wraparound without an explicit time map.
            Defaults to the signed 32-bit millisecond clock period.

    Returns:
        :class:`SegmentedVTTImportResult` with the placed subtitles and
        any quarantined cues.

    Raises:
        ValueError: If ``wrap_period_ms`` is not positive.
    """
    if wrap_period_ms <= 0:
        raise ValueError(f"wrap_period_ms must be positive, got {wrap_period_ms}")

    from .ssafile import SSAFile

    result_subs = SSAFile()
    result_subs.format = "vtt"
    quarantine: list[QuarantinedVTTCue] = []

    # offset_ms: program_time = local_time + offset_ms; None means no anchor
    offset_ms: int | None = None
    prev_local_end: int | None = None
    prev_program_end: int | None = None
    seen_cue_keys: set[tuple[int, int, int, str]] = set()
    first_segment = True
    quarantined_sequences: set[int] = set()

    for segment in sorted(segments, key=lambda item: item.sequence):
        cues = _parse_segment_cues(segment.text)

        unique_cues: list[SSAEvent] = []
        for cue in cues:
            cue_key = (segment.sequence, cue.start, cue.end, cue.text)
            if cue_key in seen_cue_keys:
                continue
            seen_cue_keys.add(cue_key)
            unique_cues.append(cue)

        if not unique_cues:
            continue

        if segment.time_map is not None:
            local_ref, program_ref = segment.time_map
            offset_ms = program_ref - local_ref
        elif segment.discontinuous:
            # Explicit program source boundary with no time map: the new
            # source clock cannot be related to the previous program, so the
            # segment is quarantined and the anchor is reset.
            _quarantine_segment(segment.sequence, unique_cues, quarantine, quarantined_sequences)
            offset_ms = None
            prev_local_end = None
            prev_program_end = None
            first_segment = False
            continue
        elif offset_ms is None:
            if first_segment:
                # No anchor and nothing on the timeline yet: assume the local
                # clock of the first segment is the program clock.
                offset_ms = 0
            else:
                # Program source switched without a new time map - the local
                # clock of the new source cannot be related to the timeline.
                _quarantine_segment(segment.sequence, unique_cues, quarantine, quarantined_sequences)
                first_segment = False
                continue
        elif prev_local_end is not None and unique_cues[0].start < prev_local_end:
            # Local clock runs backwards inside the same stream: media clock
            # wraparound. Lift the anchor by whole wrap periods so the segment
            # joins the preceding one instead of being scheduled before it.
            assert offset_ms is not None and prev_program_end is not None
            lifted_start = unique_cues[0].start + offset_ms
            periods = 1
            while lifted_start + periods * wrap_period_ms <= prev_program_end:
                periods += 1
            offset_ms += periods * wrap_period_ms

        assert offset_ms is not None
        for cue in unique_cues:
            placed = cue.copy()
            placed.start = cue.start + offset_ms
            placed.end = cue.end + offset_ms
            result_subs.events.append(placed)

        prev_local_end = unique_cues[-1].end
        prev_program_end = unique_cues[-1].end + offset_ms
        first_segment = False

    result_subs.sort()

    if quarantine:
        warnings.warn(
            f"{len(quarantine)} cue(s) from {len(quarantined_sequences)} segment(s) could not be placed "
            "on the program timeline and were returned in the quarantine list",
            QuarantinedSegmentWarning,
        )

    return SegmentedVTTImportResult(subs=result_subs, quarantine=quarantine)


def _quarantine_segment(
        sequence: int,
        cues: Sequence[SSAEvent],
        quarantine: list[QuarantinedVTTCue],
        quarantined_sequences: set[int]
) -> None:
    quarantined_sequences.add(sequence)
    for cue in cues:
        quarantine.append(QuarantinedVTTCue(
            sequence=sequence,
            start=cue.start,
            end=cue.end,
            text=cue.text,
        ))
