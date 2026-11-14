"""
Grid migration analysis for legacy polyline-encoded point records.

Legacy pipeline:
  1. The original coordinate is encoded with Google's polyline algorithm at
     precision 6 ("first encoding"; the original string survives for some
     records only).
  2. At archival time the coordinate is encoded again at precision 5; this
     precision-5 string is the extant archive every record still has.

Both encodings quantize coordinates with Python-2 style round-half-away-from-
zero (see ``polyline._py2_round``)::

    q_p(x) = sign(x) * floor(|x| * 10**p + 0.5)

so a decoded archive value ``n / 10**p`` does not pin the point to a single
location: it only proves that the true coordinate lies inside the quantization
cell that maps to ``n``. The half-grid boundaries of those cells are
asymmetric for negative coordinates (the boundary nearer zero is included,
the one farther from zero is excluded), which is why every interval here
carries explicit inclusion flags and all arithmetic is exact (``Fraction``).

The target grid is modelled as an independent uniform axis-aligned grid per
axis: cell index ``k = floor((x - origin) / step)``, cell
``[origin + k*step, origin + (k+1)*step)``. A different origin and step per
axis is enough to express any shifted or re-scaled rectangular grid.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional, Sequence, Tuple

import polyline

CoordinateInt = Tuple[int, int]


def _round_half_away(value: Fraction) -> int:
    """Python-2 style rounding, matching ``polyline._py2_round`` exactly."""
    absolute = value if value >= 0 else -value
    rounded = (absolute.numerator * 2 + absolute.denominator) // (
        2 * absolute.denominator
    )
    return rounded if value >= 0 else -rounded


def quantize(value: Fraction, precision: int) -> int:
    """Quantize an exact coordinate the way the legacy encoder did."""
    return _round_half_away(value * (10 ** precision))


@dataclass(frozen=True)
class Interval:
    """A half-open/closed interval with explicit endpoint inclusion."""

    low: Fraction
    high: Fraction
    low_inclusive: bool
    high_inclusive: bool

    def contains(self, value: Fraction) -> bool:
        lower_ok = value >= self.low if self.low_inclusive else value > self.low
        upper_ok = value <= self.high if self.high_inclusive else value < self.high
        return lower_ok and upper_ok

    def intersection(self, other: "Interval") -> Optional["Interval"]:
        if self.low > other.low:
            low, low_inclusive = self.low, self.low_inclusive
        elif self.low < other.low:
            low, low_inclusive = other.low, other.low_inclusive
        else:
            low = self.low
            low_inclusive = self.low_inclusive and other.low_inclusive

        if self.high < other.high:
            high, high_inclusive = self.high, self.high_inclusive
        elif self.high > other.high:
            high, high_inclusive = other.high, other.high_inclusive
        else:
            high = self.high
            high_inclusive = self.high_inclusive and other.high_inclusive

        if low > high:
            return None
        if low == high and not (low_inclusive and high_inclusive):
            return None
        return Interval(low, high, low_inclusive, high_inclusive)


def quantization_cell(code: int, precision: int) -> Interval:
    """
    Return the exact set of real coordinates quantizing to ``code``.

    Round-half-away-from-zero makes the cells asymmetric around zero:

    * ``code > 0``: the lower endpoint is included, the upper excluded.
    * ``code < 0``: the upper (nearer-zero) endpoint is included, the lower
      excluded.
    * ``code == 0``: both half-unit endpoints round away from zero to
      ``+1`` / ``-1``, so both are excluded.
    """
    factor = 10 ** precision
    low = Fraction(2 * code - 1, 2 * factor)
    high = Fraction(2 * code + 1, 2 * factor)
    if code > 0:
        return Interval(low, high, True, False)
    if code < 0:
        return Interval(low, high, False, True)
    return Interval(low, high, False, False)


def _decoded_integer_points(expression: str, precision: int) -> List[CoordinateInt]:
    """Decode a polyline string into exact integer codes at its precision."""
    factor = 10 ** precision
    points: List[CoordinateInt] = []
    for lat, lng in polyline.decode(expression, precision):
        lat_code = Fraction(str(lat)) * factor
        lng_code = Fraction(str(lng)) * factor
        assert lat_code.denominator == 1 and lng_code.denominator == 1
        points.append((int(lat_code), int(lng_code)))
    return points


@dataclass(frozen=True)
class GridSpec:
    """Uniform target grid, independently specified per axis."""

    lat_origin: Fraction
    lat_step: Fraction
    lng_origin: Fraction
    lng_step: Fraction

    def _axis(self, axis: str) -> Tuple[Fraction, Fraction]:
        if axis == "lat":
            return self.lat_origin, self.lat_step
        return self.lng_origin, self.lng_step

    def cell_interval(self, index: int, axis: str) -> Interval:
        """Grid cell ``index`` on one axis as a lower-inclusive interval."""
        origin, step = self._axis(axis)
        return Interval(
            origin + index * step, origin + (index + 1) * step, True, False
        )

    def cells_overlapping(self, interval: Interval, axis: str) -> List[int]:
        """Indices of grid cells on one axis whose area meets ``interval``."""
        origin, step = self._axis(axis)
        first = ((interval.low - origin) / step).numerator // (
            (interval.low - origin) / step
        ).denominator - 1
        last = ((interval.high - origin) / step).numerator // (
            (interval.high - origin) / step
        ).denominator + 1

        overlapping: List[int] = []
        for index in range(first, last + 1):
            if interval.intersection(self.cell_interval(index, axis)) is not None:
                overlapping.append(index)
        return overlapping


@dataclass
class PointResult:
    point_id: str
    archive_string: str
    archive_codes: CoordinateInt
    original_string: Optional[str]
    original_codes: Optional[CoordinateInt]
    original_reproduces_archive: Optional[bool]
    original_consistent_with_archive: Optional[bool]
    conflict: bool
    lat_interval: Interval
    lng_interval: Interval
    candidate_cells: List[CoordinateInt] = field(default_factory=list)

    @property
    def unique(self) -> bool:
        return len(self.candidate_cells) == 1

    @property
    def determined_cell(self) -> Optional[CoordinateInt]:
        return self.candidate_cells[0] if self.unique else None


def _analyze_position(
    point_id: str,
    archive_string: str,
    archive_codes: CoordinateInt,
    grid: GridSpec,
    original_string: Optional[str],
    original_codes: Optional[CoordinateInt],
    original_reproduces_archive: Optional[bool],
) -> PointResult:
    archive_lat = quantization_cell(archive_codes[0], 5)
    archive_lng = quantization_cell(archive_codes[1], 5)

    consistent: Optional[bool] = None
    conflict = False
    lat_interval, lng_interval = archive_lat, archive_lng

    if original_codes is not None:
        original_lat = quantization_cell(original_codes[0], 6)
        original_lng = quantization_cell(original_codes[1], 6)
        lat_overlap = archive_lat.intersection(original_lat)
        lng_overlap = archive_lng.intersection(original_lng)
        consistent = lat_overlap is not None and lng_overlap is not None

        if consistent:
            assert lat_overlap is not None and lng_overlap is not None
            lat_interval = lat_overlap
            lng_interval = lng_overlap
        else:
            # Conflicting precision-6 evidence is not higher-precision truth:
            # the feasible region stays exactly what the archive proves.
            conflict = True

    lat_indices = grid.cells_overlapping(lat_interval, "lat")
    lng_indices = grid.cells_overlapping(lng_interval, "lng")
    candidates = [
        (lat_index, lng_index)
        for lat_index in lat_indices
        for lng_index in lng_indices
    ]

    return PointResult(
        point_id=point_id,
        archive_string=archive_string,
        archive_codes=archive_codes,
        original_string=original_string,
        original_codes=original_codes,
        original_reproduces_archive=original_reproduces_archive,
        original_consistent_with_archive=consistent,
        conflict=conflict,
        lat_interval=lat_interval,
        lng_interval=lng_interval,
        candidate_cells=candidates,
    )


def analyze_record(
    point_id: str,
    archive_string: str,
    grid: GridSpec,
    original_string: Optional[str] = None,
) -> List[PointResult]:
    """
    Analyze one legacy record, returning one result per string position.

    Evidence rules:

    * The precision-5 archive always constrains the true point to its
      quantization cells; these cells are the baseline feasible region.
    * If the precision-6 original survives, two checks are reported:
      whether its decoded point re-encodes to the archive string, and whether
      its quantization cells intersect the archive cells (a common cause can
      explain both strings). Only an intersection refines the feasible region.
      Disjoint cells are a conflict: the original is discarded and never
      treated as a higher-precision truth value.
    """
    archive_points = _decoded_integer_points(archive_string, 5)
    original_points: Optional[List[CoordinateInt]] = None
    reproduces: Optional[bool] = None
    if original_string is not None:
        original_points = _decoded_integer_points(original_string, 6)
        if len(original_points) != len(archive_points):
            raise ValueError(
                f"{point_id}: archive holds {len(archive_points)} positions but "
                f"the original holds {len(original_points)}"
            )
        # Whole-string check: re-encoding every decoded p6 position at p5
        # must reproduce the extant archive string exactly, including its
        # delta encoding. A mismatch means the original cannot be a source of
        # the archive even if individual positions happen to agree.
        factor6 = 10 ** 6
        reencoded_points = [
            (
                float(Fraction(lat6, factor6)),
                float(Fraction(lng6, factor6)),
            )
            for lat6, lng6 in original_points
        ]
        reproduces = polyline.encode(reencoded_points, 5) == archive_string

    results: List[PointResult] = []
    for position, archive_codes in enumerate(archive_points):
        original_codes = (
            original_points[position] if original_points is not None else None
        )
        position_id = (
            point_id if len(archive_points) == 1 else f"{point_id}#{position}"
        )
        results.append(
            _analyze_position(
                position_id,
                archive_string,
                archive_codes,
                grid,
                original_string,
                original_codes,
                reproduces,
            )
        )
    return results


def analyze_records(
    records: Sequence[Tuple[str, str, Optional[str]]], grid: GridSpec
) -> List[PointResult]:
    """Flatten :func:`analyze_record` over ``(id, archive, original)`` rows."""
    results: List[PointResult] = []
    for point_id, archive_string, original_string in records:
        results.extend(
            analyze_record(point_id, archive_string, grid, original_string)
        )
    return results


def _format_interval(interval: Interval) -> str:
    left = "[" if interval.low_inclusive else "("
    right = "]" if interval.high_inclusive else ")"
    return f"{left}{float(interval.low):.7f}, {float(interval.high):.7f}{right}"


def format_report(results: Sequence[PointResult]) -> str:
    """Render a per-point report with the evidence behind every conclusion."""
    lines: List[str] = []
    for result in results:
        lines.append(f"点 {result.point_id}")
        lines.append(
            f"  旧档(p5)整数码: lat={result.archive_codes[0]}, "
            f"lng={result.archive_codes[1]}"
        )
        lines.append(
            f"  可行纬度区间: {_format_interval(result.lat_interval)} "
            f"经度区间: {_format_interval(result.lng_interval)}"
        )
        if result.original_codes is not None:
            lines.append(
                f"  原串(p6)整数码: lat={result.original_codes[0]}, "
                f"lng={result.original_codes[1]}"
            )
            lines.append(
                "  原串重编码为p5是否等于现存旧档: "
                f"{result.original_reproduces_archive}"
            )
            if result.conflict:
                lines.append(
                    "  原串与旧档的量化格不相交，判定冲突；原串不作为高精度真值，"
                    "可行域仍以旧档(p5)为准"
                )
            else:
                lines.append("  原串与旧档相容，可行域已收窄为两者量化格的交集")
        if result.unique:
            lines.append(f"  新格号(唯一): {result.determined_cell}")
        else:
            lines.append(
                f"  新格号不能唯一确定，全部 {len(result.candidate_cells)} 个候选:"
            )
            for cell in result.candidate_cells:
                lines.append(f"    - {cell}")
    return "\n".join(lines)
