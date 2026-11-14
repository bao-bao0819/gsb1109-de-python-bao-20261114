"""Spatial relations between axis-aligned rectangles and polygons.

The functions in this module are deliberately dependency-free.  They answer
one question: how does an axis-aligned rectangle (for example the positional
error envelope reported by a mobile station) relate to a service area stored as
a Polygon or MultiPolygon?

Three results are possible:

``INSIDE``
    Every point of the rectangle belongs to the area.
``PARTIAL``
    Some, but not all, points of the rectangle belong to the area.
``OUTSIDE``
    No point of the rectangle belongs to the area.

Area boundaries are part of the area.  The interior of a hole is not.  This
means a point on a polygon's exterior ring or on a hole ring belongs to the
area, while a point strictly inside a hole does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from numbers import Number
from typing import Any, Iterable, Mapping, Sequence


# Coordinates closer than this (relative to their magnitude) are treated as
# coincident.  This is only used to make boundary tests stable for ordinary
# floating point input; it is not a general purpose geometric tolerance.
_EPSILON = 1e-12


class Relation(str, Enum):
    """The three possible rectangle/area relations."""

    INSIDE = "inside"
    PARTIAL = "partial"
    OUTSIDE = "outside"

    # Readable aliases for callers that prefer OGC-style names.
    CONTAINED = "inside"
    OVERLAPS = "partial"
    DISJOINT = "outside"

    def __str__(self):
        return self.value


# A short alias that reads naturally when imported on its own.
RectangleRelationKind = Relation
RectRelation = Relation


class PointLocation(str, Enum):
    """Where a point lies relative to an area."""

    INSIDE = "inside"
    OUTSIDE = "outside"
    BOUNDARY = "boundary"

    def __str__(self):
        return self.value


@dataclass(frozen=True)
class Edge:
    """An edge of an axis-aligned rectangle."""

    side: str
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass(frozen=True)
class Rectangle:
    """An axis-aligned, closed rectangle.

    The constructor accepts the usual ``(min_x, min_y, max_x, max_y)`` order,
    which is also the order used by a GeoJSON bounding box.
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self):
        values = tuple(float(value) for value in (
            self.min_x, self.min_y, self.max_x, self.max_y))
        if not all(isfinite(value) for value in values):
            raise ValueError("rectangle bounds must be finite numbers")
        x0, y0, x1, y1 = values
        min_x, max_x = sorted((x0, x1))
        min_y, max_y = sorted((y0, y1))
        if min_x == max_x or min_y == max_y:
            raise ValueError("rectangle must have positive width and height")
        object.__setattr__(self, "min_x", min_x)
        object.__setattr__(self, "min_y", min_y)
        object.__setattr__(self, "max_x", max_x)
        object.__setattr__(self, "max_y", max_y)

    @classmethod
    def from_bounds(cls, bounds: Sequence[float]) -> "Rectangle":
        """Build a rectangle from ``(min_x, min_y, max_x, max_y)``."""
        if len(bounds) != 4:
            raise ValueError("rectangle bounds must contain four numbers")
        return cls(*bounds)

    @classmethod
    def from_center(
            cls, center: Sequence[float], width: float,
            height: float) -> "Rectangle":
        """Build a rectangle from its center and full width/height."""
        if len(center) < 2:
            raise ValueError("center must contain at least two numbers")
        width = float(width)
        height = float(height)
        if width <= 0 or height <= 0:
            raise ValueError("rectangle width and height must be positive")
        x, y = float(center[0]), float(center[1])
        half_width = width / 2.0
        half_height = height / 2.0
        return cls(x - half_width, y - half_height,
                   x + half_width, y + half_height)

    @classmethod
    def from_geojson(cls, geometry: Any) -> "Rectangle":
        """Build a rectangle from a GeoJSON-like object or four bounds."""
        return _rectangle_from_geojson(geometry)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.min_x, self.min_y, self.max_x, self.max_y)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.bounds

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0,
                (self.min_y + self.max_y) / 2.0)

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def corners(self) -> tuple[
            tuple[float, float], tuple[float, float],
            tuple[float, float], tuple[float, float]]:
        """Corners in counter-clockwise order starting at the lower left."""
        return (
            (self.min_x, self.min_y),
            (self.max_x, self.min_y),
            (self.max_x, self.max_y),
            (self.min_x, self.max_y),
        )

    @property
    def edges(self) -> tuple[Edge, Edge, Edge, Edge]:
        return (
            Edge("left", (self.min_x, self.min_y),
                 (self.min_x, self.max_y)),
            Edge("right", (self.max_x, self.min_y),
                 (self.max_x, self.max_y)),
            Edge("bottom", (self.min_x, self.min_y),
                 (self.max_x, self.min_y)),
            Edge("top", (self.min_x, self.max_y),
                 (self.max_x, self.max_y)),
        )

    def edge(self, side: str) -> Edge:
        for edge in self.edges:
            if edge.side == side:
                return edge
        raise KeyError(side)

    def contains_point(self, point: Sequence[float],
                       boundary: bool = True) -> bool:
        """Return whether *point* lies in the rectangle.

        With ``boundary=True`` (the default) points on an edge or corner are
        included.
        """
        x, y = _position(point)
        if boundary:
            return (self.min_x <= x <= self.max_x
                    and self.min_y <= y <= self.max_y)
        return (self.min_x < x < self.max_x
                and self.min_y < y < self.max_y)


