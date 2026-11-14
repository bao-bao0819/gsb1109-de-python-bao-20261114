"""Topology-aware coordinate quantization for adjacent polygon features.

Reducing coordinate precision for an exchange file is safe only when
neighboring parcels that share a boundary keep publishing that boundary with
the exact same vertices.  Quantizing every polygon independently would open
sliver gaps or overlaps on shared edges and can silently close a narrow
channel or pull a hole onto the outer boundary.

The functions in this module therefore process the polygons as a group:

* a shared edge whose one side carries intermediate vertices while the other
  side only stores the two endpoints is detected geometrically, and every
  intermediate vertex is handed to both sides so the boundary is published
  identically (in opposite winding directions);
* parcels touching along a shared edge are joined into atomic groups, so a
  boundary is never rewritten on only one side;
* the harmonized coordinates are quantized together.  When quantization
  collapses a narrow channel or merges a hole with the outer boundary, every
  parcel of that group is held back with its original geometry preserved and
  reported separately for manual repair, while unaffected groups are still
  emitted with quantized coordinates.

Only the Python standard library is used, matching the dependency-free
nature of the package.
"""

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP


# Reason codes reported for parcels that cannot be quantized safely.
CLOSED_CHANNEL = "closed_channel"
HOLE_MERGED = "hole_merged"
HELD_WITH_GROUP = "held_with_group"

_POLYGON_TYPES = ("Polygon", "MultiPolygon")
_GRID_BINS = 32


class _Quantizer:
    """Rounds scalar coordinates with a fixed, half-up quantum."""

    def __init__(self, precision):
        self.step = Decimal(1).scaleb(-precision)
        self.memo = {}

    def __call__(self, value):
        if value not in self.memo:
            quantized = Decimal(str(value)).quantize(
                self.step, rounding=ROUND_HALF_UP)
            self.memo[value] = float(quantized)
        return self.memo[value]

    def point(self, position):
        return (self(position[0]), self(position[1]))


def _edge_key(start, end):
    """Undirected identity of a primitive segment."""
    return (start, end) if start <= end else (end, start)


def _collinear(start, end, point):
    cross = ((end[0] - start[0]) * (point[1] - start[1])
             - (end[1] - start[1]) * (point[0] - start[0]))
    return cross == 0


def _between(start, end, point):
    return (min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
            and min(start[1], end[1]) <= point[1] <= max(start[1], end[1]))


def _point_on_segment(point, start, end):
    return point != start and point != end and _collinear(
        start, end, point) and _between(start, end, point)


class _PointGrid:
    """Uniform grid used only to prune candidate on-segment vertices."""

    def __init__(self, points):
        points = list(points)
        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        self.min_x, self.min_y = min_x, min_y
        self.size_x = (max_x - min_x) / _GRID_BINS or 1.0
        self.size_y = (max_y - min_y) / _GRID_BINS or 1.0
        self.cells = {}
        for point in points:
            self.cells.setdefault(self._cell(point), []).append(point)

    def _cell(self, point):
        return (int((point[0] - self.min_x) / self.size_x),
                int((point[1] - self.min_y) / self.size_y))

    def candidates(self, start, end):
        col_0 = int((min(start[0], end[0]) - self.min_x) / self.size_x)
        col_1 = int((max(start[0], end[0]) - self.min_x) / self.size_x)
        row_0 = int((min(start[1], end[1]) - self.min_y) / self.size_y)
        row_1 = int((max(start[1], end[1]) - self.min_y) / self.size_y)
        seen = set()
        for col in range(col_0, col_1 + 1):
            for row in range(row_0, row_1 + 1):
                for point in self.cells.get((col, row), ()):
                    if point not in seen:
                        seen.add(point)
                        yield point


class _UnionFind:
    """Connects parcels that must be published or held together."""

    def __init__(self):
        self.parent = {}

    def add(self, item):
        self.parent.setdefault(item, item)

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, left, right):
        self.add(left)
        self.add(right)
        self.parent[self.find(left)] = self.find(right)

    def groups(self):
        result = {}
        for item in self.parent:
            result.setdefault(self.find(item), []).append(item)
        return list(result.values())


def _as_ring(ring):
    normalized = []
    for position in ring:
        if len(position) != 2:
            raise ValueError(
                "snap_shared_edges only supports two-dimensional positions; "
                f"got position {position!r} with {len(position)} values")
        normalized.append((position[0], position[1]))
    return normalized


