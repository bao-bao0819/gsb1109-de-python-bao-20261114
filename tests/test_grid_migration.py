import random
from fractions import Fraction

import polyline
from polyline.polyline import _py2_round

from grid_migration import (
    GridSpec,
    Interval,
    analyze_record,
    analyze_records,
    format_report,
    quantize,
    quantization_cell,
)


def test_quantize_matches_library_rounder_on_decimals():
    rng = random.Random(20261114)
    for precision in (5, 6):
        factor = 10 ** precision
        for _ in range(2000):
            integer = rng.randint(-180 * factor, 180 * factor)
            value = Fraction(integer, factor)
            assert quantize(value, precision) == _py2_round(
                float(value) * factor
            )


def test_quantize_exact_halves_go_away_from_zero():
    half = Fraction(1, 2 * 10 ** 5)
    assert quantize(half, 5) == 1
    assert quantize(-half, 5) == -1
    assert quantize(Fraction(0), 5) == 0


def test_quantization_cell_round_trips_through_quantize():
    for precision in (5, 6):
        factor = 10 ** precision
        for code in (-7, -1, 0, 1, 7, 4064155, -4064155):
            cell = quantization_cell(code, precision)
            midpoint = Fraction(code, factor)
            assert cell.contains(midpoint)
            assert quantize(midpoint, precision) == code
            assert quantize(cell.low, precision) != code or cell.low_inclusive
            assert quantize(cell.high, precision) != code or cell.high_inclusive


def test_negative_cell_includes_near_zero_endpoint_only():
    cell = quantization_cell(-1, 5)
    near_zero = Fraction(-5, 10 ** 6)
    far_zero = Fraction(-15, 10 ** 6)
    assert cell.contains(near_zero)
    assert not cell.contains(far_zero)
    assert cell.low_inclusive is False
    assert cell.high_inclusive is True


def test_positive_cell_includes_far_from_zero_endpoint_only():
    cell = quantization_cell(1, 5)
    assert cell.contains(Fraction(5, 10 ** 6))
    assert not cell.contains(Fraction(15, 10 ** 6))
    assert cell.low_inclusive is True
    assert cell.high_inclusive is False


def test_zero_cell_excludes_both_half_boundaries():
    cell = quantization_cell(0, 5)
    assert not cell.contains(Fraction(5, 10 ** 6))
    assert not cell.contains(Fraction(-5, 10 ** 6))
    assert cell.contains(Fraction(0))
    assert cell.low_inclusive is False
    assert cell.high_inclusive is False


def test_interval_intersection_endpoint_flags():
    open_left = Interval(Fraction(0), Fraction(1), False, True)
    closed = Interval(Fraction(1), Fraction(2), True, True)
    overlap = open_left.intersection(closed)
    assert overlap == Interval(Fraction(1), Fraction(1), True, True)

    open_right = Interval(Fraction(1), Fraction(2), True, False)
    open_both = Interval(Fraction(0), Fraction(1), False, False)
    assert open_both.intersection(open_right) is None


def test_grid_cells_respect_open_endpoints_on_grid_boundaries():
    grid = GridSpec(Fraction(0), Fraction(1, 10 ** 5),
                    Fraction(0), Fraction(1, 10 ** 5))
    step = Fraction(1, 10 ** 5)

    closed_at_boundary = Interval(step, 2 * step, True, False)
    assert grid.cells_overlapping(closed_at_boundary, "lat") == [1]

    open_low_closed_high = Interval(step, 2 * step, False, True)
    assert grid.cells_overlapping(open_low_closed_high, "lat") == [1, 2]

    open_both = Interval(step, 2 * step, False, False)
    assert grid.cells_overlapping(open_both, "lat") == [1]

    open_high = Interval(0, step, True, False)
    assert grid.cells_overlapping(open_high, "lat") == [0]

    closed_high = Interval(0, step, True, True)
    assert grid.cells_overlapping(closed_high, "lat") == [0, 1]


