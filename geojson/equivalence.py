"""Geometric equivalence and canonicalization helpers.

The functions in this module compare geometries by their point sets rather
than by their JSON spelling.  Linear rings are normalized independently of
their starting point and winding direction, and polygon holes stay attached
to the exterior ring that contains them.  Consequently two polygons with the
same vertices but with a hole moved to a different exterior ring are *not*
considered equal.
"""

import copy
import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping
from fractions import Fraction
from numbers import Number


GEOMETRY_TYPES = (
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
)

DEFAULT_CODE_KEYS = (
    "code",
    "adcode",
    "division_code",
    "district_code",
    "区划码",
    "行政区划码",
)

__all__ = [
    "normalize_geometry",
    "canonical_geometry",
    "canonical_ring",
    "canonical_ring_key",
    "canonical_polygon",
    "geometry_key",
    "geometries_equal",
    "merge_by_geometry",
    "merge_geometries",
    "group_by_geometry",
    "deduplicate_geometries",
    "merge_to_features",
    "dedupe_features",
    "normalize",
    "canonicalize",
    "canonical_form",
    "normalize_ring",
    "normalize_polygon",
    "canonicalize_ring",
    "canonicalize_polygon",
    "canonical_key",
    "geometry_hash",
    "geometry_equal",
    "geometries_equivalent",
    "merge_features",
    "merge_feature_collections",
    "merge_by_equivalence",
    "consolidate_features",
    "group_features",
    "dedupe_geometries",
    "dedupe",
    "deduplicate_features",
]


def _as_mapping(obj):
    """Return a plain mapping for a GeoJSON object or mapping."""
    if isinstance(obj, Mapping):
        return obj
    geo_interface = getattr(obj, "__geo_interface__", None)
    if geo_interface is not None:
        return geo_interface
    raise TypeError(f"{obj!r} is not a GeoJSON object")


def _canonical_number(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value == 0:
            return 0
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, Number):
        try:
            integral = value.to_integral_value()
        except AttributeError:
            try:
                integral = int(value)
            except (OverflowError, TypeError, ValueError):
                return value
        if value == integral:
            return int(integral)
        return value
    return value


def _number_key(value):
    canonical = _canonical_number(value)
    if isinstance(canonical, bool):
        return "value:" + repr(canonical)
    if isinstance(canonical, int):
        return "int:" + str(canonical)
    if isinstance(canonical, Number):
        try:
            exact = Fraction(canonical)
        except (TypeError, ValueError, OverflowError):
            pass
        else:
            return f"rational:{exact.numerator}/{exact.denominator}"
    return "value:" + type(canonical).__name__ + ":" + repr(canonical)


def _position(position):
    """Return a canonical position as an immutable, hashable tuple."""
    if not isinstance(position, (list, tuple)):
        raise ValueError(f"{position!r} is not a coordinate position")
    return tuple(_canonical_number(value) for value in position)


def _position_key(position):
    return tuple(_number_key(value) for value in _position(position))


def _line_key(line):
    positions = tuple(_position_key(position) for position in line)
    return min(positions, tuple(reversed(positions)))


def _ring_key(ring):
    positions = tuple(_position_key(position) for position in ring[:-1])
    return min(positions, tuple(reversed(positions)))


def _least_rotation(sequence):
    """Return the index of the lexicographically least rotation.

    Uses Booth's linear-time algorithm so normalization stays practical for
    large administrative boundaries.
    """
    length = len(sequence)
    doubled = sequence + sequence
    failure = [-1] * (2 * length)
    start = 0
    for index in range(1, 2 * length):
        offset = failure[index - start - 1]
        while offset != -1 and doubled[index] != doubled[start + offset + 1]:
            if doubled[index] < doubled[start + offset + 1]:
                start = index - offset - 1
            offset = failure[offset]
        if doubled[index] != doubled[start + offset + 1]:
            if doubled[index] < doubled[start]:
                start = index
            failure[index - start] = -1
        else:
            failure[index - start] = offset + 1
    return start


