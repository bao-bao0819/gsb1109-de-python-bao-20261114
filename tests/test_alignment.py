import random

import pytest

from pysubs2 import SSAEvent, align_events
from pysubs2.alignment import (
    AlignmentResult,
    LinePair,
    _coverage_overlap,
    _interval_overlap,
    _signed_interval_overlap,
)


def make_event(start: int, end: int, text: str = "") -> SSAEvent:
    return SSAEvent(start=start, end=end, text=text)


def pair_keys(result: AlignmentResult) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    return [(pair.source_indices, pair.target_indices) for pair in result.pairs]


def ambiguous_keys(
    result: AlignmentResult,
) -> list[tuple[tuple[int, ...], tuple[int, ...]]]:
    return [
        (pair.source_indices, pair.target_indices) for pair in result.ambiguous_groups
    ]


def test_one_to_one_with_omissions() -> None:
    source = [
        make_event(0, 1000, "a"),
        make_event(2000, 3000, "b"),
        make_event(4000, 5000, "c"),
    ]
    target = [make_event(100, 900, "x"), make_event(6000, 7000, "z")]

    result = align_events(source, target)

    assert pair_keys(result) == [((0,), (0,))]
    assert result.pairs[0].overlap == 800
    assert result.source_unmatched == (1, 2)
    assert result.target_unmatched == (1,)
    assert result.ambiguous_groups == ()
    assert result.pairs[0].source == (source[0],)
    assert result.pairs[0].target == (target[0],)


def test_merge_two_source_into_one_target() -> None:
    source = [make_event(0, 1000), make_event(1000, 2000)]
    target = [make_event(100, 1900)]

    result = align_events(source, target)

    assert pair_keys(result) == [((0, 1), (0,))]
    assert result.pairs[0].overlap == 1800
    assert result.source_unmatched == ()
    assert result.target_unmatched == ()


def test_split_one_source_into_two_target() -> None:
    source = [make_event(0, 2000)]
    target = [make_event(100, 1000), make_event(1000, 1900)]

    result = align_events(source, target)

    assert pair_keys(result) == [((0,), (0, 1))]
    assert result.pairs[0].overlap == 1800


def test_many_to_many_group() -> None:
    source = [make_event(0, 1000), make_event(1000, 2000)]
    target = [make_event(100, 900), make_event(900, 1900)]

    result = align_events(source, target)

    assert pair_keys(result) == [((0, 1), (0, 1))]
    assert result.pairs[0].overlap == 1800


def test_groups_partition_lines_without_reordering_or_reuse() -> None:
    source = [make_event(0, 1000), make_event(1000, 2000), make_event(2000, 3000)]
    target = [make_event(100, 1900), make_event(2100, 2900)]

    result = align_events(source, target)
    groups = result.groups

    used_source = [index for pair in groups for index in pair.source_indices]
    used_target = [index for pair in groups for index in pair.target_indices]
    assert sorted(used_source) == used_source
    assert sorted(used_target) == used_target
    assert len(used_source) == len(set(used_source))
    assert len(used_target) == len(set(used_target))

    source_starts = [pair.source_indices[0] for pair in groups]
    target_starts = [pair.target_indices[0] for pair in groups]
    assert source_starts == sorted(source_starts)
    assert target_starts == sorted(target_starts)


def test_disjoint_tracks_have_no_pairs() -> None:
    result = align_events([make_event(0, 100)], [make_event(200, 300)])

    assert result.pairs == ()
    assert result.ambiguous_groups == ()
    assert result.source_unmatched == (0,)
    assert result.target_unmatched == (0,)


def test_touching_intervals_are_not_linked_by_default() -> None:
    result = align_events([make_event(0, 100)], [make_event(100, 200)])

    assert result.pairs == ()
    assert result.source_unmatched == (0,)
    assert result.target_unmatched == (0,)

    linked = align_events([make_event(0, 100)], [make_event(100, 200)], min_overlap=0)
    assert pair_keys(linked) == [((0,), (0,))]

    still_unlinked = align_events(
        [make_event(0, 100)], [make_event(101, 200)], min_overlap=0
    )
    assert still_unlinked.pairs == ()
    assert still_unlinked.source_unmatched == (0,)
    assert still_unlinked.target_unmatched == (0,)


