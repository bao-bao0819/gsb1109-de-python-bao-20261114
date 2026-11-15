import itertools
from random import Random

import pytest

import polyline
from polyline.merge import MAX_MERGE_FRAGMENTS, merge_candidates


def _brute_force(segments):
    """
    Reference implementation: try every order and orientation.

    It is only usable for tiny inputs.  The overlap rule mirrors the library:
    the longest shared suffix/prefix is collapsed, but a multi-point fragment
    may not be swallowed whole.
    """
    best = None
    best_points = set()
    count = len(segments)

    def overlap(left, right):
        limit = min(len(left), len(right))
        if limit > 1:
            limit -= 1
        for size in range(limit, 0, -1):
            if left[-size:] == right[:size]:
                return size
        return 0

    for order in itertools.permutations(range(count)):
        for sides in itertools.product((False, True), repeat=count):
            points = None
            tail = None
            for index, side in zip(order, sides):
                part = list(segments[index])
                if side:
                    part.reverse()
                if points is None:
                    points, tail = part, part
                else:
                    shared = overlap(tail, part)
                    points = points + part[shared:]
                    tail = part
            merged = tuple(points)
            if best is None or len(merged) < len(best):
                best, best_points = merged, {merged}
            elif len(merged) == len(best):
                best_points.add(merged)
    return best, best_points


def test_merge_basic_chain():
    merged = polyline.merge([
        [(0, 0), (1, 0), (2, 0)],
        [(2, 0), (3, 0), (4, 0)],
    ])
    assert merged == [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]


def test_merge_flips_fragments():
    merged = polyline.merge([
        [(0, 0), (1, 0), (2, 0)],
        [(4, 0), (3, 0), (2, 0)],
    ])
    assert merged == [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]


def test_merge_orders_unordered_fragments():
    merged = polyline.merge([
        [(4, 0), (3, 0)],
        [(0, 0), (1, 0)],
        [(1, 0), (2, 0), (3, 0)],
    ])
    assert merged == [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]


def test_merge_multiple_shared_junction_points():
    first = [(0, 0), (1, 0), (1, 0), (2, 0)]
    second = [(1, 0), (1, 0), (2, 0), (3, 0)]
    merged = polyline.merge([first, second])
    assert merged == [(0, 0), (1, 0), (1, 0), (2, 0), (3, 0)]


def test_merge_preserves_repeated_sampling():
    fragment = [(0, 0), (0, 0), (1, 0)]
    merged = polyline.merge([fragment, [(1, 0), (2, 0)]])
    assert merged == [(0, 0), (0, 0), (1, 0), (2, 0)]


def test_merge_keeps_real_return_leg():
    outbound = [(0, 0), (1, 0), (2, 0)]
    inbound = [(2, 0), (1, 0), (0, 0)]
    candidates = merge_candidates([outbound, inbound], include_reversals=True)
    assert {candidate.points for candidate in candidates} == {
        ((0, 0), (1, 0), (2, 0), (1, 0), (0, 0)),
        ((2, 0), (1, 0), (0, 0), (1, 0), (2, 0)),
    }
    assert all(
        candidate.points.count((1, 0)) == 2 for candidate in candidates
    )


def test_merge_out_and_back_through_shared_sample():
    candidates = merge_candidates(
        [[(0, 0), (1, 0), (2, 0)], [(1, 0), (2, 0), (1, 0), (0, 0)]]
    )
    assert candidates[0].points == ((0, 0), (1, 0), (2, 0), (1, 0), (0, 0))


def test_merge_marks_fully_contained_fragment_as_placement():
    segments = [
        [(0, 0), (1, 0), (2, 0)],
        [(0, 0), (1, 0)],
    ]
    candidate = merge_candidates(segments)[0]
    assert candidate.points == ((1, 0), (0, 0), (1, 0), (2, 0))
    assert candidate.verify(segments)


