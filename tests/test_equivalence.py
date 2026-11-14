"""Tests for geometric equivalence and merging."""

import unittest

import geojson
from geojson import equivalence


def polygon(*rings):
    return {"type": "Polygon", "coordinates": [list(ring) for ring in rings]}


def multipolygon(*polygons):
    return {
        "type": "MultiPolygon",
        "coordinates": [polygon(*rings)["coordinates"] for rings in polygons],
    }


SHELL = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
OTHER_SHELL = [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]
HOLE = [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]
OTHER_HOLE = [(6, 6), (8, 6), (8, 8), (6, 8), (6, 6)]
FAR_HOLE = [(22, 2), (24, 2), (24, 4), (22, 4), (22, 2)]


class CanonicalRingTestCase(unittest.TestCase):
    def test_start_point_is_irrelevant(self):
        rotated = SHELL[3:] + SHELL[:3]
        self.assertEqual(
            equivalence.canonical_ring(SHELL),
            equivalence.canonical_ring(rotated),
        )

    def test_direction_is_irrelevant(self):
        reversed_ring = list(reversed(SHELL))
        self.assertEqual(
            equivalence.canonical_ring(SHELL),
            equivalence.canonical_ring(reversed_ring),
        )

    def test_repeated_closing_points_are_removed(self):
        repeated = SHELL + [SHELL[0], SHELL[0]]
        self.assertEqual(
            equivalence.canonical_ring(SHELL),
            equivalence.canonical_ring(repeated),
        )

    def test_consecutive_duplicate_vertices_are_removed(self):
        duplicate = [(0, 0), (10, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        self.assertEqual(
            equivalence.canonical_ring(SHELL),
            equivalence.canonical_ring(duplicate),
        )

    def test_closed_form_is_stable(self):
        canonical = equivalence.canonical_ring(SHELL)
        self.assertEqual(canonical[0], canonical[-1])
        self.assertEqual(
            canonical,
            equivalence.canonical_ring(canonical),
        )


class GeometryEquivalenceTestCase(unittest.TestCase):
    def test_polygon_start_and_direction(self):
        reversed_ring = list(reversed(SHELL))
        self.assertTrue(
            equivalence.geometries_equal(polygon(SHELL), polygon(reversed_ring))
        )
        rotated = SHELL[2:] + SHELL[:2]
        self.assertTrue(
            equivalence.geometries_equal(polygon(SHELL), polygon(rotated))
        )
        self.assertTrue(equivalence.geometries_equal(
            polygon(SHELL), polygon(reversed(rotated))
        ))

    def test_multipolygon_face_order(self):
        one = multipolygon((SHELL,), (OTHER_SHELL,))
        two = multipolygon(
            (list(reversed(OTHER_SHELL)),),
            (list(reversed(SHELL)),),
        )
        self.assertTrue(equivalence.geometries_equal(one, two))
        self.assertEqual(
            equivalence.normalize_geometry(one),
            equivalence.normalize_geometry(two),
        )

    def test_polygon_hole_order(self):
        a = polygon(SHELL, HOLE, OTHER_HOLE)
        b = polygon(list(reversed(SHELL)), OTHER_HOLE, list(reversed(HOLE)))
        self.assertTrue(equivalence.geometries_equal(a, b))
        self.assertEqual(
            equivalence.normalize_geometry(a),
            equivalence.normalize_geometry(b),
        )

    def test_hole_moving_between_shells_is_a_change(self):
        together = multipolygon(
            (SHELL, HOLE),
            (OTHER_SHELL, FAR_HOLE),
        )
        moved = multipolygon(
            (SHELL, FAR_HOLE),
            (OTHER_SHELL, HOLE),
        )
        self.assertFalse(equivalence.geometries_equal(together, moved))
        self.assertNotEqual(
            equivalence.geometry_key(together),
            equivalence.geometry_key(moved),
        )

    def test_hole_inside_another_shell_is_a_change(self):
        first = multipolygon((SHELL, HOLE), (OTHER_SHELL,))
        second = multipolygon((SHELL,), (OTHER_SHELL, HOLE))
        self.assertFalse(equivalence.geometries_equal(first, second))

    def test_same_vertices_different_hole_grouping(self):
        a = multipolygon((SHELL, HOLE), (OTHER_SHELL, OTHER_HOLE))
        b = multipolygon((SHELL, OTHER_HOLE), (OTHER_SHELL, HOLE))
        self.assertFalse(equivalence.geometries_equal(a, b))

    def test_integer_and_float_positions_match(self):
        integer = {"type": "Point", "coordinates": [1, 2]}
        floating = {"type": "Point", "coordinates": [1.0, 2.0]}
        self.assertTrue(equivalence.geometries_equal(integer, floating))
        self.assertEqual(
            equivalence.normalize_geometry(integer),
            equivalence.normalize_geometry(floating),
        )

    def test_nested_ring_start_in_multipolygon(self):
        rotated_hole = HOLE[2:] + HOLE[:2]
        a = multipolygon((SHELL, HOLE))
        b = multipolygon((list(reversed(SHELL)), list(reversed(rotated_hole))))
        self.assertTrue(equivalence.geometries_equal(a, b))

    def test_point_and_feature_equivalence(self):
        point = geojson.Point((-115.81, 37.24))
        feature = geojson.Feature(geometry=point)
        self.assertTrue(equivalence.geometries_equal(point, feature))

    def test_geometry_collection_order(self):
        a = geojson.GeometryCollection([
            geojson.Point((1, 2)),
            geojson.Point((3, 4)),
        ])
        b = geojson.GeometryCollection([
            geojson.Point((3, 4)),
            geojson.Point((1, 2)),
        ])
        self.assertTrue(equivalence.geometries_equal(a, b))

    def test_invalid_geometry(self):
        with self.assertRaises(ValueError):
            equivalence.geometry_key({"type": "Nope", "coordinates": []})
        with self.assertRaises(ValueError):
            equivalence.canonical_ring([(0, 0), (1, 1), (0, 0)])

    def test_null_geometry_has_null_key(self):
        self.assertIsNone(equivalence.geometry_key(None))
        feature = geojson.Feature()
        self.assertIsNone(equivalence.geometry_key(feature))

    def test_signed_zero_matches_zero(self):
        positive = {"type": "Point", "coordinates": [0.0, 1]}
        negative = {"type": "Point", "coordinates": [-0.0, 1]}
        self.assertTrue(equivalence.geometries_equal(positive, negative))

    def test_normalization_is_idempotent(self):
        geometry = multipolygon(
            (SHELL, HOLE, OTHER_HOLE),
            (OTHER_SHELL, FAR_HOLE),
        )
        normalized = equivalence.normalize_geometry(geometry)
        self.assertEqual(
            normalized,
            equivalence.normalize_geometry(normalized),
        )


class MergeTestCase(unittest.TestCase):
    def test_single_geometry_record_for_reordered_rings(self):
        source_a = geojson.FeatureCollection([
            geojson.Feature(
                geometry=polygon(SHELL),
                properties={"source": "a", "code": "110101"},
            ),
        ])
        source_b = geojson.FeatureCollection([
            geojson.Feature(
                geometry=polygon(list(reversed(SHELL[2:] + SHELL[:2]))),
                properties={"source": "b", "adcode": "110102"},
            ),
        ])
        records = equivalence.merge_by_geometry(source_a, source_b)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["declarations"]), 2)
        self.assertEqual(
            equivalence.normalize_geometry(records[0]["geometry"]),
            equivalence.normalize_geometry(polygon(SHELL)),
        )

    def test_conflicting_codes_are_both_retained(self):
        features = [
            geojson.Feature(
                geometry=polygon(SHELL),
                properties={"source": "source-a", "code": "110101"},
            ),
            geojson.Feature(
                geometry=polygon(list(reversed(SHELL))),
                properties={"source": "source-b", "code": "110102"},
            ),
        ]
        records = equivalence.merge_by_geometry(features)
        self.assertEqual(len(records), 1)
        declarations = records[0]["declarations"]
        self.assertEqual(len(declarations), 2)
        self.assertEqual(
            [declaration["source"] for declaration in declarations],
            ["source-a", "source-b"],
        )
        self.assertEqual(
            [declaration["codes"]["code"] for declaration in declarations],
            ["110101", "110102"],
        )
        self.assertEqual(declarations[0]["properties"]["code"], "110101")
        self.assertEqual(declarations[1]["properties"]["code"], "110102")

    def test_conflicting_codes_are_not_overwritten(self):
        records = equivalence.merge_by_geometry({
            "source-a": [geojson.Feature(
                geometry=polygon(SHELL),
                properties={"code": "110101"},
            )],
            "source-b": [geojson.Feature(
                geometry=polygon(SHELL),
                properties={"code": "110102"},
            )],
        })
        self.assertEqual(len(records), 1)
        self.assertEqual(
            [declaration["source"] for declaration in records[0]["declarations"]],
            ["source-a", "source-b"],
        )

    def test_hole_moved_between_shells_creates_two_records(self):
        records = equivalence.merge_by_geometry([
            geojson.Feature(geometry=multipolygon(
                (SHELL, HOLE), (OTHER_SHELL, FAR_HOLE),
            )),
            geojson.Feature(geometry=multipolygon(
                (SHELL, FAR_HOLE), (OTHER_SHELL, HOLE),
            )),
        ])
        self.assertEqual(len(records), 2)

    def test_multipolygon_parts_are_merged(self):
        records = equivalence.merge_by_geometry([
            geojson.Feature(geometry=multipolygon(
                (SHELL,), (OTHER_SHELL,),
            )),
            geojson.Feature(geometry=multipolygon(
                (OTHER_SHELL,), (SHELL,),
            )),
        ])
        self.assertEqual(len(records), 1)

    def test_merge_to_features(self):
        collection = equivalence.merge_to_features([
            geojson.Feature(
                geometry=polygon(SHELL),
                properties={"code": "110101"},
            ),
            geojson.Feature(
                geometry=polygon(list(reversed(SHELL))),
                properties={"code": "110102"},
            ),
        ])
        self.assertEqual(len(collection["features"]), 1)
        declarations = collection["features"][0]["properties"]["declarations"]
        self.assertEqual(len(declarations), 2)
        self.assertEqual(declarations[0]["properties"]["code"], "110101")

    def test_deduplicate_geometries(self):
        result = equivalence.deduplicate_geometries([
            polygon(SHELL),
            polygon(list(reversed(SHELL))),
            polygon(OTHER_SHELL),
        ])
        self.assertEqual(len(result), 2)

    def test_source_pair_input(self):
        records = equivalence.merge_by_geometry(
            ("a", [geojson.Feature(
                geometry=polygon(SHELL), properties={"code": "1"},
            )]),
            ("b", [geojson.Feature(
                geometry=polygon(SHELL), properties={"code": "2"},
            )]),
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(
            [declaration["source"] for declaration in records[0]["declarations"]],
            ["a", "b"],
        )


class PublicApiTestCase(unittest.TestCase):
    def test_reexported(self):
        for name in (
            "normalize_geometry", "canonical_geometry", "canonical_ring",
            "canonical_polygon", "geometry_key", "geometries_equal",
            "merge_by_geometry", "group_by_geometry", "deduplicate_geometries",
            "merge_to_features", "dedupe_features",
        ):
            self.assertIn(name, geojson.__all__)
            self.assertTrue(hasattr(geojson, name))

    def test_utils_reexported(self):
        from geojson import utils
        for name in (
            "normalize_geometry", "canonical_geometry", "canonical_ring",
            "canonical_polygon", "geometry_key", "geometries_equal",
            "merge_by_geometry", "group_by_geometry", "deduplicate_geometries",
            "merge_to_features", "dedupe_features",
        ):
            self.assertIn(name, utils.__all__)
            self.assertTrue(hasattr(utils, name))

    def test_equivalence_module_exports(self):
        for name in equivalence.__all__:
            self.assertTrue(hasattr(equivalence, name))

    def test_merge_accepts_generators(self):
        def features():
            yield geojson.Feature(
                geometry=polygon(SHELL), properties={"code": "1"},
            )
            yield geojson.Feature(
                geometry=polygon(list(reversed(SHELL))),
                properties={"code": "2"},
            )

        records = equivalence.merge_by_geometry(features())
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["declarations"]), 2)
