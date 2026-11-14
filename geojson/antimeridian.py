"""
Utilities for splitting geometries that cross the antimeridian.

A geometry that crosses the 180th meridian is stored with longitudes jumping
between positive and negative values.  When such a geometry is loaded into a
store that partitions data by longitude, a small parcel can be interpreted as
covering half of the globe.  The functions here cut every affected geometry at
the antimeridian and rewrite the pieces into a single unwrapped longitude
frame, so that all output coordinates are ordinary longitudes in the range
``[-180, 180]``.

The algorithm first unwraps every ring into a continuous longitude window (no
edge jumps by more than 180 degrees) and then clips the ring against the
vertical slab of each 360 degree wide frame using the Sutherland-Hodgman
algorithm.  Clipping is area preserving.  Holes are clipped independently and
reattached to the exterior piece that geometrically contains them, and a ring
that only touches the antimeridian (or runs along it and turns back) stays a
single ring and is therefore emitted exactly once.
"""

import math

_EARTH_SPAN = 360.0
_MIN_LON = -180.0
_MAX_LON = 180.0


def _interpolate(start, end, fraction):
    """Linearly interpolate every dimension of two positions."""
    return tuple(
        start[index] + (end[index] - start[index]) * fraction
        for index in range(len(start))
    )


def _with_longitude(position, longitude):
    """Return a position identical to ``position`` but for its longitude."""
    return (longitude,) + tuple(position[1:])


def _unwrap_positions(positions):
    """
    Rewrite positions so consecutive longitudes never differ by more than 180.

    The first position keeps its longitude; every following longitude is
    shifted by whole 360 degree turns to remove wrap-around jumps.  The
    result is a continuous path in an unwrapped longitude space.
    """
    unwrapped = [tuple(positions[0])]
    for position in positions[1:]:
        longitude = position[0]
        previous = unwrapped[-1][0]
        while longitude - previous > _MAX_LON:
            longitude -= _EARTH_SPAN
        while previous - longitude > _MAX_LON:
            longitude += _EARTH_SPAN
        unwrapped.append((longitude,) + tuple(position[1:]))
    return unwrapped


def _point_frame(longitude):
    """Frame index of a single longitude (frame 0 is the canonical frame)."""
    frame = math.floor((longitude + _MAX_LON) / _EARTH_SPAN)
    # A point exactly on a meridian boundary belongs to the lower frame, so
    # rings that merely touch the antimeridian are not split in two.
    if longitude >= _MAX_LON and (longitude - _MAX_LON) % _EARTH_SPAN == 0:
        frame -= 1
    return frame


def _frame_bounds(frame):
    lower = _MIN_LON + _EARTH_SPAN * frame
    upper = _MAX_LON + _EARTH_SPAN * frame
    return lower, upper


def _frame_span(positions):
    """Return the inclusive range of frames covered by unwrapped positions."""
    longitudes = [position[0] for position in positions]
    lowest = min(longitudes)
    highest = max(longitudes)
    first_frame = _point_frame(lowest)
    last_frame = _point_frame(highest)
    return first_frame, last_frame


def _clip_half_plane(ring, boundary, keep_greater):
    """Sutherland-Hodgman clipping of a closed ring against a vertical line."""
    if not ring:
        return []

    def inside(position):
        if keep_greater:
            return position[0] >= boundary
        return position[0] <= boundary

    output = []
    previous = ring[-1]
    previous_inside = inside(previous)
    for current in ring:
        current_inside = inside(current)
        if current_inside:
            if not previous_inside:
                fraction = ((boundary - previous[0])
                            / (current[0] - previous[0]))
                crossing = _interpolate(previous, current, fraction)
                output.append(_with_longitude(crossing, boundary))
            output.append(current)
        elif previous_inside:
            fraction = ((boundary - previous[0])
                        / (current[0] - previous[0]))
            crossing = _interpolate(previous, current, fraction)
            output.append(_with_longitude(crossing, boundary))
        previous = current
        previous_inside = current_inside
    return output


def _ring_area(ring):
    """Absolute shoelace area of a closed ring in planar coordinates."""
    area = 0.0
    for start, end in zip(ring, ring[1:]):
        area += start[0] * end[1] - end[0] * start[1]
    return abs(area) / 2.0


def _clip_ring_to_frames(ring):
    """
    Clip one closed ring into the longitude frames it covers.

    Returns a list of ``(frame, ring)`` pairs where every ring is expressed
    in the coordinates of its frame.
    """
    unwrapped = _unwrap_positions(ring)
    first_frame, last_frame = _frame_span(unwrapped)

    pieces = []
    for frame in range(first_frame, last_frame + 1):
        lower, upper = _frame_bounds(frame)
        clipped = _clip_half_plane(unwrapped, lower, keep_greater=True)
        clipped = _clip_half_plane(clipped, upper, keep_greater=False)
        if len(clipped) < 4 or _ring_area(clipped) == 0.0:
            continue
        if clipped[-1][:2] != clipped[0][:2]:
            clipped.append(clipped[0])
        pieces.append((frame, clipped))
    return pieces


