from random import randint, seed

import pytest

import polyline
from polyline import (
    Checkpoint,
    OversizedPointError,
    PolylineIndex,
    StringRange,
)
from polyline.polyline import _trans


# pylint: disable=invalid-name,too-few-public-methods
class RecordingRange:
    """RangeReadable wrapper that records every read and enforces the budget."""

    def __init__(self, expression, max_read):
        self._expression = expression
        self._max_read = max_read
        self.reads = []

    def read_range(self, start, end):
        size = end - start
        self.reads.append((start, end, size))
        assert size <= self._max_read, f"read of {size} chars exceeds {self._max_read}"
        return self._expression[start:end]


def point_lengths(expression):
    """Length in characters of each point's two-value encoding."""
    lengths = []
    index = 0
    while index < len(expression):
        start = index
        _, index = _trans(expression, index)
        _, index = _trans(expression, index)
        lengths.append(index - start)
    return lengths


def optimal_segment_count(lengths, max_read):
    """DP for the minimum number of segments covering all points."""
    count = len(lengths)
    dp = [0] * (count + 1)
    for k in range(count - 1, -1, -1):
        total = 0
        best = count
        for j in range(k, count):
            total += lengths[j]
            if total > max_read:
                break
            best = min(best, 1 + dp[j + 1])
        dp[k] = best
    return dp[0]


def random_polyline(num_points, precision=5):
    coords = []
    for _ in range(num_points):
        coords.append((randint(-89000, 89000) / 1000.0,
                       randint(-179000, 179000) / 1000.0))
    return polyline.encode(coords, precision), coords


def test_build_rejects_non_positive_budget():
    expression = polyline.encode([(1.0, 2.0)])
    with pytest.raises(ValueError):
        PolylineIndex.build(expression, 0)
    with pytest.raises(ValueError):
        PolylineIndex.build(expression, -3)


def test_empty_polyline():
    index = PolylineIndex.build("", 10)
    assert index.point_count == 0
    assert index.checkpoints == (Checkpoint(0, 0, 0, 0),)
    assert index.decode_slice() == []
    with pytest.raises(IndexError):
        index.decode_point(0)


def test_single_point_roundtrip():
    expression = polyline.encode([(40.64155, -8.65344)])
    index = PolylineIndex.build(expression, len(expression))
    assert index.point_count == 1
    assert len(index.checkpoints) == 1
    assert index.decode_point(0) == (40.64155, -8.65344)


def test_every_point_matches_full_decode_and_respects_budget():
    seed(42)
    for _ in range(20):
        expression, _ = random_polyline(randint(1, 60))
        expected = polyline.decode(expression)
        lengths = point_lengths(expression)
        for max_read in range(max(lengths), max(lengths) + 8):
            index = PolylineIndex.build(expression, max_read)
            storage = RecordingRange(expression, max_read)
            rebuilt = PolylineIndex.from_source(
                storage, list(index.checkpoints), index.point_count,
                len(expression), max_read=max_read,
            )
            for point_index in range(len(expected)):
                assert rebuilt.decode_point(point_index) == expected[point_index]
            assert storage.reads, "each point must trigger a ranged read"
            for _, _, size in storage.reads:
                assert size <= max_read


def test_checkpoint_count_is_minimal():
    seed(7)
    for _ in range(30):
        expression, _ = random_polyline(randint(1, 80))
        lengths = point_lengths(expression)
        for max_read in range(max(lengths), sum(lengths) + 1):
            index = PolylineIndex.build(expression, max_read)
            assert len(index.checkpoints) == optimal_segment_count(lengths, max_read)


def test_budget_larger_than_string_uses_single_checkpoint():
    expression, _ = random_polyline(25)
    index = PolylineIndex.build(expression, len(expression) + 5)
    assert len(index.checkpoints) == 1
    assert index.checkpoints[0] == Checkpoint(0, 0, 0, 0)