def _strip_ring(ring):
    """Remove repeated closing points and consecutive duplicate vertices."""
    if not isinstance(ring, (list, tuple)):
        raise ValueError(f"{ring!r} is not a linear ring")
    positions = [_position(position) for position in ring]
    while len(positions) > 1 and positions[-1] == positions[0]:
        positions.pop()

    deduplicated = []
    for position in positions:
        if not deduplicated or position != deduplicated[-1]:
            deduplicated.append(position)
    positions = deduplicated

    while len(positions) > 1 and positions[-1] == positions[0]:
        positions.pop()

    if len(positions) < 3:
        raise ValueError(
            "a linear ring must contain at least 3 distinct positions"
        )
    return positions


def canonical_ring(ring):
    """Return a canonical form of a linear ring.

    The result has no repeated closing point, starts at a deterministic
    position, and has a deterministic direction.  Two rings containing the
    same closed sequence are therefore represented identically regardless of
    where they start or which way they are wound.

    :param ring: A sequence of coordinate positions.
    :return: A list of coordinate lists, closed exactly once.
    :rtype: list
    """
    positions = tuple(_strip_ring(ring))
    keys = tuple(_position_key(position) for position in positions)
    reversed_positions = tuple(reversed(positions))
    reversed_keys = tuple(reversed(keys))

    rotation = _least_rotation(keys)
    reverse_rotation = _least_rotation(reversed_keys)
    forward_key = keys[rotation:] + keys[:rotation]
    backward_key = reversed_keys[reverse_rotation:] + reversed_keys[:reverse_rotation]

    if backward_key < forward_key:
        result = (
            reversed_positions[reverse_rotation:]
            + reversed_positions[:reverse_rotation]
        )
    else:
        result = positions[rotation:] + positions[:rotation]
    return [list(position) for position in result] + [list(result[0])]


def canonical_ring_key(ring):
    """Return a hashable key for a linear ring."""
    return _ring_key(canonical_ring(ring))


def _canonical_polygon_rings(polygon):
    if not isinstance(polygon, (list, tuple)) or not polygon:
        raise ValueError("a polygon must contain at least one linear ring")
    exterior = canonical_ring(polygon[0])
    holes = [canonical_ring(hole) for hole in polygon[1:]]
    holes.sort(key=_ring_key)
    return [exterior] + holes


def _polygon_key(rings):
    exterior = _ring_key(rings[0])
    holes = tuple(sorted(_ring_key(hole) for hole in rings[1:]))
    return exterior, holes


def canonical_polygon(polygon):
    """Return a canonical polygon coordinate array.

    Exterior and hole rings are canonicalized individually and holes are
    sorted.  Holes are grouped only within their own exterior ring; this
    function never moves a hole to another polygon.

    :param polygon: A polygon coordinate array.
    :return: A canonical polygon coordinate array.
    :rtype: list
    """
    return _canonical_polygon_rings(polygon)


def canonical_line(line):
    """Return a canonical, direction-independent line coordinate array."""
    if not isinstance(line, (list, tuple)) or len(line) < 2:
        raise ValueError("a line must contain at least two positions")
    positions = tuple(_position(position) for position in line)
    keys = tuple(_position_key(position) for position in positions)
    reversed_keys = tuple(reversed(keys))
    if reversed_keys < keys:
        positions = tuple(reversed(positions))
    return [list(position) for position in positions]


def _normalize_coordinates(type_, coordinates):
    if type_ == "Point":
        return list(_position(coordinates))
    if type_ == "MultiPoint":
        points = sorted(
            (_position(position) for position in coordinates),
            key=_position_key,
        )
        return [list(position) for position in points]
    if type_ == "LineString":
        return canonical_line(coordinates)
    if type_ == "MultiLineString":
        lines = [canonical_line(line) for line in coordinates]
        lines.sort(key=lambda line: tuple(
            _position_key(position) for position in line
        ))
        return lines
    if type_ == "Polygon":
        return canonical_polygon(coordinates)
    if type_ == "MultiPolygon":
        polygons = [canonical_polygon(polygon) for polygon in coordinates]
        polygons.sort(key=_polygon_key)
        return polygons
    raise ValueError(f"Unsupported geometry type {type_!r}")