def _shift_ring(ring, shift):
    if shift == 0:
        return [tuple(position) for position in ring]
    return [(position[0] + shift,) + tuple(position[1:])
            for position in ring]


def _to_canonical(ring, frame):
    return _shift_ring(ring, -_EARTH_SPAN * frame)


def _point_in_ring(longitude, latitude, ring):
    """Point-in-polygon test for a single closed linear ring."""
    inside = False
    for start, end in zip(ring, ring[1:]):
        start_lon, start_lat = start[0], start[1]
        end_lon, end_lat = end[0], end[1]
        crosses = (start_lat > latitude) != (end_lat > latitude)
        if crosses:
            crossing_lon = (
                start_lon
                + (latitude - start_lat) * (end_lon - start_lon)
                / (end_lat - start_lat))
            if longitude < crossing_lon:
                inside = not inside
    return inside


def _interior_probe(ring):
    """A point safely inside a ring, avoiding its boundary vertices."""
    longitudes = [position[0] for position in ring]
    latitudes = [position[1] for position in ring]
    center_longitude = (min(longitudes) + max(longitudes)) / 2.0
    center_latitude = (min(latitudes) + max(latitudes)) / 2.0
    if _point_in_ring(center_longitude, center_latitude, ring):
        return center_longitude, center_latitude
    # Degenerate center (on an edge): probe every non-boundary vertex.
    for position in ring:
        if _point_in_ring(position[0], position[1], ring):
            return position[0], position[1]
    return ring[0][0], ring[0][1]


def _split_polygon(polygon_coordinates):
    """
    Split a polygon at the antimeridian.

    Returns a list of polygon coordinate arrays in the canonical longitude
    frame.  Every hole fragment is attached to the exterior fragment that
    geometrically contains it; matching is done in the canonical frame rather
    than by frame index, because the exterior and each hole are unwrapped
    independently and identical frame numbers need not denote the same
    physical longitudes.
    """
    exterior_pieces = [
        _to_canonical(ring, frame)
        for frame, ring in _clip_ring_to_frames(polygon_coordinates[0])
    ]
    holes = [[] for _ in exterior_pieces]

    for hole in polygon_coordinates[1:]:
        for frame, hole_ring in _clip_ring_to_frames(hole):
            canonical_hole = _to_canonical(hole_ring, frame)
            probe_lon, probe_lat = _interior_probe(canonical_hole)
            target = None
            for index, exterior in enumerate(exterior_pieces):
                # Try the neighbouring frames too, since a hole sitting on
                # the seam shares a boundary longitude with its exterior.
                for shift in (0, _EARTH_SPAN, -_EARTH_SPAN):
                    if _point_in_ring(
                            probe_lon + shift, probe_lat, exterior):
                        target = index
                        break
                if target is not None:
                    break
            if target is None:
                # The probe lies on an edge: fall back to bounding boxes.
                target = _overlapping_exterior(
                    exterior_pieces, canonical_hole)
            holes[target].append(canonical_hole)

    return [
        [exterior] + piece_holes
        for exterior, piece_holes in zip(exterior_pieces, holes)
    ]


def _overlapping_exterior(exterior_pieces, hole):
    hole_lons = [position[0] for position in hole]
    hole_lats = [position[1] for position in hole]
    hole_box = (min(hole_lons), min(hole_lats),
                max(hole_lons), max(hole_lats))

    def overlaps(exterior):
        lons = [position[0] for position in exterior]
        lats = [position[1] for position in exterior]
        return not (
            max(lons) < hole_box[0] or min(lons) > hole_box[2]
            or max(lats) < hole_box[1] or min(lats) > hole_box[3])

    for index, exterior in enumerate(exterior_pieces):
        if overlaps(exterior):
            return index
    return 0


def _crosses_antimeridian(ring):
    unwrapped = _unwrap_positions(ring)
    first_frame, last_frame = _frame_span(unwrapped)
    return first_frame != last_frame


def _normalize_polygon(coordinates):
    """Shift every ring of a polygon into the canonical longitude frame."""
    unwrapped = _unwrap_positions(coordinates[0])
    first_frame, _ = _frame_span(unwrapped)
    if first_frame == 0:
        return coordinates
    shift = -_EARTH_SPAN * first_frame
    return [_shift_ring(ring, shift) for ring in coordinates]