def _cell_overlaps_independent(cell: Interval, interval: Interval) -> bool:
    """Overlap predicate written independently from Interval.intersection."""
    if cell.low == interval.high:
        return interval.high_inclusive
    if cell.high == interval.low:
        return False
    return cell.low < interval.high and interval.low < cell.high


def _oracle_candidates(grid, lat_interval, lng_interval):
    """Enumerate every overlapping cell with an independent predicate."""
    def indices(interval, axis):
        origin, step = grid._axis(axis)
        mid_index = ((interval.low + interval.high) / 2 - origin) / step
        center_index = mid_index.numerator // mid_index.denominator
        found = []
        for index in range(center_index - 6, center_index + 7):
            if _cell_overlaps_independent(
                grid.cell_interval(index, axis), interval
            ):
                found.append(index)
        return found

    lat = indices(lat_interval, "lat")
    lng = indices(lng_interval, "lng")
    return {(a, b) for a in lat for b in lng}


def test_archive_only_candidate_cells_match_independent_oracle():
    rng = random.Random(97)
    grid = GridSpec(
        Fraction(13, 1000), Fraction(7, 10 ** 6),
        Fraction(-29, 1000), Fraction(11, 10 ** 6),
    )
    for _ in range(50):
        lat = rng.uniform(-80, 80)
        lng = rng.uniform(-170, 170)
        archive = polyline.encode([(lat, lng)], 5)
        result = analyze_record("r", archive, grid)[0]
        expected = _oracle_candidates(
            grid, result.lat_interval, result.lng_interval
        )
        assert set(result.candidate_cells) == expected
        assert len(result.candidate_cells) >= 1


def test_original_string_refines_region_and_reproduces_archive():
    point = (40.641552, -8.653441)
    original = polyline.encode([point], 6)
    archive = polyline.encode([point], 5)

    grid = GridSpec(Fraction(0), Fraction(1, 10 ** 5),
                    Fraction(0), Fraction(1, 10 ** 5))
    result = analyze_record("p", archive, grid, original)[0]

    assert result.original_reproduces_archive is True
    assert result.conflict is False
    original_lat, original_lng = polyline.decode(original, 6)[0]
    expected_lat = quantization_cell(4064155, 5).intersection(
        quantization_cell(round(original_lat * 10 ** 6), 6)
    )
    expected_lng = quantization_cell(-865344, 5).intersection(
        quantization_cell(round(original_lng * 10 ** 6), 6)
    )
    assert result.lat_interval == expected_lat
    assert result.lng_interval == expected_lng

    coarse_only = analyze_record("p", archive, grid)[0]
    assert len(result.candidate_cells) <= len(coarse_only.candidate_cells)
    assert set(result.candidate_cells).issubset(
        set(coarse_only.candidate_cells)
    )


def test_conflicting_original_is_flagged_and_discarded():
    archive = polyline.encode([(40.64155, -8.653)], 5)
    original = polyline.encode([(40.6416, -8.653)], 6)

    grid = GridSpec(Fraction(0), Fraction(5, 10 ** 6),
                    Fraction(0), Fraction(5, 10 ** 6))
    result = analyze_record("c", archive, grid, original)[0]
    archive_only = analyze_record("c", archive, grid)[0]

    assert result.conflict is True
    assert result.original_consistent_with_archive is False
    assert result.original_reproduces_archive is False
    assert result.lat_interval == archive_only.lat_interval
    assert result.lng_interval == archive_only.lng_interval
    assert result.candidate_cells == archive_only.candidate_cells


def test_negative_half_boundary_grid_overlap_end_to_end():
    archive = polyline.encode([(-40.64155, -8.653)], 5)
    grid = GridSpec(Fraction(0), Fraction(5, 10 ** 6),
                    Fraction(0), Fraction(5, 10 ** 6))
    result = analyze_record("n", archive, grid)[0]

    lat_indices = [cell[0] for cell in result.candidate_cells]
    # Feasible lat cell is (-40.641555, -40.641545] with 5e-6 grid steps.
    assert -8128312 not in lat_indices
    assert -8128311 in lat_indices
    assert -8128310 in lat_indices
    assert -8128309 in lat_indices
    assert -8128308 not in lat_indices


