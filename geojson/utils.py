"""Coordinate utility functions."""

_COVERAGE_EPS = 1e-9


def _normalize_longitude(lon):
    """Normalizes a longitude to the [-180, 180) range."""
    return ((lon + 180.0) % 360.0) - 180.0


def _leaf_geometries(obj):
    """Yields the leaf (non-collection) geometries of any GeoJSON object."""
    obj_type = obj.get('type')
    if obj_type == 'FeatureCollection':
        for feature in obj['features']:
            yield from _leaf_geometries(feature)
    elif obj_type == 'Feature':
        geometry = obj.get('geometry')
        if geometry is not None:
            yield from _leaf_geometries(geometry)
    elif obj_type == 'GeometryCollection':
        for geometry in obj['geometries']:
            if geometry is not None:
                yield from _leaf_geometries(geometry)
    else:
        yield obj


def coverage(objects):
    """
    Computes the minimal longitude/latitude coverage of a batch of GeoJSON
    objects (Features, GeometryCollections, or any other GeoJSON), and
    reports which source coordinates touch the resulting boundary.

    Longitude is treated as circular: the coverage is the shortest arc
    containing all longitudes, which may cross the antimeridian (or zero)
    and be shorter than a plain min/max interval. If several shortest arcs
    are equally long, all of them are reported.

    Empty geometries (null geometries, empty coordinate arrays, empty
    collections) are kept in the source list but do not expand the
    boundary.

    :param objects: An iterable of GeoJSON objects, or a single object.
    :type objects: iterable or Feature, GeometryCollection, ...
    :return: A dict with the following keys:

        - ``sources``: one entry per input object, ``{'index', 'type',
          'empty'}``. Empty sources contribute no coordinates.
        - ``longitude``: ``{'span', 'arcs'}`` where ``span`` is the
          shortest arc length in degrees (``None`` if no coordinates) and
          ``arcs`` lists every equally short arc as ``{'start', 'end',
          'span', 'crosses_antimeridian', 'west', 'east'}``. ``start`` and
          ``end`` are normalized to [-180, 180); the arc runs eastward
          from ``start`` to ``end``. ``west``/``east`` list the source
          coordinates sitting on the arc endpoints.
        - ``latitude``: ``{'min', 'max', 'south', 'north'}`` with the
          source coordinates reaching each bound.
        - ``altitude``: ``None`` if no coordinate carries an altitude,
          otherwise ``{'min', 'max', 'coordinate_count', 'geometries'}``
          where ``geometries`` identifies the sub-geometries (by source
          and geometry index) that actually carry altitude values.

        Boundary coordinates are reported as ``{'source', 'geometry',
        'coordinate'}`` references.
    :rtype: dict
    """
    if isinstance(objects, dict):
        objects = [objects]

    sources = []
    points = []  # (source_index, geometry_index, coordinate)
    geometry_types = {}  # (source_index, geometry_index) -> geometry type

    for source_index, obj in enumerate(objects):
        before = len(points)
        for geometry_index, geometry in enumerate(_leaf_geometries(obj)):
            geometry_types[(source_index, geometry_index)] = geometry.get('type')
            for coordinate in coords(geometry):
                points.append((source_index, geometry_index, tuple(coordinate)))
        sources.append({
            'index': source_index,
            'type': obj.get('type'),
            'empty': len(points) == before,
        })

    def reference(point):
        source_index, geometry_index, coordinate = point
        return {
            'source': source_index,
            'geometry': geometry_index,
            'coordinate': coordinate,
        }

    # Latitude is a plain interval.
    if points:
        lat_min = min(p[2][1] for p in points)
        lat_max = max(p[2][1] for p in points)
        latitude = {
            'min': lat_min,
            'max': lat_max,
            'south': [reference(p) for p in points if p[2][1] == lat_min],
            'north': [reference(p) for p in points if p[2][1] == lat_max],
        }
    else:
        latitude = {'min': None, 'max': None, 'south': [], 'north': []}

    # Longitude is circular: the minimal covering arc is the complement of
    # the largest gap between consecutive longitudes on the circle.
    arcs = []
    span = None
    if points:
        circle = sorted({p[2][0] % 360.0 for p in points})
        gaps = []
        for i, lon in enumerate(circle):
            nxt = circle[(i + 1) % len(circle)]
            if i == len(circle) - 1:
                nxt += 360.0
            gaps.append((nxt - lon, lon, nxt))
        max_gap = max(gap for gap, _, _ in gaps)
        span = 360.0 - max_gap
        for gap, gap_start, gap_end in gaps:
            if abs(gap - max_gap) > _COVERAGE_EPS:
                continue
            start = _normalize_longitude(gap_end)
            end = _normalize_longitude(gap_start)
            arcs.append({
                'start': start,
                'end': end,
                'span': span,
                'crosses_antimeridian': start > end,
                'west': [reference(p) for p in points
                         if abs(_normalize_longitude(p[2][0]) - start)
                         <= _COVERAGE_EPS],
                'east': [reference(p) for p in points
                         if abs(_normalize_longitude(p[2][0]) - end)
                         <= _COVERAGE_EPS],
            })

    # Altitude statistics only cover the coordinates that actually carry
    # an altitude value.
    alt_points = [p for p in points if len(p[2]) > 2]
    if alt_points:
        altitudes = [p[2][2] for p in alt_points]
        altitude = {
            'min': min(altitudes),
            'max': max(altitudes),
            'coordinate_count': len(alt_points),
            'geometries': [
                {
                    'source': source_index,
                    'geometry': geometry_index,
                    'type': geometry_types[(source_index, geometry_index)],
                }
                for source_index, geometry_index
                in sorted({(p[0], p[1]) for p in alt_points})
            ],
        }
    else:
        altitude = None

    return {
        'sources': sources,
        'longitude': {'span': span, 'arcs': arcs},
        'latitude': latitude,
        'altitude': altitude,
    }


