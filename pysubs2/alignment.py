"""
Time-overlap based alignment of two subtitle tracks (eg. original language and its translation).

Subtitle segmentation differs between tracks: two cues on one side may correspond to a single
cue on the other side (or the other way round). The :func:`align_events` function finds a
global pairing of *groups* of contiguous cues, allowing omissions (untranslated cues) while
forbidding reordering and reuse of lines.

See :func:`align_events` for the full description of the pairing rules.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .ssaevent import SSAEvent

__all__ = ["LinePair", "AlignmentResult", "align_events"]


@dataclass(frozen=True)
class LinePair:
    """
    One paired group of contiguous subtitle cues from the two tracks.

    Attributes:
        source_indices: Original (0-based) indices of the source cues in the group.
        target_indices: Original (0-based) indices of the target cues in the group.
        overlap: Total length (in milliseconds) of the intersection of the two sides'
            covered time sets, excluding gaps between adjacent cues.
        source: The source :class:`SSAEvent` instances referenced by :attr:`source_indices`.
        target: The target :class:`SSAEvent` instances referenced by :attr:`target_indices`.
    """

    source_indices: tuple[int, ...]
    target_indices: tuple[int, ...]
    overlap: int
    source: tuple[SSAEvent, ...]
    target: tuple[SSAEvent, ...]


@dataclass(frozen=True)
class AlignmentResult:
    """
    Result of :func:`align_events`.

    Attributes:
        pairs: Groups that appear in *every* optimal pairing, ordered by their position.
            These are the pairs safe to ingest into a parallel corpus.
        ambiguous_groups: Groups that appear in *some* but not all optimal pairings,
            ordered by their position. These are meant for human review instead of
            automatic ingestion.
        source_unmatched: Source cue indices that no optimal pairing covers (omissions).
        target_unmatched: Target cue indices that no optimal pairing covers (omissions).
    """

    pairs: tuple[LinePair, ...]
    ambiguous_groups: tuple[LinePair, ...]
    source_unmatched: tuple[int, ...]
    target_unmatched: tuple[int, ...]

    @property
    def groups(self) -> tuple[LinePair, ...]:
        """All groups (ingested and ambiguous), ordered by their position in the tracks."""
        return tuple(
            sorted(
                (*self.pairs, *self.ambiguous_groups),
                key=lambda pair: (pair.source_indices[0], pair.target_indices[0]),
            )
        )


def _interval_overlap(
    source_start: int, source_end: int, target_start: int, target_end: int
) -> int:
    return max(0, min(source_end, target_end) - max(source_start, target_start))


def _signed_interval_overlap(
    source_start: int, source_end: int, target_start: int, target_end: int
) -> int:
    """Overlap length without clamping, so disjoint cues are distinguishable (negative)."""
    return min(source_end, target_end) - max(source_start, target_start)


def _append_interval(
    merged: list[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    """Extend ``merged`` with the ``[start, end)`` interval, merging it when adjacent."""
    if merged and start <= merged[-1][1]:
        if end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    else:
        merged.append((start, end))
    return merged


def _coverage_overlap(
    source_intervals: list[tuple[int, int]],
    target_intervals: list[tuple[int, int]],
) -> int:
    """Total length of the intersection of two lists of disjoint, sorted intervals."""
    total = 0
    source_index = 0
    target_index = 0
    while source_index < len(source_intervals) and target_index < len(target_intervals):
        source_start, source_end = source_intervals[source_index]
        target_start, target_end = target_intervals[target_index]
        total += max(0, min(source_end, target_end) - max(source_start, target_start))
        if source_end <= target_end:
            source_index += 1
        else:
            target_index += 1
    return total


def _is_connected(
    overlaps: list[list[bool]],
    source_lo: int,
    source_hi: int,
    target_lo: int,
    target_hi: int,
) -> bool:
    """
    Check that the bipartite line-overlap graph of a block is connected.

    Vertices are the source cues ``source_lo..source_hi`` and target cues
    ``target_lo..target_hi``; an edge exists when the two cue intervals overlap
    (per the precomputed ``overlaps`` matrix). A disconnected graph means some cue
    has no temporal engagement with the other side of the block, ie. the block would
    absorb an unrelated cue, so it is not an eligible group.
    """
    reached_source = {source_lo}
    reached_target: set[int] = set()
    frontier = [source_lo]
    while frontier:
        source_index = frontier.pop()
        for target_index in range(target_lo, target_hi + 1):
            if target_index in reached_target:
                continue
            if overlaps[source_index][target_index]:
                reached_target.add(target_index)
                for next_source in range(source_lo, source_hi + 1):
                    if (
                        next_source not in reached_source
                        and overlaps[next_source][target_index]
                    ):
                        reached_source.add(next_source)
                        frontier.append(next_source)

    expected_source = source_hi - source_lo + 1
    expected_target = target_hi - target_lo + 1
    return (
        len(reached_source) == expected_source
        and len(reached_target) == expected_target
    )


def _connected_components(
    overlaps: list[list[bool]],
) -> list[tuple[list[int], list[int]]]:
    """
    Find connected components of the bipartite cue-overlap graph.

    Returns one ``(source_indices, target_indices)`` tuple per component, each with
    sorted vertex indices. Isolated cues appear in a single-side component and can
    never belong to an eligible group. A connected group can only contain vertices
    from a single component, which keeps block enumeration local and efficient.
    """
    source_count = len(overlaps)
    target_count = len(overlaps[0]) if source_count else 0
    source_component = [-1] * source_count
    target_component = [-1] * target_count
    components: list[tuple[list[int], list[int]]] = []

    for start_source in range(source_count):
        if source_component[start_source] != -1:
            continue
        component_id = len(components)
        component_sources: list[int] = []
        component_targets: list[int] = []
        source_stack = [start_source]
        target_stack: list[int] = []
        source_component[start_source] = component_id
        while source_stack or target_stack:
            if source_stack:
                source_index = source_stack.pop()
                component_sources.append(source_index)
                for target_index in range(target_count):
                    if (
                        overlaps[source_index][target_index]
                        and target_component[target_index] == -1
                    ):
                        target_component[target_index] = component_id
                        target_stack.append(target_index)
            else:
                target_index = target_stack.pop()
                component_targets.append(target_index)
                for source_index in range(source_count):
                    if (
                        overlaps[source_index][target_index]
                        and source_component[source_index] == -1
                    ):
                        source_component[source_index] = component_id
                        source_stack.append(source_index)
        component_sources.sort()
        component_targets.sort()
        components.append((component_sources, component_targets))

    # Target-only components cannot originate from the source-driven traversal,
    # so account for any isolated target cues separately.
    for target_index in range(target_count):
        if target_component[target_index] == -1:
            components.append(([], [target_index]))

    return components


def _contiguous_runs(indices: list[int]) -> list[tuple[int, int]]:
    """Split sorted indices into maximal runs of consecutive values."""
    if not indices:
        return []
    runs: list[tuple[int, int]] = []
    run_lo = indices[0]
    previous = indices[0]
    for index in indices[1:]:
        if index != previous + 1:
            runs.append((run_lo, previous))
            run_lo = index
        previous = index
    runs.append((run_lo, previous))
    return runs


def align_events(
    source: Sequence[SSAEvent],
    target: Sequence[SSAEvent],
    *,
    min_overlap: int = 1,
    max_block_size: int | None = 10,
) -> AlignmentResult:
    """
    Align two chronologically ordered subtitle tracks by temporal overlap.

    A *group* pairs one contiguous run of source cues with one contiguous run of target
    cues. Two cues are linked when their intervals overlap by at least ``min_overlap``
    milliseconds; a group is eligible only when the bipartite overlap graph of its cues
    is connected, meaning every cue is temporally engaged with the other side. This
    permits many-to-one and one-to-many groups (merged or split sentences) while keeping
    unrelated cues (eg. untranslated lines) out of groups they do not belong to.

    The score of an eligible group is the total length of the intersection of the two
    sides' covered time sets (in milliseconds, with gaps between adjacent cues excluded).
    A full pairing is a sequence of non-overlapping, ordered groups; cues may be left
    unpaired (omissions), but groups may never reorder or reuse cues.

    The best pairing is chosen globally by two lexicographic criteria:

    1. maximize the total temporal overlap across all groups,
    2. on ties, minimize the number of unpaired cues.

    Because the optimization is global, a cue with the longest local overlap is not
    necessarily part of the chosen pairing when a different global decomposition scores
    better.

    When several distinct pairings remain optimal, only groups present in *every* optimal
    pairing are returned as :attr:`AlignmentResult.pairs` (safe for corpus ingestion);
    the remaining optimal groups are returned as :attr:`AlignmentResult.ambiguous_groups`
    for human review. Cues not covered by any optimal group are reported as unmatched.

    Args:
        source: Source-track cues in their original (chronological) order, eg. an
            :class:`pysubs2.SSAFile`. Indices in the result refer to positions in this
            sequence, which is not reordered.
        target: Target-track cues in their original (chronological) order.
        min_overlap: Minimum overlap (in milliseconds) for two cues to be considered
            linked. Defaults to 1, so merely touching or disjoint cues are not linked
            (they can still be left unmatched). Pass 0 to also treat touching cues as linked.
        max_block_size: Optional cap on how many cues a single group may contain on
            either side. Defaults to 10, which is ample for ordinary sentence
            merges/splits and keeps block enumeration (the dominant cost) bounded even
            when long overlapping chains form one large connected component. Pass
            ``None`` to allow groups of any size at the cost of potentially quadratic
            runtime per component.

    Returns:
        :class:`AlignmentResult` with ingested pairs, ambiguous groups and unmatched indices.

    Raises:
        ValueError: If arguments are invalid.
    """
    if min_overlap < 0:
        raise ValueError("min_overlap must be non-negative")
    if max_block_size is not None and max_block_size < 1:
        raise ValueError("max_block_size must be at least 1")

    source_count = len(source)
    target_count = len(target)

    overlaps = [
        [
            _signed_interval_overlap(
                source[source_index].start,
                source[source_index].end,
                target[target_index].start,
                target[target_index].end,
            )
            >= min_overlap
            for target_index in range(target_count)
        ]
        for source_index in range(source_count)
    ]

    # 2-D prefix sum of overlap edges so the edge count of any rectangular block is O(1).
    # A connected bipartite graph on (a + b) vertices needs at least a + b - 1 edges,
    # which lets us reject disconnected blocks without a graph traversal.
    edge_prefix = [[0] * (target_count + 1) for _ in range(source_count + 1)]
    for source_index in range(source_count):
        row_total = 0
        for target_index in range(target_count):
            row_total += int(overlaps[source_index][target_index])
            edge_prefix[source_index + 1][target_index + 1] = (
                edge_prefix[source_index][target_index + 1] + row_total
            )

    def edge_count(
        source_lo: int, source_hi: int, target_lo: int, target_hi: int
    ) -> int:
        return (
            edge_prefix[source_hi + 1][target_hi + 1]
            - edge_prefix[source_lo][target_hi + 1]
            - edge_prefix[source_hi + 1][target_lo]
            + edge_prefix[source_lo][target_lo]
        )

    # Sorted overlap-edge adjacency; subtitle timing is local, so each cue typically
    # overlaps only a handful of cues on the other side.
    source_neighbors = [
        [
            target_index
            for target_index in range(target_count)
            if overlaps[source_index][target_index]
        ]
        for source_index in range(source_count)
    ]

    # Enumerate every eligible group. A connected group's vertices all belong to a single
    # connected component of the overlap graph, so enumeration stays local to each
    # component (split into contiguous index runs). The smallest target index of a
    # connected group must overlap some source in the group, hence target anchors are
    # drawn from the sources' overlap adjacency instead of scanning every target. Each
    # group is scored by the total length of the intersection of the two sides' covered
    # time sets (gaps excluded); its line-overlap graph must be connected.
    blocks_from: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for component_sources, component_targets in _connected_components(overlaps):
        if not component_sources or not component_targets:
            continue  # isolated cue on one side, it cannot belong to any group
        source_runs = _contiguous_runs(component_sources)
        target_run_of: dict[int, tuple[int, int]] = {}
        for run_lo, run_hi in _contiguous_runs(component_targets):
            for target_index in range(run_lo, run_hi + 1):
                target_run_of[target_index] = (run_lo, run_hi)

        for source_run_lo, source_run_hi in source_runs:
            for source_lo in range(source_run_lo, source_run_hi + 1):
                source_hi_limit = (
                    source_run_hi + 1
                    if max_block_size is None
                    else min(source_run_hi + 1, source_lo + max_block_size)
                )
                source_merged: list[tuple[int, int]] = []
                for source_hi in range(source_lo, source_hi_limit):
                    source_merged = _append_interval(
                        source_merged, source[source_hi].start, source[source_hi].end
                    )
                    candidate_targets = sorted(
                        {
                            target_index
                            for window_source in range(source_lo, source_hi + 1)
                            for target_index in source_neighbors[window_source]
                        }
                    )
                    for target_lo in candidate_targets:
                        _, target_run_hi = target_run_of[target_lo]
                        target_hi_limit = (
                            target_run_hi + 1
                            if max_block_size is None
                            else min(target_run_hi + 1, target_lo + max_block_size)
                        )
                        target_merged: list[tuple[int, int]] = []
                        for target_hi in range(target_lo, target_hi_limit):
                            target_merged = _append_interval(
                                target_merged,
                                target[target_hi].start,
                                target[target_hi].end,
                            )
                            vertices = (source_hi - source_lo + 1) + (
                                target_hi - target_lo + 1
                            )
                            if (
                                edge_count(source_lo, source_hi, target_lo, target_hi)
                                < vertices - 1
                            ):
                                continue
                            if not _is_connected(
                                overlaps, source_lo, source_hi, target_lo, target_hi
                            ):
                                continue
                            overlap = _coverage_overlap(source_merged, target_merged)
                            blocks_from.setdefault((source_lo, target_lo), []).append(
                                (source_hi, target_hi, overlap)
                            )

    # Score is (total overlap, negative unmatched count), maximized lexicographically.
    # Forward pass: best score and number of optimal paths reaching each grid state.
    forward: list[list[tuple[int, int] | None]] = [
        [None] * (target_count + 1) for _ in range(source_count + 1)
    ]
    forward_count: list[list[int]] = [
        [0] * (target_count + 1) for _ in range(source_count + 1)
    ]
    forward[0][0] = (0, 0)
    forward_count[0][0] = 1

    def relax(
        next_source: int,
        next_target: int,
        next_score: tuple[int, int],
        path_count: int,
    ) -> None:
        current = forward[next_source][next_target]
        if current is None or next_score > current:
            forward[next_source][next_target] = next_score
            forward_count[next_source][next_target] = path_count
        elif next_score == current:
            forward_count[next_source][next_target] += path_count

    for source_pos in range(source_count + 1):
        for target_pos in range(target_count + 1):
            current = forward[source_pos][target_pos]
            if current is None:
                continue
            path_count = forward_count[source_pos][target_pos]
            if source_pos < source_count:
                relax(
                    source_pos + 1,
                    target_pos,
                    (current[0], current[1] - 1),
                    path_count,
                )
            if target_pos < target_count:
                relax(
                    source_pos,
                    target_pos + 1,
                    (current[0], current[1] - 1),
                    path_count,
                )
            for source_hi, target_hi, overlap in blocks_from.get(
                (source_pos, target_pos), ()
            ):
                relax(
                    source_hi + 1,
                    target_hi + 1,
                    (current[0] + overlap, current[1]),
                    path_count,
                )

    best_score = forward[source_count][target_count]
    assert best_score is not None
    total_optimal_paths = forward_count[source_count][target_count]

    # Backward pass: best suffix score and number of optimal suffix paths per state.
    backward: list[list[tuple[int, int] | None]] = [
        [None] * (target_count + 1) for _ in range(source_count + 1)
    ]
    backward_count: list[list[int]] = [
        [0] * (target_count + 1) for _ in range(source_count + 1)
    ]
    backward[source_count][target_count] = (0, 0)
    backward_count[source_count][target_count] = 1

    for source_pos in range(source_count, -1, -1):
        for target_pos in range(target_count, -1, -1):
            if source_pos == source_count and target_pos == target_count:
                continue
            candidates: list[tuple[tuple[int, int], int, int]] = []
            if source_pos < source_count:
                suffix = backward[source_pos + 1][target_pos]
                if suffix is not None:
                    candidates.append(
                        ((suffix[0], suffix[1] - 1), source_pos + 1, target_pos)
                    )
            if target_pos < target_count:
                suffix = backward[source_pos][target_pos + 1]
                if suffix is not None:
                    candidates.append(
                        ((suffix[0], suffix[1] - 1), source_pos, target_pos + 1)
                    )
            for source_hi, target_hi, overlap in blocks_from.get(
                (source_pos, target_pos), ()
            ):
                suffix = backward[source_hi + 1][target_hi + 1]
                if suffix is not None:
                    candidates.append(
                        (
                            (suffix[0] + overlap, suffix[1]),
                            source_hi + 1,
                            target_hi + 1,
                        )
                    )
            if not candidates:
                continue
            best = max(candidate[0] for candidate in candidates)
            backward[source_pos][target_pos] = best
            backward_count[source_pos][target_pos] = sum(
                backward_count[next_source][next_target]
                for score, next_source, next_target in candidates
                if score == best
            )

    def make_pair(
        source_lo: int,
        source_hi: int,
        target_lo: int,
        target_hi: int,
        overlap: int,
    ) -> LinePair:
        return LinePair(
            source_indices=tuple(range(source_lo, source_hi + 1)),
            target_indices=tuple(range(target_lo, target_hi + 1)),
            overlap=overlap,
            source=tuple(source[index] for index in range(source_lo, source_hi + 1)),
            target=tuple(target[index] for index in range(target_lo, target_hi + 1)),
        )

    consensus: list[LinePair] = []
    ambiguous: list[LinePair] = []
    covered_source: set[int] = set()
    covered_target: set[int] = set()

    for (source_lo, target_lo), blocks in blocks_from.items():
        prefix = forward[source_lo][target_lo]
        assert prefix is not None
        for source_hi, target_hi, overlap in blocks:
            suffix = backward[source_hi + 1][target_hi + 1]
            assert suffix is not None
            edge_score = (
                prefix[0] + overlap + suffix[0],
                prefix[1] + suffix[1],
            )
            if edge_score != best_score:
                continue
            paths_through = (
                forward_count[source_lo][target_lo]
                * backward_count[source_hi + 1][target_hi + 1]
            )
            pair = make_pair(source_lo, source_hi, target_lo, target_hi, overlap)
            covered_source.update(pair.source_indices)
            covered_target.update(pair.target_indices)
            if paths_through == total_optimal_paths:
                consensus.append(pair)
            else:
                ambiguous.append(pair)

    consensus.sort(key=lambda pair: (pair.source_indices[0], pair.target_indices[0]))
    ambiguous.sort(key=lambda pair: (pair.source_indices[0], pair.target_indices[0]))

    source_unmatched = tuple(
        index for index in range(source_count) if index not in covered_source
    )
    target_unmatched = tuple(
        index for index in range(target_count) if index not in covered_target
    )

    return AlignmentResult(
        pairs=tuple(consensus),
        ambiguous_groups=tuple(ambiguous),
        source_unmatched=source_unmatched,
        target_unmatched=target_unmatched,
    )