def _collect_records(obj):
    """Returns (wrapper kind, feature list, polygon records).

    A record remembers the feature, geometry type, polygon index and the
    normalized closed rings of every polygon in the input.
    """
    kind = obj["type"]
    if kind == "FeatureCollection":
        features = list(obj["features"])
        bare = False
    elif kind == "Feature":
        features = [obj]
        bare = False
    elif kind in _POLYGON_TYPES:
        features = [{"type": "Feature", "geometry": obj, "properties": {}}]
        bare = True
    else:
        raise ValueError(f"Invalid polygon container object {obj!r}")

    records = []
    for index, feature in enumerate(features):
        geometry = feature.get("geometry")
        if geometry is None or geometry["type"] not in _POLYGON_TYPES:
            continue
        fid = feature.get("id")
        if fid is None:
            fid = index
        if geometry["type"] == "Polygon":
            polygons = [geometry["coordinates"]]
        else:
            polygons = geometry["coordinates"]
        for polygon_index, polygon in enumerate(polygons):
            records.append({
                "fid": fid,
                "geometry_type": geometry["type"],
                "polygon_index": polygon_index,
                "rings": [_as_ring(ring) for ring in polygon],
            })
    return kind, bare, features, records


def _order_along(start, end, points):
    """Orders collinear vertices from start towards end."""
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length_sq = delta_x * delta_x + delta_y * delta_y

    def parameter(point):
        if length_sq == 0:
            return 0.0
        return ((point[0] - start[0]) * delta_x
                + (point[1] - start[1]) * delta_y) / length_sq

    return sorted(points, key=parameter)


def _rebuild_ring(ring, insertions, quantizer):
    """Rebuilds a ring, adding shared vertices and quantizing coordinates."""
    rebuilt = []
    for index in range(len(ring) - 1):
        start, end = ring[index], ring[index + 1]
        if start == end:
            continue
        points = {start, end}
        points.update(insertions.get(_edge_key(start, end), ()))
        ordered = _order_along(start, end, points)
        if quantizer is not None:
            ordered = [quantizer.point(point) for point in ordered]
        if rebuilt:
            rebuilt.extend(ordered[1:])
        else:
            rebuilt.extend(ordered)
    return rebuilt


def _ring_segments(ring):
    for index in range(len(ring) - 1):
        yield ring[index], ring[index + 1]


def _ring_degenerate(ring):
    vertices = ring[:-1]
    if len(vertices) < 3 or len(set(vertices)) < 3:
        return True
    return any(start == end for start, end in _ring_segments(ring))


def _orientation(a, b, c):
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _segments_intersect(a, b, c, d):
    """True for crossing, endpoint touching or collinearly overlapping edges."""
    if (max(a[0], b[0]) < min(c[0], d[0])
            or min(a[0], b[0]) > max(c[0], d[0])
            or max(a[1], b[1]) < min(c[1], d[1])
            or min(a[1], b[1]) > max(c[1], d[1])):
        return False
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 == 0 and _between(a, b, c):
        return True
    if o2 == 0 and _between(a, b, d):
        return True
    if o3 == 0 and _between(c, d, a):
        return True
    if o4 == 0 and _between(c, d, b):
        return True
    return ((o1 > 0 > o2) or (o1 < 0 < o2)) and (
        (o3 > 0 > o4) or (o3 < 0 < o4))


def _indexed_segments(ring):
    return [(ring[index], ring[index + 1])
            for index in range(len(ring) - 1)]


def _ring_self_intersects(ring):
    segments = _indexed_segments(ring)
    count = len(segments)
    for i in range(count):
        start, end = segments[i]
        for j in range(i + 1, count):
            if j == i + 1 or (i == 0 and j == count - 1):
                continue
            other_start, other_end = segments[j]
            if _segments_intersect(start, end, other_start, other_end):
                return True
    return False


def _rings_intersect(left, right):
    right_segments = _indexed_segments(right)
    for start, end in _indexed_segments(left):
        for other_start, other_end in right_segments:
            if _segments_intersect(start, end, other_start, other_end):
                return True
    return False


def _polygon_problems(rings):
    """Detects quantization damage on one polygon's quantized rings."""
    problems = []
    outer, holes = rings[0], rings[1:]

    if _ring_degenerate(outer) or _ring_self_intersects(outer):
        problems.append({"reason": CLOSED_CHANNEL, "ring": 0})

    for ring_index, hole in enumerate(holes, start=1):
        if _ring_degenerate(hole) or _ring_self_intersects(hole):
            problems.append({"reason": CLOSED_CHANNEL, "ring": ring_index})
        if _rings_intersect(outer, hole):
            problems.append({"reason": HOLE_MERGED, "ring": ring_index})
        for other_index, other_hole in enumerate(holes[:ring_index - 1],
                                                  start=1):
            if _rings_intersect(hole, other_hole):
                problems.append({
                    "reason": HOLE_MERGED,
                    "ring": ring_index,
                    "with_ring": other_index,
                })
    return problems