def test_oversized_point_is_reported_with_index():
    coords = [(0.0, 0.0), (89.0, 179.0), (89.1, 179.1), (0.0, 0.0)]
    expression = polyline.encode(coords)
    lengths = point_lengths(expression)
    max_read = max(lengths) - 1
    oversized = next(i for i, size in enumerate(lengths) if size > max_read)

    with pytest.raises(OversizedPointError) as exc_info:
        PolylineIndex.build(expression, max_read)
    error = exc_info.value
    assert error.point_index == oversized
    assert error.required == lengths[oversized]
    assert error.limit == max_read
    assert str(oversized) in str(error)


def test_smallest_fixed_points_still_decode_at_budget_one():
    expression = polyline.encode([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)])
    assert point_lengths(expression) == [2, 2, 2]
    with pytest.raises(OversizedPointError) as exc_info:
        PolylineIndex.build(expression, 1)
    assert exc_info.value.point_index == 0
    assert exc_info.value.required == 2

    index = PolylineIndex.build(expression, 2)
    storage = RecordingRange(expression, 2)
    rebuilt = PolylineIndex.from_source(
        storage, list(index.checkpoints), index.point_count, len(expression),
        max_read=2,
    )
    for point_index in range(3):
        assert rebuilt.decode_point(point_index) == (0.0, 0.0)
    for _, _, size in storage.reads:
        assert size <= 2


def test_slices_match_full_decode():
    seed(99)
    expression, _ = random_polyline(40)
    expected = polyline.decode(expression)
    lengths = point_lengths(expression)
    index = PolylineIndex.build(expression, max(lengths) + 3)
    storage = RecordingRange(expression, max(lengths) + 3)
    rebuilt = PolylineIndex.from_source(
        storage, list(index.checkpoints), index.point_count, len(expression),
        max_read=max(lengths) + 3,
    )

    bounds = [None, 0, 1, 5, 39, 40, 100, -1, -5, -40, -100]
    for start in bounds:
        for stop in bounds:
            kwargs = {} if start is None else {"start": start}
            if stop is not None:
                kwargs["stop"] = stop
            py_start = 0 if start is None else start
            py_stop = len(expected) if stop is None else stop
            assert rebuilt.decode_slice(**kwargs) == expected[py_start:py_stop]


def test_full_slice_reads_only_budget_sized_chunks():
    seed(123)
    expression, _ = random_polyline(50)
    lengths = point_lengths(expression)
    max_read = max(lengths) + 2
    index = PolylineIndex.build(expression, max_read)
    storage = RecordingRange(expression, max_read)
    rebuilt = PolylineIndex.from_source(
        storage, list(index.checkpoints), index.point_count, len(expression),
        max_read=max_read,
    )
    assert rebuilt.decode_slice() == polyline.decode(expression)
    assert len(storage.reads) == len(index.checkpoints)
    for _, _, size in storage.reads:
        assert size <= max_read


def test_precision_and_geojson_options():
    coords = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    expression = polyline.encode(coords, 6)
    index = PolylineIndex.build(expression, 20, precision=6, geojson=True)
    assert index.precision == 6
    assert index.geojson is True
    expected = polyline.decode(expression, 6, geojson=True)
    for point_index, coordinate in enumerate(expected):
        assert index.decode_point(point_index) == coordinate
    assert index.decode_slice(1, 3) == expected[1:3]


def test_dict_roundtrip_with_fresh_storage():
    expression, _ = random_polyline(30)
    lengths = point_lengths(expression)
    max_read = max(lengths) + 4
    index = PolylineIndex.build(expression, max_read)
    data = index.to_dict()
    assert data["point_count"] == index.point_count
    assert data["max_read"] == max_read

    storage = RecordingRange(expression, max_read)
    rebuilt = PolylineIndex.from_dict(storage, data)
    expected = polyline.decode(expression)
    for point_index, coordinate in enumerate(expected):
        assert rebuilt.decode_point(point_index) == coordinate
    assert rebuilt.decode_slice(10, 20) == expected[10:20]


def test_string_range_adapter():
    source = StringRange("abcdef")
    assert source.read_range(2, 5) == "cde"
    assert isinstance(source, polyline.RangeReadable)
