"""
Tests for geojson generation
"""

import random
import unittest

import geojson
from geojson.utils import coverage, generate_random, map_geometries


def generate_bbox():
    min_lat = random.random() * 180.0 - 90.0
    max_lat = random.random() * 180.0 - 90.0
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    min_lon = random.random() * 360.0 - 180.0
    max_lon = random.random() * 360.0 - 180.0
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon
    return [min_lon, min_lat, max_lon, max_lat]


def check_polygon_bbox(polygon, bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    eps = 1e-3
    for linear_ring in polygon['coordinates']:
        for coordinate in linear_ring:
            if not (min_lon-eps <= coordinate[0] <= max_lon+eps
                    and min_lat-eps <= coordinate[1] <= max_lat+eps):
                return False
    return True


def check_point_bbox(point, bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    eps = 1e-3
    if (
        min_lon - eps <= point["coordinates"][0] <= max_lon + eps
        and min_lat - eps <= point["coordinates"][1] <= max_lat + eps
    ):
        return True
    return False


class TestGenerateRandom(unittest.TestCase):
    def test_simple_polygon(self):
        for _ in range(5000):
            bbox = [-180.0, -90.0, 180.0, 90.0]
            result = generate_random('Polygon')
            self.assertIsInstance(result, geojson.geometry.Polygon)
            self.assertTrue(geojson.geometry.check_polygon(result))
            self.assertTrue(check_polygon_bbox(result, bbox))

    def test_bbox_polygon(self):
        for _ in range(5000):
            bbox = generate_bbox()
            result = generate_random('Polygon', boundingBox=bbox)
            self.assertIsInstance(result, geojson.geometry.Polygon)
            self.assertTrue(geojson.geometry.check_polygon(result))
            self.assertTrue(check_polygon_bbox(result, bbox))

    def test_bbox_point(self):
        for _ in range(5000):
            bbox = generate_bbox()
            result = generate_random("Point", boundingBox=bbox)
            self.assertIsInstance(result, geojson.geometry.Point)
            self.assertTrue(geojson.geometry.check_point(result))
            self.assertTrue(check_point_bbox(result, bbox))

    def test_raise_value_error(self):
        with self.assertRaises(ValueError):
            generate_random("MultiPolygon")


class TestMapGeometries(unittest.TestCase):
    def test_with_simple_type(self):
        new_point = map_geometries(
            lambda g: geojson.MultiPoint([g["coordinates"]]),
            geojson.Point((-115.81, 37.24)),
        )
        self.assertEqual(new_point, geojson.MultiPoint([(-115.81, 37.24)]))

    def test_with_feature_collection(self):
        new_point = map_geometries(
            lambda g: geojson.MultiPoint([g["coordinates"]]),
            geojson.FeatureCollection([geojson.Point((-115.81, 37.24))]),
        )
        self.assertEqual(
            new_point,
            geojson.FeatureCollection(
                [geojson.MultiPoint([geojson.Point((-115.81, 37.24))])]
            ),
        )

    def test_raise_value_error(self):
        invalid_object = geojson.Feature(type="InvalidType")
        with self.assertRaises(ValueError):
            map_geometries(lambda g: g, invalid_object)


class TestCoverage(unittest.TestCase):
    def test_simple_batch(self):
        objects = [
            geojson.Feature(geometry=geojson.Point((10.0, 20.0))),
            geojson.Feature(geometry=geojson.Point((30.0, -5.0))),
        ]
        result = coverage(objects)
        self.assertEqual(result['longitude']['span'], 20.0)
        self.assertEqual(len(result['longitude']['arcs']), 1)
        arc = result['longitude']['arcs'][0]
        self.assertEqual(arc['start'], 10.0)
        self.assertEqual(arc['end'], 30.0)
        self.assertFalse(arc['crosses_antimeridian'])
        self.assertEqual(result['latitude']['min'], -5.0)
        self.assertEqual(result['latitude']['max'], 20.0)
        self.assertIsNone(result['altitude'])
        self.assertEqual([s['empty'] for s in result['sources']],
                         [False, False])
        # boundary coordinates point back at their sources
        self.assertEqual([r['source'] for r in arc['west']], [0])
        self.assertEqual([r['source'] for r in arc['east']], [1])
        self.assertEqual(arc['west'][0]['coordinate'], (10.0, 20.0))
        self.assertEqual([r['source'] for r in result['latitude']['north']],
                         [0])
        self.assertEqual([r['source'] for r in result['latitude']['south']],
                         [1])

    def test_antimeridian_crossing(self):
        objects = [
            geojson.GeometryCollection([
                geojson.Point((170.0, 0.0)),
                geojson.Point((-170.0, 5.0)),
            ]),
        ]
        result = coverage(objects)
        self.assertEqual(result['longitude']['span'], 20.0)
        arc = result['longitude']['arcs'][0]
        self.assertEqual(arc['start'], 170.0)
        self.assertEqual(arc['end'], -170.0)
        self.assertTrue(arc['crosses_antimeridian'])
        self.assertEqual([r['coordinate'] for r in arc['west']],
                         [(170.0, 0.0)])
        self.assertEqual([r['coordinate'] for r in arc['east']],
                         [(-170.0, 5.0)])

    def test_multiple_equal_shortest_arcs(self):
        objects = [
            geojson.Feature(geometry=geojson.Point((0.0, 0.0))),
            geojson.Feature(geometry=geojson.Point((120.0, 0.0))),
            geojson.Feature(geometry=geojson.Point((-120.0, 0.0))),
        ]
        result = coverage(objects)
        self.assertEqual(result['longitude']['span'], 240.0)
        arcs = result['longitude']['arcs']
        self.assertEqual(len(arcs), 3)
        for arc in arcs:
            self.assertEqual(arc['span'], 240.0)
        self.assertEqual(
            {(arc['start'], arc['end']) for arc in arcs},
            {(120.0, 0.0), (-120.0, 120.0), (0.0, -120.0)},
        )

    def test_empty_geometries_stay_in_sources(self):
        objects = [
            geojson.Feature(geometry=None),
            geojson.GeometryCollection([]),
            {'type': 'Point', 'coordinates': []},
            geojson.Feature(geometry=geojson.Point((1.0, 2.0))),
        ]
        result = coverage(objects)
        self.assertEqual([s['empty'] for s in result['sources']],
                         [True, True, True, False])
        self.assertEqual([s['index'] for s in result['sources']],
                         [0, 1, 2, 3])
        # empty geometries do not expand the boundary
        self.assertEqual(result['longitude']['span'], 0.0)
        self.assertEqual(result['latitude']['min'], 2.0)
        self.assertEqual(result['latitude']['max'], 2.0)

    def test_all_empty(self):
        result = coverage([geojson.Feature(geometry=None)])
        self.assertIsNone(result['longitude']['span'])
        self.assertEqual(result['longitude']['arcs'], [])
        self.assertIsNone(result['latitude']['min'])
        self.assertIsNone(result['altitude'])

    def test_partial_altitude_reports_covered_subgeometries(self):
        objects = [
            geojson.GeometryCollection([
                geojson.Point((0.0, 0.0, 100.0)),
                geojson.Point((10.0, 10.0)),
            ]),
            geojson.Feature(geometry=geojson.LineString([
                (20.0, 0.0, 50.0),
                (30.0, 1.0, 250.0),
            ])),
        ]
        result = coverage(objects)
        altitude = result['altitude']
        self.assertEqual(altitude['min'], 50.0)
        self.assertEqual(altitude['max'], 250.0)
        self.assertEqual(altitude['coordinate_count'], 3)
        self.assertEqual(
            altitude['geometries'],
            [
                {'source': 0, 'geometry': 0, 'type': 'Point'},
                {'source': 1, 'geometry': 0, 'type': 'LineString'},
            ],
        )

    def test_single_object_input(self):
        result = coverage(geojson.Feature(geometry=geojson.Point((5.0, 6.0))))
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['longitude']['span'], 0.0)
        self.assertEqual(result['latitude']['min'], 6.0)