@dataclass(frozen=True)
class EdgeContact:
    """A contact between one rectangle edge and one area ring segment.

    ``side`` names the rectangle edge (``"left"``, ``"right"``, ``"top"`` or
    ``"bottom"``).  ``segment`` holds the two endpoints of the area boundary
    segment, so the contact can be traced back to a concrete ring edge.
    """

    side: str
    polygon_index: int
    ring_index: int
    ring_role: str
    segment_index: int
    seg_start: tuple[float, float]
    seg_end: tuple[float, float]
    kind: str
    points: tuple[tuple[float, float], ...]

    @property
    def point(self) -> tuple[float, float] | None:
        """The contact point for a point contact, otherwise ``None``."""
        return self.points[0] if self.kind == "point" else None

    @property
    def segment(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """The service-area boundary segment that was contacted."""
        return (self.seg_start, self.seg_end)

    def as_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "polygon_index": self.polygon_index,
            "ring_index": self.ring_index,
            "ring": self.ring_role,
            "segment_index": self.segment_index,
            "segment": [list(self.seg_start), list(self.seg_end)],
            "kind": self.kind,
            "points": [list(point) for point in self.points],
        }


@dataclass(frozen=True)
class EdgeRelation:
    """How one rectangle edge relates to the area.

    ``relation`` is ``"inside"`` when every sampled part of the edge belongs
    to the area, ``"outside"`` when none does, and ``"mixed"`` otherwise.
    ``contacts`` describes every crossing, touch, or collinear overlap with an
    area ring.
    """

    side: str
    start: tuple[float, float]
    end: tuple[float, float]
    relation: str
    contacts: tuple[EdgeContact, ...] = ()

    @property
    def touches_boundary(self) -> bool:
        return bool(self.contacts)

    @property
    def crosses_boundary(self) -> bool:
        return any(contact.kind == "point" for contact in self.contacts)

    @property
    def overlaps_boundary(self) -> bool:
        return any(contact.kind == "overlap" for contact in self.contacts)

    @property
    def contact_points(self) -> tuple[tuple[float, float], ...]:
        points = []
        for contact in self.contacts:
            for point in contact.points:
                if point not in points:
                    points.append(point)
        return tuple(points)


@dataclass(frozen=True)
class HoleRelation:
    """Diagnostics for one hole of one polygon."""

    polygon_index: int
    hole_index: int
    intersects_rectangle: bool
    enclosed: bool
    contacts: tuple[EdgeContact, ...] = ()

    @property
    def ring_index(self) -> int:
        """The index in the polygon's complete coordinate array."""
        return self.hole_index + 1


