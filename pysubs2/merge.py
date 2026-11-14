"""
Traceable merging of duplicated subtitle events.

This module implements the merge mode used by
:meth:`pysubs2.SSAFile.merge_duplicate_events` for event cleaning (eg. of
training corpora): duplicated lines - like the same sentence typeset once at
the top and once at the bottom of the screen - are folded into a single kept
event, while two people saying the same thing at the same time are kept as
separate lines. The returned :class:`MergeReport` records which original line
numbers were folded into each kept line, so the merge can be audited and
reprocessed without losing provenance.
"""

import dataclasses
from collections.abc import Callable, Hashable

from .ssaevent import SSAEvent


@dataclasses.dataclass
class KeptLine:
    """
    Provenance of one line kept by the merge.

    Attributes:
        line: Line number (0-based, in the original file) of the kept event.
        folded: Line numbers (0-based, in the original file) of duplicated
            events that were folded into the kept line, sorted ascending.
    """

    line: int
    folded: list[int] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ReviewItem:
    """
    An event with unknown speaker that could not be merged automatically.

    The event looks like a duplicate of lines attributed to different known
    speakers, so merging it would wrongly join those speakers into one group.
    It is kept in the output and reported here for manual review.

    Attributes:
        line: Line number (0-based, in the original file) of the event.
        candidates: Line numbers of kept lines it appears to duplicate.
    """

    line: int
    candidates: list[int] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class MergeReport:
    """
    Result of :meth:`pysubs2.SSAFile.merge_duplicate_events`.

    Attributes:
        kept: Maps each line number in the merged file (0-based) to its
            :class:`KeptLine` provenance in the original file.
        review: Events with unknown speaker that need manual review
            (see :class:`ReviewItem`).
    """

    kept: dict[int, KeptLine] = dataclasses.field(default_factory=dict)
    review: list[ReviewItem] = dataclasses.field(default_factory=list)

    @property
    def merged(self) -> dict[int, KeptLine]:
        """Subset of :attr:`kept` for lines which absorbed at least one duplicate."""
        return {position: kept_line for position, kept_line in self.kept.items() if kept_line.folded}


