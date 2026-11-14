import math

import polyline
from polyline import encode
from polyline.feasibility import (
    CellRange,
    cell_range,
    compare,
    decode_grid,
    intersect_cells,
    main,
    render_comparison,
    _nextafter,
)
from polyline.polyline import _py2_round


OFFICIAL = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
OFFICIAL_6 = "_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI"


def test_decode_grid_matches_decoded_coordinates():
    grid = decode_grid(OFFICIAL, 5)
    decoded = polyline.decode(OFFICIAL, 5)
    assert len(grid) == len(decoded)
    for (lat_n, lng_n), (lat, lng) in zip(grid, decoded):
        assert lat_n == round(lat * 10 ** 5)
        assert lng_n == round(lng * 10 ** 5)
    assert grid == [(3850000, -12020000), (4070000, -12095000), (4325200, -12645300)]


def test_decode_grid_precision_six():
    grid = decode_grid(OFFICIAL_6, 6)
    assert grid == [
        (38500000, -120200000),
        (40700000, -120950000),
        (43252000, -126453000),
    ]


def test_nextafter_matches_math_library():
    if not hasattr(math, "nextafter"):
        return
    values = [0.0, -0.0, 1.0, -1.0, 38.5, -120.2, 5e-324, -5e-324, 1e308]
    directions = [math.inf, -math.inf]
    for value in values:
        for direction in directions:
            assert _nextafter(value, direction) == math.nextafter(value, direction)
    assert _nextafter(2.0, 2.0) == 2.0


def test_cell_endpoints_satisfy_library_predicate():
    for n in (-1234567, -1, 0, 1, 9876543):
        cell = cell_range(n, 5)
        assert cell.contains(cell.lower)
        assert cell.contains(cell.upper)
        assert _py2_round(cell.lower * cell.factor) == n
        assert _py2_round(cell.upper * cell.factor) == n
        assert _py2_round(_nextafter(cell.lower, -math.inf) * cell.factor) != n
        assert _py2_round(_nextafter(cell.upper, math.inf) * cell.factor) != n


def test_half_values_round_away_from_zero_at_boundaries():
    cell_10, cell_11 = cell_range(10, 5), cell_range(11, 5)
    assert not cell_10.contains(0.000105)
    assert cell_11.contains(0.000105)
    cell_m10, cell_m11 = cell_range(-10, 5), cell_range(-11, 5)
    assert not cell_m10.contains(-0.000105)
    assert cell_m11.contains(-0.000105)


def test_floating_point_boundary_flip_is_reported_and_still_predicate_valid():
    # At this negative cell both nominal half-cell doubles round toward
    # zero, so the true preimage shrinks to the neighbouring doubles.
    cell = cell_range(-1800000, 5)
    assert not cell.ideal_lower_included
    assert cell.ideal_upper_included
    assert not cell.contains(cell.ideal_lower)
    assert cell.contains(cell.lower) and cell.contains(cell.upper)
    assert cell.lower > cell.ideal_lower
    assert isinstance(cell, CellRange)


def test_naive_half_cell_rule_disagrees_in_both_directions():
    # The library predicate must be authoritative: comparing the decoded
    # error against half a cell misclassifies some doubles in each direction.
    n, factor = -1999001, 10 ** 5
    center, half = n / factor, 0.5 / factor
    cell = cell_range(n, 5)

    # Naive rule accepts this double, but the encoder rounds it to n-1.
    false_positive = -19.990015
    assert abs(false_positive - center) <= half
    assert not cell.contains(false_positive)
    assert _py2_round(false_positive * factor) == n - 1

    # Naive rule rejects this double, but the encoder rounds it to n.
    false_negative = -19.990005
    assert abs(false_negative - center) > half
    assert cell.contains(false_negative)
    assert _py2_round(false_negative * factor) == n


def test_same_precision_intersection_requires_identical_cells():
    assert intersect_cells(cell_range(10, 5), cell_range(10, 5)) is not None
    assert intersect_cells(cell_range(10, 5), cell_range(11, 5)) is None
    assert intersect_cells(cell_range(10, 5), cell_range(-10, 5)) is None


def test_cross_precision_ranges_satisfy_both_encoder_predicates():
    coords = [(38.5, -120.2), (40.7123456, -120.95), (43.252, -126.4531)]
    forward, _ = compare(encode(coords, 5), encode(coords, 6), 5, 6)
    assert forward.feasible
    for point, (lat, lng) in zip(forward.points, coords):
        low, high = point.lat_range
        assert low <= lat <= high
        low_lng, high_lng = point.lng_range
        assert low_lng <= lng <= high_lng
        for value in (low, high):
            assert _py2_round(value * 1e5) == point.lat_cell_a
            assert _py2_round(value * 1e6) == point.lat_cell_b
        for value in (low_lng, high_lng):
            assert _py2_round(value * 1e5) == point.lng_cell_a
            assert _py2_round(value * 1e6) == point.lng_cell_b


def test_round_trip_random_tracks_are_always_feasible():
    for seed in range(25):
        coords = [
            (seed * 1.5 + 0.1 * idx, -seed - 0.2 * idx)
            for idx in range(1, 6)
        ]
        forward, reversed_reading = compare(
            encode(coords, 5), encode(coords, 7), 5, 7
        )
        assert forward.feasible
        for point, (lat, lng) in zip(forward.points, coords):
            assert point.lat_range[0] <= lat <= point.lat_range[1]
            assert point.lng_range[0] <= lng <= point.lng_range[1]
        # A reversed copy of the same track is only feasible reversed.
        reverse_forward, reverse_reversed = compare(
            encode(coords, 5), encode(list(reversed(coords)), 5)
        )
        assert not reverse_forward.feasible
        assert reverse_reversed.feasible
        assert [point.index_in_b for point in reverse_reversed.points] == [4, 3, 2, 1, 0]


def test_first_infeasible_point_is_localised():
    base = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
    perturbed = [(1.0, 2.0), (3.00002, 4.0), (5.0, 6.0)]
    forward, _ = compare(encode(base, 5), encode(perturbed, 5))
    assert not forward.feasible
    first = forward.first_infeasible
    assert first.index == 1
    assert first.lat_range is None
    assert first.lng_range is not None
    text = render_comparison(forward)
    assert "first impossible point: i=1" in text
    assert "EMPTY" in text


def test_length_mismatch_is_not_feasible():
    short = encode([(1.0, 2.0), (3.0, 4.0)], 5)
    long = encode([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)], 5)
    forward, reversed_reading = compare(short, long)
    assert not forward.feasible and not reversed_reading.feasible
    assert "do not have the same number of points" in render_comparison(forward)


def test_both_orientations_feasible_for_palindromic_track():
    track = [(1.0, 2.0), (3.0, 4.0), (1.0, 2.0)]
    forward, reversed_reading = compare(encode(track, 5), encode(track[::-1], 5))
    assert forward.feasible and reversed_reading.feasible


def test_cli_prints_both_orientations_and_verdict(capsys):
    track = [(10.0, 20.0), (10.0001, 20.0002), (10.0003, 20.0001)]
    rc = main([encode(track, 5), encode(list(reversed(track)), 5)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "orientation=forward" in out
    assert "orientation=reversed" in out
    assert "verdict: only the REVERSED orientation is feasible." in out


def test_cli_rejects_incompatible_strings(capsys):
    rc = main([encode([(1.0, 2.0)], 5), encode([(9.0, 9.0)], 5)])
    assert rc == 0
    assert "NEITHER orientation" in capsys.readouterr().out