@dataclass(frozen=True)
class RectangleRelation:
    """The result of classifying a rectangle against an area.

    ``relation`` is one of :class:`Relation`'s three values.  The object also
    carries the evidence used to reach it, so callers can explain tangency or
    reject a classification when the data is suspicious.
    """

    relation: Relation
    rectangle: Rectangle
    corner_locations: tuple[PointLocation, ...]
    edges: tuple[EdgeRelation, ...]
    holes: tuple[HoleRelation, ...] = ()
    tangent_only: bool = False
    reason: str = ""

    @property
    def value(self) -> str:
        return self.relation.value

    @property
    def is_inside(self) -> bool:
        return self.relation is Relation.INSIDE

    @property
    def is_partial(self) -> bool:
        return self.relation is Relation.PARTIAL

    @property
    def is_outside(self) -> bool:
        return self.relation is Relation.OUTSIDE

    # Short aliases make the result pleasant to use in conditionals.
    inside = is_inside
    partial = is_partial
    outside = is_outside

    @property
    def contact_edges(self) -> tuple[str, ...]:
        """Names of rectangle edges that touch any area boundary."""
        return tuple(edge.side for edge in self.edges if edge.contacts)

    @property
    def tangent_edges(self) -> tuple[str, ...]:
        """Edges responsible for a pure boundary tangency.

        This is empty unless the rectangle merely touches the area boundary:
        no point of the rectangle's interior lies in the area.  Edges whose
        whole clipped extent lies in the closed area are reported first,
        because those are the sides that run along the boundary.  If there
        are none, every edge with a point contact is reported, which covers a
        corner-only touch.  Sides are ordered ``left``, ``right``, ``bottom``,
        ``top`` so the result is stable for callers that display it.
        """
        if not self.tangent_only:
            return ()
        order = {side: index for index, side in enumerate(
            ("left", "right", "bottom", "top"))}

        def side_order(side: str) -> int:
            return order[side]

        inside = tuple(edge.side for edge in self.edges
                       if edge.contacts and edge.relation == "inside")
        if inside:
            return tuple(sorted(inside, key=side_order))
        return tuple(sorted(
            (edge.side for edge in self.edges if edge.contacts),
            key=side_order))

    @property
    def enclosed_holes(self) -> tuple[HoleRelation, ...]:
        """Holes whose whole ring is enclosed by the rectangle."""
        return tuple(hole for hole in self.holes if hole.enclosed)

    @property
    def edge_map(self) -> dict[str, EdgeRelation]:
        return {edge.side: edge for edge in self.edges}

    def as_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation.value,
            "rectangle": list(self.rectangle.bounds),
            "corner_locations": [location.value
                                 for location in self.corner_locations],
            "tangent_only": self.tangent_only,
            "reason": self.reason,
            "tangent_edges": list(self.tangent_edges),
            "contact_edges": list(self.contact_edges),
            "edges": [
                {
                    "side": edge.side,
                    "relation": edge.relation,
                    "contacts": [contact.as_dict()
                                 for contact in edge.contacts],
                }
                for edge in self.edges
            ],
            "holes": [
                {
                    "polygon_index": hole.polygon_index,
                    "hole_index": hole.hole_index,
                    "intersects_rectangle": hole.intersects_rectangle,
                    "enclosed": hole.enclosed,
                }
                for hole in self.holes
            ],
        }


@dataclass(frozen=True)
class _AreaPolygon:
    rings: tuple[tuple[tuple[float, float], ...], ...]

    @property
    def exterior(self) -> tuple[tuple[float, float], ...]:
        return self.rings[0]

    @property
    def holes(self) -> tuple[tuple[tuple[float, float], ...], ...]:
        return self.rings[1:]


@dataclass
class _SegmentIntersection:
    kind: str
    points: tuple[tuple[float, float], ...]


# ---------------------------------------------------------------------------
# Input coercion