def test_multi_position_strings_analyzed_per_position():
    points = [(40.641123, -8.653321), (40.642, -8.654)]
    original = polyline.encode(points, 6)
    archive = polyline.encode(points, 5)
    grid = GridSpec(Fraction(0), Fraction(1, 10 ** 5),
                    Fraction(0), Fraction(1, 10 ** 5))

    results = analyze_record("m", archive, grid, original)
    assert [r.point_id for r in results] == ["m#0", "m#1"]
    assert all(r.conflict is False for r in results)
    assert all(r.original_reproduces_archive is True for r in results)
    assert all(r.candidate_cells for r in results)


def test_analyze_records_and_report_render():
    records = [
        ("a", polyline.encode([(1.5, 2.5)], 5), None),
        (
            "b",
            polyline.encode([(-1.5, -2.5)], 5),
            polyline.encode([(-1.5, -2.5)], 6),
        ),
    ]
    grid = GridSpec(Fraction(0), Fraction(1, 10 ** 4),
                    Fraction(0), Fraction(1, 10 ** 4))
    results = analyze_records(records, grid)
    report = format_report(results)
    assert "点 a" in report and "点 b" in report
    assert "候选" in report or "唯一" in report
    assert len(results) == 2


def test_original_can_fail_reproduction_yet_remain_geometrically_consistent():
    # The p6 center lands exactly on a p5 half-grid boundary on the far side
    # of the true coordinate, so re-encoding the original yields a different
    # archive string even though both strings derive from the same point.
    point = (35.0797845, -129.57145)
    archive = polyline.encode([point], 5)
    original = polyline.encode([point], 6)
    assert polyline.encode(polyline.decode(original, 6), 5) != archive

    grid = GridSpec(Fraction(0), Fraction(5, 10 ** 6),
                    Fraction(0), Fraction(5, 10 ** 6))
    result = analyze_record("h", archive, grid, original)[0]

    assert result.original_reproduces_archive is False
    assert result.original_consistent_with_archive is True
    assert result.conflict is False
    true_lat = Fraction(350797845, 10 ** 7)
    assert result.lat_interval.contains(true_lat)


def test_feasible_regions_always_contain_the_true_cell():
    rng = random.Random(2026)
    grid = GridSpec(
        Fraction(13, 1000), Fraction(3, 10 ** 6),
        Fraction(-29, 1000), Fraction(7, 10 ** 6),
    )

    def index(value, origin, step):
        quotient = (value - origin) / step
        return quotient.numerator // quotient.denominator

    for trial in range(600):
        lat = Fraction(rng.randint(-80 * 10 ** 7, 80 * 10 ** 7), 10 ** 7)
        lng = Fraction(rng.randint(-170 * 10 ** 7, 170 * 10 ** 7), 10 ** 7)
        archive = polyline.encode([(float(lat), float(lng))], 5)
        true_cell = (
            index(lat, grid.lat_origin, grid.lat_step),
            index(lng, grid.lng_origin, grid.lng_step),
        )
        mode = trial % 3

        if mode == 0:
            result = analyze_record(str(trial), archive, grid)[0]
        elif mode == 1:
            original = polyline.encode([(float(lat), float(lng))], 6)
            result = analyze_record(str(trial), archive, grid, original)[0]
            assert result.conflict is False
            assert result.lat_interval.contains(lat)
            assert result.lng_interval.contains(lng)
        else:
            shifted_lat = lat + Fraction(
                rng.choice((-1, 1)) * rng.randint(30, 90), 10 ** 6
            )
            original = polyline.encode(
                [(float(shifted_lat), float(lng))], 6
            )
            result = analyze_record(str(trial), archive, grid, original)[0]
            assert result.conflict is True
            baseline = analyze_record(str(trial), archive, grid)[0]
            assert result.candidate_cells == baseline.candidate_cells

        assert true_cell in result.candidate_cells
        if result.unique:
            assert result.determined_cell == true_cell
