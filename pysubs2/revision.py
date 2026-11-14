"""
Three-way merging of proofread subtitle revisions.

The original subtitle file is the common ancestor.  Every reviewer submits a
complete revised copy of it.  :func:`merge_revisions` compares each revision
with the original and merges changes field by field, so retiming one event does
not overwrite another reviewer's text edit.

Text changes are attributed down to the edited span.  When one reviewer splits
an event, text edits from other reviewers are mapped into the resulting events:
an edit which fits exactly one of them is applied there, while an edit with two
or more possible destinations is reported in ``MergeResult.unresolved``.

Example::

    import pysubs2

    base = pysubs2.load("original.srt")
    merged = pysubs2.merge_revisions(base, {
        "甲": pysubs2.load("retimed.srt"),
        "乙": pysubs2.load("proofread.srt"),
        "丙": pysubs2.load("split.srt"),
    })
    merged.subs.save("merged.srt")

The result also keeps a :class:`MergeGroup` for every event of the original
file.  A group records the original time range and text, the resulting event or
events, and who contributed each field, which makes the merged revision
traceable per original subtitle.
"""

import difflib
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .ssaevent import SSAEvent
from .ssafile import SSAFile

__all__ = [
    "EventProvenance",
    "MergeConflict",
    "MergeGroup",
    "MergeResult",
    "Placement",
    "Revision",
    "TextEdit",
    "UnresolvedChange",
    "merge_revisions",
]

#: Text fields which are merged independently from timing and text.
_METADATA_FIELDS = (
    "marked",
    "layer",
    "style",
    "name",
    "marginl",
    "marginr",
    "marginv",
    "effect",
    "type",
)

_DELETE_COST = 1.0
_INSERT_COST = 1.0
_MAX_SPLIT_EVENTS = 4
_FULL_ALIGNMENT_LIMIT = 250_000
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Revision:
    """A named proofread revision of the base subtitle file."""

    author: str
    subs: SSAFile


@dataclass(frozen=True)
class Placement:
    """A possible position for an edit inside one resulting event segment."""

    segment: int
    start: int
    end: int

    def to_dict(self) -> dict[str, int]:
        return {"segment": self.segment, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class TextEdit:
    """A text edit which was applied to the merged subtitles."""

    authors: tuple[str, ...]
    base_index: int
    base_start: int
    base_end: int
    old: str
    new: str
    segment: int
    target_start: int
    target_end: int

    @property
    def author(self) -> str:
        """Comma-separated names of the reviewers who provided this edit."""
        return ", ".join(self.authors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "authors": list(self.authors),
            "base_index": self.base_index,
            "base_span": [self.base_start, self.base_end],
            "old": self.old,
            "new": self.new,
            "segment": self.segment,
            "target_span": [self.target_start, self.target_end],
        }


@dataclass(frozen=True)
class UnresolvedChange:
    """
    A text change which could not be placed unambiguously.

    ``candidates`` lists every possible destination.  A typical case is an
    insertion exactly at the boundary between two events produced by a split:
    it may belong to either event, so it is reported here instead of being
    guessed.
    """

    author: str
    base_index: int
    base_start: int
    base_end: int
    old: str
    new: str
    candidates: tuple[Placement, ...] = ()
    reason: str = "ambiguous placement"

    def to_dict(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "base_index": self.base_index,
            "base_span": [self.base_start, self.base_end],
            "old": self.old,
            "new": self.new,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MergeConflict:
    """Changes which cannot be merged because they disagree."""

    base_index: int
    field: str
    authors: tuple[str, ...]
    values: dict[str, str]
    reason: str = "conflicting edits"

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_index": self.base_index,
            "field": self.field,
            "authors": list(self.authors),
            "values": self.values,
            "reason": self.reason,
        }


@dataclass
class EventProvenance:
    """Attribution of one event in the merged subtitles."""

    base_index: int
    segment: int
    authors: tuple[str, ...]
    field_authors: dict[str, tuple[str, ...]] = field(default_factory=dict)
    split_by: str | None = None
    source_events: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_index": self.base_index,
            "segment": self.segment,
            "authors": list(self.authors),
            "field_authors": {
                name: list(names) for name, names in self.field_authors.items()
            },
            "split_by": self.split_by,
            "source_events": {
                author: list(indices) for author, indices in self.source_events.items()
            },
        }


@dataclass
class MergeGroup:
    """
    One original subtitle event and the merged event(s) produced from it.

    ``base_index``, ``base_start``, ``base_end`` and ``base_text`` identify the
    original sentence range.  ``events`` contains its replacement in the merged
    file, which may be several events after a split.  The group is the unit in
    which a revision can be submitted back to storage.
    """

    base_index: int
    base_start: int
    base_end: int
    base_text: str
    base_plaintext: str
    events: list[SSAEvent]
    provenance: list[EventProvenance]
    edits: list[TextEdit] = field(default_factory=list)
    split_by: str | None = None
    author_order: tuple[str, ...] = ()

    @property
    def authors(self) -> tuple[str, ...]:
        """All reviewers who changed this original event, in input order."""
        changed = {
            author
            for item in self.provenance
            for author in item.authors
            if author != "base"
        }
        ordered = [author for author in self.author_order if author in changed]
        if not ordered:
            return ("base",)
        return tuple(ordered)

    @property
    def timing_authors(self) -> tuple[str, ...]:
        """Reviewers who changed this event's ``start`` or ``end``."""
        changed = {
            author
            for item in self.provenance
            for field_name in ("start", "end")
            for author in item.field_authors.get(field_name, ())
            if author != "base"
        }
        ordered = [author for author in self.author_order if author in changed]
        return tuple(ordered) or ("base",)

    @property
    def text_authors(self) -> tuple[str, ...]:
        """Reviewers who changed this event's text."""
        changed = {
            author
            for item in self.provenance
            for author in item.field_authors.get("text", ())
            if author != "base"
        }
        ordered = [author for author in self.author_order if author in changed]
        return tuple(ordered) or ("base",)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_index": self.base_index,
            "base_span": {"start": self.base_start, "end": self.base_end},
            "base_text": self.base_text,
            "base_plaintext": self.base_plaintext,
            "split_by": self.split_by,
            "authors": list(self.authors),
            "timing_authors": list(self.timing_authors),
            "text_authors": list(self.text_authors),
            "events": [
                {
                    "start": event.start,
                    "end": event.end,
                    "text": event.text,
                    "plaintext": event.plaintext,
                    "provenance": provenance.to_dict(),
                }
                for event, provenance in zip(self.events, self.provenance)
            ],
            "edits": [edit.to_dict() for edit in self.edits],
        }