def _coordinate_key(type_, coordinates):
    if type_ == "Point":
        return ("Point", _position_key(coordinates))
    if type_ == "MultiPoint":
        return (
            "MultiPoint",
            tuple(sorted(_position_key(position) for position in coordinates)),
        )
    if type_ == "LineString":
        return ("LineString", _line_key(coordinates))
    if type_ == "MultiLineString":
        return (
            "MultiLineString",
            tuple(sorted(_line_key(line) for line in coordinates)),
        )
    if type_ == "Polygon":
        rings = _canonical_polygon_rings(coordinates)
        return ("Polygon", _polygon_key(rings))
    if type_ == "MultiPolygon":
        polygons = [
            _canonical_polygon_rings(polygon) for polygon in coordinates
        ]
        return (
            "MultiPolygon",
            tuple(sorted(_polygon_key(polygon) for polygon in polygons)),
        )
    raise ValueError(f"Unsupported geometry type {type_!r}")


def normalize_geometry(geometry):
    """Return the canonical form of a GeoJSON geometry.

    Point positions are preserved, multipoint and multiline ordering is
    normalized, linear rings ignore their starting point and direction,
    polygon holes are sorted within their exterior ring, and multipolygon
    faces are sorted as complete ``(exterior, holes)`` units.  Repeated
    closing points and consecutive duplicate vertices are removed from rings.

    :param geometry: A GeoJSON geometry object or mapping.
    :return: A canonical GeoJSON geometry mapping.
    :rtype: dict
    """
    if geometry is None:
        return None
    mapping = _as_mapping(geometry)
    type_ = mapping.get("type")
    if type_ == "Feature":
        return normalize_geometry(mapping.get("geometry"))
    if type_ == "GeometryCollection":
        geometries = [
            normalize_geometry(item) for item in mapping.get("geometries", [])
        ]
        if any(item is None for item in geometries):
            raise ValueError(
                "a GeometryCollection cannot contain null geometries"
            )
        geometries.sort(key=geometry_key)
        return {"type": "GeometryCollection", "geometries": geometries}
    if type_ not in GEOMETRY_TYPES:
        raise ValueError(f"Invalid geometry object {geometry!r}")
    return {
        "type": type_,
        "coordinates": _normalize_coordinates(type_, mapping["coordinates"]),
    }


def canonical_geometry(geometry):
    """Alias for :func:`normalize_geometry`."""
    return normalize_geometry(geometry)


def _encoded_key(payload):
    encoded = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, default=repr
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def geometry_key(geometry):
    """Return a hashable key identifying a geometry up to geometric equality.

    Null geometries return ``None`` so callers can still group them.

    :param geometry: A GeoJSON geometry, Feature, or geometry mapping.
    :return: A deterministic hexadecimal digest, or ``None`` for null
        geometry.
    :rtype: str or None
    """
    if geometry is None:
        return None
    mapping = _as_mapping(geometry)
    type_ = mapping.get("type")
    if type_ == "Feature":
        return geometry_key(mapping.get("geometry"))
    if type_ == "GeometryCollection":
        keys = [
            geometry_key(item) for item in mapping.get("geometries", [])
        ]
        return _encoded_key(("GeometryCollection", tuple(sorted(keys))))
    if type_ not in GEOMETRY_TYPES:
        raise ValueError(f"Invalid geometry object {geometry!r}")
    payload = _coordinate_key(type_, mapping["coordinates"])
    return _encoded_key(payload)


def geometry_fingerprint(geometry):
    """Alias for :func:`geometry_key`."""
    return geometry_key(geometry)


def geometries_equal(left, right):
    """Return whether two geometries are geometrically equivalent."""
    return geometry_key(left) == geometry_key(right)


