"""
Tests for cumulative rainfall surfaces and mutually exclusive bands.
"""

import unittest

import geojson
from geojson import _planar
from geojson.rainfall import build_bands


def rectangle(x0, y0, x1, y1):
    """A single clockwise-free closed ring for a planar rectangle."""
    return [[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]]


def multipolygon(*rectangles):
    return [rectangle(*values) for values in rectangles]


class TestPlanarOperations(unittest.TestCase):

    def test_area_and_bbox(self):
        polygon = _planar.normalize(rectangle(0, 0, 10, 20))
        self.assertAlmostEqual(_planar.area(polygon), 200.0)
        self.assertEqual(_planar.bbox(polygon), [0.0, 0.0, 10.0, 20.0])

    def test_overlap_identities(self):
        first = _planar.normalize(rectangle(0, 0, 10, 10))
        second = _planar.normalize(rectangle(5, 5, 15, 15))
        union = _planar.union(first, second)
        intersection = _planar.intersection(first, second)
        difference = _planar.difference(first, second)
        self.assertAlmostEqual(_planar.area(union), 175.0)
        self.assertAlmostEqual(_planar.area(intersection), 25.0)
        self.assertAlmostEqual(_planar.area(difference), 75.0)

    def test_hole_is_preserved(self):
        with_hole = _planar.normalize([
            [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)],
            [(5, 5), (5, 15), (15, 15), (15, 5), (5, 5)],
        ])
        self.assertAlmostEqual(_planar.area(with_hole), 300.0)
        hole = _planar.normalize(rectangle(6, 6, 14, 14))
        self.assertAlmostEqual(_planar.area(_planar.intersection(with_hole, hole)), 0.0)

    def test_shared_boundary_union_is_one_part(self):
        first = _planar.normalize(rectangle(0, 0, 10, 10))
        second = _planar.normalize(rectangle(10, 0, 20, 10))
        union = _planar.union(first, second)
        self.assertAlmostEqual(_planar.area(union), 200.0)
        self.assertEqual(len(union), 1)
        self.assertEqual(len(union[0]), 1)

    def test_disjoint_union_keeps_parts(self):
        first = _planar.normalize(rectangle(0, 0, 10, 10))
        second = _planar.normalize(rectangle(20, 0, 30, 10))
        union = _planar.union(first, second)
        self.assertAlmostEqual(_planar.area(union), 200.0)
        self.assertEqual(len(union), 2)


class TestNestedSurfaces(unittest.TestCase):

    def test_bands_partition_the_lowest_surface(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(10, 10, 90, 90),
            50: rectangle(30, 30, 70, 70),
        }
        result = build_bands(surfaces)
        self.assertEqual(result.thresholds, (10.0, 25.0, 50.0))
        self.assertFalse(result.has_conflicts)
        self.assertTrue(result.is_complete)
        self.assertEqual([band.label for band in result.bands],
                         ["[10, 25)", "[25, 50)", "[50, inf)"])
        self.assertAlmostEqual(result.total_area, 10000.0)
        self.assertAlmostEqual(result.bands[0].area, 3600.0)
        self.assertAlmostEqual(result.bands[1].area, 4800.0)
        self.assertAlmostEqual(result.bands[2].area, 1600.0)

    def test_areas_are_disjoint(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(10, 10, 90, 90),
            50: rectangle(30, 30, 70, 70),
        }
        result = build_bands(surfaces)
        geometries = [
            _planar.normalize(band.geometry) for band in result.bands
        ]
        for index, first in enumerate(geometries):
            for second in geometries[index + 1:]:
                self.assertAlmostEqual(
                    _planar.area(_planar.intersection(first, second)), 0.0
                )

    def test_identical_surfaces_produce_empty_bands(self):
        surfaces = {
            10: rectangle(0, 0, 10, 10),
            25: rectangle(0, 0, 10, 10),
        }
        result = build_bands(surfaces)
        self.assertAlmostEqual(result.bands[0].area, 0.0)
        self.assertAlmostEqual(result.bands[1].area, 100.0)
        self.assertFalse(result.has_conflicts)