def test_empty_inputs() -> None:
    assert align_events([], []).groups == ()
    result = align_events([make_event(0, 100)], [])
    assert result.source_unmatched == (0,)
    assert result.target_unmatched == ()


def test_tie_reports_ambiguous_groups_without_consensus() -> None:
    source = [make_event(0, 100), make_event(100, 200), make_event(200, 300)]
    target = [make_event(0, 150), make_event(150, 300)]

    result = align_events(source, target, max_block_size=2)

    assert result.pairs == ()
    assert set(ambiguous_keys(result)) == {
        ((0,), (0,)),
        ((0, 1), (0,)),
        ((1, 2), (1,)),
        ((2,), (1,)),
    }
    assert result.source_unmatched == ()
    assert result.target_unmatched == ()


def test_fixed_consensus_pair_coexists_with_tie_region() -> None:
    source = [
        make_event(0, 50, "p"),
        make_event(1000, 1100, "a"),
        make_event(1090, 1200, "b"),
    ]
    target = [
        make_event(0, 50, "u"),
        make_event(1000, 1100, "x"),
        make_event(1100, 1200, "y"),
    ]

    result = align_events(source, target)

    assert pair_keys(result) == [((0,), (0,))]
    assert set(ambiguous_keys(result)) == {
        ((1,), (1,)),
        ((1, 2), (1, 2)),
        ((2,), (2,)),
    }
    assert result.source_unmatched == ()
    assert result.target_unmatched == ()


def test_tie_broken_by_fewer_unmatched_lines() -> None:
    source = [make_event(0, 100)]
    target = [make_event(10, 40), make_event(50, 90)]

    result = align_events(source, target)

    assert pair_keys(result) == [((0,), (0, 1))]
    assert result.ambiguous_groups == ()


def test_global_optimum_beats_locally_longest_edge() -> None:
    # B overlaps X (60ms) more than Y (40ms), but X is A's only viable partner.
    # Greedily assigning B->X scores 60 with A unmatched; the global 1:1 optimum
    # assigns A->X (100ms) and B->Y (40ms) for 140ms total.
    source = [make_event(0, 100, "a"), make_event(100, 200, "b")]
    target = [make_event(0, 160, "x"), make_event(160, 200, "y")]

    result = align_events(source, target, max_block_size=1)

    assert pair_keys(result) == [((0,), (0,)), ((1,), (1,))]
    assert [pair.overlap for pair in result.pairs] == [100, 40]


def test_unrelated_line_is_not_absorbed_into_group() -> None:
    source = [make_event(0, 1000, "matched"), make_event(5000, 6000, "untranslated")]
    target = [make_event(100, 900, "x")]

    result = align_events(source, target)

    assert pair_keys(result) == [((0,), (0,))]
    assert result.source_unmatched == (1,)


def test_max_block_size_caps_group() -> None:
    source = [make_event(0, 1000), make_event(1000, 2000), make_event(2000, 3000)]
    target = [make_event(100, 2900)]

    capped = align_events(source, target, max_block_size=2)
    assert all(len(pair.source_indices) <= 2 for pair in capped.groups)

    unrestricted = align_events(source, target)
    assert pair_keys(unrestricted) == [((0, 1, 2), (0,))]


def test_invalid_arguments_raise() -> None:
    with pytest.raises(ValueError):
        align_events([], [], min_overlap=-1)
    with pytest.raises(ValueError):
        align_events([], [], max_block_size=0)


def test_coverage_intersection_excludes_internal_gaps() -> None:
    # Two source cues with a gap inside a single target span; the gap must not count.
    source = [make_event(0, 100), make_event(200, 300)]
    target = [make_event(0, 300)]

    result = align_events(source, target)
    assert pair_keys(result) == [((0, 1), (0,))]
    assert result.pairs[0].overlap == 200


def test_groups_property_orders_all_groups() -> None:
    source = [make_event(0, 50), make_event(1000, 1100), make_event(1090, 1200)]
    target = [make_event(0, 50), make_event(1000, 1100), make_event(1100, 1200)]

    result = align_events(source, target)
    starts = [pair.source_indices[0] for pair in result.groups]
    assert starts == sorted(starts)
    assert len(result.groups) == len(result.pairs) + len(result.ambiguous_groups)


def test_interval_overlap_helper() -> None:
    assert _interval_overlap(0, 100, 50, 150) == 50
    assert _interval_overlap(0, 100, 100, 200) == 0
    assert _interval_overlap(0, 100, 200, 300) == 0