def coords(obj):
    """
    Yields the coordinates from a Feature or Geometry.

    :param obj: A geometry or feature to extract the coordinates from.
    :type obj: Feature, Geometry
    :return: A generator with coordinate tuples from the geometry or feature.
    :rtype: generator
    """
    # Handle recursive case first
    if 'features' in obj:  # FeatureCollection
        for f in obj['features']:
            yield from coords(f)
    elif 'geometry' in obj:  # Feature
        yield from coords(obj['geometry'])
    elif 'geometries' in obj:  # GeometryCollection
        for g in obj['geometries']:
            yield from coords(g)
    else:
        if isinstance(obj, (tuple, list)):
            coordinates = obj
        else:
            coordinates = obj.get('coordinates', obj)
        for e in coordinates:
            if isinstance(e, (float, int)):
                yield tuple(coordinates)
                break
            for f in coords(e):
                yield f


def map_coords(func, obj):
    """
    Returns the mapped coordinates from a Geometry after applying the provided
    function to each dimension in tuples list (ie, linear scaling).

    :param func: Function to apply to individual coordinate values
    independently
    :type func: function
    :param obj: A geometry or feature to extract the coordinates from.
    :type obj: Point, LineString, MultiPoint, MultiLineString, Polygon,
    MultiPolygon
    :return: The result of applying the function to each dimension in the
    array.
    :rtype: list
    :raises ValueError: if the provided object is not GeoJSON.
    """

    def tuple_func(coord):
        return (func(coord[0]), func(coord[1]))

    return map_tuples(tuple_func, obj)


def map_tuples(func, obj):
    """
    Returns the mapped coordinates from a Geometry after applying the provided
    function to each coordinate.

    :param func: Function to apply to tuples
    :type func: function
    :param obj: A geometry or feature to extract the coordinates from.
    :type obj: Point, LineString, MultiPoint, MultiLineString, Polygon,
    MultiPolygon
    :return: The result of applying the function to each dimension in the
    array.
    :rtype: list
    :raises ValueError: if the provided object is not GeoJSON.
    """

    if obj['type'] == 'Point':
        coordinates = tuple(func(obj['coordinates']))
    elif obj['type'] in ['LineString', 'MultiPoint']:
        coordinates = [tuple(func(c)) for c in obj['coordinates']]
    elif obj['type'] in ['MultiLineString', 'Polygon']:
        coordinates = [[
            tuple(func(c)) for c in curve]
            for curve in obj['coordinates']]
    elif obj['type'] == 'MultiPolygon':
        coordinates = [[[
            tuple(func(c)) for c in curve]
            for curve in part]
            for part in obj['coordinates']]
    elif obj['type'] in ['Feature', 'FeatureCollection', 'GeometryCollection']:
        return map_geometries(lambda g: map_tuples(func, g), obj)
    else:
        raise ValueError(f"Invalid geometry object {obj!r}")
    return {'type': obj['type'], 'coordinates': coordinates}