def _position(value: Any) -> tuple[float, float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("a position must be a sequence of numbers")
    if len(value) < 2:
        raise ValueError("a position must contain at least two numbers")
    x, y = value[0], value[1]
    if (isinstance(x, bool) or isinstance(y, bool)
            or not isinstance(x, Number) or not isinstance(y, Number)):
        raise ValueError("a position must contain numbers")
    x, y = float(x), float(y)
    if not (isfinite(x) and isfinite(y)):
        raise ValueError("position coordinates must be finite")
    return x, y


def _coordinates(geometry: Any) -> tuple[str, Any]:
    if hasattr(geometry, "__geo_interface__"):
        geometry = geometry.__geo_interface__
    if not isinstance(geometry, Mapping):
        raise ValueError("a geometry must be a mapping or GeoJSON object")
    geometry = dict(geometry)
    kind = geometry.get("type")
    if kind == "Feature":
        if geometry.get("geometry") is None:
            raise ValueError("feature has no geometry")
        return _coordinates(geometry["geometry"])
    if kind == "GeometryCollection":
        raise ValueError("GeometryCollection is not a supported area")
    if "coordinates" not in geometry:
        raise ValueError("geometry has no coordinates")
    return kind, geometry["coordinates"]


def _looks_like_position(value: Any) -> bool:
    return (isinstance(value, Sequence)
            and not isinstance(value, (str, bytes))
            and len(value) >= 2
            and not isinstance(value[0], (Sequence, Mapping)))


def _rectangle_from_geojson(rectangle: Any) -> Rectangle:
    if isinstance(rectangle, Rectangle):
        return rectangle
    if hasattr(rectangle, "__geo_interface__"):
        rectangle = rectangle.__geo_interface__
    if isinstance(rectangle, Mapping):
        rectangle = dict(rectangle)
        if rectangle.get("type") == "Feature":
            return _rectangle_from_geojson(rectangle.get("geometry"))
        if rectangle.get("type") == "Polygon":
            rings = rectangle.get("coordinates") or []
            if len(rings) != 1:
                raise ValueError("an error rectangle cannot contain holes")
            return _rectangle_from_ring(rings[0])
        if "bbox" in rectangle:
            return Rectangle.from_bounds(rectangle["bbox"])
        for keys in (
                ("min_x", "min_y", "max_x", "max_y"),
                ("minx", "miny", "maxx", "maxy"),
                ("xmin", "ymin", "xmax", "ymax"),
                ("west", "south", "east", "north"),
                ("left", "bottom", "right", "top"),
        ):
            if all(key in rectangle for key in keys):
                return Rectangle.from_bounds(
                    [rectangle[key] for key in keys])
        raise ValueError("cannot construct a rectangle from this mapping")

    if isinstance(rectangle, (str, bytes)) or not isinstance(
            rectangle, Sequence):
        raise ValueError("rectangle must be four bounds or a GeoJSON Polygon")
    if len(rectangle) == 4 and all(
            isinstance(value, Number) and not isinstance(value, bool)
            for value in rectangle):
        return Rectangle.from_bounds(rectangle)
    if rectangle and all(_looks_like_position(point) for point in rectangle):
        return _rectangle_from_ring(rectangle)
    raise ValueError(
        "rectangle must be four bounds or a sequence of four positions")


def _rectangle_from_ring(ring: Any) -> Rectangle:
    if isinstance(ring, (str, bytes)) or not isinstance(ring, Sequence):
        raise ValueError("an error rectangle must be a list of positions")
    points = [_position(point) for point in ring]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) != 4:
        raise ValueError("an error rectangle must have exactly four corners")
    xs = {point[0] for point in points}
    ys = {point[1] for point in points}
    if len(xs) != 2 or len(ys) != 2:
        raise ValueError("an error rectangle must be axis-aligned")
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    expected = {(min_x, min_y), (min_x, max_y),
                (max_x, min_y), (max_x, max_y)}
    if set(points) != expected:
        raise ValueError("an error rectangle must be axis-aligned")
    return Rectangle(min_x, min_y, max_x, max_y)


def _area_polygons(area: Any) -> tuple[_AreaPolygon, ...]:
    kind, coordinates = _coordinates(area)
    if kind == "Polygon":
        coordinates = [coordinates]
    elif kind != "MultiPolygon":
        raise ValueError("area must be a Polygon or MultiPolygon")
    if not isinstance(coordinates, Sequence):
        raise ValueError("area coordinates must be a sequence")

    polygons = []
    for polygon_coordinates in coordinates:
        if (isinstance(polygon_coordinates, (str, bytes))
                or not isinstance(polygon_coordinates, Sequence)
                or not polygon_coordinates):
            raise ValueError("each polygon must contain at least one ring")
        rings = []
        for ring_coordinates in polygon_coordinates:
            rings.append(_ring_points(ring_coordinates))
        polygons.append(_AreaPolygon(tuple(rings)))
    return tuple(polygons)


def _ring_points(ring: Any) -> tuple[tuple[float, float], ...]:
    if isinstance(ring, (str, bytes)) or not isinstance(ring, Sequence):
        raise ValueError("a linear ring must be a sequence of positions")
    points = [_position(point) for point in ring]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        raise ValueError("a linear ring must contain at least three positions")
    return tuple(points)


# ---------------------------------------------------------------------------
# Basic predicates


def _tolerance(*values: float) -> float:
    return _EPSILON * max(1.0, *(abs(value) for value in values))


def _cross(a: tuple[float, float], b: tuple[float, float],
           c: tuple[float, float]) -> float:
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _sign(value: float, *scale: float) -> int:
    tolerance = _tolerance(*scale)
    if value > tolerance:
        return 1
    if value < -tolerance:
        return -1
    return 0


def _point_on_segment(point: tuple[float, float],
                      a: tuple[float, float],
                      b: tuple[float, float]) -> bool:
    scale = max(1.0, abs(a[0]), abs(a[1]), abs(b[0]), abs(b[1]),
                abs(point[0]), abs(point[1]))
    tolerance = _EPSILON * scale
    if _sign(_cross(a, b, point), *(
            abs(a[0]), abs(a[1]), abs(b[0]), abs(b[1]),
            abs(point[0]), abs(point[1]))) != 0:
        return False
    return (min(a[0], b[0]) - tolerance <= point[0]
            <= max(a[0], b[0]) + tolerance
            and min(a[1], b[1]) - tolerance <= point[1]
            <= max(a[1], b[1]) + tolerance)