def geometry_equivalent(left, right):
    """Alias for :func:`geometries_equal`."""
    return geometries_equal(left, right)


def _iter_features(obj):
    if obj is None:
        return
    if isinstance(obj, (str, bytes, bytearray)):
        raise TypeError(f"{obj!r} is not a GeoJSON object")
    if isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _iter_features(item)
        return
    if isinstance(obj, Mapping):
        if obj.get("type") not in (
            GEOMETRY_TYPES + ("Feature", "FeatureCollection")
        ):
            raise TypeError(f"{obj!r} is not a GeoJSON object")
    elif hasattr(obj, "__iter__"):
        for item in obj:
            yield from _iter_features(item)
        return
    mapping = _as_mapping(obj)
    type_ = mapping.get("type")
    if type_ == "FeatureCollection":
        for feature in mapping.get("features", []):
            yield from _iter_features(feature)
    else:
        yield obj


def _is_source_mapping(value):
    return isinstance(value, Mapping) and value.get("type") not in (
        GEOMETRY_TYPES + ("Feature", "FeatureCollection")
    )


def _iter_inputs(inputs):
    for item in inputs:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
        ):
            source, value = item
            for feature in _iter_features(value):
                yield feature, source
        elif _is_source_mapping(item):
            for source, value in item.items():
                for feature in _iter_features(value):
                    yield feature, source
        else:
            for feature in _iter_features(item):
                yield feature, None


def _feature_geometry(feature):
    mapping = _as_mapping(feature)
    if mapping.get("type") == "Feature":
        return mapping.get("geometry")
    return feature


def _feature_properties(feature):
    mapping = _as_mapping(feature)
    if mapping.get("type") == "Feature":
        return copy.deepcopy(mapping.get("properties") or {})
    return {}


def _feature_id(feature):
    mapping = _as_mapping(feature)
    if mapping.get("type") == "Feature":
        return mapping.get("id")
    return None


def _declaration(feature, source, source_key, code_keys):
    properties = _feature_properties(feature)
    if source is None:
        source = properties.get(source_key)
    codes = {
        key: copy.deepcopy(properties[key])
        for key in code_keys
        if key in properties
    }
    return {
        "source": source,
        "id": _feature_id(feature),
        "properties": properties,
        "codes": codes,
    }


def merge_by_geometry(*inputs, features=None, source_key="source",
                      code_keys=DEFAULT_CODE_KEYS):
    """Consolidate features into one record per geometric equivalence class.

    Every input declaration is retained.  In particular, if two sources
    provide the same geometry with different administrative division codes,
    both declarations remain in the record; this function never elects one
    declaration as authoritative.

    Each returned record has the shape::

        {
            "geometry": <canonical GeoJSON geometry>,
            "declarations": [
                {"source": ..., "id": ..., "properties": {...}, "codes": {...}},
                ...
            ]
        }

    Inputs may be Features, FeatureCollections, bare geometries, sequences of
    those, ``(source, value)`` pairs, or mappings of ``source -> value``.

    :param inputs: Feature collections or other supported inputs.
    :param features: Additional input accepted by keyword.
    :param source_key: Property key used as a source label when no explicit
        source is supplied.
    :param code_keys: Property keys whose values are division codes.
    :return: One merged record per distinct geometry.
    :rtype: list
    """
    all_inputs = list(inputs)
    if features is not None:
        all_inputs.append(features)

    groups = OrderedDict()
    for feature, source in _iter_inputs(all_inputs):
        geometry = _feature_geometry(feature)
        if geometry is None:
            continue
        key = geometry_key(geometry)
        if key not in groups:
            groups[key] = [geometry, []]
        groups[key][1].append(
            _declaration(feature, source, source_key, code_keys)
        )

    records = []
    for geometry, declarations in groups.values():
        records.append({
            "geometry": normalize_geometry(geometry),
            "declarations": declarations,
        })
    return records


