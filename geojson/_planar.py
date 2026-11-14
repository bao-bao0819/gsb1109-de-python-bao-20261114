"""Minimal planar polygon operations used by :mod:`geojson.rainfall`.

The rainfall bands are built from polygons expressed in a projected,
cartesian coordinate system.  Adding a dependency on GEOS/Shapely is not
desirable for a small, pure-Python package, so this module implements the
subset of operations that is needed here:

* normalisation of GeoJSON polygons/multipolygons,
* polygon area and bounding boxes,
* union, intersection and difference of multipolygons.

The overlay algorithm nodes all input segments, builds the planar
arrangement and classifies every face by its position with respect to the
inputs.  It is deliberately dependency free and works for polygons with
holes, multiple parts and shared boundaries.  Coordinates are kept in the
caller's planar units; no projection is performed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from math import atan2, hypot
from numbers import Real


__all__ = [
    "EMPTY",
    "area",
    "bbox",
    "difference",
    "intersection",
    "is_empty",
    "normalize",
    "to_geojson",
    "union",
]


# A multipolygon is a list of polygons.  A polygon is a list of rings and a
# ring is a closed list of (x, y) tuples, the first ring being the exterior.
EMPTY = []


def _is_position(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], Real)
        and isinstance(value[1], Real)
    )


def _is_ring(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 3
        and all(_is_position(item) for item in value)
    )


def _is_polygon(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 1
        and all(_is_ring(item) for item in value)
    )


def _coordinate_list(obj):
    if isinstance(obj, dict):
        if obj.get("type") == "Feature":
            return _coordinate_list(obj.get("geometry"))
        if obj.get("type") == "FeatureCollection":
            parts = []
            for feature in obj.get("features") or []:
                parts.extend(normalize(feature))
            return parts
        if obj.get("type") == "GeometryCollection":
            parts = []
            for geometry in obj.get("geometries") or []:
                parts.extend(normalize(geometry))
            return parts
        if "coordinates" in obj:
            return obj["coordinates"]
        raise ValueError(f"unsupported geometry object: {obj!r}")
    if isinstance(obj, (list, tuple)):
        return obj
    raise ValueError(f"unsupported geometry object: {obj!r}")


def _close_ring(ring, tolerance=0.0):
    points = [(float(point[0]), float(point[1])) for point in ring]
    cleaned = []
    for point in points:
        if not cleaned or _distance(cleaned[-1], point) > tolerance:
            cleaned.append(point)
    if len(cleaned) < 3:
        return []
    if cleaned[0] != cleaned[-1]:
        cleaned.append(cleaned[0])
    if len(cleaned) < 4:
        return []
    return cleaned


def _distance(a, b):
    return hypot(a[0] - b[0], a[1] - b[1])


def _signed_ring_area(ring):
    total = 0.0
    for first, second in zip(ring, ring[1:]):
        total += first[0] * second[1] - second[0] * first[1]
    return total / 2.0


def _normalize_polygon(rings):
    if not rings:
        return []
    cleaned = []
    for ring in rings:
        closed = _close_ring(ring)
        if not closed:
            continue
        signed = _signed_ring_area(closed)
        if abs(signed) <= 0.0:
            continue
        # GeoJSON does not require a winding order.  Orientation is decided
        # below from the nesting structure, not from the input order.
        if signed < 0:
            closed.reverse()
        cleaned.append(closed)
    if not cleaned:
        return []

    # A GeoJSON Polygon has one exterior; additional rings must be holes.
    # Some products nevertheless put several disjoint exteriors in one
    # polygon (or omit the hole ordering).  Group by containment depth so
    # every disconnected ring becomes its own polygon and every ring
    # enclosed by another becomes a hole.  This keeps all area instead of
    # silently subtracting a disjoint ring as if it were a hole.
    depths = []
    for index, ring in enumerate(cleaned):
        depth = 0
        for other_index, other in enumerate(cleaned):
            if index == other_index:
                continue
            if _ring_contains(other, ring):
                depth += 1
        depths.append(depth)

    polygons = {}
    pending_holes = []
    for ring, depth in sorted(zip(cleaned, depths), key=lambda pair: pair[1]):
        if depth % 2 == 0:
            polygons[id(ring)] = [ring]
        else:
            pending_holes.append((ring, depth))
    for ring, depth in pending_holes:
        parent = None
        parent_area = None
        for candidate, candidate_depth in zip(cleaned, depths):
            if candidate_depth == depth - 1 and _ring_contains(candidate, ring):
                candidate_area = abs(_signed_ring_area(candidate))
                if parent_area is None or candidate_area < parent_area:
                    parent = candidate
                    parent_area = candidate_area
        if parent is None or id(parent) not in polygons:
            polygons[id(ring)] = [ring]
        else:
            polygons[id(parent)].append(list(reversed(ring)))
    return list(polygons.values())


def normalize(obj):
    """Return *obj* as a list of polygons with closed rings.

    Accepts a GeoJSON geometry/feature/feature-collection, a ``geojson``
    object, a list of positions, a list of rings or a list of polygons.
    ``None`` and empty inputs become :data:`EMPTY`.
    """
    if obj is None:
        return []
    if hasattr(obj, "__geo_interface__"):
        obj = obj.__geo_interface__
    coords = _coordinate_list(obj)
    if not coords:
        return []
    if _is_position(coords):
        # A single position is not an area; treat it as invalid rather than
        # silently producing a degenerate polygon.
        raise ValueError("a point cannot be converted to a polygon")
    if _is_ring(coords):
        return _normalize_polygon([coords])
    if _is_polygon(coords):
        return _normalize_polygon(coords)
    polygons = []
    for candidate in coords:
        if not _is_polygon(candidate):
            raise ValueError(f"invalid polygon coordinates: {candidate!r}")
        polygons.extend(_normalize_polygon(candidate))
    return polygons


def is_empty(multipolygon):
    return not multipolygon


def _polygon_area(polygon):
    if not polygon:
        return 0.0
    total = abs(_signed_ring_area(polygon[0]))
    for hole in polygon[1:]:
        total -= abs(_signed_ring_area(hole))
    return total


def area(multipolygon):
    """Return the area of *multipolygon* in squared coordinate units."""
    return sum(_polygon_area(polygon) for polygon in multipolygon)


def _points_of(multipolygon):
    for polygon in multipolygon:
        for ring in polygon:
            yield from ring


def bbox(multipolygon):
    """Return ``[minx, miny, maxx, maxy]`` or ``None`` when empty."""
    xs = []
    ys = []
    for x, y in _points_of(multipolygon):
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _tolerance(multipolygon):
    bounds = bbox(multipolygon)
    if bounds is None:
        return 1e-9
    scale = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1.0)
    return scale * 1e-9


class _PointIndex:
    """Snap points that are closer than *tolerance* to a single vertex."""

    def __init__(self, tolerance):
        self.tolerance = tolerance
        self.points = []
        self.cells = {}

    def add(self, point):
        x, y = point
        cell = (int(x // self.tolerance), int(y // self.tolerance))
        best = None
        best_distance = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index in self.cells.get((cell[0] + dx, cell[1] + dy), ()):
                    other = self.points[index]
                    distance = _distance(point, other)
                    if distance <= self.tolerance and (
                        best is None or distance < best_distance
                    ):
                        best = index
                        best_distance = distance
        if best is not None:
            return best
        index = len(self.points)
        self.points.append((x, y))
        self.cells.setdefault(cell, []).append(index)
        return index


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def _dot(ax, ay, bx, by):
    return ax * bx + ay * by


def _parameters(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator == 0.0:
        return 0.0
    return ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator


def _on_segment(point, start, end, tolerance):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = hypot(dx, dy)
    if length == 0.0:
        return _distance(point, start) <= tolerance
    if abs(_cross(point[0] - start[0], point[1] - start[1], dx, dy)) > tolerance * length:
        return False
    projection = _dot(point[0] - start[0], point[1] - start[1], dx, dy)
    return -tolerance * length <= projection <= length * length + tolerance * length


def _segment_intersections(first, second, tolerance):
    """Return ``(t, u)`` parameter pairs for two segments."""
    p1, p2 = first
    p3, p4 = second
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = p4[0] - p3[0], p4[1] - p3[1]
    denominator = _cross(rx, ry, sx, sy)
    qpx, qpy = p3[0] - p1[0], p3[1] - p1[1]
    scale = max(hypot(rx, ry) * hypot(sx, sy), 1.0)
    if abs(denominator) > 1e-12 * scale:
        t = _cross(qpx, qpy, sx, sy) / denominator
        u = _cross(qpx, qpy, rx, ry) / denominator
        if -1e-12 <= t <= 1.0 + 1e-12 and -1e-12 <= u <= 1.0 + 1e-12:
            return [(min(1.0, max(0.0, t)), min(1.0, max(0.0, u)))]
        return []
    # Parallel: only collinear overlapping segments matter.
    if abs(_cross(qpx, qpy, rx, ry)) > tolerance * max(hypot(rx, ry), 1.0):
        return []
    pairs = []
    for point in (p1, p2, p3, p4):
        if _on_segment(point, p1, p2, tolerance) and _on_segment(
            point, p3, p4, tolerance
        ):
            t = min(1.0, max(0.0, _parameters(point, p1, p2)))
            u = min(1.0, max(0.0, _parameters(point, p3, p4)))
            pairs.append((t, u))
    return pairs


def _unique_parameters(values, tolerance=1e-12):
    result = []
    for value in sorted(values):
        if not result or value - result[-1] > tolerance:
            result.append(value)
    return result


def _node_segments(multipolygons, tolerance):
    segments = []
    for multipolygon in multipolygons:
        for polygon in multipolygon:
            for ring in polygon:
                for start, end in zip(ring, ring[1:]):
                    if start != end:
                        segments.append((start, end))

    cuts = [[0.0, 1.0] for _ in segments]
    boxes = []
    for start, end in segments:
        boxes.append(
            (
                min(start[0], end[0]),
                min(start[1], end[1]),
                max(start[0], end[0]),
                max(start[1], end[1]),
            )
        )
    for first in range(len(segments)):
        a = boxes[first]
        for second in range(first + 1, len(segments)):
            b = boxes[second]
            if a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]:
                continue
            for t, u in _segment_intersections(
                segments[first], segments[second], tolerance
            ):
                cuts[first].append(t)
                cuts[second].append(u)

    index = _PointIndex(tolerance)
    edges = set()
    for (start, end), parameters in zip(segments, cuts):
        parameters = _unique_parameters(parameters)
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        for left, right in zip(parameters, parameters[1:]):
            if right - left <= 1e-12:
                continue
            first = index.add((start[0] + dx * left, start[1] + dy * left))
            second = index.add((start[0] + dx * right, start[1] + dy * right))
            if first != second:
                edges.add((first, second) if first < second else (second, first))
    return index.points, edges


def _build_adjacency(edges):
    adjacency = defaultdict(set)
    for first, second in edges:
        adjacency[first].add(second)
        adjacency[second].add(first)
    return adjacency


def _ordered_neighbours(points, adjacency):
    ordered = {}
    for vertex, neighbours in adjacency.items():
        x, y = points[vertex]
        ordered[vertex] = sorted(
            neighbours,
            key=lambda neighbour: atan2(
                points[neighbour][1] - y, points[neighbour][0] - x
            ),
        )
    return ordered


def _extract_cycles(points, edges):
    """Extract every face boundary as a list of directed edges.

    A directed edge ``(u, v)`` has the face on its left.  The next edge is
    the neighbour immediately clockwise from the reverse direction at ``v``.
    """
    adjacency = _build_adjacency(edges)
    ordered = _ordered_neighbours(points, adjacency)
    positions = {
        vertex: {neighbour: index for index, neighbour in enumerate(order)}
        for vertex, order in ordered.items()
    }
    visited = set()
    cycles = []
    limit = max(8, len(edges) * 4 + 8)
    for start_vertex in adjacency:
        for start_neighbour in adjacency[start_vertex]:
            if (start_vertex, start_neighbour) in visited:
                continue
            cycle = []
            first, second = start_vertex, start_neighbour
            while (first, second) not in visited:
                visited.add((first, second))
                cycle.append((first, second))
                order = ordered[second]
                index = positions[second][first]
                third = order[(index - 1) % len(order)]
                first, second = second, third
                if len(cycle) > limit:
                    raise ValueError("could not reconstruct polygon faces")
            cycles.append(cycle)
    return cycles


def _distance_to_segment(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0.0:
        return _distance(point, start)
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared
    t = min(1.0, max(0.0, t))
    return _distance(point, (start[0] + dx * t, start[1] + dy * t))


def _cycle_interior_point(cycle, points, edges):
    """Return a point strictly on the left (interior) side of a cycle."""
    best = None
    best_length = -1.0
    for first, second in cycle:
        length = _distance(points[first], points[second])
        if length > best_length:
            best = (first, second)
            best_length = length
    first, second = best
    start = points[first]
    end = points[second]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = hypot(dx, dy)
    middle = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    normal = (-dy / length, dx / length)

    clearance = float("inf")
    for left, right in edges:
        if (left, right) == (first, second) or (left, right) == (second, first):
            continue
        clearance = min(
            clearance,
            _distance_to_segment(middle, points[left], points[right]),
        )
    if clearance == float("inf"):
        step = length / 4.0
    else:
        step = min(length / 4.0, clearance / 2.0)
    if step <= 0.0:
        step = max(length * 1e-6, 1e-12)
    return (middle[0] + normal[0] * step, middle[1] + normal[1] * step)


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for first, second in zip(ring, ring[1:]):
        x1, y1 = first
        x2, y2 = second
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
    return inside


def point_in_multipolygon(point, multipolygon):
    for polygon in multipolygon:
        if not polygon:
            continue
        if _point_in_ring(point, polygon[0]):
            if not any(_point_in_ring(point, hole) for hole in polygon[1:]):
                return True
    return False


def _choose_next(vertex, previous, candidates, points):
    if len(candidates) == 1:
        return candidates[0]
    x, y = points[vertex]
    ordered = sorted(
        set(candidates) | {previous},
        key=lambda neighbour: atan2(
            points[neighbour][1] - y, points[neighbour][0] - x
        ),
    )
    index = ordered.index(previous)
    chosen = ordered[(index - 1) % len(ordered)]
    if chosen == previous and len(ordered) > 1:
        chosen = ordered[(index - 2) % len(ordered)]
    return chosen


def _chain_edges(edges, points):
    outgoing = defaultdict(list)
    for first, second in edges:
        outgoing[first].append(second)
    used = set()
    rings = []
    for start in edges:
        if start in used:
            continue
        first, second = start
        ring = [first]
        while (first, second) not in used:
            used.add((first, second))
            ring.append(second)
            candidates = [n for n in outgoing[second] if (second, n) not in used]
            if not candidates:
                break
            third = _choose_next(second, first, candidates, points)
            first, second = second, third
        if len(ring) >= 3 and ring[0] == ring[-1]:
            rings.append([points[index] for index in ring])
    return rings


def _ring_contains(outer, inner):
    """True when *inner* lies (almost) entirely inside *outer*.

    Vertices can sit exactly on the boundary (touching rings are common in
    clipped products), and a boundary vertex is ambiguous for a point-in-
    polygon test.  Edge midpoints avoid that ambiguity; the majority vote
    keeps the answer stable for rings that share part of their boundary.
    """
    probes = [
        ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
        for first, second in zip(inner, inner[1:])
    ]
    if not probes:
        return False
    inside = sum(1 for probe in probes if _point_in_ring(probe, outer))
    return inside > len(probes) / 2.0


def _rings_to_multipolygon(rings):
    exteriors = []
    holes = []
    for ring in rings:
        signed = _signed_ring_area(ring)
        if abs(signed) <= 1e-15:
            continue
        if signed > 0:
            exteriors.append(ring)
        else:
            holes.append(ring)

    assigned = [[] for _ in exteriors]
    for hole in holes:
        candidates = [
            index
            for index, exterior in enumerate(exteriors)
            if _ring_contains(exterior, hole)
        ]
        if not candidates:
            # Degenerate touching ring: keep it as its own exterior so that
            # no area silently disappears.
            exteriors.append(list(reversed(hole)))
            assigned.append([])
            continue
        owner = min(candidates, key=lambda index: abs(_signed_ring_area(exteriors[index])))
        assigned[owner].append(hole)

    polygons = []
    for exterior, polygon_holes in zip(exteriors, assigned):
        polygons.append([exterior] + polygon_holes)
    polygons.sort(key=lambda polygon: bbox([polygon])[:2])
    return polygons


def _overlay(multipolygons, predicate):
    if not any(multipolygons):
        return []
    tolerance = max(_tolerance(multipolygons[0]), 1e-12)
    if len(multipolygons) > 1:
        for mp in multipolygons[1:]:
            tolerance = max(tolerance, _tolerance(mp))
    points, edges = _node_segments(multipolygons, tolerance)
    if not edges:
        return []
    cycles = _extract_cycles(points, edges)
    selected = []
    for cycle in cycles:
        point = _cycle_interior_point(cycle, points, edges)
        membership = tuple(
            point_in_multipolygon(point, mp) for mp in multipolygons
        )
        if predicate(membership):
            selected.extend(cycle)
    if not selected:
        return []

    counts = Counter(selected)
    for edge in list(counts):
        reverse = (edge[1], edge[0])
        if reverse in counts:
            removed = min(counts[edge], counts[reverse])
            counts[edge] -= removed
            counts[reverse] -= removed
    remaining = []
    for edge, count in counts.items():
        remaining.extend([edge] * count)
    rings = _chain_edges(remaining, points)
    return _rings_to_multipolygon(rings)


def union(*multipolygons):
    """Return the union of the given multipolygons."""
    return _overlay(list(multipolygons), lambda members: any(members))


def intersection(*multipolygons):
    """Return the intersection of the given multipolygons."""
    return _overlay(list(multipolygons), lambda members: all(members))


def difference(first, *others):
    """Return ``first`` minus the union of *others*."""
    return _overlay([first, *others], lambda members: members[0] and not any(members[1:]))


def to_geojson(multipolygon, precision=10):
    """Serialise a multipolygon to a GeoJSON geometry mapping."""
    coordinates = []
    for polygon in multipolygon:
        rings = []
        for ring in polygon:
            cleaned = []
            for x, y in ring:
                x = round(x, precision)
                y = round(y, precision)
                if x == 0:
                    x = 0.0
                if y == 0:
                    y = 0.0
                cleaned.append([x, y])
            if cleaned and cleaned[0] != cleaned[-1]:
                cleaned.append(list(cleaned[0]))
            if len(cleaned) >= 4:
                rings.append(cleaned)
        if rings:
            coordinates.append(rings)
    if not coordinates:
        return {"type": "MultiPolygon", "coordinates": []}
    return {"type": "MultiPolygon", "coordinates": coordinates}