class TestNestingViolations(unittest.TestCase):

    def test_local_conflict_is_outlined_not_discarded(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(0, 0, 95, 100),
            50: rectangle(20, 20, 110, 80),
        }
        result = build_bands(surfaces)
        self.assertTrue(result.has_conflicts)
        self.assertEqual(len(result.violations), 1)
        violation = result.violations[0]
        self.assertEqual(violation.upper, 50.0)
        self.assertEqual(set(violation.escaped_thresholds), {10.0, 25.0})
        self.assertAlmostEqual(violation.area, 900.0)
        self.assertEqual(violation.bbox, [95.0, 20.0, 110.0, 80.0])
        self.assertAlmostEqual(result.conflict_area, 900.0)

    def test_conflict_geometry_is_the_exact_escape(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(0, 0, 95, 100),
            50: rectangle(20, 20, 110, 80),
        }
        result = build_bands(surfaces)
        conflict = _planar.normalize(result.conflict_geometry)
        expected = _planar.normalize(rectangle(95, 20, 110, 80))
        self.assertAlmostEqual(_planar.area(conflict), 900.0)
        self.assertAlmostEqual(
            _planar.area(_planar.difference(conflict, expected)), 0.0
        )
        self.assertAlmostEqual(
            _planar.area(_planar.difference(expected, conflict)), 0.0
        )

    def test_conflict_stays_local(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(0, 0, 95, 100),
            50: rectangle(20, 20, 110, 80),
        }
        result = build_bands(surfaces)
        self.assertAlmostEqual(result.total_area, 10000.0)
        self.assertAlmostEqual(
            result.bands[0].area + result.bands[1].area + result.bands[2].area,
            10000.0,
        )
        self.assertAlmostEqual(result.bands[2].bbox[2], 95.0)

    def test_several_disconnected_conflicts_are_kept(self):
        base = [
            [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)],
        ]
        upper = [
            [(-10, -10), (-5, -10), (-5, 5), (-10, 5), (-10, -10)],
            [(90, 80), (120, 80), (120, 115), (90, 115), (90, 80)],
        ]
        result = build_bands({10: base, 25: upper})
        conflict = result.violations[0]
        self.assertEqual(conflict.geometry["type"], "MultiPolygon")
        self.assertEqual(len(conflict.geometry["coordinates"]), 2)

    def test_high_threshold_escaping_a_low_threshold(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(0, 0, 100, 100),
            50: rectangle(0, 0, 100, 100),
            75: rectangle(50, 50, 150, 150),
        }
        result = build_bands(surfaces)
        self.assertEqual(len(result.violations), 1)
        violation = result.violations[0]
        self.assertEqual(violation.escaped_thresholds, (10.0, 25.0, 50.0))
        self.assertAlmostEqual(violation.area, 7500.0)

    def test_feature_collection_includes_conflict_union(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(0, 0, 90, 100),
            50: rectangle(20, 20, 110, 80),
        }
        result = build_bands(surfaces)
        collection = result.to_feature_collection(include_violations=True)
        kinds = [feature["properties"]["kind"] for feature in collection["features"]]
        self.assertEqual(kinds, ["band", "band", "band", "conflict_union"])


class TestMissingLayers(unittest.TestCase):

    def test_missing_middle_layer_is_a_joint_interval(self):
        surfaces = {10: rectangle(0, 0, 100, 100),
                    50: rectangle(30, 30, 70, 70)}
        result = build_bands(surfaces, thresholds=[10, 25, 50])
        self.assertEqual(result.missing_thresholds, (25.0,))
        self.assertEqual([band.label for band in result.bands],
                         ["[10, 50) joint", "[50, inf)"])
        self.assertTrue(result.bands[0].joint)
        self.assertEqual(result.bands[0].missing_thresholds, (25.0,))
        self.assertAlmostEqual(result.bands[0].area, 8400.0)

    def test_missing_layer_is_not_filled_with_zero(self):
        surfaces = {10: rectangle(0, 0, 100, 100),
                    50: rectangle(30, 30, 70, 70)}
        result = build_bands(surfaces, thresholds=[10, 25, 50])
        labels = [band.label for band in result.bands]
        self.assertNotIn("[10, 25)", labels)
        self.assertNotIn("[25, 50)", labels)
        self.assertEqual(len(result.bands), 2)

    def test_missing_lowest_layer_is_unavailable(self):
        surfaces = {25: rectangle(0, 0, 50, 50),
                    50: rectangle(10, 10, 40, 40)}
        result = build_bands(surfaces, thresholds=[10, 25, 50])
        self.assertEqual(result.missing_thresholds, (10.0,))
        self.assertEqual(len(result.unavailable), 1)
        self.assertEqual(result.unavailable[0].label, "[10, 25)")
        self.assertNotIn("[10, 25)", [band.label for band in result.bands])

    def test_no_surface_at_all(self):
        result = build_bands({}, thresholds=[10, 25, 50])
        self.assertEqual(result.missing_thresholds, (10.0, 25.0, 50.0))
        self.assertEqual(result.bands, ())
        self.assertTrue(result.unavailable)

    def test_empty_surface_differs_from_missing(self):
        result = build_bands(
            {10: rectangle(0, 0, 100, 100), 25: [], 50: rectangle(20, 20, 80, 80)},
            thresholds=[10, 25, 50],
        )
        self.assertEqual(result.missing_thresholds, ())
        self.assertEqual([band.label for band in result.bands],
                         ["[10, 25)", "[25, 50)", "[50, inf)"])
        self.assertAlmostEqual(result.bands[0].area, 10000.0)
        self.assertAlmostEqual(result.bands[1].area, 0.0)