def merge_geometries(*inputs, features=None, source_key="source",
                     code_keys=DEFAULT_CODE_KEYS):
    """Alias for :func:`merge_by_geometry`."""
    return merge_by_geometry(
        *inputs,
        features=features,
        source_key=source_key,
        code_keys=code_keys,
    )


def group_by_geometry(inputs, source_key="source",
                      code_keys=DEFAULT_CODE_KEYS):
    """Alias for :func:`merge_by_geometry` for a single input."""
    return merge_by_geometry(
        inputs, source_key=source_key, code_keys=code_keys
    )


def consolidate_by_geometry(*inputs, features=None, source_key="source",
                            code_keys=DEFAULT_CODE_KEYS):
    """Alias for :func:`merge_by_geometry`."""
    return merge_by_geometry(
        *inputs,
        features=features,
        source_key=source_key,
        code_keys=code_keys,
    )


def group_features_by_geometry(*inputs, features=None, source_key="source",
                               code_keys=DEFAULT_CODE_KEYS):
    """Alias for :func:`merge_by_geometry`."""
    return merge_by_geometry(
        *inputs,
        features=features,
        source_key=source_key,
        code_keys=code_keys,
    )


def deduplicate_geometries(geometries):
    """Return each distinct canonical geometry once, preserving first order."""
    seen = set()
    result = []
    for geometry in _iter_features(geometries):
        key = geometry_key(geometry)
        if key not in seen:
            seen.add(key)
            result.append(normalize_geometry(geometry))
    return result


def _geometry_instance(canonical):
    """Wrap a canonical mapping without re-rounding its coordinates."""
    from geojson.base import GeoJSON

    instance = GeoJSON.to_instance(canonical, strict=True)
    if canonical["type"] == "GeometryCollection":
        instance["geometries"] = [
            _geometry_instance(item) for item in canonical["geometries"]
        ]
    else:
        instance["coordinates"] = copy.deepcopy(canonical["coordinates"])
    return instance


def merge_to_features(*inputs, features=None, source_key="source",
                      code_keys=DEFAULT_CODE_KEYS):
    """Return merged geometry records as a FeatureCollection.

    Each output Feature carries the canonical geometry and a ``declarations``
    property containing every source declaration.  No declaration is selected
    as authoritative.

    The canonical geometry is wrapped without applying coordinate rounding
    again, so the result keeps every position exactly as canonicalized.
    """
    from geojson.feature import Feature, FeatureCollection

    records = merge_by_geometry(
        *inputs,
        features=features,
        source_key=source_key,
        code_keys=code_keys,
    )
    output = [
        Feature(
            geometry=_geometry_instance(record["geometry"]),
            properties={"declarations": record["declarations"]},
        )
        for record in records
    ]
    return FeatureCollection(output)


def merge_features_by_geometry(*inputs, features=None, source_key="source",
                               code_keys=DEFAULT_CODE_KEYS):
    """Alias for :func:`merge_to_features`."""
    return merge_to_features(
        *inputs,
        features=features,
        source_key=source_key,
        code_keys=code_keys,
    )


def dedupe_features(*inputs, source_key="source",
                    code_keys=DEFAULT_CODE_KEYS):
    """Alias for :func:`merge_to_features`."""
    return merge_to_features(
        *inputs, source_key=source_key, code_keys=code_keys
    )


normalize = normalize_geometry
canonicalize = normalize_geometry
canonical_form = normalize_geometry
normalize_ring = canonical_ring
normalize_polygon = canonical_polygon
canonicalize_ring = canonical_ring
canonicalize_polygon = canonical_polygon
canonical_key = geometry_key
geometry_hash = geometry_key
geometry_equal = geometries_equal
geometries_equivalent = geometries_equal
merge_features = merge_by_geometry
merge_feature_collections = merge_by_geometry
merge_by_equivalence = merge_by_geometry
consolidate_features = merge_by_geometry
group_features = merge_by_geometry
dedupe_geometries = deduplicate_geometries
dedupe = deduplicate_geometries
deduplicate_features = merge_to_features
