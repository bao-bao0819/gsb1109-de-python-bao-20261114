"""
Tests for antimeridian splitting.
"""

import math
import unittest

import geojson
from geojson.antimeridian import split_antimeridian


def _ring_area(ring):
    area = 0.0
    for start, end in zip(ring, ring[1:]):
        area += start[0] * end[1] - end[0] * start[1]
    return abs(area) / 2.0


def _unwrap_longitudes(ring):
    """Rewrite a ring so consecutive edges never jump more than 180 degrees."""
    unwrapped = [ring[0]]
    for position in ring[1:]:
        longitude = position[0]
        previous = unwrapped[-1][0]
        while longitude - previous > 180.0:
            longitude -= 360.0
        while previous - longitude > 180.0:
            longitude += 360.0
        unwrapped.append((longitude,) + tuple(position[1:]))
    return unwrapped


def _polygon_area(polygon):
    exterior, *holes = polygon
    area = _ring_area(_unwrap_longitudes(exterior))
    for hole in holes:
        area -= _ring_area(_unwrap_longitudes(hole))
    return area


def _all_longitudes(obj):
    return [position[0] for position in geojson.utils.coords(obj)]


def _assert_canonical_longitudes(test_case, obj):
    longitudes = _all_longitudes(obj)
    test_case.assertTrue(
        all(-180.0 <= lon <= 180.0 for lon in longitudes))


def _all_polygons(obj):
    geometry = obj["geometry"] if obj["type"] == "Feature" else obj
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "MultiPolygon":
        return [polygon for polygon in geometry["coordinates"]]
    raise AssertionError("expected a polygon geometry")


CROSSING_RECTANGLE = [
    (170.0, -10.0), (-170.0, -10.0),
    (-170.0, 10.0), (170.0, 10.0),
    (170.0, -10.0),
]

CROSSING_HOLE = [
    (175.0, -5.0), (-175.0, -5.0),
    (-175.0, 5.0), (175.0, 5.0),
    (175.0, -5.0),
]