def _ring_segments(ring: Sequence[tuple[float, float]]):
    for index in range(len(ring)):
        yield index, ring[index], ring[(index + 1) % len(ring)]


def _ring_location(point: tuple[float, float],
                   ring: Sequence[tuple[float, float]]) -> PointLocation:
    """Return the location of *point* relative to one linear ring."""
    for _, a, b in _ring_segments(ring):
        if _point_on_segment(point, a, b):
            return PointLocation.BOUNDARY

    x, y = point
    inside = False
    for _, a, b in _ring_segments(ring):
        if (a[1] > y) != (b[1] > y):
            crossing_x = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < crossing_x:
                inside = not inside
    return PointLocation.INSIDE if inside else PointLocation.OUTSIDE


def _polygon_location(point: tuple[float, float],
                      polygon: _AreaPolygon) -> PointLocation:
    exterior = _ring_location(point, polygon.exterior)
    if exterior is PointLocation.OUTSIDE:
        return PointLocation.OUTSIDE
    if exterior is PointLocation.BOUNDARY:
        return PointLocation.BOUNDARY

    on_hole_boundary = False
    for hole in polygon.holes:
        location = _ring_location(point, hole)
        if location is PointLocation.INSIDE:
            return PointLocation.OUTSIDE
        if location is PointLocation.BOUNDARY:
            on_hole_boundary = True
    if on_hole_boundary:
        return PointLocation.BOUNDARY
    return PointLocation.INSIDE


def _locate_in_polygons(
        position: tuple[float, float],
        polygons: Sequence[_AreaPolygon]) -> PointLocation:
    on_boundary = False
    for polygon in polygons:
        location = _polygon_location(position, polygon)
        if location is PointLocation.INSIDE:
            return PointLocation.INSIDE
        if location is PointLocation.BOUNDARY:
            on_boundary = True
    return PointLocation.BOUNDARY if on_boundary else PointLocation.OUTSIDE


def point_location(point: Sequence[float], area: Any) -> PointLocation:
    """Locate a point relative to a Polygon or MultiPolygon area.

    Boundary points, including points on a hole ring, are reported as
    ``BOUNDARY`` and are considered to belong to the area.  Points strictly
    inside a hole are ``OUTSIDE``.
    """
    return _locate_in_polygons(_position(point), _area_polygons(area))


def _belongs(location: PointLocation) -> bool:
    return location is not PointLocation.OUTSIDE


def _point_strictly_in_rectangle(point: tuple[float, float],
                                 rectangle: Rectangle) -> bool:
    return (rectangle.min_x < point[0] < rectangle.max_x
            and rectangle.min_y < point[1] < rectangle.max_y)


def _point_in_rectangle(point: tuple[float, float],
                        rectangle: Rectangle) -> bool:
    return rectangle.contains_point(point)


def _segment_intersection(
        a: tuple[float, float], b: tuple[float, float],
        c: tuple[float, float], d: tuple[float, float]
) -> _SegmentIntersection | None:
    """Return the non-empty intersection of two closed segments."""
    scale = max(abs(a[0]), abs(a[1]), abs(b[0]), abs(b[1]),
                abs(c[0]), abs(c[1]), abs(d[0]), abs(d[1]), 1.0)
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    q_minus_p = (c[0] - a[0], c[1] - a[1])
    denominator_sign = _sign(denominator, scale * scale)

    if denominator_sign == 0:
        if _sign(_cross(a, b, c), scale * scale) != 0:
            return None
        r_length_squared = r[0] * r[0] + r[1] * r[1]
        if r_length_squared == 0:
            return (_SegmentIntersection("point", (a,))
                    if _point_on_segment(a, c, d) else None)
        t0 = (q_minus_p[0] * r[0] + q_minus_p[1] * r[1]) / r_length_squared
        t1 = t0 + (s[0] * r[0] + s[1] * r[1]) / r_length_squared
        lower, upper = sorted((t0, t1))
        lower = max(0.0, lower)
        upper = min(1.0, upper)
        tolerance = _tolerance(scale) / max(1.0, abs(r[0]), abs(r[1]))
        if upper < lower - tolerance:
            return None
        if upper - lower <= tolerance:
            t = (lower + upper) / 2.0
            return _SegmentIntersection(
                "point", ((a[0] + t * r[0], a[1] + t * r[1]),))
        p0 = (a[0] + lower * r[0], a[1] + lower * r[1])
        p1 = (a[0] + upper * r[0], a[1] + upper * r[1])
        return _SegmentIntersection("overlap", (p0, p1))

    t = (q_minus_p[0] * s[1] - q_minus_p[1] * s[0]) / denominator
    u = (q_minus_p[0] * r[1] - q_minus_p[1] * r[0]) / denominator
    parameter_tolerance = _tolerance(scale) / max(
        1.0, abs(r[0]), abs(r[1]), abs(s[0]), abs(s[1]))
    if (-parameter_tolerance <= t <= 1.0 + parameter_tolerance
            and -parameter_tolerance <= u <= 1.0 + parameter_tolerance):
        t = min(1.0, max(0.0, t))
        return _SegmentIntersection(
            "point", ((a[0] + t * r[0], a[1] + t * r[1]),))
    return None