def test_coverage_overlap_helper() -> None:
    assert _coverage_overlap([(0, 100), (200, 300)], [(0, 300)]) == 200
    assert _coverage_overlap([(0, 100)], [(200, 300)]) == 0


def _brute_force_optimum(
    source: list[SSAEvent],
    target: list[SSAEvent],
    min_overlap: int,
    max_block_size: int | None = None,
) -> tuple[
    set[tuple[int, int, int, int]],
    set[tuple[int, int, int, int]],
    dict[tuple[int, int, int, int], int],
    set[int],
    set[int],
]:
    """Independent reference implementation via exhaustive path enumeration."""
    n, m = len(source), len(target)
    edges = [
        [
            _signed_interval_overlap(
                source[i].start, source[i].end, target[j].start, target[j].end
            )
            >= min_overlap
            for j in range(m)
        ]
        for i in range(n)
    ]

    def connected(s_lo: int, s_hi: int, t_lo: int, t_hi: int) -> bool:
        seen_s = {s_lo}
        seen_t: set[int] = set()
        stack = [s_lo]
        while stack:
            si = stack.pop()
            for tj in range(t_lo, t_hi + 1):
                if tj not in seen_t and edges[si][tj]:
                    seen_t.add(tj)
                    for nxt in range(s_lo, s_hi + 1):
                        if nxt not in seen_s and edges[nxt][tj]:
                            seen_s.add(nxt)
                            stack.append(nxt)
        return len(seen_s) == s_hi - s_lo + 1 and len(seen_t) == t_hi - t_lo + 1

    def block_overlap(s_lo: int, s_hi: int, t_lo: int, t_hi: int) -> int:
        source_intervals = [(ev.start, ev.end) for ev in source[s_lo : s_hi + 1]]
        target_intervals = [(ev.start, ev.end) for ev in target[t_lo : t_hi + 1]]
        return _coverage_overlap(source_intervals, target_intervals)

    best: tuple[int, int] | None = None
    edge_in_all: set[tuple[int, int, int, int]] | None = None
    edge_in_any: set[tuple[int, int, int, int]] = set()
    edge_overlaps: dict[tuple[int, int, int, int], int] = {}

    def search(
        i: int,
        j: int,
        overlap: int,
        unmatched: int,
        used: set[tuple[int, int, int, int]],
    ) -> None:
        nonlocal best, edge_in_all, edge_in_any
        if i == n and j == m:
            score = (overlap, -unmatched)
            if best is None or score > best:
                best = score
                edge_in_all = set(used)
                edge_in_any = set(used)
            elif score == best:
                assert edge_in_all is not None
                edge_in_all &= set(used)
                edge_in_any |= set(used)
            return

        if i < n:
            search(i + 1, j, overlap, unmatched + 1, used)
        if j < m:
            search(i, j + 1, overlap, unmatched + 1, used)
        source_hi_limit = n if max_block_size is None else min(n, i + max_block_size)
        target_hi_limit = m if max_block_size is None else min(m, j + max_block_size)
        for source_hi in range(i, source_hi_limit):
            for target_hi in range(j, target_hi_limit):
                if connected(i, source_hi, j, target_hi):
                    edge = (i, source_hi, j, target_hi)
                    edge_overlaps.setdefault(
                        edge, block_overlap(i, source_hi, j, target_hi)
                    )
                    search(
                        source_hi + 1,
                        target_hi + 1,
                        overlap + edge_overlaps[edge],
                        unmatched,
                        used | {edge},
                    )

    search(0, 0, 0, 0, set())
    assert best is not None and edge_in_all is not None

    covered_source = {
        si for (s_lo, s_hi, _, _) in edge_in_any for si in range(s_lo, s_hi + 1)
    }
    covered_target = {
        tj for (_, _, t_lo, t_hi) in edge_in_any for tj in range(t_lo, t_hi + 1)
    }
    never_source = {index for index in range(n) if index not in covered_source}
    never_target = {index for index in range(m) if index not in covered_target}
    return edge_in_all, edge_in_any, edge_overlaps, never_source, never_target


