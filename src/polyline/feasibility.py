"""
Common-source feasibility analysis for encoded polylines.

Two survey vendors may return polylines that claim to come from the same
sequence of positioning points. This module decides whether a single
sequence of original coordinates could have produced both encodings and,
point by point, computes the range every original coordinate must lie in.

Endpoint semantics follow the rounding actually implemented by this
package in :func:`polyline.polyline._py2_round`: half values are rounded
away from zero, and the whole predicate is evaluated in floating point.
Decoding both strings and comparing the error against half a grid cell is
not sufficient, because floating-point multiplication moves a fraction of
the nominal half-cell boundaries onto the neighbouring cell. Every range
produced here is therefore verified against the real predicate
``_py2_round(x * factor) == n`` rather than against an idealised interval.
"""
from __future__ import annotations

import argparse
import math
import struct
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# Reuse the exact encoder predicate and chunk reader so every range is
# guaranteed to match what encode() actually does.
from .polyline import _py2_round, _trans  # pylint: disable=protected-access

GridPoint = Tuple[int, int]
ScalarRange = Tuple[float, float]


def _nextafter(value: float, toward: float) -> float:
    """Return the next representable double from ``value`` toward ``toward``.

    ``math.nextafter`` only exists from Python 3.9 onwards; the struct
    fallback keeps the package compatible with its declared 3.8 floor.
    """
    nextafter = getattr(math, "nextafter", None)
    if nextafter is not None:
        return nextafter(value, toward)
    if value == toward:
        return toward
    if value == 0.0:
        bits = 1 if toward > 0 else (1 << 63) | 1
        return struct.unpack("<d", struct.pack("<Q", bits))[0]
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    increase = toward > value
    if value < 0:
        increase = not increase
    bits += 1 if increase else -1
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def decode_grid(expression: str, precision: int = 5) -> List[GridPoint]:
    """Decode a polyline into exact integer grid coordinates.

    Each returned pair ``(lat_n, lng_n)`` is the integer such that an
    original coordinate ``x`` produced this grid value exactly when
    ``_py2_round(x * 10**precision) == n``. No floating-point division is
    performed, so the integers are exact.
    """
    factor = 10 ** precision
    index, length = 0, len(expression)
    lat, lng = 0, 0
    points: List[GridPoint] = []
    while index < length:
        lat_change, index = _trans(expression, index)
        lng_change, index = _trans(expression, index)
        lat += lat_change
        lng += lng_change
        points.append((lat, lng))
    return points


def _first_double(low: float, high: float, predicate: Callable[[float], bool]) -> float:
    """Smallest float in [low, high] satisfying a monotone predicate."""
    for _ in range(200):
        middle = (low + high) / 2.0
        if middle == low or middle == high:
            break
        if predicate(middle):
            high = middle
        else:
            low = middle
    if not predicate(high):
        raise ValueError("predicate never becomes true in the search interval")
    return high


@dataclass(frozen=True)
class CellRange:
    """Set of floating-point coordinates that encode to one grid cell.

    ``lower`` and ``upper`` are the smallest/largest doubles for which
    ``_py2_round(x * factor) == n``; both are included. ``ideal_lower`` /
    ``ideal_upper`` are the nominal half-cell boundaries
    ``(n +/- 0.5) / factor``; the ``ideal_*_included`` flags record whether
    the library predicate actually accepts those doubles, which documents
    the floating-point boundary flips.
    """

    n: int
    factor: int
    lower: float
    upper: float
    ideal_lower: float
    ideal_upper: float
    ideal_lower_included: bool
    ideal_upper_included: bool

    def contains(self, value: float) -> bool:
        return _py2_round(value * self.factor) == self.n


_CELL_CACHE: dict[Tuple[int, int], CellRange] = {}


def cell_range(n: int, precision: int) -> CellRange:
    """Float-exact preimage of grid cell ``n`` under the library encoder."""
    cache_key = (n, precision)
    cached = _CELL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    factor = 10 ** precision
    step = 1.0 / factor

    upper_cut = _first_double(
        n * step, (n + 1) * step, lambda x: _py2_round(x * factor) > n
    )
    lower_cut = _first_double(
        (n - 1) * step, n * step, lambda x: _py2_round(x * factor) >= n
    )

    result = CellRange(
        n=n,
        factor=factor,
        lower=lower_cut,
        upper=_nextafter(upper_cut, -math.inf),
        ideal_lower=(n - 0.5) * step,
        ideal_upper=(n + 0.5) * step,
        ideal_lower_included=_py2_round((n - 0.5) * step * factor) == n,
        ideal_upper_included=_py2_round((n + 0.5) * step * factor) == n,
    )
    _CELL_CACHE[cache_key] = result
    return result


def intersect_cells(left: CellRange, right: CellRange) -> Optional[ScalarRange]:
    """Intersect two cell preimages; ``None`` when they cannot share a value."""
    low = max(left.lower, right.lower)
    high = min(left.upper, right.upper)
    if low > high:
        return None
    if not (left.contains(low) and right.contains(low)):
        raise AssertionError("intersection lower endpoint rejected by encoder predicate")
    if not (left.contains(high) and right.contains(high)):
        raise AssertionError("intersection upper endpoint rejected by encoder predicate")
    middle = (low + high) / 2.0
    if not (left.contains(middle) and right.contains(middle)):
        raise AssertionError("intersection interior rejected by encoder predicate")
    return low, high


@dataclass(frozen=True)
class PointResult:
    """Feasibility of one paired point from the two polylines."""

    index: int
    index_in_b: int
    lat_cell_a: int
    lng_cell_a: int
    lat_cell_b: int
    lng_cell_b: int
    lat_range: Optional[ScalarRange]
    lng_range: Optional[ScalarRange]

    @property
    def feasible(self) -> bool:
        return self.lat_range is not None and self.lng_range is not None