class SplitPolygonTests(unittest.TestCase):
    def test_rectangle_crossing_antimeridian(self):
        polygon = geojson.Polygon([CROSSING_RECTANGLE])
        result = split_antimeridian(polygon)

        self.assertEqual(result["type"], "MultiPolygon")
        self.assertEqual(len(result["coordinates"]), 2)
        for piece in result["coordinates"]:
            self.assertEqual(len(piece), 1)
            ring = piece[0]
            self.assertEqual(ring[0], ring[-1])
            self.assertGreaterEqual(_ring_area(ring), 0.0)

        _assert_canonical_longitudes(self, result)

        total_area = sum(_polygon_area(piece)
                         for piece in result["coordinates"])
        self.assertAlmostEqual(total_area, _polygon_area(polygon["coordinates"]))

    def test_single_crossing_polygon_keeps_polygon_type(self):
        ring = [
            (170.0, 0.0), (180.0, 0.0),
            (180.0, 10.0), (170.0, 10.0),
            (170.0, 0.0),
        ]
        result = split_antimeridian(geojson.Polygon([ring]))
        self.assertEqual(result["type"], "Polygon")
        self.assertEqual(
            [list(p) for p in result["coordinates"][0]],
            [list(p) for p in ring])

    def test_hole_crossing_antimeridian_follows_exterior(self):
        polygon = geojson.Polygon([CROSSING_RECTANGLE, CROSSING_HOLE])
        result = split_antimeridian(polygon)

        self.assertEqual(result["type"], "MultiPolygon")
        self.assertEqual(len(result["coordinates"]), 2)
        for piece in result["coordinates"]:
            self.assertEqual(len(piece), 2)
            exterior, hole = piece
            self.assertGreater(_ring_area(exterior), _ring_area(hole))

        total_area = sum(_polygon_area(piece)
                         for piece in result["coordinates"])
        self.assertAlmostEqual(
            total_area, _polygon_area(polygon["coordinates"]))

    def test_same_side_hole_matches_spatially_not_by_frame(self):
        # The hole stays on the positive side of the seam.  Because the hole
        # is unwrapped independently, its own frame number must not decide
        # which exterior piece receives it: only spatial containment does.
        exterior = [
            (-170.0, -10.0), (174.0, -10.0),
            (174.0, 10.0), (-170.0, 10.0),
            (-170.0, -10.0),
        ]
        hole = [
            (178.0, -4.0), (179.0, -4.0),
            (179.0, 4.0), (178.0, 4.0),
            (178.0, -4.0),
        ]
        result = split_antimeridian(
            geojson.Polygon([exterior, hole]))

        self.assertEqual(result["type"], "MultiPolygon")
        positive = next(
            piece for piece in result["coordinates"]
            if any(point[0] > 0.0 for ring in piece for point in ring))
        negative = next(
            piece for piece in result["coordinates"]
            if all(point[0] <= 0.0 for ring in piece for point in ring))
        self.assertEqual(len(positive), 2)
        self.assertEqual(len(negative), 1)

    def test_elevation_is_interpolated_and_preserved(self):
        polygon = geojson.Polygon([[
            (170.0, -10.0, 100.0), (-170.0, -10.0, 200.0),
            (-170.0, 10.0, 200.0), (170.0, 10.0, 100.0),
            (170.0, -10.0, 100.0),
        ]])
        result = split_antimeridian(polygon)

        for piece in result["coordinates"]:
            for ring in piece:
                for position in ring:
                    self.assertEqual(len(position), 3)
        seam_positions = [
            position for piece in result["coordinates"]
            for ring in piece for position in ring
            if position[0] in (-180.0, 180.0)]
        self.assertTrue(seam_positions)
        for position in seam_positions:
            self.assertAlmostEqual(position[2], 150.0)

    def test_ring_running_along_meridian_is_emitted_once(self):
        ring = [
            (170.0, -10.0), (180.0, -10.0),
            (180.0, 10.0), (170.0, 10.0),
            (170.0, -10.0),
        ]
        result = split_antimeridian(geojson.Polygon([ring]))
        self.assertEqual(result["type"], "Polygon")
        self.assertEqual(len(result["coordinates"]), 1)
        self.assertEqual(
            [list(p) for p in result["coordinates"][0]],
            [list(p) for p in ring])

    def test_ring_merely_touching_meridian_is_unchanged(self):
        ring = [
            (160.0, -10.0), (180.0, 0.0),
            (160.0, 10.0), (150.0, 0.0),
            (160.0, -10.0),
        ]
        result = split_antimeridian(geojson.Polygon([ring]))
        self.assertEqual(result["type"], "Polygon")
        self.assertEqual(
            [list(p) for p in result["coordinates"][0]],
            [list(p) for p in ring])

    def test_unwrapped_coordinates_are_split_and_normalized(self):
        ring = [
            (170.0, -10.0), (190.0, -10.0),
            (190.0, 10.0), (170.0, 10.0),
            (170.0, -10.0),
        ]
        polygon = geojson.Polygon([ring])
        result = split_antimeridian(polygon)

        self.assertEqual(result["type"], "MultiPolygon")
        _assert_canonical_longitudes(self, result)
        total_area = sum(_polygon_area(piece)
                         for piece in result["coordinates"])
        self.assertAlmostEqual(total_area, _polygon_area([ring]))

    def test_polygon_crossing_meridian_twice_keeps_area(self):
        ring = [
            (170.0, -10.0), (-170.0, -10.0), (-170.0, -5.0),
            (175.0, -5.0), (175.0, 5.0), (-170.0, 5.0),
            (-170.0, 10.0), (170.0, 10.0), (170.0, -10.0),
        ]
        polygon = geojson.Polygon([ring])
        result = split_antimeridian(polygon)
        total_area = sum(_polygon_area(piece)
                         for piece in _all_polygons(result))
        self.assertAlmostEqual(total_area, _polygon_area([ring]))
        _assert_canonical_longitudes(self, result)

    def test_split_is_idempotent(self):
        polygon = geojson.Polygon([CROSSING_RECTANGLE, CROSSING_HOLE])
        once = split_antimeridian(polygon)
        twice = split_antimeridian(once)
        self.assertEqual(once, twice)