@dataclass
class MergeResult:
    """Result of :func:`merge_revisions`."""

    subs: SSAFile
    groups: list[MergeGroup]
    unresolved: list[UnresolvedChange]
    conflicts: list[MergeConflict]
    authors: tuple[str, ...]

    @property
    def events(self) -> list[SSAEvent]:
        """The merged subtitle events (same list as ``result.subs.events``)."""
        return self.subs.events

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable description of the merged revision."""
        return {
            "authors": list(self.authors),
            "groups": [group.to_dict() for group in self.groups],
            "unresolved": [change.to_dict() for change in self.unresolved],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
        }


@dataclass(frozen=True)
class _AlignedGroup:
    base_index: int | None
    rev_indices: tuple[int, ...]


@dataclass
class _TextMerge:
    segments: list[str]
    edits: list[TextEdit]
    segment_authors: list[tuple[str, ...]]


@dataclass
class _PendingEdit:
    authors: tuple[str, ...]
    base_index: int
    base_start: int
    base_end: int
    old: str
    new: str
    placement: Placement


def merge_revisions(
        base: SSAFile,
        revisions: Mapping[str, SSAFile] | Iterable[Revision] | Iterable[tuple[str, SSAFile]],
        *,
        max_split_events: int = _MAX_SPLIT_EVENTS,
) -> MergeResult:
    """
    Merge proofread revisions against their common original file.

    Each reviewer supplies a complete revised copy of ``base``.  Changes are
    merged independently per field:

    - a timing-only revision changes ``start`` and ``end`` only,
    - a text revision changes the edited text spans only,
    - a split revision replaces one original event by several events,
    - every field nobody changed is taken from ``base``.

    Because unchanged fields come from the base rather than from an arbitrary
    revision, a timing-only proofread copy cannot revert someone else's text.
    Text edits from other reviewers are applied to split events when their
    original span maps to exactly one of the new events; edits at a split
    boundary with several possible destinations are listed in
    :attr:`MergeResult.unresolved`.

    Arguments:
        base: Original subtitle file, the common ancestor.
        revisions: Mapping of reviewer name to revised :class:`SSAFile`, or an
            iterable of :class:`Revision` or ``(name, SSAFile)`` pairs.
        max_split_events: Maximum number of events one original event may be
            split into while aligning a revision.

    Returns:
        A :class:`MergeResult` containing the merged file, one
        :class:`MergeGroup` per original event, provenance, unresolved changes
        and conflicts.

    Raises:
        TypeError: If an input is not an :class:`SSAFile`.
        ValueError: If reviewer names are empty or duplicated, or if
            ``max_split_events`` is not positive.
    """
    if not isinstance(base, SSAFile):
        raise TypeError("base must be an SSAFile")
    if max_split_events < 1:
        raise ValueError("max_split_events must be positive")

    named_revisions = _normalize_revisions(revisions)
    author_order = tuple(author for author, _ in named_revisions)

    if not named_revisions:
        return MergeResult(
            subs=_copy_file(base),
            groups=_identity_groups(base),
            unresolved=[],
            conflicts=[],
            authors=author_order,
        )

    aligned = {
        author: _align_events(base.events, revision.events, max_split_events)
        for author, revision in named_revisions
    }
    revision_by_author = dict(named_revisions)

    groups: list[MergeGroup] = []
    unresolved: list[UnresolvedChange] = []
    conflicts: list[MergeConflict] = []

    _record_alignment_conflicts(
        base=base,
        named_revisions=named_revisions,
        aligned=aligned,
        conflicts=conflicts,
    )

    for base_index, base_event in enumerate(base.events):
        groups_for_author = {
            author: _group_for_base(aligned[author], base_index)
            for author in author_order
        }
        events_for_author = {
            author: _events_for_group(revision_by_author[author], aligned[author], base_index)
            for author in author_order
        }

        structural = _choose_structural_group(
            base_index=base_index,
            author_order=author_order,
            groups_for_author=groups_for_author,
            conflicts=conflicts,
        )

        if structural is None:
            split_by = None
            target_events = [base_event]
        else:
            split_by, split_indices = structural
            target_events = [revision_by_author[split_by].events[index] for index in split_indices]

        text_merge = _merge_text(
            base_event=base_event,
            base_index=base_index,
            author_order=author_order,
            events_for_author=events_for_author,
            split_by=split_by,
            target_events=target_events,
            unresolved=unresolved,
            conflicts=conflicts,
        )

        merged_events: list[SSAEvent] = []
        provenances: list[EventProvenance] = []

        for segment_index in range(len(text_merge.segments)):
            target_event = target_events[segment_index]
            merged_event = target_event.copy()
            merged_event.text = text_merge.segments[segment_index]

            start_authors, end_authors = _merge_timing(
                merged_event=merged_event,
                base_event=base_event,
                base_index=base_index,
                author_order=author_order,
                events_for_author=events_for_author,
                split_by=split_by,
                target_event=target_event,
                conflicts=conflicts,
            )

            metadata_authors = _merge_metadata_fields(
                merged_event=merged_event,
                base_event=base_event,
                base_index=base_index,
                author_order=author_order,
                events_for_author=events_for_author,
                split_by=split_by,
                target_events=target_events,
                segment_index=segment_index,
                conflicts=conflicts,
            )

            field_authors: dict[str, tuple[str, ...]] = {}
            if text_merge.segment_authors[segment_index] != ("base",):
                field_authors["text"] = text_merge.segment_authors[segment_index]
            if start_authors and start_authors != ("base",):
                field_authors["start"] = start_authors
            if end_authors and end_authors != ("base",):
                field_authors["end"] = end_authors
            field_authors.update(metadata_authors)

            contributors = {
                author
                for names in field_authors.values()
                for author in names
                if author != "base"
            }
            if split_by is not None:
                contributors.add(split_by)

            provenance = EventProvenance(
                base_index=base_index,
                segment=segment_index,
                authors=tuple(
                    author for author in author_order if author in contributors
                ) or ("base",),
                field_authors=field_authors,
                split_by=split_by,
                source_events=_source_events(
                    author_order=author_order,
                    groups_for_author=groups_for_author,
                    split_by=split_by,
                    segment_index=segment_index,
                ),
            )
            provenances.append(provenance)
            merged_events.append(merged_event)

        groups.append(MergeGroup(
            base_index=base_index,
            base_start=base_event.start,
            base_end=base_event.end,
            base_text=base_event.text,
            base_plaintext=base_event.plaintext,
            events=merged_events,
            provenance=provenances,
            edits=text_merge.edits,
            split_by=split_by,
            author_order=author_order,
        ))

    merged_subs = _copy_file(base)
    merged_subs.events = [event for group in groups for event in group.events]

    return MergeResult(
        subs=merged_subs,
        groups=groups,
        unresolved=unresolved,
        conflicts=conflicts,
        authors=author_order,
    )


def _normalize_revisions(
        revisions: Mapping[str, SSAFile] | Iterable[Revision] | Iterable[tuple[str, SSAFile]],
) -> list[tuple[str, SSAFile]]:
    if isinstance(revisions, Mapping):
        items = list(revisions.items())
    else:
        items = []
        for item in revisions:
            if isinstance(item, Revision):
                items.append((item.author, item.subs))
            else:
                try:
                    author, subs = item
                except (TypeError, ValueError):
                    raise TypeError(
                        "revisions must be a mapping, Revision instances, "
                        "or (author, SSAFile) pairs"
                    )
                items.append((author, subs))

    seen: set[str] = set()
    for author, subs in items:
        if not isinstance(author, str) or not author:
            raise ValueError("Each revision must have a non-empty author name")
        if author in seen:
            raise ValueError(f"Duplicate revision author: {author!r}")
        seen.add(author)
        if not isinstance(subs, SSAFile):
            raise TypeError(f"Revision {author!r} is not an SSAFile")

    return items


def _copy_file(source: SSAFile) -> SSAFile:
    subs = SSAFile()
    subs.styles = {name: style.copy() for name, style in source.styles.items()}
    subs.info = dict(source.info)
    subs.aegisub_project = dict(source.aegisub_project)
    subs.fonts_opaque = dict(source.fonts_opaque)
    subs.graphics_opaque = dict(source.graphics_opaque)
    subs.fps = source.fps
    subs.format = source.format
    subs.events = [event.copy() for event in source.events]
    return subs


def _identity_groups(base: SSAFile) -> list[MergeGroup]:
    groups = []
    for index, event in enumerate(base.events):
        groups.append(MergeGroup(
            base_index=index,
            base_start=event.start,
            base_end=event.end,
            base_text=event.text,
            base_plaintext=event.plaintext,
            events=[event.copy()],
            provenance=[EventProvenance(
                base_index=index,
                segment=0,
                authors=("base",),
            )],
        ))
    return groups


def _alignment_text(event: SSAEvent) -> str:
    return _WHITESPACE.sub(" ", event.plaintext).strip()


def _similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


def _time_similarity(left: SSAEvent, right: SSAEvent) -> float:
    union = max(left.end, right.end) - min(left.start, right.start)
    if union <= 0:
        return 1.0
    intersection = max(0, min(left.end, right.end) - max(left.start, right.start))
    return intersection / union


def _pair_cost(base_event: SSAEvent, rev_event: SSAEvent) -> float:
    text_score = _similarity(_alignment_text(base_event), _alignment_text(rev_event))
    time_score = _time_similarity(base_event, rev_event)
    return 0.35 + 0.9 * (1.0 - text_score) + 0.2 * (1.0 - time_score)


def _split_cost(base_event: SSAEvent, rev_events: Sequence[SSAEvent]) -> float:
    if any(not _alignment_text(event) for event in rev_events):
        return math.inf

    base_text = _alignment_text(base_event)
    parts = [_alignment_text(event) for event in rev_events]
    similarity = max(
        _similarity(base_text, " ".join(parts)),
        _similarity(base_text, "".join(parts)),
    )
    if similarity < 0.55:
        return math.inf

    best_single = max(
        _similarity(base_text, part)
        for part in parts
    )
    # Two unrelated short events can look like a plausible "split" to a plain
    # similarity ratio.  Concatenating the parts must explain the original
    # text better than any one part alone does.
    if similarity < best_single + 0.15 and similarity < 1.0:
        return math.inf

    first = rev_events[0]
    last = rev_events[-1]
    duration = max(base_event.duration, 1)
    outside = max(0, base_event.start - first.start) + max(0, last.end - base_event.end)
    time_penalty = min(0.3, outside / duration)
    return (
        0.15
        + 0.25 * (len(rev_events) - 1)
        + 0.9 * (1.0 - similarity)
        + 0.15 * time_penalty
    )


def _align_events(
        base_events: Sequence[SSAEvent],
        rev_events: Sequence[SSAEvent],
        max_split_events: int,
) -> list[_AlignedGroup]:
    """
    Align revised events to original ones.

    The dynamic program allows pairing, splitting (one original event to
    several revised events), deleting an original event, and inserting a
    revised event.

    Only text which occurs exactly once in each file is used as an anchor.
    Repeated lines are never anchored by a text-only heuristic; they are
    resolved by the DP using both text and timing.  This prevents a common
    failure mode where a run of identical short lines shifts the alignment.
    """
    anchors = _find_unique_anchors(base_events, rev_events)

    aligned: list[_AlignedGroup] = []
    base_offset = 0
    rev_offset = 0

    for base_index, rev_index in anchors:
        aligned.extend(_align_gap(
            base_events[base_offset:base_index],
            rev_events[rev_offset:rev_index],
            base_offset=base_offset,
            rev_offset=rev_offset,
            max_split_events=max_split_events,
        ))
        aligned.append(_AlignedGroup(
            base_index=base_index,
            rev_indices=(rev_index,),
        ))
        base_offset = base_index + 1
        rev_offset = rev_index + 1

    aligned.extend(_align_gap(
        base_events[base_offset:],
        rev_events[rev_offset:],
        base_offset=base_offset,
        rev_offset=rev_offset,
        max_split_events=max_split_events,
    ))

    return aligned


def _find_unique_anchors(
        base_events: Sequence[SSAEvent],
        rev_events: Sequence[SSAEvent],
) -> list[tuple[int, int]]:
    """
    Find (base_index, rev_index) pairs suitable as alignment anchors.

    The text must occur exactly once in each file.  In addition, it must be
    either reasonably long or overlap in time; a one-character line repeated
    elsewhere in the file is not enough to identify an event.  Anything not
    anchored is resolved by the DP using both text and timing.
    """
    base_counts: dict[str, int] = {}
    base_indices: dict[str, int] = {}
    for index, event in enumerate(base_events):
        text = _alignment_text(event)
        base_counts[text] = base_counts.get(text, 0) + 1
        base_indices[text] = index

    rev_counts: dict[str, int] = {}
    rev_indices: dict[str, int] = {}
    for index, event in enumerate(rev_events):
        text = _alignment_text(event)
        rev_counts[text] = rev_counts.get(text, 0) + 1
        rev_indices[text] = index

    candidates: list[tuple[int, int]] = []
    for text, base_index in base_indices.items():
        if base_counts[text] != 1 or rev_counts.get(text) != 1:
            continue

        rev_index = rev_indices[text]
        if len(text) < 8 and _time_similarity(
                base_events[base_index],
                rev_events[rev_index],
        ) <= 0:
            continue

        candidates.append((base_index, rev_index))

    candidates.sort()

    anchors: list[tuple[int, int]] = []
    last_base = -1
    last_rev = -1
    for base_index, rev_index in candidates:
        if base_index <= last_base or rev_index <= last_rev:
            continue
        anchors.append((base_index, rev_index))
        last_base = base_index
        last_rev = rev_index

    return anchors


def _align_gap(
        base_events: Sequence[SSAEvent],
        rev_events: Sequence[SSAEvent],
        *,
        base_offset: int,
        rev_offset: int,
        max_split_events: int,
) -> list[_AlignedGroup]:
    n = len(base_events)
    m = len(rev_events)

    if n == 0:
        return [
            _AlignedGroup(base_index=None, rev_indices=(rev_offset + index,))
            for index in range(m)
        ]

    if m == 0:
        return [
            _AlignedGroup(base_index=base_offset + index, rev_indices=())
            for index in range(n)
        ]

    if n == m and _pairwise_plausible(base_events, rev_events):
        return [
            _AlignedGroup(
                base_index=base_offset + index,
                rev_indices=(rev_offset + index,),
            )
            for index in range(n)
        ]

    if n * m > _FULL_ALIGNMENT_LIMIT:
        return _align_proportionally(
            base_events,
            rev_events,
            base_offset=base_offset,
            rev_offset=rev_offset,
        )

    return _align_block(
        base_events,
        rev_events,
        base_offset=base_offset,
        rev_offset=rev_offset,
        max_split_events=max_split_events,
    )


def _pairwise_plausible(
        base_events: Sequence[SSAEvent],
        rev_events: Sequence[SSAEvent],
) -> bool:
    """Whether equal-length gaps can safely be paired one to one."""
    for base_event, rev_event in zip(base_events, rev_events):
        text_score = _similarity(_alignment_text(base_event), _alignment_text(rev_event))
        time_score = _time_similarity(base_event, rev_event)
        if text_score < 0.4 and time_score < 0.3:
            return False
    return True


def _align_proportionally(
        base_events: Sequence[SSAEvent],
        rev_events: Sequence[SSAEvent],
        *,
        base_offset: int,
        rev_offset: int,
) -> list[_AlignedGroup]:
    """
    Bounded fallback for huge, heavily rewritten regions.

    Events are paired in order; surplus revised events are recorded as
    insertions and surplus original events as deletions.  This is conservative:
    no split is invented, and the resulting structure mismatch is reported.
    """
    aligned: list[_AlignedGroup] = []
    common = min(len(base_events), len(rev_events))

    for index in range(common):
        aligned.append(_AlignedGroup(
            base_index=base_offset + index,
            rev_indices=(rev_offset + index,),
        ))
    for index in range(common, len(base_events)):
        aligned.append(_AlignedGroup(
            base_index=base_offset + index,
            rev_indices=(),
        ))
    for index in range(common, len(rev_events)):
        aligned.append(_AlignedGroup(
            base_index=None,
            rev_indices=(rev_offset + index,),
        ))

    return aligned


def _align_block(
        base_events: Sequence[SSAEvent],
        rev_events: Sequence[SSAEvent],
        *,
        base_offset: int,
        rev_offset: int,
        max_split_events: int,
) -> list[_AlignedGroup]:
    n = len(base_events)
    m = len(rev_events)

    if n == 0:
        return [
            _AlignedGroup(base_index=None, rev_indices=(rev_offset + index,))
            for index in range(m)
        ]

    infinity = math.inf
    costs = [[infinity] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, str, int] | None]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    costs[0][0] = 0.0

    def relax(new_i: int, new_j: int, new_cost: float,
              step: tuple[int, int, str, int]) -> None:
        if new_cost < costs[new_i][new_j] - 1e-12:
            costs[new_i][new_j] = new_cost
            back[new_i][new_j] = step

    for i in range(n + 1):
        for j in range(m + 1):
            current = costs[i][j]
            if current == infinity:
                continue

            if i < n and j < m:
                relax(i + 1, j + 1, current + _pair_cost(base_events[i], rev_events[j]),
                       (i, j, "pair", 1))

            if i < n:
                max_count = min(max_split_events, m - j)
                for count in range(2, max_count + 1):
                    split_cost = _split_cost(base_events[i], rev_events[j:j + count])
                    if split_cost < infinity:
                        relax(i + 1, j + count, current + split_cost,
                               (i, j, "split", count))

                relax(i + 1, j, current + _DELETE_COST, (i, j, "delete", 0))

            if j < m:
                relax(i, j + 1, current + _INSERT_COST, (i, j, "insert", 0))

    aligned: list[_AlignedGroup] = []
    i, j = n, m
    while i > 0 or j > 0:
        step = back[i][j]
        if step is None:
            raise RuntimeError("Could not align subtitle events")

        previous_i, previous_j, kind, count = step
        if kind == "pair":
            aligned.append(_AlignedGroup(
                base_index=base_offset + previous_i,
                rev_indices=(rev_offset + previous_j,),
            ))
        elif kind == "split":
            aligned.append(_AlignedGroup(
                base_index=base_offset + previous_i,
                rev_indices=tuple(
                    rev_offset + index
                    for index in range(previous_j, previous_j + count)
                ),
            ))
        elif kind == "delete":
            aligned.append(_AlignedGroup(
                base_index=base_offset + previous_i,
                rev_indices=(),
            ))
        elif kind == "insert":
            aligned.append(_AlignedGroup(
                base_index=None,
                rev_indices=(rev_offset + previous_j,),
            ))
        else:
            raise RuntimeError(f"Unknown alignment step: {kind}")

        i, j = previous_i, previous_j

    aligned.reverse()
    return aligned


def _group_for_base(
        groups: Sequence[_AlignedGroup],
        base_index: int,
) -> _AlignedGroup | None:
    for group in groups:
        if group.base_index == base_index:
            return group
    return None


def _events_for_group(
        subs: SSAFile,
        groups: Sequence[_AlignedGroup],
        base_index: int,
) -> list[SSAEvent]:
    group = _group_for_base(groups, base_index)
    if group is None:
        return []
    return [subs.events[index] for index in group.rev_indices]


def _choose_structural_group(
        *,
        base_index: int,
        author_order: tuple[str, ...],
        groups_for_author: Mapping[str, _AlignedGroup | None],
        conflicts: list[MergeConflict],
) -> tuple[str, tuple[int, ...]] | None:
    splits: list[tuple[str, tuple[int, ...]]] = []
    for author in author_order:
        group = groups_for_author[author]
        if group is not None and len(group.rev_indices) > 1:
            splits.append((author, group.rev_indices))

    if not splits:
        return None

    if len(splits) == 1:
        return splits[0]

    conflicts.append(MergeConflict(
        base_index=base_index,
        field="structure",
        authors=tuple(author for author, _ in splits),
        values={
            author: f"split into {len(indices)} events"
            for author, indices in splits
        },
        reason="multiple incompatible splits",
    ))
    return splits[0]


def _merge_text(
        *,
        base_event: SSAEvent,
        base_index: int,
        author_order: tuple[str, ...],
        events_for_author: Mapping[str, list[SSAEvent]],
        split_by: str | None,
        target_events: Sequence[SSAEvent],
        unresolved: list[UnresolvedChange],
        conflicts: list[MergeConflict],
) -> _TextMerge:
    if split_by is None:
        segments = [base_event.text]
        target_map = None
        pure_split = False
    else:
        segments = [event.text for event in target_events]
        target_text = "".join(segments)
        target_map = _TargetMap(base_event.text, target_text)
        # A pure split only distributes the original text between events.  In
        # that case an edit can be checked by reconstructing the reviewer's
        # sentence exactly, which resolves many otherwise ambiguous hunks.
        pure_split = target_text == base_event.text

    pending: list[_PendingEdit] = []

    for author in author_order:
        if author == split_by:
            continue

        author_events = events_for_author[author]
        if len(author_events) != 1:
            continue

        edited_text = author_events[0].text
        if edited_text == base_event.text:
            continue
        if author_events[0].plaintext == base_event.plaintext:
            # The visible words are unchanged; differences are only in markup
            # representation.  Keep the original markup rather than letting a
            # retiming-only round trip overwrite it.
            continue

        author_edits, author_unresolved = _collect_author_edits(
            author=author,
            base_event=base_event,
            base_index=base_index,
            edited_text=edited_text,
            segments=segments,
            target_map=target_map,
            pure_split=pure_split,
        )

        pending.extend(author_edits)
        unresolved.extend(author_unresolved)

    segment_authors: list[set[str]] = [
        {split_by} if split_by is not None else set()
        for _ in segments
    ]
    applied_edits: list[TextEdit] = []

    by_segment: dict[int, list[_PendingEdit]] = {}
    for edit in pending:
        by_segment.setdefault(edit.placement.segment, []).append(edit)

    for segment_index in sorted(by_segment):
        accepted = _resolve_segment_edits(
            edits=by_segment[segment_index],
            base_index=base_index,
            author_order=author_order,
            conflicts=conflicts,
        )

        text = segments[segment_index]
        for edit in sorted(
                accepted,
                key=lambda item: (item.placement.start, item.placement.end),
                reverse=True,
        ):
            start = edit.placement.start
            end = edit.placement.end
            text = text[:start] + edit.new + text[end:]
            segment_authors[segment_index].update(edit.authors)
            applied_edits.append(TextEdit(
                authors=edit.authors,
                base_index=edit.base_index,
                base_start=edit.base_start,
                base_end=edit.base_end,
                old=edit.old,
                new=edit.new,
                segment=segment_index,
                target_start=start,
                target_end=end,
            ))
        segments[segment_index] = text

    normalized_authors = [
        tuple(author for author in author_order if author in names)
        if names
        else ("base",)
        for names in segment_authors
    ]

    return _TextMerge(
        segments=segments,
        edits=applied_edits,
        segment_authors=normalized_authors,
    )


def _edits_reproduce_text(
        *,
        edited_text: str,
        segments: Sequence[str],
        edits: Sequence[_PendingEdit],
) -> bool:
    """
    Check whether mapped edits turn the split text into the reviewer's text.

    ``segments`` are the split events' texts before other reviewers' edits are
    applied.  Applying all of this reviewer's edits must yield exactly the
    text they submitted.
    """
    rebuilt_segments = list(segments)
    by_segment: dict[int, list[_PendingEdit]] = {}
    for edit in edits:
        by_segment.setdefault(edit.placement.segment, []).append(edit)

    for segment_index, segment_edits in by_segment.items():
        segment_text = rebuilt_segments[segment_index]
        for edit in sorted(
                segment_edits,
                key=lambda item: (item.placement.start, item.placement.end),
                reverse=True,
        ):
            segment_text = (
                segment_text[:edit.placement.start]
                + edit.new
                + segment_text[edit.placement.end:]
            )
        rebuilt_segments[segment_index] = segment_text

    return "".join(rebuilt_segments) == edited_text


def _decompose_deletion_across_segments(
        *,
        author: str,
        base_index: int,
        base_start: int,
        base_end: int,
        old: str,
        segments: Sequence[str],
        target_map: "_TargetMap",
) -> list[_PendingEdit] | None:
    """
    Turn a deletion spanning a split boundary into one deletion per segment.

    Deleting "defg" from "abcde|fghij" is equivalent to removing "de" from the
    first event and "fg" from the second.  The decomposition is accepted only
    if the concatenated segments then equal the reviewer's text, so this never
    invents a partial edit.  A replacement crossing a boundary is not
    decomposed, because its destination is genuinely ambiguous.
    """
    joined = "".join(segments)
    spans = target_map.map_hunk(base_start, base_end)
    if len(spans) != 1:
        return None

    target_start, target_end = spans[0]
    if not (0 <= target_start < target_end <= len(joined)):
        return None
    if joined[target_start:target_end] != old:
        return None

    offsets: list[tuple[int, int]] = []
    offset = 0
    for segment in segments:
        offsets.append((offset, offset + len(segment)))
        offset += len(segment)

    edits: list[_PendingEdit] = []
    for segment_index, (segment_start, segment_end) in enumerate(offsets):
        overlap_start = max(target_start, segment_start)
        overlap_end = min(target_end, segment_end)
        if overlap_start >= overlap_end:
            continue

        local_start = overlap_start - segment_start
        local_end = overlap_end - segment_start
        edits.append(_PendingEdit(
            authors=(author,),
            base_index=base_index,
            base_start=base_start,
            base_end=base_end,
            old=segments[segment_index][local_start:local_end],
            new="",
            placement=Placement(segment_index, local_start, local_end),
        ))

    if not edits:
        return None

    rebuilt = list(segments)
    for edit in edits:
        segment_text = rebuilt[edit.placement.segment]
        rebuilt[edit.placement.segment] = (
            segment_text[:edit.placement.start]
            + segment_text[edit.placement.end:]
        )
    if "".join(rebuilt) != joined[:target_start] + joined[target_end:]:
        return None

    return edits


def _collect_author_edits(
        *,
        author: str,
        base_event: SSAEvent,
        base_index: int,
        edited_text: str,
        segments: Sequence[str],
        target_map: "_TargetMap | None",
        pure_split: bool,
) -> tuple[list[_PendingEdit], list[UnresolvedChange]]:
    edits: list[_PendingEdit] = []
    unresolved: list[UnresolvedChange] = []

    for base_start, base_end, old, new in _text_hunks(base_event.text, edited_text):
        if target_map is None:
            edits.append(_PendingEdit(
                authors=(author,),
                base_index=base_index,
                base_start=base_start,
                base_end=base_end,
                old=old,
                new=new,
                placement=Placement(0, base_start, base_end),
            ))
            continue

        placements = _map_hunk_to_placements(
            base_text=base_event.text,
            segments=segments,
            base_start=base_start,
            base_end=base_end,
            old=old,
            new=new,
            target_map=target_map,
        )

        if len(placements) == 1:
            edits.append(_PendingEdit(
                authors=(author,),
                base_index=base_index,
                base_start=base_start,
                base_end=base_end,
                old=old,
                new=new,
                placement=placements[0],
            ))
        elif placements:
            unresolved.append(UnresolvedChange(
                author=author,
                base_index=base_index,
                base_start=base_start,
                base_end=base_end,
                old=old,
                new=new,
                candidates=tuple(placements),
                reason="ambiguous placement after split",
            ))
        elif not new:
            split_edits = _decompose_deletion_across_segments(
                author=author,
                base_index=base_index,
                base_start=base_start,
                base_end=base_end,
                old=old,
                segments=segments,
                target_map=target_map,
            )
            if split_edits is None:
                unresolved.append(UnresolvedChange(
                    author=author,
                    base_index=base_index,
                    base_start=base_start,
                    base_end=base_end,
                    old=old,
                    new=new,
                    reason="edit does not fit any split segment",
                ))
            else:
                edits.extend(split_edits)
        else:
            unresolved.append(UnresolvedChange(
                author=author,
                base_index=base_index,
                base_start=base_start,
                base_end=base_end,
                old=old,
                new=new,
                reason="edit does not fit any split segment",
            ))

    if pure_split and edits and not unresolved:
        if not _edits_reproduce_text(
                edited_text=edited_text,
                segments=segments,
                edits=edits,
        ):
            # Applying only part of a reviewer's edits could invent text that
            # reviewer never submitted.  If the mapped edits do not reproduce
            # their sentence, report the whole sentence's changes as
            # unresolved instead of applying a subset.
            inconsistent = [
                UnresolvedChange(
                    author=author,
                    base_index=base_index,
                    base_start=edit.base_start,
                    base_end=edit.base_end,
                    old=edit.old,
                    new=edit.new,
                    candidates=(edit.placement,),
                    reason="split mapping is inconsistent with the revised sentence",
                )
                for edit in edits
            ]
            return [], unresolved + inconsistent

    return edits, unresolved


def _resolve_segment_edits(
        *,
        edits: Sequence[_PendingEdit],
        base_index: int,
        author_order: tuple[str, ...],
        conflicts: list[MergeConflict],
) -> list[_PendingEdit]:
    ordered = sorted(
        edits,
        key=lambda item: (item.placement.start, item.placement.end, item.new),
    )
    accepted: list[_PendingEdit] = []
    remaining = list(ordered)

    while remaining:
        cluster = [remaining.pop(0)]
        grew = True
        while grew:
            grew = False
            for candidate in list(remaining):
                if any(_edits_overlap(candidate, member) for member in cluster):
                    cluster.append(candidate)
                    remaining.remove(candidate)
                    grew = True

        first = cluster[0]
        # Several identical edits (possibly from different reviewers) are one
        # change.  The same replacement at overlapping but distinct spans is
        # still a disagreement and must be reported.
        identical = all(
            edit.placement.start == first.placement.start
            and edit.placement.end == first.placement.end
            and edit.new == first.new
            and edit.old == first.old
            for edit in cluster
        )
        if len(cluster) == 1 or identical:
            authors = {
                author for edit in cluster for author in edit.authors
            }
            accepted.append(_PendingEdit(
                authors=tuple(author for author in author_order if author in authors),
                base_index=first.base_index,
                base_start=first.base_start,
                base_end=first.base_end,
                old=first.old,
                new=first.new,
                placement=first.placement,
            ))
            continue

        conflicts.append(MergeConflict(
            base_index=base_index,
            field="text",
            authors=tuple(
                author for author in author_order
                if any(author in edit.authors for edit in cluster)
            ),
            values={
                ", ".join(edit.authors): edit.new
                for edit in cluster
            },
            reason="overlapping text edits",
        ))

    return accepted


def _edits_overlap(left: _PendingEdit, right: _PendingEdit) -> bool:
    left_start = left.placement.start
    left_end = left.placement.end
    right_start = right.placement.start
    right_end = right.placement.end

    if left.old == right.old and left.new == right.new and left_start == right_start:
        return True
    if left_start == left_end and right_start == right_end:
        return left_start == right_start
    if left_start == left_end:
        return right_start < left_start < right_end
    if right_start == right_end:
        return left_start < right_start < left_end
    return max(left_start, right_start) < min(left_end, right_end)


def _text_hunks(base_text: str, edited_text: str) -> list[tuple[int, int, str, str]]:
    if len(base_text) == len(edited_text):
        return _positional_hunks(base_text, edited_text)

    matcher = difflib.SequenceMatcher(None, base_text, edited_text, autojunk=False)
    hunks: list[tuple[int, int, str, str]] = []
    current: list[int] | None = None

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if current is not None:
                hunks.append(_hunk_from_indices(base_text, edited_text, current))
                current = None
        else:
            if current is None:
                current = [i1, i2, j1, j2]
            else:
                current[1] = i2
                current[3] = j2

    if current is not None:
        hunks.append(_hunk_from_indices(base_text, edited_text, current))

    return hunks


def _positional_hunks(base_text: str, edited_text: str) -> list[tuple[int, int, str, str]]:
    """
    Split an equal-length substitution into contiguous changed spans.

    A sequence diff can describe one substitution as a deletion plus an
    insertion at different offsets when a character repeats.  For equal-length
    text, comparing position by position is unambiguous and gives the intuitive
    edit.
    """
    hunks: list[tuple[int, int, str, str]] = []
    start: int | None = None

    for index, (old_char, new_char) in enumerate(zip(base_text, edited_text)):
        if old_char != new_char:
            if start is None:
                start = index
        elif start is not None:
            hunks.append((start, index, base_text[start:index], edited_text[start:index]))
            start = None

    if start is not None:
        hunks.append((
            start,
            len(base_text),
            base_text[start:],
            edited_text[start:],
        ))

    return hunks


def _hunk_from_indices(
        base_text: str,
        edited_text: str,
        indices: Sequence[int],
) -> tuple[int, int, str, str]:
    base_start, base_end, edited_start, edited_end = indices
    return (
        base_start,
        base_end,
        base_text[base_start:base_end],
        edited_text[edited_start:edited_end],
    )


class _TargetMap:
    def __init__(self, base_text: str, target_text: str) -> None:
        self.base_text = base_text
        self.target_text = target_text
        matcher = difflib.SequenceMatcher(None, base_text, target_text, autojunk=False)
        self.blocks = [
            block for block in matcher.get_matching_blocks()
            if block.size
        ]

    def map_hunk(self, base_start: int, base_end: int) -> list[tuple[int, int]]:
        if base_start == base_end:
            return [
                (position, position)
                for position in self._map_insertion_positions(base_start)
            ]

        spans: list[tuple[int, int]] = []

        for start in self._map_boundary(base_start, prefer_end=False):
            for end in self._map_boundary(base_end, prefer_end=True):
                if start <= end and (start, end) not in spans:
                    spans.append((start, end))

        if spans:
            return spans

        # Conservative fallback: only map a span when an equal block fully
        # contains it.
        for block in self.blocks:
            if block.a <= base_start and base_end <= block.a + block.size:
                return [(
                    block.b + (base_start - block.a),
                    block.b + (base_end - block.a),
                )]

        return []

    def _map_boundary(self, position: int, *, prefer_end: bool) -> list[int]:
        if position <= 0:
            return [0]
        if position >= len(self.base_text):
            return [len(self.target_text)]

        for block in self.blocks:
            if block.a < position < block.a + block.size:
                return [block.b + (position - block.a)]

        candidates: list[int] = []
        for block in self.blocks:
            if block.a == position:
                candidates.append(block.b)
            if block.a + block.size == position:
                candidates.append(block.b + block.size)

        if candidates:
            return sorted(set(candidates))

        # Position falls inside text that the split revision changed.  Map it
        # to the nearest edge of the surrounding equal blocks.
        preceding = [
            block.b + block.size
            for block in self.blocks
            if block.a + block.size < position
        ]
        following = [
            block.b
            for block in self.blocks
            if block.a > position
        ]

        if prefer_end and preceding:
            return [max(preceding)]
        if following:
            return [min(following)]
        if preceding:
            return [max(preceding)]
        return [0 if prefer_end else len(self.target_text)]

    def _map_insertion_positions(self, position: int) -> list[int]:
        if position <= 0:
            return [0]
        if position >= len(self.base_text):
            return [len(self.target_text)]

        positions: list[int] = []
        for block in self.blocks:
            if block.a < position < block.a + block.size:
                positions.append(block.b + (position - block.a))
            elif position == block.a:
                positions.append(block.b)
            elif position == block.a + block.size:
                positions.append(block.b + block.size)

        if not positions:
            preceding = [
                block.b + block.size
                for block in self.blocks
                if block.a + block.size <= position
            ]
            if preceding:
                positions.append(max(preceding))
            else:
                positions.append(0)

        return sorted(set(positions))


def _map_hunk_to_placements(
        *,
        base_text: str,
        segments: Sequence[str],
        base_start: int,
        base_end: int,
        old: str,
        new: str,
        target_map: _TargetMap,
) -> list[Placement]:
    joined = "".join(segments)

    if not new and len(old) >= 2 and base_text.count(old) > 1:
        # Deleting a repeated phrase is the one case where a diff cannot say
        # *which* occurrence was removed.  If the possible occurrences land in
        # different split events, report them all rather than guessing.
        textual = _find_placements_by_text(segments=segments, old=old)
        if len({placement.segment for placement in textual}) > 1:
            return textual

    placements: list[Placement] = []
    mapped_spans = target_map.map_hunk(base_start, base_end)

    if mapped_spans:
        for target_start, target_end in mapped_spans:
            mapped_text = joined[target_start:target_end]
            if old and _similarity(old, mapped_text) < 0.6:
                continue

            for placement in _placements_in_segments(segments, target_start, target_end):
                if placement not in placements:
                    placements.append(placement)

        if placements:
            return placements

    return _find_placements_by_text(segments=segments, old=old)


def _find_placements_by_text(
        *,
        segments: Sequence[str],
        old: str,
) -> list[Placement]:
    """Find every occurrence of ``old`` in the split events."""
    if not old:
        return []

    joined = "".join(segments)
    occurrences = list(_find_occurrences(joined, old))
    placements: list[Placement] = []

    for start, end in occurrences:
        for placement in _placements_in_segments(segments, start, end):
            if placement not in placements:
                placements.append(placement)

    return placements


def _find_occurrences(text: str, needle: str) -> Iterable[tuple[int, int]]:
    start = text.find(needle)
    while start != -1:
        yield start, start + len(needle)
        start = text.find(needle, start + 1)


def _placements_in_segments(
        segments: Sequence[str],
        target_start: int,
        target_end: int,
) -> list[Placement]:
    offsets: list[tuple[int, int]] = []
    offset = 0
    for segment in segments:
        offsets.append((offset, offset + len(segment)))
        offset += len(segment)

    total_length = offset

    if target_start == target_end:
        if total_length == 0:
            return [Placement(0, 0, 0)]
        if target_start <= 0:
            return [Placement(0, 0, 0)]
        if target_start >= total_length:
            last = len(segments) - 1
            return [Placement(last, len(segments[last]), len(segments[last]))]

        placements = [
            Placement(index, target_start - segment_start, target_start - segment_start)
            for index, (segment_start, segment_end) in enumerate(offsets)
            if segment_start <= target_start <= segment_end
        ]
        return _deduplicate_placements(placements)

    placements = []
    for index, (segment_start, segment_end) in enumerate(offsets):
        if segment_start <= target_start and target_end <= segment_end:
            placements.append(Placement(
                index,
                target_start - segment_start,
                target_end - segment_start,
            ))

    return _deduplicate_placements(placements)


def _deduplicate_placements(placements: Sequence[Placement]) -> list[Placement]:
    result: list[Placement] = []
    for placement in placements:
        if placement not in result:
            result.append(placement)
    return result


def _merge_timing(
        *,
        merged_event: SSAEvent,
        base_event: SSAEvent,
        base_index: int,
        author_order: tuple[str, ...],
        events_for_author: Mapping[str, list[SSAEvent]],
        split_by: str | None,
        target_event: SSAEvent,
        conflicts: list[MergeConflict],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    timing_values = [
        (author, events[0].start, events[0].end)
        for author in author_order
        if len(events := events_for_author[author]) == 1
    ]

    if split_by is None:
        start, start_authors = _merge_scalar_field(
            base_value=base_event.start,
            field_name="start",
            base_index=base_index,
            author_order=author_order,
            values=[(author, value) for author, value, _ in timing_values],
            conflicts=conflicts,
        )
        end, end_authors = _merge_scalar_field(
            base_value=base_event.end,
            field_name="end",
            base_index=base_index,
            author_order=author_order,
            values=[(author, value) for author, _, value in timing_values],
            conflicts=conflicts,
        )
        merged_event.start = int(start)
        merged_event.end = int(end)
        return start_authors, end_authors

    changes = [
        (author, start, end)
        for author, start, end in timing_values
        if start != base_event.start or end != base_event.end
    ]
    if not changes:
        merged_event.start = target_event.start
        merged_event.end = target_event.end
        return ("base",), ("base",)

    unique_intervals = {(start, end) for _, start, end in changes}
    if len(unique_intervals) > 1:
        conflicts.append(MergeConflict(
            base_index=base_index,
            field="timing",
            authors=tuple(author for author, _, _ in changes),
            values={
                author: f"{start}-{end}"
                for author, start, end in changes
            },
            reason="incompatible retiming after split",
        ))
        merged_event.start = target_event.start
        merged_event.end = target_event.end
        return ("base",), ("base",)

    _, revised_start, revised_end = changes[0]
    merged_event.start, merged_event.end = _transform_interval(
        target_event.start,
        target_event.end,
        base_event.start,
        base_event.end,
        revised_start,
        revised_end,
    )
    start_authors = tuple(
        author for author in author_order
        if any(name == author and start != base_event.start for name, start, _ in changes)
    )
    end_authors = tuple(
        author for author in author_order
        if any(name == author and end != base_event.end for name, _, end in changes)
    )
    return start_authors or ("base",), end_authors or ("base",)


def _transform_interval(
        start: int,
        end: int,
        base_start: int,
        base_end: int,
        revised_start: int,
        revised_end: int,
) -> tuple[int, int]:
    if base_end == base_start:
        delta = revised_start - base_start
        return start + delta, end + delta

    ratio = (revised_end - revised_start) / (base_end - base_start)
    return (
        round(revised_start + (start - base_start) * ratio),
        round(revised_start + (end - base_start) * ratio),
    )


def _merge_metadata_fields(
        *,
        merged_event: SSAEvent,
        base_event: SSAEvent,
        base_index: int,
        author_order: tuple[str, ...],
        events_for_author: Mapping[str, list[SSAEvent]],
        split_by: str | None,
        target_events: Sequence[SSAEvent],
        segment_index: int,
        conflicts: list[MergeConflict],
) -> dict[str, tuple[str, ...]]:
    field_authors: dict[str, tuple[str, ...]] = {}

    for field_name in _METADATA_FIELDS:
        base_value = getattr(base_event, field_name)

        if split_by is None:
            values = [
                (author, getattr(events[0], field_name))
                for author in author_order
                if len(events := events_for_author[author]) == 1
            ]
            value, contributors = _merge_scalar_field(
                base_value=base_value,
                field_name=field_name,
                base_index=base_index,
                author_order=author_order,
                values=values,
                conflicts=conflicts,
            )
            setattr(merged_event, field_name, value)
            if contributors:
                field_authors[field_name] = contributors
            continue

        target_value = getattr(target_events[segment_index], field_name)
        external_values = [
            (author, getattr(events[0], field_name))
            for author in author_order
            if author != split_by
            and len(events := events_for_author[author]) == 1
        ]
        changed_values = [
            (author, value) for author, value in external_values
            if value != base_value
        ]

        if not changed_values:
            setattr(merged_event, field_name, target_value)
            continue

        unique_values = {value for _, value in changed_values}
        if len(unique_values) == 1:
            setattr(merged_event, field_name, changed_values[0][1])
            field_authors[field_name] = tuple(
                author for author in author_order
                if any(name == author for name, _ in changed_values)
            )
        else:
            conflicts.append(MergeConflict(
                base_index=base_index,
                field=field_name,
                authors=tuple(author for author, _ in changed_values),
                values={
                    author: repr(value) for author, value in changed_values
                },
                reason="incompatible field edits after split",
            ))
            setattr(merged_event, field_name, target_value)

    return field_authors


def _merge_scalar_field(
        *,
        base_value: Any,
        field_name: str,
        base_index: int,
        author_order: tuple[str, ...],
        values: Sequence[tuple[str, Any]],
        conflicts: list[MergeConflict],
) -> tuple[Any, tuple[str, ...]]:
    changed = [(author, value) for author, value in values if value != base_value]
    if not changed:
        return base_value, ()

    unique_values = {value for _, value in changed}
    if len(unique_values) == 1:
        return changed[0][1], tuple(
            author for author in author_order
            if any(name == author for name, _ in changed)
        )

    conflicts.append(MergeConflict(
        base_index=base_index,
        field=field_name,
        authors=tuple(author for author, _ in changed),
        values={
            author: repr(value) for author, value in changed
        },
        reason="conflicting edits",
    ))
    return base_value, ()


def _source_events(
        *,
        author_order: tuple[str, ...],
        groups_for_author: Mapping[str, _AlignedGroup | None],
        split_by: str | None,
        segment_index: int,
) -> dict[str, tuple[int, ...]]:
    result: dict[str, tuple[int, ...]] = {}
    for author in author_order:
        group = groups_for_author[author]
        if group is None:
            continue
        if author == split_by and len(group.rev_indices) > 1:
            if segment_index < len(group.rev_indices):
                result[author] = (group.rev_indices[segment_index],)
        else:
            result[author] = group.rev_indices
    return result


def _record_alignment_conflicts(
        *,
        base: SSAFile,
        named_revisions: Sequence[tuple[str, SSAFile]],
        aligned: Mapping[str, Sequence[_AlignedGroup]],
        conflicts: list[MergeConflict],
) -> None:
    for author, _ in named_revisions:
        missing = [
            group.base_index
            for group in aligned[author]
            if group.base_index is not None and not group.rev_indices
        ]
        inserted = [
            index
            for group in aligned[author]
            if group.base_index is None
            for index in group.rev_indices
        ]

        if missing:
            conflicts.append(MergeConflict(
                base_index=missing[0],
                field="structure",
                authors=(author,),
                values={author: f"{len(missing)} original events missing"},
                reason="revision omitted base events",
            ))
        if inserted:
            conflicts.append(MergeConflict(
                base_index=-1,
                field="structure",
                authors=(author,),
                values={
                    author: (
                        f"{len(inserted)} additional events outside "
                        f"{len(base.events)} original ranges"
                    )
                },
                reason="revision added events outside base ranges",
            ))
