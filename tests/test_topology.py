"""Tests for topology-aware shared-edge snapping and quantization."""

import copy
import unittest

import geojson
from geojson import Feature, FeatureCollection, MultiPolygon, Point, Polygon
from geojson.topology import (
    CLOSED_CHANNEL,
    HELD_WITH_GROUP,
    HOLE_MERGED,
    snap_shared_edges,
)


def ring_of(feature_collection, feature_id):
    feature = next(f for f in feature_collection["features"]
                   if f.get("id") == feature_id)
    return feature["geometry"]["coordinates"][0]


class SnapSharedEdgesTestCase(unittest.TestCase):
    def test_asymmetric_shared_edge_is_harmonized_on_both_sides(self):
        left = Polygon([[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]])
        right = Polygon([[
            (10, 0), (20, 0), (20, 10), (10, 10), (10, 5), (10, 0)]])
        collection = FeatureCollection([
            Feature(id="left", geometry=left),
            Feature(id="right", geometry=right),
        ])

        result, report = snap_shared_edges(collection, precision=2)
        left_ring = ring_of(result, "left")
        right_ring = ring_of(result, "right")

        self.assertIn((10.0, 5.0), left_ring)
        self.assertIn((10.0, 5.0), right_ring)
        left_edge = {point for point in left_ring if point[0] == 10.0}
        right_edge = {point for point in right_ring if point[0] == 10.0}
        self.assertEqual(left_edge, right_edge)
        self.assertEqual(report["harmonized_edges"], 1)
        self.assertEqual(report["quantized_features"], ["left", "right"])
        self.assertEqual(report["affected_features"], [])

    def test_multiple_midpoints_are_inserted_in_order(self):
        lower = Polygon([
            [(0, 0), (12, 0), (12, -5), (0, -5), (0, 0)]])
        upper = Polygon([
            [(0, 0), (4, 0), (8, 0), (12, 0), (12, 5), (0, 5), (0, 0)]])
        collection = FeatureCollection([
            Feature(id="lower", geometry=lower),
            Feature(id="upper", geometry=upper),
        ])

        result, _ = snap_shared_edges(collection, precision=2)
        lower_ring = ring_of(result, "lower")
        self.assertIn((4.0, 0.0), lower_ring)
        self.assertIn((8.0, 0.0), lower_ring)
        self.assertLess(lower_ring.index((4.0, 0.0)),
                        lower_ring.index((8.0, 0.0)))

    def test_coordinates_are_quantized_to_requested_precision(self):
        polygon = Polygon([
            [(0.111, 0.222), (5.555, 0.222), (5.555, 3.333),
             (0.111, 3.333), (0.111, 0.222)]])
        result, report = snap_shared_edges(polygon, precision=2)
        self.assertEqual(result["coordinates"], [
            [(0.11, 0.22), (5.56, 0.22), (5.56, 3.33),
             (0.11, 3.33), (0.11, 0.22)]])
        self.assertEqual(report["precision"], 2)

    def test_rounding_is_half_up(self):
        polygon = Polygon([
            [(0.005, 0.015), (1.235, 0.015), (1.235, 2.005),
             (0.005, 2.005), (0.005, 0.015)]])
        result, _ = snap_shared_edges(polygon, precision=2)
        self.assertEqual(result["coordinates"][0][0], (0.01, 0.02))
        self.assertEqual(result["coordinates"][0][1], (1.24, 0.02))

    def test_input_object_is_not_mutated(self):
        left = Polygon([[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]])
        right = Polygon([[
            (10, 0), (20, 0), (20, 10), (10, 10), (10, 5), (10, 0)]])
        collection = FeatureCollection([
            Feature(id="left", geometry=left),
            Feature(id="right", geometry=right),
        ])
        snapshot = copy.deepcopy(collection)

        snap_shared_edges(collection, precision=2)
        self.assertEqual(collection, snapshot)

    def test_closed_channel_holds_back_whole_shared_group(self):
        strip = Polygon([
            [(0, 0), (10, 0), (10, 0.004), (0, 0.004), (0, 0)]])
        neighbor = Polygon([
            [(0, 0.004), (10, 0.004), (10, 5), (0, 5), (0, 0.004)]])
        standalone = Polygon([
            [(50, 50), (60, 50), (60, 60), (50, 60), (50, 50)]])
        collection = FeatureCollection([
            Feature(id="strip", geometry=strip),
            Feature(id="neighbor", geometry=neighbor),
            Feature(id="standalone", geometry=standalone),
        ])
        original = copy.deepcopy(collection)

        result, report = snap_shared_edges(collection, precision=2)
        by_id = {feature["id"]: feature for feature in result["features"]}

        # The damaged parcel and its neighbor keep their original geometry.
        self.assertEqual(by_id["strip"]["geometry"],
                         original["features"][0]["geometry"])
        self.assertEqual(by_id["neighbor"]["geometry"],
                         original["features"][1]["geometry"])
        # The unrelated parcel is still published with quantized coordinates.
        self.assertEqual(by_id["standalone"]["geometry"]["coordinates"], [[
            (50.0, 50.0), (60.0, 50.0), (60.0, 60.0),
            (50.0, 60.0), (50.0, 50.0)]])

        reasons = {entry["id"]: entry["reasons"]
                   for entry in report["affected_features"]}
        self.assertEqual(reasons["strip"], [CLOSED_CHANNEL])
        self.assertEqual(reasons["neighbor"], [HELD_WITH_GROUP])
        self.assertEqual(report["quantized_features"], ["standalone"])
        held_group = next(group for group in report["groups"]
                          if group["status"] == "held_back")
        self.assertEqual(held_group["features"], ["neighbor", "strip"])

    def test_hole_merging_outer_boundary_is_detected_and_preserved(self):
        holed = Polygon([
            [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
            [(0.004, 1), (0.004, 9), (1, 9), (1, 1), (0.004, 1)],
        ])
        collection = FeatureCollection([Feature(id="holed", geometry=holed)])
        original = copy.deepcopy(collection)

        result, report = snap_shared_edges(collection, precision=2)
        self.assertEqual(result["features"][0]["geometry"],
                         original["features"][0]["geometry"])
        self.assertEqual(report["affected_features"][0]["reasons"],
                         [HOLE_MERGED])
        self.assertEqual(report["quantized_features"], [])

    def test_shared_edge_is_never_published_on_one_side_only(self):
        strip = Polygon([
            [(0, 0), (10, 0), (10, 0.004), (0, 0.004), (0, 0)]])
        neighbor = Polygon([
            [(0, 0.004), (10, 0.004), (10, 5), (0, 5), (0, 0.004)]])
        collection = FeatureCollection([
            Feature(id="strip", geometry=strip),
            Feature(id="neighbor", geometry=neighbor),
        ])

        result, report = snap_shared_edges(collection, precision=2)
        held = {entry["id"] for entry in report["affected_features"]}
        self.assertEqual(held, {"strip", "neighbor"})
        for feature in result["features"]:
            self.assertNotIn(feature["id"], report["quantized_features"])

    def test_bare_polygon_and_feature_inputs_are_supported(self):
        polygon = Polygon([
            [(0.111, 0.222), (5.555, 0.222), (5.555, 3.333),
             (0.111, 3.333), (0.111, 0.222)]])
        bare_result, bare_report = snap_shared_edges(polygon, precision=2)
        self.assertEqual(bare_result["type"], "Polygon")
        self.assertEqual(bare_report["quantized_features"], [0])

        feature = Feature(id="parcel", geometry=polygon)
        feature_result, feature_report = snap_shared_edges(feature, precision=2)
        self.assertEqual(feature_result["type"], "Feature")
        self.assertEqual(feature_report["quantized_features"], ["parcel"])
        self.assertEqual(feature_result["geometry"]["coordinates"],
                         bare_result["coordinates"])

    def test_multipolygon_failure_preserves_entire_feature(self):
        multipolygon = MultiPolygon([
            [[(0, 0), (10, 0), (10, 0.004), (0, 0.004), (0, 0)]],
            [[(50, 50), (60, 50), (60, 60), (50, 60), (50, 50)]],
        ])
        feature = Feature(id="multi", geometry=multipolygon)
        original = copy.deepcopy(feature)

        result, report = snap_shared_edges(feature, precision=2)
        self.assertEqual(result["geometry"], original["geometry"])
        self.assertEqual(report["affected_features"][0]["id"], "multi")

    def test_features_without_id_use_their_index(self):
        first = Polygon([
            [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]])
        second = Polygon([
            [(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)]])
        collection = FeatureCollection([
            Feature(geometry=first), Feature(geometry=second)])

        _, report = snap_shared_edges(collection, precision=2)
        self.assertEqual(report["quantized_features"], [0, 1])

    def test_non_polygon_features_are_left_untouched(self):
        point = Point((1.234567, 2.345678))
        polygon = Polygon([
            [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]])
        collection = FeatureCollection([
            Feature(id="point", geometry=point),
            Feature(id="polygon", geometry=polygon),
        ])

        result, report = snap_shared_edges(collection, precision=2)
        by_id = {feature["id"]: feature for feature in result["features"]}
        self.assertEqual(by_id["point"]["geometry"], point)
        self.assertEqual(report["quantized_features"], ["polygon"])

    def test_empty_feature_collection_returns_empty_report(self):
        collection = FeatureCollection([])
        result, report = snap_shared_edges(collection, precision=2)
        self.assertEqual(result["features"], [])
        self.assertEqual(report["groups"], [])
        self.assertEqual(report["affected_features"], [])
        self.assertEqual(report["harmonized_edges"], 0)

    def test_unsupported_geometry_type_raises(self):
        with self.assertRaises(ValueError):
            snap_shared_edges(Point((1, 2)), precision=2)

    def test_three_dimensional_positions_are_rejected(self):
        polygon = Polygon([
            [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1), (0, 0, 1)]])
        with self.assertRaises(ValueError):
            snap_shared_edges(polygon, precision=2)

    def test_result_remains_serializable_geojson(self):
        left = Polygon([[(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]])
        right = Polygon([[
            (10, 0), (20, 0), (20, 10), (10, 10), (10, 5), (10, 0)]])
        collection = FeatureCollection([
            Feature(id="left", geometry=left),
            Feature(id="right", geometry=right),
        ])
        result, _ = snap_shared_edges(collection, precision=2)
        decoded = geojson.loads(geojson.dumps(result))
        self.assertEqual(decoded["type"], "FeatureCollection")
        self.assertEqual(len(decoded["features"]), 2)


if __name__ == "__main__":
    unittest.main()