@dataclass(frozen=True)
class Comparison:
    """Result of pairing two polylines under one orientation."""

    reverse: bool
    length_a: int
    length_b: int
    points: List[PointResult]

    @property
    def first_infeasible(self) -> Optional[PointResult]:
        for point in self.points:
            if not point.feasible:
                return point
        return None

    @property
    def feasible(self) -> bool:
        return (
            self.length_a == self.length_b
            and self.first_infeasible is None
        )


def _pair(
    grid_a: List[GridPoint],
    grid_b: List[GridPoint],
    precision_a: int,
    precision_b: int,
    reverse: bool,
) -> Comparison:
    ordered_b = list(reversed(grid_b)) if reverse else grid_b
    paired = min(len(grid_a), len(ordered_b))
    results: List[PointResult] = []
    for i in range(paired):
        lat_a, lng_a = grid_a[i]
        lat_b, lng_b = ordered_b[i]
        lat_range = intersect_cells(
            cell_range(lat_a, precision_a), cell_range(lat_b, precision_b)
        )
        lng_range = intersect_cells(
            cell_range(lng_a, precision_a), cell_range(lng_b, precision_b)
        )
        index_in_b = len(ordered_b) - 1 - i if reverse else i
        results.append(
            PointResult(
                index=i,
                index_in_b=index_in_b,
                lat_cell_a=lat_a,
                lng_cell_a=lng_a,
                lat_cell_b=lat_b,
                lng_cell_b=lng_b,
                lat_range=lat_range,
                lng_range=lng_range,
            )
        )
    return Comparison(
        reverse=reverse,
        length_a=len(grid_a),
        length_b=len(grid_b),
        points=results,
    )


def compare(
    polyline_a: str,
    polyline_b: str,
    precision_a: int = 5,
    precision_b: Optional[int] = None,
) -> Tuple[Comparison, Comparison]:
    """Compare two encodings under both the forward and reversed readings.

    Returns ``(forward, reversed)`` comparisons. The reversed reading
    pairs point ``i`` of A with point ``len(B)-1-i`` of B, covering the
    case where one vendor sampled the same track in reverse order.
    """
    if precision_b is None:
        precision_b = precision_a
    grid_a = decode_grid(polyline_a, precision_a)
    grid_b = decode_grid(polyline_b, precision_b)
    return (
        _pair(grid_a, grid_b, precision_a, precision_b, reverse=False),
        _pair(grid_a, grid_b, precision_a, precision_b, reverse=True),
    )


def _format_interval(interval: Optional[ScalarRange], decimals: int) -> str:
    if interval is None:
        return "EMPTY"
    low, high = interval
    return f"[{low:.{decimals}f}, {high:.{decimals}f}]"


def render_comparison(comparison: Comparison, decimals: int = 8) -> str:
    """Human-readable table of the per-point common allowed ranges."""
    header = (
        f"orientation={'reversed' if comparison.reverse else 'forward'} "
        f"len_a={comparison.length_a} len_b={comparison.length_b}"
    )
    lines = [header, "=" * len(header)]
    lines.append(
        f"{'i':>4} {'i_b':>4} {'lat_cell(A,B)':>20} "
        f"{'common lat range':>26} {'lng_cell(A,B)':>20} {'common lng range':>26}"
    )
    for point in comparison.points:
        lines.append(
            f"{point.index:>4} {point.index_in_b:>4} "
            f"{str((point.lat_cell_a, point.lat_cell_b)):>20} "
            f"{_format_interval(point.lat_range, decimals):>26} "
            f"{str((point.lng_cell_a, point.lng_cell_b)):>20} "
            f"{_format_interval(point.lng_range, decimals):>26}"
        )
    first = comparison.first_infeasible
    if first is not None:
        lines.append(
            f"first impossible point: i={first.index} "
            f"(b index={first.index_in_b}); "
            f"lat={_format_interval(first.lat_range, decimals)} "
            f"lng={_format_interval(first.lng_range, decimals)}"
        )
    elif comparison.length_a != comparison.length_b:
        lines.append(
            "all paired points overlap, but the polylines do not have the "
            "same number of points, so no single point sequence fits both."
        )
    else:
        lines.append("every point admits a common original coordinate.")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether two encoded polylines can come from the same "
            "sequence of original coordinates and print the per-point "
            "common allowed ranges."
        )
    )
    parser.add_argument("polyline_a")
    parser.add_argument("polyline_b")
    parser.add_argument(
        "-p", "--precision-a", type=int, default=5,
        help="precision (decimal places) of polyline A, default 5",
    )
    parser.add_argument(
        "-q", "--precision-b", type=int, default=None,
        help="precision of polyline B, defaults to --precision-a",
    )
    parser.add_argument(
        "-d", "--decimals", type=int, default=8,
        help="decimals used when printing ranges, default 8",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    forward, reversed_reading = compare(
        args.polyline_a,
        args.polyline_b,
        args.precision_a,
        args.precision_b,
    )
    print(render_comparison(forward, args.decimals))
    print()
    print(render_comparison(reversed_reading, args.decimals))
    print()
    if forward.feasible and reversed_reading.feasible:
        print("verdict: BOTH orientations are feasible.")
    elif forward.feasible:
        print("verdict: only the FORWARD orientation is feasible.")
    elif reversed_reading.feasible:
        print("verdict: only the REVERSED orientation is feasible.")
    else:
        print("verdict: NEITHER orientation admits a common coordinate sequence.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