def _clip_segment_to_rectangle(
        a: tuple[float, float], b: tuple[float, float],
        rectangle: Rectangle) -> tuple[float, float] | None:
    """Return the parameter interval of a segment inside the rectangle."""
    delta_x = b[0] - a[0]
    delta_y = b[1] - a[1]
    lower, upper = 0.0, 1.0
    for p, q in (
            (-delta_x, a[0] - rectangle.min_x),
            (delta_x, rectangle.max_x - a[0]),
            (-delta_y, a[1] - rectangle.min_y),
            (delta_y, rectangle.max_y - a[1]),
    ):
        if p == 0:
            if q < 0:
                return None
        else:
            ratio = q / p
            if p < 0:
                lower = max(lower, ratio)
            else:
                upper = min(upper, ratio)
    if upper < lower:
        return None
    return lower, upper


def _ring_intervals_at_x(
        ring: Sequence[tuple[float, float]],
        x: float) -> list[tuple[float, float]]:
    """Return the open y-intervals where a vertical line is inside a ring."""
    crossings = []
    for _, a, b in _ring_segments(ring):
        if a[0] == b[0]:
            continue
        if (a[0] <= x < b[0]) or (b[0] <= x < a[0]):
            t = (x - a[0]) / (b[0] - a[0])
            crossings.append(a[1] + t * (b[1] - a[1]))
    crossings.sort()
    return [(crossings[index], crossings[index + 1])
            for index in range(0, len(crossings) - 1, 2)
            if crossings[index + 1] > crossings[index]]