def map_geometries(func, obj):
    """
    Returns the result of passing every geometry in the given geojson object
    through func.

    :param func: Function to apply to tuples
    :type func: function
    :param obj: A geometry or feature to extract the coordinates from.
    :type obj: GeoJSON
    :return: The result of applying the function to each geometry
    :rtype: list
    :raises ValueError: if the provided object is not geojson.
    """
    simple_types = [
        'Point',
        'LineString',
        'MultiPoint',
        'MultiLineString',
        'Polygon',
        'MultiPolygon',
    ]

    if obj['type'] in simple_types:
        return func(obj)
    elif obj['type'] == 'GeometryCollection':
        geoms = [func(geom) if geom else None for geom in obj['geometries']]
        return {'type': obj['type'], 'geometries': geoms}
    elif obj['type'] == 'Feature':
        obj['geometry'] = func(obj['geometry']) if obj['geometry'] else None
        return obj
    elif obj['type'] == 'FeatureCollection':
        feats = [map_geometries(func, feat) for feat in obj['features']]
        return {'type': obj['type'], 'features': feats}
    else:
        raise ValueError(f"Invalid GeoJSON object {obj!r}")


def generate_random(featureType, numberVertices=3,
                    boundingBox=[-180.0, -90.0, 180.0, 90.0]):
    """
    Generates random geojson features depending on the parameters
    passed through.
    The bounding box defaults to the world - [-180.0, -90.0, 180.0, 90.0].
    The number of vertices defaults to 3.

    :param featureType: A geometry type
    :type featureType: Point, LineString, Polygon
    :param numberVertices: The number vertices that a linestring or polygon
    will have
    :type numberVertices: int
    :param boundingBox: A bounding box in which features will be restricted to
    :type boundingBox: list
    :return: The resulting random geojson object or geometry collection.
    :rtype: object
    :raises ValueError: if there is no featureType provided.
    """

    from geojson import Point, LineString, Polygon
    import random
    import math

    lon_min, lat_min, lon_max, lat_max = boundingBox

    def random_lon():
        return random.uniform(lon_min, lon_max)

    def random_lat():
        return random.uniform(lat_min, lat_max)

    def create_point():
        return Point((random_lon(), random_lat()))

    def create_line():
        return LineString([create_point() for _ in range(numberVertices)])

    def create_poly():
        ave_radius = 60
        ctr_x = 0.1
        ctr_y = 0.2
        irregularity = clip(0.1, 0, 1) * math.tau / numberVertices
        spikeyness = clip(0.5, 0, 1) * ave_radius

        lower = (math.tau / numberVertices) - irregularity
        upper = (math.tau / numberVertices) + irregularity
        angle_steps = []
        for _ in range(numberVertices):
            angle_steps.append(random.uniform(lower, upper))
        sum_angle = sum(angle_steps)

        k = sum_angle / math.tau
        angle_steps = [x / k for x in angle_steps]

        points = []
        angle = random.uniform(0, math.tau)

        for angle_step in angle_steps:
            r_i = clip(random.gauss(ave_radius, spikeyness), 0, 2 * ave_radius)
            x = ctr_x + r_i * math.cos(angle)
            y = ctr_y + r_i * math.sin(angle)
            x = (x + 180.0) * (abs(lon_min - lon_max) / 360.0) + lon_min
            y = (y + 90.0) * (abs(lat_min - lat_max) / 180.0) + lat_min
            x = clip(x, lon_min, lon_max)
            y = clip(y, lat_min, lat_max)
            points.append((x, y))
            angle += angle_step

        points.append(points[0])  # append first point to the end
        return Polygon([points])

    def clip(x, min_val, max_val):
        if min_val > max_val:
            return x
        else:
            return min(max(min_val, x), max_val)

    if featureType == 'Point':
        return create_point()

    if featureType == 'LineString':
        return create_line()

    if featureType == 'Polygon':
        return create_poly()

    raise ValueError(f"featureType: {featureType} is not supported.")
