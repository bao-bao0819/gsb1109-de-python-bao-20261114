"""Apportion per-segment emission totals onto mileage bins.

The functions in this module work with odometer-style mileage readings that
run alongside a :class:`geojson.LineString`.  Every consecutive pair of
readings is one segment and carries one emission total.  A segment is split
across mileage bins in proportion to the distance actually travelled inside
each bin.  Mileage is allowed to decrease, so reversing and backtracking are
handled by taking the absolute length of a segment.

All arithmetic is done with :class:`fractions.Fraction`; floating point is
only used at the public boundary.  Integer delivery uses the largest
remainder method, and equal remainders are settled in ascending mileage order
with stops placed between their two neighbouring bins.  Rounding happens once
for the whole batch, so the delivered total is never off by a unit, even when
a segment backtracks through bins it has already visited.
"""

import math
from bisect import bisect_right
from decimal import Decimal
from fractions import Fraction
from numbers import Integral, Rational


_MILEAGE_KEYS = ("mileage", "mileages", "odometer", "odometer_readings")
_EMISSION_KEYS = ("emissions", "emission", "emission_totals")


def apportion(linestring, mileage=None, emissions=None, *,
              bin_width=None, boundaries=None, mileage_axis=None):
    """Apportion a LineString's per-segment emissions onto mileage bins.

    :param linestring: A ``LineString``, a ``Feature`` whose geometry is a
        ``LineString``, or a plain sequence of coordinate positions.
    :param mileage: Cumulative mileage readings, one per vertex.  When
        omitted, the value is read from a ``mileage``/``mileages``/
        ``odometer`` member on the geometry or feature, or from its
        ``properties`` mapping.  If ``mileage_axis`` is given, readings are
        taken from that ordinate of each position instead.
    :param emissions: Emission totals, one per segment (one fewer item than
        there are mileage readings).  When omitted, the value is read from an
        ``emissions``/``emission``/``emission_totals`` member in the same way.
    :param bin_width: Width of each mileage bin.  Bins are the half-open
        intervals ``[i * bin_width, (i + 1) * bin_width)`` numbered by
        ``i = floor(mileage / bin_width)``.  Defaults to ``1`` when
        ``boundaries`` is omitted.
    :param boundaries: Alternative explicit, strictly increasing bin edges.
        Bins are the half-open intervals between consecutive edges and are
        numbered from zero.  Samples must stay inside the closed range.
    :param mileage_axis: Index of the ordinate carrying the mileage marker,
        for example ``2`` for a ``(lon, lat, mileage)`` position.
    :return: A mapping with integer ``bins``, separately recorded ``stops``,
        the whole-unit ``total``, and the ``bins_total``/``stops_total``
        audit values.  ``bins_total + stops_total`` always equals ``total``.
    :rtype: dict
    :raises TypeError: if the geometry or a numeric value is of a bad type.
    :raises ValueError: if lengths, bins, or values are inconsistent.

    A zero-length segment whose mileage lies exactly on a bin edge is a stop.
    It has no distance to apportion, so it is reported in ``stops`` instead of
    being pushed into one side of the boundary.  A zero-length segment
    elsewhere belongs to the bin that contains it.
    """
    positions = _line_positions(linestring)

    if mileage is None and mileage_axis is not None:
        mileage = [_position_value(position, mileage_axis)
                   for position in positions]
    if mileage is None:
        mileage = _attached_value(linestring, _MILEAGE_KEYS)
    if emissions is None:
        emissions = _attached_value(linestring, _EMISSION_KEYS)

    if mileage is None:
        raise ValueError("mileage readings are required")
    if emissions is None:
        raise ValueError("segment emission totals are required")

    mileage = list(mileage)
    if len(mileage) != len(positions):
        raise ValueError(
            "expected one mileage reading per LineString vertex: "
            f"{len(positions)} vertices, {len(mileage)} readings")

    return apportion_mileage(
        mileage, emissions, bin_width=bin_width, boundaries=boundaries)