def snap_shared_edges(obj, precision=2):
    """Harmonizes shared boundaries and quantizes polygon coordinates.

    :param obj: A ``FeatureCollection``, ``Feature``, ``Polygon`` or
        ``MultiPolygon`` holding the parcels to export.
    :type obj: geojson object or dict
    :param precision: Number of decimal places kept after quantization.
    :type precision: int
    :return: A ``(snapshotted_object, report)`` tuple.  Clean parcels carry
        quantized coordinates with identical shared edges; parcels whose
        group would collapse keep their original geometry and are listed in
        the report.
    :rtype: tuple
    :raises ValueError: if the input is not a supported polygon container.
    """
    kind, bare, features, records = _collect_records(obj)
    if not records:
        return deepcopy(obj), {
            "precision": precision,
            "shared_boundary_segments": 0,
            "harmonized_edges": 0,
            "groups": [],
            "quantized_features": [],
            "affected_features": [],
        }

    edge_owners = {}
    vertex_owners = {}
    union = _UnionFind()
    for record in records:
        fid = record["fid"]
        union.add(fid)
        for ring in record["rings"]:
            for point in ring:
                vertex_owners.setdefault(point, set()).add(fid)
            for start, end in _ring_segments(ring):
                if start == end:
                    continue
                key = _edge_key(start, end)
                owners = edge_owners.setdefault(key, set())
                for other in owners:
                    union.union(fid, other)
                owners.add(fid)

    grid = _PointGrid(vertex_owners)
    insertions = {}
    for (start, end), owners in edge_owners.items():
        for point in grid.candidates(start, end):
            if not _point_on_segment(point, start, end):
                continue
            foreign = vertex_owners.get(point, set()) - owners
            if not foreign:
                continue
            insertions.setdefault((start, end), set()).add(point)
            for neighbor in foreign:
                union.union(next(iter(owners)), neighbor)

    quantizer = _Quantizer(precision)
    quantized = {}
    problems_by_fid = {}
    for record in records:
        fid = record["fid"]
        rings = [_rebuild_ring(ring, insertions, quantizer)
                 for ring in record["rings"]]
        quantized.setdefault(fid, {})[record["polygon_index"]] = {
            "type": record["geometry_type"],
            "rings": rings,
        }
        for problem in _polygon_problems(rings):
            problem["polygon"] = record["polygon_index"]
            problems_by_fid.setdefault(fid, []).append(problem)

    held_groups, clean_groups = [], []
    for group in union.groups():
        members = sorted(group, key=_feature_sort_key)
        reasons = []
        for fid in members:
            reasons.extend(problems_by_fid.get(fid, ()))
        entry = {"features": members}
        if reasons:
            entry["status"] = "held_back"
            entry["problems"] = reasons
            held_groups.append(entry)
        else:
            entry["status"] = "quantized"
            clean_groups.append(entry)
    held_groups.sort(key=lambda entry: _feature_sort_key(entry["features"][0]))
    clean_groups.sort(key=lambda entry: _feature_sort_key(entry["features"][0]))

    held_fids = {fid for group in held_groups for fid in group["features"]}
    output = deepcopy(obj)

    if bare:
        fid = records[0]["fid"] if records else 0
        if fid in quantized and fid not in held_fids:
            output["coordinates"] = _geometry_for(
                features[0], quantized[fid])["coordinates"]
    else:
        output_features = (
            [output] if kind == "Feature" else output["features"])
        for index, feature in enumerate(features):
            fid = feature.get("id")
            if fid is None:
                fid = index
            if fid in held_fids or fid not in quantized:
                continue
            output_features[index]["geometry"] = _geometry_for(
                feature, quantized[fid])

    report = {
        "precision": precision,
        "shared_boundary_segments": sum(
            1 for owners in edge_owners.values() if len(owners) > 1),
        "harmonized_edges": len(insertions),
        "groups": clean_groups + held_groups,
        "quantized_features": sorted(
            (fid for fid in quantized if fid not in held_fids),
            key=_feature_sort_key),
        "affected_features": _affected_report(held_groups, problems_by_fid),
    }
    return output, report


def _feature_sort_key(fid):
    return (0, fid) if isinstance(fid, int) else (1, str(fid))


def _geometry_for(feature, polygons):
    geometry_type = feature["geometry"]["type"]
    if geometry_type == "Polygon":
        coordinates = polygons[0]["rings"]
    else:
        coordinates = [polygons[index]["rings"]
                       for index in sorted(polygons)]
    return {"type": geometry_type, "coordinates": coordinates}


def _affected_report(held_groups, problems_by_fid):
    affected = []
    for group in held_groups:
        for fid in group["features"]:
            own_problems = problems_by_fid.get(fid, [])
            reasons = {problem["reason"] for problem in own_problems}
            if not reasons:
                reasons = {HELD_WITH_GROUP}
            affected.append({
                "id": fid,
                "reasons": sorted(reasons),
                "problems": own_problems,
            })
    affected.sort(key=lambda entry: _feature_sort_key(entry["id"]))
    return affected