def _subtract_intervals(
        intervals: Sequence[tuple[float, float]],
        holes: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    result = list(intervals)
    for hole_lower, hole_upper in holes:
        updated = []
        for lower, upper in result:
            if hole_upper <= lower or hole_lower >= upper:
                updated.append((lower, upper))
                continue
            if lower < hole_lower:
                updated.append((lower, hole_lower))
            if hole_upper < upper:
                updated.append((hole_upper, upper))
        result = updated
    return result


def _merge_intervals(
        intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((lower, upper) for lower, upper in intervals
                     if upper > lower)
    merged: list[tuple[float, float]] = []
    for lower, upper in ordered:
        if merged and lower <= merged[-1][1] + _EPSILON:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    return merged


def _polygon_intervals_at_x(
        polygon: _AreaPolygon, x: float) -> list[tuple[float, float]]:
    exterior = _ring_intervals_at_x(polygon.exterior, x)
    holes = []
    for hole in polygon.holes:
        holes.extend(_ring_intervals_at_x(hole, x))
    return _subtract_intervals(exterior, holes)


def _area_intervals_at_x(
        polygons: Sequence[_AreaPolygon], x: float
) -> list[tuple[float, float]]:
    intervals = []
    for polygon in polygons:
        intervals.extend(_polygon_intervals_at_x(polygon, x))
    return _merge_intervals(intervals)


def _interval_covers(lower: float, upper: float,
                     intervals: Sequence[tuple[float, float]],
                     tolerance: float) -> bool:
    cursor = lower
    for interval_lower, interval_upper in intervals:
        if interval_upper <= cursor + tolerance:
            continue
        if interval_lower > cursor + tolerance:
            return False
        cursor = max(cursor, interval_upper)
        if cursor >= upper - tolerance:
            return True
    return cursor >= upper - tolerance


def _interval_overlaps(lower: float, upper: float,
                       intervals: Sequence[tuple[float, float]],
                       tolerance: float) -> bool:
    return any(min(upper, interval_upper)
               - max(lower, interval_lower) > tolerance
               for interval_lower, interval_upper in intervals)


def _coverage_by_slab(
        polygons: Sequence[_AreaPolygon], rectangle: Rectangle,
        contact_points: Iterable[tuple[float, float]]
) -> tuple[bool, bool]:
    """Return ``(fully_covered, interior_intersects)`` for a rectangle.

    The rectangle is split into vertical slabs at every x where the area
    boundary can change the coverage pattern.  Within a slab the set of
    boundary crossings is stable, so one representative x suffices.
    """
    events_x = {rectangle.min_x, rectangle.max_x}
    for polygon in polygons:
        for ring in polygon.rings:
            for x, _ in ring:
                if rectangle.min_x < x < rectangle.max_x:
                    events_x.add(x)
            for _, a, b in _ring_segments(ring):
                if a[1] == b[1]:
                    continue
                for horizontal in (rectangle.min_y, rectangle.max_y):
                    t = (horizontal - a[1]) / (b[1] - a[1])
                    if 0.0 < t < 1.0:
                        x = a[0] + t * (b[0] - a[0])
                        if rectangle.min_x < x < rectangle.max_x:
                            events_x.add(x)
    for x, _ in contact_points:
        if rectangle.min_x < x < rectangle.max_x:
            events_x.add(x)

    ordered_x = sorted(events_x)
    tolerance = _tolerance(rectangle.min_y, rectangle.max_y,
                           rectangle.min_x, rectangle.max_x)
    fully_covered = True
    interior_intersects = False

    for lower_x, upper_x in zip(ordered_x, ordered_x[1:]):
        if upper_x - lower_x <= tolerance:
            continue
        sample_x = (lower_x + upper_x) / 2.0
        intervals = _area_intervals_at_x(polygons, sample_x)
        if _interval_overlaps(rectangle.min_y, rectangle.max_y,
                              intervals, tolerance):
            interior_intersects = True
        if not _interval_covers(rectangle.min_y, rectangle.max_y,
                                intervals, tolerance):
            fully_covered = False

    if len(ordered_x) < 2:
        fully_covered = False
    return fully_covered, interior_intersects


def _edge_contacts(
        edge: Edge, polygons: Sequence[_AreaPolygon]) -> tuple[EdgeContact,
                                                               ...]:
    contacts = []
    for polygon_index, polygon in enumerate(polygons):
        for ring_index, ring in enumerate(polygon.rings):
            role = "exterior" if ring_index == 0 else "hole"
            for segment_index, a, b in _ring_segments(ring):
                hit = _segment_intersection(edge.start, edge.end, a, b)
                if hit is None:
                    continue
                contacts.append(EdgeContact(
                    side=edge.side,
                    polygon_index=polygon_index,
                    ring_index=ring_index,
                    ring_role=role,
                    segment_index=segment_index,
                    seg_start=a,
                    seg_end=b,
                    kind=hit.kind,
                    points=hit.points,
                ))
    return tuple(dict.fromkeys(contacts))


def _edge_parameter(point: tuple[float, float],
                    edge: Edge) -> float:
    delta_x = edge.end[0] - edge.start[0]
    delta_y = edge.end[1] - edge.start[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared == 0:
        return 0.0
    parameter = ((point[0] - edge.start[0]) * delta_x
                 + (point[1] - edge.start[1]) * delta_y) / length_squared
    return min(1.0, max(0.0, parameter))


def _edge_relation(edge: Edge, contacts: tuple[EdgeContact, ...],
                   polygons: Sequence[_AreaPolygon]) -> EdgeRelation:
    parameters = {0.0, 1.0}
    for contact in contacts:
        for point in contact.points:
            parameters.add(_edge_parameter(point, edge))

    inside_seen = bool(contacts)
    outside_seen = False
    ordered = sorted(parameters)
    for lower, upper in zip(ordered, ordered[1:]):
        if upper - lower <= _EPSILON:
            continue
        midpoint_t = (lower + upper) / 2.0
        midpoint = (
            edge.start[0] + midpoint_t * (edge.end[0] - edge.start[0]),
            edge.start[1] + midpoint_t * (edge.end[1] - edge.start[1]),
        )
        location = _locate_in_polygons(midpoint, polygons)
        if location is PointLocation.OUTSIDE:
            outside_seen = True
        else:
            inside_seen = True

    if inside_seen and outside_seen:
        relation = "mixed"
    elif inside_seen:
        relation = "inside"
    else:
        relation = "outside"
    return EdgeRelation(edge.side, edge.start, edge.end, relation, contacts)


def _hole_relations(
        polygons: Sequence[_AreaPolygon],
        rectangle: Rectangle) -> tuple[HoleRelation, ...]:
    relations = []
    for polygon_index, polygon in enumerate(polygons):
        for hole_index, hole in enumerate(polygon.holes):
            enclosed = all(_point_in_rectangle(point, rectangle)
                           for point in hole)
            vertex_inside = any(
                _point_in_rectangle(point, rectangle) for point in hole)
            corner_inside_hole = any(
                _ring_location(corner, hole) is PointLocation.INSIDE
                for corner in rectangle.corners)
            contacts = []
            for segment_index, a, b in _ring_segments(hole):
                for edge in rectangle.edges:
                    hit = _segment_intersection(
                        edge.start, edge.end, a, b)
                    if hit is not None:
                        contacts.append(EdgeContact(
                            side=edge.side,
                            polygon_index=polygon_index,
                            ring_index=hole_index + 1,
                            ring_role="hole",
                            segment_index=segment_index,
                            seg_start=a,
                            seg_end=b,
                            kind=hit.kind,
                            points=hit.points,
                        ))
            intersects = bool(
                enclosed or vertex_inside or contacts or corner_inside_hole)
            relations.append(HoleRelation(
                polygon_index=polygon_index,
                hole_index=hole_index,
                intersects_rectangle=intersects,
                enclosed=enclosed,
                contacts=tuple(contacts),
            ))
    return tuple(relations)


def _as_area_geometry(polygons: Sequence[_AreaPolygon]) -> dict[str, Any]:
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [[list(point) for point in ring] for ring in polygon.rings]
            for polygon in polygons
        ],
    }


# ---------------------------------------------------------------------------
# Public classifier


def classify_rectangle(rectangle: Any,
                       area: Any) -> RectangleRelation:
    """Classify an axis-aligned rectangle against a Polygon/MultiPolygon.

    :param rectangle: ``(min_x, min_y, max_x, max_y)`` bounds, a
        :class:`Rectangle`, or an axis-aligned GeoJSON Polygon.  A Feature
        wrapping such a Polygon is accepted too.
    :param area: a Polygon or MultiPolygon (or a Feature wrapping one).
    :return: a :class:`RectangleRelation` with the classification and the
        boundary contacts that produced it.
    :raises ValueError: if the rectangle is malformed or is not axis-aligned,
        or if the area is not a Polygon/MultiPolygon.
    """
    rect = _rectangle_from_geojson(rectangle)
    polygons = _area_polygons(area)
    area_geometry = _as_area_geometry(polygons)

    corner_locations = tuple(
        point_location(corner, area_geometry) for corner in rect.corners)

    edges = tuple(
        _edge_relation(edge, _edge_contacts(edge, polygons), polygons)
        for edge in rect.edges)
    contacts_present = any(edge.contacts for edge in edges)

    vertex_in_rectangle = any(
        _point_in_rectangle(point, rect)
        for polygon in polygons
        for ring in polygon.rings
        for point in ring)
    any_corner_in = any(_belongs(location)
                        for location in corner_locations)
    intersects = bool(contacts_present or vertex_in_rectangle
                      or any_corner_in)

    holes = _hole_relations(polygons, rect)
    full_cover, interior_intersects = _coverage_by_slab(
        polygons, rect,
        (point for edge in edges for contact in edge.contacts
         for point in contact.points))

    if not intersects:
        relation = Relation.OUTSIDE
        tangent_only = False
        reason = "disjoint"
    elif full_cover:
        relation = Relation.INSIDE
        tangent_only = False
        reason = "rectangle_inside_area"
    else:
        relation = Relation.PARTIAL
        tangent_only = not interior_intersects
        if tangent_only:
            reason = "boundary_tangent"
        elif any(hole.enclosed for hole in holes):
            reason = "enclosed_hole"
        else:
            reason = "rectangle_crosses_boundary"

    return RectangleRelation(
        relation=relation,
        rectangle=rect,
        corner_locations=corner_locations,
        edges=edges,
        holes=holes,
        tangent_only=tangent_only,
        reason=reason,
    )


def rectangle_relation(rectangle: Any, area: Any) -> RectangleRelation:
    """Alias for :func:`classify_rectangle`."""
    return classify_rectangle(rectangle, area)


def classify_rectangle_relation(rectangle: Any,
                                area: Any) -> RectangleRelation:
    """Alias for :func:`classify_rectangle`."""
    return classify_rectangle(rectangle, area)


__all__ = [
    "Relation",
    "RectangleRelationKind",
    "RectRelation",
    "PointLocation",
    "Rectangle",
    "Edge",
    "EdgeContact",
    "EdgeRelation",
    "HoleRelation",
    "RectangleRelation",
    "classify_rectangle",
    "classify_rectangle_relation",
    "rectangle_relation",
    "point_location",
]