def apportion_mileage(mileage, emissions, *, bin_width=None, boundaries=None):
    """Apportion emissions using mileage readings alone.

    This is the geometry-free core of :func:`apportion`.  ``mileage`` contains
    one reading per vertex and ``emissions`` one total per segment between
    consecutive readings.  See :func:`apportion` for the bin and rounding
    rules.
    """
    mileage = [_as_fraction(value, "mileage") for value in mileage]
    emissions = [_as_fraction(value, "emission") for value in emissions]

    if not mileage:
        raise ValueError("at least one mileage reading is required")
    if len(emissions) != len(mileage) - 1:
        raise ValueError(
            "expected one emission total per segment: "
            f"{len(mileage) - 1} segments, {len(emissions)} emission totals")
    for value in emissions:
        if value < 0:
            raise ValueError("emission totals cannot be negative")

    bins = _BinSpec(bin_width=bin_width, boundaries=boundaries)

    # Exact claims are tagged so bins and stops can share one remainder pool
    # without their numbers colliding.
    exact = {}
    positions = {}
    stops = []

    for index, emission in enumerate(emissions):
        start, end = mileage[index], mileage[index + 1]
        bins.check_mileage(start)
        bins.check_mileage(end)

        if start == end:
            if bins.is_boundary(start):
                key = ("stop", len(stops))
                exact[key] = emission
                positions[key] = bins.stop_position(start)
                stops.append({
                    "mileage": _display_number(start),
                    "units": 0,
                    "left_bin": bins.left_bin(start),
                    "right_bin": bins.right_bin(start),
                    "segment": index,
                })
            elif emission:
                key = ("bin", bins.point_bin(start))
                exact[key] = exact.get(key, Fraction(0)) + emission
                positions[key] = Fraction(key[1])
            continue

        low, high = sorted((start, end))
        distance = high - low
        for bin_number, overlap in bins.overlaps(low, high):
            key = ("bin", bin_number)
            exact[key] = exact.get(key, Fraction(0)) + (
                emission * overlap / distance)
            positions[key] = Fraction(bin_number)

    delivered, total = _largest_remainder(exact, positions)

    result_bins = {
        key[1]: units
        for key, units in delivered.items()
        if key[0] == "bin" and units
    }
    result_bins = dict(sorted(result_bins.items()))

    for index, stop in enumerate(stops):
        stop["units"] = delivered[("stop", index)]

    bins_total = sum(result_bins.values())
    stops_total = sum(stop["units"] for stop in stops)

    return {
        "bins": result_bins,
        "stops": stops,
        "total": total,
        "bins_total": bins_total,
        "stops_total": stops_total,
    }


class _BinSpec:
    """Resolve mileage values to numbered, half-open bins."""

    def __init__(self, bin_width=None, boundaries=None):
        if boundaries is not None and bin_width is not None:
            raise ValueError("pass either bin_width or boundaries, not both")

        self.width = None
        self.boundaries = None

        if boundaries is not None:
            edges = [_as_fraction(value, "boundary") for value in boundaries]
            if len(edges) < 2:
                raise ValueError("at least two bin boundaries are required")
            if any(left >= right for left, right in zip(edges, edges[1:])):
                raise ValueError("bin boundaries must be strictly increasing")
            self.boundaries = edges
            return

        if bin_width is None:
            bin_width = 1
        width = _as_fraction(bin_width, "bin_width")
        if width <= 0:
            raise ValueError("bin_width must be positive")
        self.width = width

    def check_mileage(self, value):
        if self.boundaries is not None:
            if value < self.boundaries[0] or value > self.boundaries[-1]:
                raise ValueError(
                    f"mileage {_display_number(value)!r} is outside the "
                    "declared bin boundaries")

    def point_bin(self, value):
        """Return the bin number containing a point."""
        if self.boundaries is None:
            return math.floor(value / self.width)
        index = bisect_right(self.boundaries, value) - 1
        if index >= len(self.boundaries) - 1:
            raise ValueError("mileage lies beyond the final bin boundary")
        return index

    def is_boundary(self, value):
        if self.boundaries is None:
            return value % self.width == 0
        return value in self.boundaries

    def left_bin(self, value):
        """Number of the bin immediately below a boundary, if any."""
        if self.boundaries is None:
            return math.floor(value / self.width) - 1
        index = self.boundaries.index(value)
        return index - 1 if index > 0 else None

    def right_bin(self, value):
        """Number of the bin immediately above a boundary, if any."""
        if self.boundaries is None:
            return math.floor(value / self.width)
        index = self.boundaries.index(value)
        return index if index < len(self.boundaries) - 1 else None

    def stop_position(self, value):
        """Ordering position for a stop, between its two neighbouring bins."""
        if self.boundaries is None:
            return Fraction(math.floor(value / self.width)) - Fraction(1, 2)
        return Fraction(self.boundaries.index(value)) - Fraction(1, 2)

    def overlaps(self, low, high):
        """Yield ``(bin_number, overlap)`` for the non-empty part of a span."""
        if self.boundaries is None:
            first = math.floor(low / self.width)
            last = math.floor(high / self.width)
            for number in range(first, last + 1):
                bin_low = number * self.width
                bin_high = bin_low + self.width
                overlap_low = max(low, bin_low)
                overlap_high = min(high, bin_high)
                if overlap_high > overlap_low:
                    yield number, overlap_high - overlap_low
            return

        first = bisect_right(self.boundaries, low) - 1
        for number in range(first, len(self.boundaries) - 1):
            bin_low = self.boundaries[number]
            bin_high = self.boundaries[number + 1]
            if bin_low >= high:
                break
            overlap_low = max(low, bin_low)
            overlap_high = min(high, bin_high)
            if overlap_high > overlap_low:
                yield number, overlap_high - overlap_low