def merge_duplicate_events(
    events: list[SSAEvent],
    *,
    source: str | Callable[[SSAEvent], Hashable] | None = None,
    speaker: str | Callable[[SSAEvent], str | None] = "name",
    overlap_threshold: float = 0.5,
    previous: MergeReport | None = None,
) -> tuple[list[SSAEvent], MergeReport]:
    """
    Merge duplicated events, returning the new event list and a merge report.

    This is the implementation of :meth:`pysubs2.SSAFile.merge_duplicate_events`;
    see its documentation for a description of the arguments.
    """
    if not 0.0 <= overlap_threshold <= 1.0:
        raise ValueError("overlap_threshold must be between 0 and 1")

    source_key: Callable[[SSAEvent], Hashable]
    if source is None:
        source_key = lambda event: None
    elif isinstance(source, str):
        source_key = lambda event: getattr(event, source)
    else:
        source_key = source

    speaker_of: Callable[[SSAEvent], object]
    if isinstance(speaker, str):
        speaker_of = lambda event: getattr(event, speaker)
    else:
        speaker_of = speaker

    lineage = _initial_lineage(events, previous)

    speakers: list[str | None] = []
    buckets: dict[tuple[Hashable, Hashable], list[int]] = {}
    for i, event in enumerate(events):
        if event.is_text and event.plaintext.strip():
            speakers.append(_normalize_speaker(speaker_of(event)))
            buckets.setdefault((event.plaintext.strip(), source_key(event)), []).append(i)
        else:
            speakers.append(None)

    eligible = {i for bucket in buckets.values() for i in bucket}
    parent = list(range(len(events)))
    root_speaker: dict[int, str | None] = {i: speakers[i] for i in eligible}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        # the earlier line in the file stays the representative
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            root, other = (root_a, root_b) if root_a < root_b else (root_b, root_a)
            parent[other] = root
            root_speaker[root] = root_speaker[root] or root_speaker[other]

    # candidate duplicate pairs: same text and source, overlapping in time
    adjacent: dict[int, set[int]] = {i: set() for i in eligible}
    for bucket in buckets.values():
        for pos, i in enumerate(bucket):
            for j in bucket[pos + 1:]:
                if _overlap_ratio(events[i], events[j]) >= overlap_threshold:
                    adjacent[i].add(j)
                    adjacent[j].add(i)

    # pass 1: merge events attributed to the same known speaker
    for i in sorted(eligible):
        if speakers[i] is not None:
            for j in adjacent[i]:
                if speakers[j] == speakers[i]:
                    union(i, j)

    # pass 2: merge events with unknown speaker, unless that would join
    # two different known speakers into one group
    review_lines: list[ReviewItem] = []
    unknown = {i for i in eligible if speakers[i] is None}
    seen: set[int] = set()
    for i in sorted(unknown):
        if i in seen:
            continue
        component = []
        stack = [i]
        while stack:
            u = stack.pop()
            if u in seen or u not in unknown:
                continue
            seen.add(u)
            component.append(u)
            stack.extend(adjacent[u])

        component_speakers = set()
        candidate_lines: set[int] = set()
        for u in component:
            for v in adjacent[u]:
                if v not in unknown:
                    root = find(v)
                    if root_speaker[root] is not None:
                        component_speakers.add(root_speaker[root])
                        candidate_lines.add(lineage[root].line)

        if len(component_speakers) <= 1:
            for u in component:
                for v in adjacent[u]:
                    union(u, v)
        else:
            for u in component:
                review_lines.append(ReviewItem(line=lineage[u].line, candidates=sorted(candidate_lines)))

    # collect folded provenance per group representative
    folded_of_root: dict[int, list[int]] = {}
    for i in sorted(eligible):
        root = find(i)
        if root != i:
            folded_of_root.setdefault(root, []).append(lineage[i].line)
            folded_of_root[root].extend(lineage[i].folded)

    new_events: list[SSAEvent] = []
    report = MergeReport()
    for i, event in enumerate(events):
        if i in eligible and find(i) != i:
            continue
        folded = lineage[i].folded + folded_of_root.get(i, [])
        report.kept[len(new_events)] = KeptLine(line=lineage[i].line, folded=sorted(set(folded)))
        new_events.append(event)

    report.review = _merge_review_items(review_lines, _carry_review_items(previous, report))
    return new_events, report


def _initial_lineage(events: list[SSAEvent], previous: MergeReport | None) -> list[KeptLine]:
    if previous is None:
        return [KeptLine(line=i) for i in range(len(events))]
    if len(previous.kept) != len(events):
        raise ValueError(
            f"previous report describes {len(previous.kept)} lines, but there are {len(events)} events"
        )
    try:
        return [KeptLine(line=previous.kept[i].line, folded=list(previous.kept[i].folded))
                for i in range(len(events))]
    except KeyError:
        raise ValueError("previous report does not match the given events") from None


def _carry_review_items(previous: MergeReport | None, report: MergeReport) -> list[ReviewItem]:
    if previous is None:
        return []
    kept_lines = {kept_line.line for kept_line in report.kept.values()}
    return [ReviewItem(line=item.line, candidates=list(item.candidates))
            for item in previous.review if item.line in kept_lines]


def _merge_review_items(fresh: list[ReviewItem], carried: list[ReviewItem]) -> list[ReviewItem]:
    by_line: dict[int, ReviewItem] = {}
    for item in carried + fresh:
        if item.line in by_line:
            merged = by_line[item.line]
            merged.candidates = sorted(set(merged.candidates) | set(item.candidates))
        else:
            by_line[item.line] = ReviewItem(line=item.line, candidates=list(item.candidates))
    return [by_line[line] for line in sorted(by_line)]


def _normalize_speaker(name: object) -> str | None:
    if name is None:
        return None
    return str(name).strip() or None


def _overlap_ratio(a: SSAEvent, b: SSAEvent) -> float:
    """Overlap of the two time intervals, relative to the shorter one."""
    overlap = min(a.end, b.end) - max(a.start, b.start)
    shorter = min(a.end - a.start, b.end - b.start)
    if shorter <= 0:
        return 1.0 if overlap >= 0 else 0.0
    return max(0, overlap) / shorter