def test_merge_encoded_round_trip():
    first = polyline.encode([(38.5, -120.2), (40.7, -120.95)])
    second = polyline.encode([(40.7, -120.95), (43.252, -126.453)])
    encoded = polyline.merge_encoded([first, second])
    assert polyline.decode(encoded) == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_merge_respects_geojson_input():
    first = [(-120.2, 38.5), (-120.95, 40.7)]
    second = [(-120.95, 40.7), (-126.453, 43.252)]
    merged = polyline.merge([first, second], start_point=(-120.2, 38.5), geojson=True)
    assert merged == [
        (-120.2, 38.5),
        (-120.95, 40.7),
        (-126.453, 43.252),
    ]


def test_merge_accepts_numbered_mapping():
    segments = {
        'a': [(0, 0), (1, 0), (2, 0)],
        'b': [(2, 0), (3, 0)],
    }
    candidate = merge_candidates(segments)[0]
    assert candidate.fragment_ids == ('a', 'b')
    assert candidate.verify(segments)
    assert candidate.points == ((0, 0), (1, 0), (2, 0), (3, 0))


def test_merge_single_fragment_is_unchanged():
    points = [(0, 0), (1, 0), (2, 0)]
    assert polyline.merge([points]) == points


def test_merge_requires_unique_fragment_ids():
    with pytest.raises(ValueError):
        polyline.merge([('same', [(0, 0), (1, 0)]), ('same', [(1, 0), (2, 0)])])


def test_merge_limits_batch_to_twelve_fragments():
    fragments = [[(index, 0), (index + 1, 0)] for index in range(12)]
    assert polyline.merge(fragments)[0] == (0, 0)
    assert polyline.merge(fragments)[-1] == (12, 0)

    with pytest.raises(ValueError):
        polyline.merge(fragments + [[(12, 0), (13, 0)]])

    assert MAX_MERGE_FRAGMENTS == 12


def test_merge_start_point_chooses_direction():
    fragments = [
        [(0, 0), (1, 0), (2, 0)],
        [(2, 0), (3, 0)],
    ]
    forward = polyline.merge(fragments, start_point=(0, 0))
    backward = polyline.merge(fragments, start_point=(3, 0))
    assert forward[0] == (0, 0)
    assert backward[0] == (3, 0)
    assert forward == list(reversed(backward))


def test_merge_start_point_keeps_loop_directions():
    origin = (0, 0)
    left = (0, -1)
    right = (0, 1)
    spur = (1, 0)
    candidates = merge_candidates(
        {
            'loop': [origin, left, right, origin],
            'spur': [origin, spur],
        },
        start_point=origin,
    )
    routes = {candidate.points for candidate in candidates}
    assert routes == {
        (origin, left, right, origin, spur),
        (origin, right, left, origin, spur),
    }


def test_merge_two_verifiable_candidates_at_repeated_intersection():
    origin = (0, 0)
    left = (0, -1)
    right = (0, 1)
    spur = (1, 0)
    segments = {
        'loop': [origin, left, right, origin],
        'spur': [origin, spur],
    }
    candidates = merge_candidates(segments)
    assert len(candidates) == 2
    assert {candidate.points for candidate in candidates} == {
        (origin, left, right, origin, spur),
        (origin, right, left, origin, spur),
    }
    for candidate in candidates:
        assert candidate.verify(segments)
    assert all(candidate.points.count(origin) == 2 for candidate in candidates)


def test_merge_candidates_are_shortest_against_brute_force():
    rng = Random(20261114)
    alphabet = [(index, 0) for index in range(4)]

    for _ in range(25):
        size = rng.randint(1, 4)
        segments = [
            [rng.choice(alphabet) for _ in range(rng.randint(1, 4))]
            for _ in range(size)
        ]
        best, expected = _brute_force(segments)
        candidates = merge_candidates(
            segments, include_reversals=True, max_candidates=4096
        )
        assert {candidate.points for candidate in candidates} == expected
        assert all(len(candidate.points) == len(best) for candidate in candidates)


def test_merge_candidate_verify_rejects_tampering():
    segments = [[(0, 0), (1, 0), (2, 0)], [(2, 0), (3, 0)]]
    candidate = merge_candidates(segments)[0]
    assert candidate.verify(segments)
    tampered = polyline.MergeCandidate(
        points=candidate.points[:-1],
        fragment_ids=candidate.fragment_ids,
        flipped=candidate.flipped,
        placements=candidate.placements,
        overlap=candidate.overlap,
    )
    assert not tampered.verify(segments)