def _largest_remainder(exact, positions):
    """Turn exact fractional claims into whole units.

    The batch total must itself be a whole number of units; that is the only
    way the delivered result can match the original batch exactly.  Claims are
    ordered by descending remainder, then by mileage position: bins use their
    bin number and a stop sits half a bin after the bin on its left.  That
    keeps equal remainders in ascending mileage order and lets a stop settle
    between its two neighbouring bins.  Stops at the same place keep route
    order because the sort is stable.
    """
    total = sum(exact.values(), Fraction(0))
    if total.denominator != 1:
        raise ValueError(
            "emission totals must add up to a whole number of units: "
            f"{_display_number(total)!r}")
    whole_total = int(total)

    delivered = {
        key: value.numerator // value.denominator
        for key, value in exact.items()
    }
    remaining = whole_total - sum(delivered.values())

    def rank(key):
        value = exact[key]
        floor = delivered[key]
        return (-(value - floor), positions[key])

    order = sorted(exact, key=rank)
    if remaining < 0 or remaining > len(order):
        raise ValueError("emission totals are internally inconsistent")

    for key in order[:remaining]:
        delivered[key] += 1

    return delivered, whole_total


def _line_positions(obj):
    """Return the coordinate positions of a LineString-like object."""
    if isinstance(obj, (list, tuple)):
        return list(obj)

    if not hasattr(obj, "get"):
        raise TypeError("linestring must be a LineString, Feature, or "
                        "sequence of positions")

    object_type = obj.get("type")
    if object_type == "Feature":
        geometry = obj.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError("feature does not contain a LineString geometry")
        obj = geometry
        object_type = obj.get("type")

    if object_type != "LineString":
        raise ValueError("only LineString geometries are supported")

    coordinates = obj.get("coordinates")
    if not isinstance(coordinates, (list, tuple)):
        raise ValueError("LineString coordinates must be a sequence")
    return list(coordinates)


def _attached_value(obj, names):
    """Look for an attached member on the object or in its properties."""
    if not hasattr(obj, "get"):
        return None

    for name in names:
        if name in obj:
            return obj[name]

    properties = obj.get("properties")
    if isinstance(properties, dict):
        for name in names:
            if name in properties:
                return properties[name]

    if obj.get("type") == "Feature":
        geometry = obj.get("geometry")
        if isinstance(geometry, dict):
            for name in names:
                if name in geometry:
                    return geometry[name]
    return None


def _position_value(position, axis):
    """Return the mileage marker stored in a coordinate position."""
    try:
        return position[axis]
    except (IndexError, TypeError):
        raise ValueError(
            f"coordinate position {position!r} has no axis {axis}")


def _as_fraction(value, name):
    """Convert a real value to an exact Fraction without binary drift."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a real number, not bool")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Integral):
        return Fraction(value)
    if isinstance(value, Rational):
        return Fraction(value.numerator, value.denominator)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        return Fraction(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{name} must be finite")
        return Fraction(value)
    raise TypeError(f"{name} must be a real number, got "
                    f"{type(value).__name__}")


def _display_number(value):
    """Render an exact value as an int or float for public output."""
    if value.denominator == 1:
        return int(value)
    return float(value)