class SplitMultiGeometryTests(unittest.TestCase):
    def test_multi_polygon_keeps_type(self):
        other = [
            (0.0, 0.0), (10.0, 0.0),
            (10.0, 10.0), (0.0, 10.0),
            (0.0, 0.0),
        ]
        multi = geojson.MultiPolygon(
            [[CROSSING_RECTANGLE], [other]])
        result = split_antimeridian(multi)

        self.assertEqual(result["type"], "MultiPolygon")
        self.assertEqual(len(result["coordinates"]), 3)
        total_area = sum(_polygon_area(piece)
                         for piece in result["coordinates"])
        expected = (_polygon_area([CROSSING_RECTANGLE])
                    + _polygon_area([other]))
        self.assertAlmostEqual(total_area, expected)

    def test_line_string_crossing_antimeridian(self):
        line = geojson.LineString([(170.0, 0.0), (-170.0, 10.0)])
        result = split_antimeridian(line)

        self.assertEqual(result["type"], "MultiLineString")
        self.assertEqual(len(result["coordinates"]), 2)
        right, left = result["coordinates"]
        self.assertEqual(right[0], (170.0, 0.0))
        self.assertEqual(right[-1][0], 180.0)
        self.assertEqual(left[0][0], -180.0)
        self.assertEqual(left[-1], (-170.0, 10.0))
        self.assertAlmostEqual(right[-1][1], left[0][1])

    def test_multi_line_string_keeps_type(self):
        multi = geojson.MultiLineString(
            [[(170.0, 0.0), (-170.0, 10.0)], [(0.0, 0.0), (1.0, 1.0)]])
        result = split_antimeridian(multi)

        self.assertEqual(result["type"], "MultiLineString")
        self.assertEqual(len(result["coordinates"]), 3)

    def test_split_line_keeps_total_length(self):
        def length(positions):
            total = 0.0
            for start, end in zip(positions, positions[1:]):
                total += math.hypot(end[0] - start[0], end[1] - start[1])
            return total

        # In the unwrapped frame the crossing edge runs from 170 to 190.
        original = length([(170.0, 0.0), (190.0, 10.0)])
        result = split_antimeridian(
            geojson.LineString([(170.0, 0.0), (-170.0, 10.0)]))
        split_length = sum(length(line) for line in result["coordinates"])
        self.assertAlmostEqual(split_length, original)

    def test_point_longitudes_are_wrapped(self):
        result = split_antimeridian(geojson.Point((200.0, 45.0)))
        self.assertEqual(result["coordinates"], (-160.0, 45.0))

        multi = split_antimeridian(
            geojson.MultiPoint([(200.0, 45.0), (-200.0, -45.0)]))
        self.assertEqual(
            multi["coordinates"], [(-160.0, 45.0), (160.0, -45.0)])


class SplitFeatureTests(unittest.TestCase):
    def test_feature_properties_are_preserved_on_every_piece(self):
        feature = geojson.Feature(
            id="fid-1",
            geometry=geojson.Polygon([CROSSING_RECTANGLE]),
            properties={"name": "crossing parcel", "area": 400},
        )
        result = split_antimeridian(feature)

        self.assertEqual(result["type"], "FeatureCollection")
        self.assertEqual(len(result["features"]), 2)
        for piece in result["features"]:
            self.assertEqual(piece["properties"], feature["properties"])
            self.assertEqual(piece["id"], "fid-1")
            self.assertEqual(piece["geometry"]["type"], "Polygon")

    def test_feature_without_crossing_remains_a_feature(self):
        ring = [
            (0.0, 0.0), (10.0, 0.0),
            (10.0, 10.0), (0.0, 10.0),
            (0.0, 0.0),
        ]
        feature = geojson.Feature(geometry=geojson.Polygon([ring]))
        result = split_antimeridian(feature)

        self.assertEqual(result["type"], "Feature")
        self.assertEqual(
            [list(p) for p in result["geometry"]["coordinates"][0]],
            [list(p) for p in ring])

    def test_feature_collection_is_split_feature_by_feature(self):
        collection = geojson.FeatureCollection([
            geojson.Feature(
                geometry=geojson.Polygon([CROSSING_RECTANGLE]),
                properties={"name": "first"}),
            geojson.Feature(
                geometry=geojson.Polygon([[
                    (0.0, 0.0), (1.0, 0.0),
                    (1.0, 1.0), (0.0, 1.0),
                    (0.0, 0.0),
                ]]),
                properties={"name": "second"}),
        ])
        result = split_antimeridian(collection)

        self.assertEqual(result["type"], "FeatureCollection")
        self.assertEqual(len(result["features"]), 3)
        self.assertEqual(result["features"][0]["properties"]["name"], "first")
        self.assertEqual(result["features"][1]["properties"]["name"], "first")
        self.assertEqual(result["features"][2]["properties"]["name"], "second")

    def test_null_geometry_feature_is_preserved(self):
        feature = geojson.Feature(
            geometry=None, properties={"name": "empty"})
        result = split_antimeridian(feature)
        self.assertIsNone(result["geometry"])
        self.assertEqual(result["properties"], {"name": "empty"})

    def test_geometry_collection(self):
        collection = geojson.GeometryCollection([
            geojson.Polygon([CROSSING_RECTANGLE]),
            geojson.Point((200.0, 10.0)),
        ])
        result = split_antimeridian(collection)

        self.assertEqual(result["type"], "GeometryCollection")
        polygon, point = result["geometries"]
        self.assertEqual(polygon["type"], "MultiPolygon")
        self.assertEqual(point["coordinates"], (-160.0, 10.0))


class ErrorHandlingTests(unittest.TestCase):
    def test_invalid_object_raises_value_error(self):
        with self.assertRaises(ValueError):
            split_antimeridian({"type": "NotAGeoJsonType"})


if __name__ == "__main__":
    unittest.main()