def _assert_matches_brute_force(
    source: list[SSAEvent],
    target: list[SSAEvent],
    min_overlap: int,
    max_block_size: int | None,
) -> None:
    result = align_events(
        source,
        target,
        min_overlap=min_overlap,
        max_block_size=max_block_size,
    )
    all_edges, any_edges, edge_overlaps, never_source, never_target = (
        _brute_force_optimum(source, target, min_overlap, max_block_size)
    )

    def as_edge(pair: LinePair) -> tuple[int, int, int, int]:
        return (
            pair.source_indices[0],
            pair.source_indices[-1],
            pair.target_indices[0],
            pair.target_indices[-1],
        )

    dp_all = {as_edge(pair) for pair in result.pairs}
    dp_any = {as_edge(pair) for pair in result.groups}
    assert dp_all == all_edges
    assert dp_any == any_edges

    for pair in result.groups:
        assert pair.overlap == edge_overlaps[as_edge(pair)]

    assert set(result.source_unmatched) == never_source
    assert set(result.target_unmatched) == never_target


def test_matches_brute_force_on_random_small_tracks() -> None:
    generator = random.Random(42)
    configs = [
        (1, None),
        (1, 1),
        (1, 2),
        (0, None),
        (0, 2),
    ]

    for _ in range(40):
        n = generator.randrange(1, 5)
        m = generator.randrange(1, 5)
        source: list[SSAEvent] = []
        cursor = 0
        for _ in range(n):
            start = cursor + generator.randrange(0, 50)
            end = start + generator.randrange(10, 120)
            source.append(make_event(start, end))
            cursor = end
        target: list[SSAEvent] = []
        cursor = 0
        for _ in range(m):
            start = cursor + generator.randrange(0, 50)
            end = start + generator.randrange(10, 120)
            target.append(make_event(start, end))
            cursor = end

        for min_overlap, max_block_size in configs:
            _assert_matches_brute_force(source, target, min_overlap, max_block_size)


def test_line_pair_is_frozen_dataclass() -> None:
    pair = LinePair((0,), (0,), 10, (make_event(0, 10),), (make_event(0, 10),))
    with pytest.raises(AttributeError):
        pair.overlap = 20  # type: ignore[misc]


def test_append_interval_merges_adjacent() -> None:
    from pysubs2.alignment import _append_interval

    merged = _append_interval([], 0, 100)
    merged = _append_interval(merged, 50, 150)
    assert merged == [(0, 150)]
    merged = _append_interval(merged, 300, 400)
    assert merged == [(0, 150), (300, 400)]


def test_contiguous_runs_helper() -> None:
    from pysubs2.alignment import _contiguous_runs

    assert _contiguous_runs([]) == []
    assert _contiguous_runs([0, 1, 2, 4, 5, 8]) == [(0, 2), (4, 5), (8, 8)]


def test_connected_components_helper() -> None:
    from pysubs2.alignment import _connected_components

    # edges: 0-0, 0-1 (component A); isolated source 1; isolated target 3.
    overlaps = [
        [True, True, False, False],
        [False, False, False, False],
    ]
    components = _connected_components(overlaps)
    assert ([0], [0, 1]) in components
    assert ([1], []) in components
    assert ([], [3]) in components


def test_disconnected_time_regions_align_independently() -> None:
    # Two clearly separated 1:1 matches; nothing may bridge across the gap.
    source = [
        make_event(0, 100, "a"),
        make_event(500, 600, "gap"),
        make_event(1000, 1100, "b"),
    ]
    target = [make_event(10, 90, "x"), make_event(1010, 1090, "y")]

    result = align_events(source, target)

    assert pair_keys(result) == [((0,), (0,)), ((2,), (1,))]
    assert result.source_unmatched == (1,)
    assert result.target_unmatched == ()


def test_component_with_nonconsecutive_indices() -> None:
    # Source 1 is isolated; sources 0 and 2 join the same component through targets.
    source = [
        make_event(0, 100),
        make_event(500, 600),
        make_event(90, 200),
    ]
    target = [make_event(10, 150), make_event(120, 190)]

    result = align_events(source, target)

    assert result.source_unmatched == (1,)
    grouped = {index for pair in result.groups for index in pair.source_indices}
    assert grouped == {0, 2}


def test_default_cap_matches_explicit_cap() -> None:
    source = [make_event(i * 100, i * 100 + 90) for i in range(12)]
    target = [make_event(i * 100 + 5, i * 100 + 95) for i in range(12)]

    assert (
        align_events(source, target).groups
        == align_events(source, target, max_block_size=10).groups
    )