class TestInputs(unittest.TestCase):

    def test_accepts_feature_collection(self):
        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"threshold": ">= 10"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": rectangle(0, 0, 100, 100),
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"min_mm": "25+"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": rectangle(10, 10, 90, 90),
                    },
                },
            ],
        }
        result = build_bands(collection)
        self.assertEqual(result.thresholds, (10.0, 25.0))
        self.assertAlmostEqual(result.bands[0].area, 3600.0)

    def test_accepts_geojson_objects(self):
        result = build_bands({
            10: geojson.Polygon(rectangle(0, 0, 100, 100)),
            25: geojson.Polygon(rectangle(10, 10, 90, 90)),
        })
        self.assertAlmostEqual(result.bands[0].area, 3600.0)

    def test_accepts_pair_sequence(self):
        result = build_bands([
            (10, rectangle(0, 0, 10, 10)),
            (25, rectangle(2, 2, 8, 8)),
        ])
        self.assertEqual(result.thresholds, (10.0, 25.0))

    def test_duplicate_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands([(10, rectangle(0, 0, 1, 1)),
                         (10, rectangle(0, 0, 2, 2))])

    def test_unknown_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands({10: rectangle(0, 0, 1, 1),
                         25: rectangle(0, 0, 1, 1)}, thresholds=[10])

    def test_to_dict_is_serialisable(self):
        result = build_bands({
            10: rectangle(0, 0, 100, 100),
            25: rectangle(10, 10, 90, 90),
        })
        encoded = geojson.dumps(result.to_feature_collection())
        self.assertIn('"FeatureCollection"', encoded)


class TestCommandLine(unittest.TestCase):

    def test_report_from_feature_collection(self):
        import json
        import os
        import tempfile

        from geojson.rainfall_cli import main

        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"threshold": 10},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": rectangle(0, 0, 100, 100),
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"threshold": 25},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": rectangle(0, 0, 95, 100),
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"threshold": 50},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": rectangle(20, 20, 110, 80),
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "surfaces.json")
            target = os.path.join(directory, "report.json")
            with open(source, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
            self.assertEqual(main([source, "--report", "-o", target]), 0)
            with open(target, encoding="utf-8") as stream:
                report = json.load(stream)
        self.assertEqual(report["thresholds"], [10.0, 25.0, 50.0])
        self.assertAlmostEqual(report["total_area"], 10000.0)
        self.assertAlmostEqual(report["conflict_area"], 900.0)


class TestNumericalRobustness(unittest.TestCase):

    def test_subtle_float_noise_is_not_a_conflict(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(1e-9, 1e-9, 100 - 1e-9, 100 - 1e-9),
        }
        result = build_bands(surfaces)
        self.assertFalse(result.has_conflicts)
        self.assertAlmostEqual(result.total_area, 10000.0, places=6)

    def test_real_escape_of_micro_metres_is_reported(self):
        surfaces = {
            10: rectangle(0, 0, 100, 100),
            25: rectangle(0, 0, 100.001, 100),
        }
        result = build_bands(surfaces)
        self.assertTrue(result.has_conflicts)
        self.assertAlmostEqual(result.violations[0].area, 0.1)

    def test_ring_sharing_boundary_with_hole_does_not_crash(self):
        surfaces = {
            10: [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]],
            25: [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
                 [(0, 0), (10, 0), (10, 5), (0, 5), (0, 0)]],
        }
        result = build_bands(surfaces)
        self.assertAlmostEqual(result.total_area, 100.0)

    def test_disjoint_rings_in_one_polygon_are_kept(self):
        surfaces = {
            10: [[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
                 [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]],
        }
        result = build_bands(surfaces)
        self.assertAlmostEqual(result.bands[0].area, 200.0)


class TestValidation(unittest.TestCase):

    def test_non_numeric_threshold_is_rejected(self):
        with self.assertRaises(TypeError):
            build_bands({None: rectangle(0, 0, 1, 1)})

    def test_non_finite_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands({float("nan"): rectangle(0, 0, 1, 1)})

    def test_non_numeric_string_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands({"wet": rectangle(0, 0, 1, 1)})

    def test_point_geometry_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands({10: (1, 2)})

    def test_malformed_sequence_entry_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands([(10, rectangle(0, 0, 1, 1), 5)])

    def test_bad_surface_entry_type_is_rejected(self):
        with self.assertRaises(TypeError):
            build_bands([42])

    def test_empty_threshold_list_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands({}, thresholds=[])

    def test_duplicate_thresholds_are_rejected(self):
        with self.assertRaises(ValueError):
            build_bands({10: rectangle(0, 0, 1, 1)}, thresholds=[10, 10])

    def test_feature_without_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            build_bands({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {},
                 "geometry": None},
            ]})