def _split_line_string(positions):
    """Split a line string at frame boundaries and normalize every piece."""
    unwrapped = _unwrap_positions(positions)
    fragments = []
    current = [unwrapped[0]]
    current_frame = _point_frame(unwrapped[0][0])

    for position in unwrapped[1:]:
        frame = _point_frame(position[0])
        if frame == current_frame:
            current.append(position)
        else:
            # Close the outgoing piece and open the next one at the shared
            # meridian boundary.
            previous = current[-1]
            lower, upper = _frame_bounds(current_frame)
            boundary = upper if frame > current_frame else lower
            fraction = ((boundary - previous[0])
                        / (position[0] - previous[0]))
            crossing = _interpolate(previous, position, fraction)
            boundary_point = _with_longitude(crossing, boundary)
            current.append(boundary_point)
            fragments.append(
                _shift_ring(current, -_EARTH_SPAN * current_frame))
            current = [boundary_point, position]
            current_frame = frame

    fragments.append(_shift_ring(current, -_EARTH_SPAN * current_frame))
    return [fragment for fragment in fragments if len(fragment) >= 2]


def _wrap_position(position):
    longitude = ((position[0] + _MAX_LON) % _EARTH_SPAN) - _MAX_LON
    return (longitude,) + tuple(position[1:])


def _split_geometry(geometry):
    geometry_type = geometry["type"]

    if geometry_type == "Polygon":
        coordinates = geometry["coordinates"]
        if not coordinates or not _crosses_antimeridian(coordinates[0]):
            return {
                "type": "Polygon",
                "coordinates": _normalize_polygon(coordinates),
            }
        polygons = _split_polygon(coordinates)
        if len(polygons) == 1:
            return {"type": "Polygon", "coordinates": polygons[0]}
        return {"type": "MultiPolygon", "coordinates": polygons}

    if geometry_type == "MultiPolygon":
        polygons = []
        for polygon in geometry["coordinates"]:
            if not polygon or not _crosses_antimeridian(polygon[0]):
                polygons.append(_normalize_polygon(polygon))
            else:
                polygons.extend(_split_polygon(polygon))
        return {"type": "MultiPolygon", "coordinates": polygons}

    if geometry_type == "LineString":
        lines = _split_line_string(geometry["coordinates"])
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}

    if geometry_type == "MultiLineString":
        lines = []
        for line in geometry["coordinates"]:
            lines.extend(_split_line_string(line))
        return {"type": "MultiLineString", "coordinates": lines}

    if geometry_type == "Point":
        return {
            "type": "Point",
            "coordinates": _wrap_position(geometry["coordinates"]),
        }

    if geometry_type == "MultiPoint":
        return {
            "type": "MultiPoint",
            "coordinates": [
                _wrap_position(position)
                for position in geometry["coordinates"]],
        }

    if geometry_type == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [
                _split_geometry(child) if child else None
                for child in geometry["geometries"]],
        }

    raise ValueError(f"Invalid geometry object {geometry!r}")


def _split_feature(feature):
    geometry = feature.get("geometry")
    if geometry is None:
        return dict(feature)

    split_geometry = _split_geometry(geometry)
    geometry_type = geometry["type"]
    split_type = split_geometry["type"]

    # A single polygon or line string that crossed the meridian is now a
    # collection of pieces.  Emit one feature per piece so that every
    # resulting feature lives in a single longitude partition; every piece
    # keeps the original properties, identifier and foreign members.
    if (geometry_type in ("Polygon", "LineString")
            and split_type in ("MultiPolygon", "MultiLineString")
            and len(split_geometry["coordinates"]) > 1):
        single_type = (
            "Polygon" if split_type == "MultiPolygon" else "LineString")
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    **feature,
                    "geometry": {
                        "type": single_type,
                        "coordinates": coordinates,
                    },
                }
                for coordinates in split_geometry["coordinates"]
            ],
        }

    return {**feature, "geometry": split_geometry}


def split_antimeridian(obj):
    """
    Split geometries that cross the 180th meridian.

    Polygons and line strings are cut at the antimeridian and the resulting
    pieces are rewritten with longitudes in the canonical ``[-180, 180]``
    range.  Polygon holes are kept attached to the exterior piece that
    contains them, and feature properties, identifiers and foreign members are
    preserved.  A feature whose polygon or line string crosses the meridian
    becomes a feature collection holding one feature per resulting piece.
    Geometries that do not cross the meridian are only normalized (longitudes
    expressed in an unwrapped frame are shifted back into the canonical
    frame) and otherwise returned unchanged.

    :param obj: A GeoJSON geometry, feature or collection of features.
    :type obj: GeoJSON
    :return: A new GeoJSON object whose coordinates never cross the
    antimeridian.
    :rtype: dict
    :raises ValueError: if the provided object is not GeoJSON.
    """
    object_type = obj["type"]

    if object_type == "Feature":
        return _split_feature(obj)

    if object_type == "FeatureCollection":
        features = []
        for feature in obj["features"]:
            split = split_antimeridian(feature)
            if split["type"] == "FeatureCollection":
                features.extend(split["features"])
            else:
                features.append(split)
        return {**obj, "features": features}

    if object_type == "GeometryCollection":
        return _split_geometry(obj)

    simple_types = (
        "Point", "MultiPoint",
        "LineString", "MultiLineString",
        "Polygon", "MultiPolygon",
    )
    if object_type in simple_types:
        return _split_geometry(obj)

    raise ValueError(f"Invalid GeoJSON object {obj!r}")
