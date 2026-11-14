"""Build mutually exclusive rainfall bands from cumulative surfaces.

Rainfall products are commonly delivered as *cumulative* surfaces: a
geometry tagged "at least 10 mm" is the area where accumulated rainfall
reached 10 mm, "at least 25 mm" the area where it reached 25 mm, and so
on.  Data warehouses usually need the complementary, mutually exclusive
bands instead (10--25 mm, 25--50 mm, ...).

The cumulative surfaces are expected to nest: the area for a higher
threshold must be a subset of the area for every lower threshold.  Real
products violate this locally, typically because a watershed was clipped
to a slightly different outline or because a hole was handled
inconsistently.  :func:`build_bands` does not discard such a product.
It derives the bands from set differences, and reports the part of a
higher surface that falls outside the lower surfaces as an exact
:class:`NestingViolation` geometry.

A configured threshold may also have no surface at all.  This is *not*
the same as an empty surface (an empty surface is an observation that no
area reached the threshold).  When a layer is missing, the bands on both
sides of it cannot be separated.  They are therefore delivered as one
joint interval (for example ``[10, 50)`` when the 25 mm layer is
missing), never as two bands with the missing one filled with zero
rainfall.

All coordinates are treated as planar coordinates in the caller's
projected units.  Areas are returned in squared coordinate units; the
module performs no projection and makes no assumption about longitude or
latitude.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real

import geojson._planar as _planar


__all__ = [
    "NestingViolation",
    "RainfallBand",
    "RainfallBands",
    "UnavailableInterval",
    "build_bands",
    "build_rainfall_bands",
    "derive_bands",
]


@dataclass(frozen=True)
class RainfallBand:
    """One mutually exclusive rainfall band.

    ``lower`` is inclusive and ``upper`` exclusive.  ``upper`` is ``None``
    for the open-ended band above the highest present threshold.  ``joint``
    is true when the band combines several nominal intervals because one
    or more intervening cumulative surfaces were missing.
    """

    lower: float
    upper: float | None
    area: float
    geometry: dict
    bbox: list | None
    joint: bool = False
    missing_thresholds: tuple = ()

    @property
    def label(self):
        return _interval_label(self.lower, self.upper, self.joint)

    def to_dict(self):
        return {
            "kind": "band",
            "lower": self.lower,
            "upper": self.upper,
            "label": self.label,
            "joint": self.joint,
            "missing_thresholds": list(self.missing_thresholds),
            "area": self.area,
            "bbox": self.bbox,
            "geometry": self.geometry,
        }


@dataclass(frozen=True)
class NestingViolation:
    """The part of a higher surface that falls outside lower surfaces.

    ``envelope`` is the intersection of every lower cumulative surface,
    i.e. the region where the higher surface still nests in all of them.
    ``geometry`` is ``surface(upper) - envelope``: exactly the points of
    the higher surface that violate nesting against at least one lower
    threshold.  The geometry may contain several disconnected pieces, and
    those pieces are kept individually rather than invalidating the whole
    product.

    ``lower`` is the immediately lower threshold, ``escaped_thresholds``
    lists every lower threshold whose surface is escaped, and
    ``overlap_area`` is the part of the higher surface that is still
    inside the envelope.
    """

    lower: float
    upper: float
    area: float
    geometry: dict
    bbox: list | None
    upper_area: float
    overlap_area: float
    escaped_thresholds: tuple = ()
    envelope_area: float = 0.0

    @property
    def label(self):
        if self.escaped_thresholds:
            passed = ", ".join(_number(value) for value in self.escaped_thresholds)
            return f">= {_number(self.upper)} outside >= {passed}"
        return f">= {_number(self.upper)} outside >= {_number(self.lower)}"

    def to_dict(self):
        return {
            "kind": "nesting_violation",
            "lower": self.lower,
            "upper": self.upper,
            "escaped_thresholds": list(self.escaped_thresholds),
            "label": self.label,
            "area": self.area,
            "bbox": self.bbox,
            "upper_area": self.upper_area,
            "envelope_area": self.envelope_area,
            "overlap_area": self.overlap_area,
            "geometry": self.geometry,
        }


@dataclass(frozen=True)
class UnavailableInterval:
    """A nominal interval that cannot be derived from the received data.

    This occurs when the lowest configured threshold is missing: there is
    no received cumulative surface to combine it with, so no joint band
    can be built either.
    """

    lower: float
    upper: float | None
    reason: str
    missing_thresholds: tuple = ()

    @property
    def label(self):
        return _interval_label(self.lower, self.upper, False)

    def to_dict(self):
        return {
            "kind": "unavailable",
            "lower": self.lower,
            "upper": self.upper,
            "label": self.label,
            "reason": self.reason,
            "missing_thresholds": list(self.missing_thresholds),
        }


@dataclass(frozen=True)
class RainfallBands:
    """Result of :func:`build_bands`."""

    thresholds: tuple
    core: dict = field(default_factory=dict, repr=False)
    bands: tuple = ()
    violations: tuple = ()
    unavailable: tuple = ()
    missing_thresholds: tuple = ()
    precision: int = 10

    @property
    def has_conflicts(self):
        return bool(self.violations)

    @property
    def is_complete(self):
        return not self.missing_thresholds

    @property
    def total_area(self):
        """Total area covered by the delivered (disjoint) bands."""
        return sum(band.area for band in self.bands)

    @property
    def conflict_area(self):
        """Area of the de-duplicated union of all nesting violations."""
        return _planar.area(self.conflict_geometry_data)

    @property
    def conflict_geometry_data(self):
        geometry = []
        for violation in self.violations:
            geometry = _planar.union(
                geometry, _planar.normalize(violation.geometry)
            )
        return geometry

    @property
    def conflict_geometry(self):
        """Exact GeoJSON union of every piece where nesting is violated."""
        return _planar.to_geojson(
            self.conflict_geometry_data, precision=self.precision
        )

    def to_dict(self):
        return {
            "thresholds": list(self.thresholds),
            "missing_thresholds": list(self.missing_thresholds),
            "is_complete": self.is_complete,
            "has_conflicts": self.has_conflicts,
            "total_area": self.total_area,
            "conflict_area": self.conflict_area,
            "conflict_geometry": self.conflict_geometry,
            "bands": [band.to_dict() for band in self.bands],
            "violations": [violation.to_dict() for violation in self.violations],
            "unavailable": [item.to_dict() for item in self.unavailable],
        }

    def to_feature_collection(self, include_violations=False):
        """Return a GeoJSON FeatureCollection of bands.

        Properties carry the band metadata; geometry stays a plain mapping
        so that the coordinates produced by the overlay are not rounded
        again by the GeoJSON constructors.
        """
        features = []
        for band in self.bands:
            features.append({
                "type": "Feature",
                "geometry": band.geometry,
                "properties": {
                    "kind": "band",
                    "lower": band.lower,
                    "upper": band.upper,
                    "label": band.label,
                    "joint": band.joint,
                    "missing_thresholds": list(band.missing_thresholds),
                    "area": band.area,
                    "bbox": band.bbox,
                },
            })
        if include_violations and self.violations:
            features.append({
                "type": "Feature",
                "geometry": self.conflict_geometry,
                "properties": {
                    "kind": "conflict_union",
                    "label": "nesting conflicts",
                    "area": self.conflict_area,
                    "pairs": [
                        [violation.lower, violation.upper]
                        for violation in self.violations
                    ],
                },
            })
        return {"type": "FeatureCollection", "features": features}


def _number(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _interval_label(lower, upper, joint):
    if upper is None:
        text = f"[{_number(lower)}, inf)"
    else:
        text = f"[{_number(lower)}, {_number(upper)})"
    return text + " joint" if joint else text


def _threshold(value):
    if isinstance(value, str):
        value = value.strip()
        for prefix in (">=", "≥"):
            if value.startswith(prefix):
                value = value[len(prefix):].strip()
        if value.endswith("+"):
            value = value[:-1].strip()
        if not value:
            raise ValueError("empty threshold")
        try:
            value = float(value)
        except ValueError as error:
            raise ValueError(f"threshold must be a number, got {value!r}") from error
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"threshold must be a real number, got {value!r}")
    value = float(value)
    if not isfinite(value):
        raise ValueError(f"threshold must be finite, got {value!r}")
    return value


_THRESHOLD_KEYS = ("threshold", "min_mm", "threshold_mm", "value", "rainfall")


def _feature_threshold(properties):
    for key in _THRESHOLD_KEYS:
        if key in properties:
            value = properties[key]
            return _threshold(value)
    raise ValueError(
        "feature properties need one of "
        f"{', '.join(_THRESHOLD_KEYS)}"
    )


def _feature_collection(value):
    if isinstance(value, Mapping):
        if value.get("type") == "FeatureCollection":
            return value.get("features") or []
        if "features" in value:
            return value["features"]
        return None
    features = getattr(value, "features", None)
    if features is not None and getattr(value, "type", None) == "FeatureCollection":
        return features
    return None


def _parse_sequence(surfaces):
    parsed = {}
    for item in surfaces:
        if isinstance(item, Mapping) and item.get("type") == "Feature":
            properties = item.get("properties") or {}
            threshold = _feature_threshold(properties)
            geometry = item.get("geometry")
        elif isinstance(item, Mapping):
            if "threshold" not in item:
                raise ValueError("each surface mapping needs a 'threshold' key")
            threshold = _threshold(item["threshold"])
            if "geometry" in item:
                geometry = item["geometry"]
            elif "surface" in item:
                geometry = item["surface"]
            else:
                geometry = None
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            if len(item) != 2:
                raise ValueError("each surface sequence must be (threshold, geometry)")
            threshold = _threshold(item[0])
            geometry = item[1]
        else:
            raise TypeError(f"unsupported surface entry: {item!r}")
        if threshold in parsed:
            raise ValueError(f"duplicate threshold {_number(threshold)}")
        parsed[threshold] = geometry
    return parsed


def _collect_surfaces(surfaces, thresholds):
    features = _feature_collection(surfaces)
    if features is not None:
        surfaces = features
    if isinstance(surfaces, Mapping):
        provided = {_threshold(key): value for key, value in surfaces.items()}
    else:
        if isinstance(surfaces, (str, bytes)) or not isinstance(surfaces, Sequence):
            raise TypeError("surfaces must be a mapping, sequence or FeatureCollection")
        provided = _parse_sequence(surfaces)

    if thresholds is None:
        ordered = sorted(provided)
    else:
        if isinstance(thresholds, (str, bytes)) or not isinstance(thresholds, Sequence):
            raise TypeError("thresholds must be a sequence")
        ordered = [_threshold(value) for value in thresholds]
        unknown = set(provided) - set(ordered)
        if unknown:
            raise ValueError(f"surfaces for unconfigured thresholds: {sorted(unknown)}")

    if not ordered:
        raise ValueError("at least one threshold must be configured")
    if len(set(ordered)) != len(ordered):
        raise ValueError("thresholds must be unique")
    ordered = sorted(ordered)

    resolved = {}
    for threshold in ordered:
        if threshold in provided:
            value = provided[threshold]
            resolved[threshold] = None if value is None else value
        else:
            resolved[threshold] = None
    return ordered, resolved


def build_bands(surfaces, thresholds=None, *, precision=10):
    """Build mutually exclusive rainfall bands from cumulative surfaces.

    Parameters
    ----------
    surfaces:
        One of:

        * a mapping ``threshold -> geometry``;
        * a sequence of ``(threshold, geometry)`` pairs;
        * a sequence of mappings with ``threshold`` and ``geometry`` (or
          ``surface``) keys;
        * a GeoJSON FeatureCollection whose features carry the threshold in
          ``properties`` under ``threshold`` (also accepted: ``min_mm``,
          ``threshold_mm``, ``value``, ``rainfall``; strings such as
          ``">= 25"`` or ``"25+"`` are understood).

        A geometry may be any GeoJSON Polygon/MultiPolygon, a
        :mod:`geojson` object, or a list of coordinates accepted by
        :func:`geojson._planar.normalize`.  ``None`` means the layer is
        missing.  An empty geometry means the layer was received and
        contains no area.
    thresholds:
        Optional complete list of configured thresholds.  With a mapping,
        a configured threshold without a key is treated as a missing
        layer.  With a sequence, every entry must be listed.  If omitted,
        the thresholds present in ``surfaces`` are used.
    precision:
        Decimal places used when serialising the result geometries.

    Returns
    -------
    RainfallBands
        The bands partition the lowest present cumulative surface:
        ``core(a) = surface(a) & surface(lower thresholds)`` and band
        ``[a, b)`` is ``core(a) - core(b)``.  Bands are therefore
        mutually exclusive and their union is exactly the lowest present
        surface, even when the inputs do not nest.

        ``violations`` contains one entry per higher surface that escapes
        any lower surface.  Its geometry is
        ``surface(upper) - intersection(all lower surfaces)``, i.e. the
        exact part of the higher surface that violates nesting against at
        least one lower threshold, and ``escaped_thresholds`` lists the
        thresholds it escapes.  Every point at which any higher surface
        escapes any lower surface belongs to exactly one of these
        geometries, so the conflicts are fully outlined without being
        double-counted and without rejecting the product.  The union is
        available as :attr:`RainfallBands.conflict_geometry`.

        ``unavailable`` lists leading nominal intervals that cannot be
        derived because their lower cumulative surface is missing.
        Missing interior thresholds merge the bands on both sides into a
        joint interval; they are never filled with zero rainfall.
    """
    ordered, resolved = _collect_surfaces(surfaces, thresholds)

    present = [threshold for threshold in ordered if resolved[threshold] is not None]
    missing = [threshold for threshold in ordered if resolved[threshold] is None]
    if not present:
        return RainfallBands(
            thresholds=tuple(ordered),
            unavailable=(
                UnavailableInterval(
                    lower=ordered[0],
                    upper=None,
                    reason="no cumulative surface was received",
                    missing_thresholds=tuple(missing),
                ),
            ),
            missing_thresholds=tuple(missing),
        )

    normalized = {
        threshold: _planar.normalize(resolved[threshold]) for threshold in present
    }

    core = {}
    previous = None
    for threshold in present:
        geometry = normalized[threshold]
        if previous is None:
            core[threshold] = geometry
        else:
            core[threshold] = _planar.intersection(geometry, previous)
        previous = core[threshold]

    bands = []
    violations = []
    unavailable = []

    first_present = present[0]
    first_index = ordered.index(first_present)
    leading_missing = ordered[:first_index]
    if leading_missing:
        unavailable.append(
            UnavailableInterval(
                lower=ordered[0],
                upper=first_present,
                reason=(
                    "the lower cumulative surface is missing; the interval "
                    "cannot be separated or joined with a lower band"
                ),
                missing_thresholds=tuple(leading_missing),
            )
        )

    for lower, upper in zip(present, present[1:]):
        geometry = _planar.difference(core[lower], core[upper])
        missing_between = tuple(
            threshold
            for threshold in ordered
            if lower < threshold < upper and resolved[threshold] is None
        )
        bands.append(
            RainfallBand(
                lower=lower,
                upper=upper,
                area=_planar.area(geometry),
                geometry=_planar.to_geojson(geometry, precision=precision),
                bbox=_rounded_bbox(_planar.bbox(geometry), precision),
                joint=bool(missing_between),
                missing_thresholds=missing_between,
            )
        )

    for index, upper in enumerate(present):
        if index == 0:
            continue
        lower_geometries = [normalized[value] for value in present[:index]]
        envelope = lower_geometries[0]
        for geometry in lower_geometries[1:]:
            envelope = _planar.intersection(envelope, geometry)
        upper_geometry = normalized[upper]
        violation_geometry = _planar.difference(upper_geometry, envelope)
        violation_area = _planar.area(violation_geometry)
        if violation_area <= 0.0 or _planar.is_empty(violation_geometry):
            continue
        escaped = tuple(
            value
            for value in present[:index]
            if _planar.area(
                _planar.difference(upper_geometry, normalized[value])
            )
            > 0.0
        )
        violations.append(
            NestingViolation(
                lower=present[index - 1],
                upper=upper,
                area=violation_area,
                geometry=_planar.to_geojson(
                    violation_geometry, precision=precision
                ),
                bbox=_rounded_bbox(_planar.bbox(violation_geometry), precision),
                upper_area=_planar.area(upper_geometry),
                envelope_area=_planar.area(envelope),
                overlap_area=_planar.area(
                    _planar.intersection(upper_geometry, envelope)
                ),
                escaped_thresholds=escaped,
            )
        )

    highest = present[-1]
    highest_index = ordered.index(highest)
    trailing_missing = tuple(ordered[highest_index + 1:])
    bands.append(
        RainfallBand(
            lower=highest,
            upper=None,
            area=_planar.area(core[highest]),
            geometry=_planar.to_geojson(core[highest], precision=precision),
            bbox=_rounded_bbox(_planar.bbox(core[highest]), precision),
            joint=bool(trailing_missing),
            missing_thresholds=trailing_missing,
        )
    )

    return RainfallBands(
        thresholds=tuple(ordered),
        core={threshold: core[threshold] for threshold in present},
        bands=tuple(bands),
        violations=tuple(violations),
        unavailable=tuple(unavailable),
        missing_thresholds=tuple(missing),
        precision=precision,
    )


def _rounded_bbox(bounds, precision):
    if bounds is None:
        return None
    return [round(value, precision) for value in bounds]


# The operation is also commonly referred to as deriving rainfall bands.
build_rainfall_bands = build_bands
derive_bands = build_bands
