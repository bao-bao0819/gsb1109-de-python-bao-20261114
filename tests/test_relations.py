"""
Tests for geojson/relations.py
"""

import unittest

import geojson
from geojson.relations import (
    PointLocation,
    RectangleRelation,
    Relation,
    classify_rectangle,
)


def multipolygon(*polygons):
    """Build a MultiPolygon from (outer, hole, ...) ring lists."""
    return geojson.MultiPolygon([
        [[tuple(point) for point in ring] for ring in polygon]
        for polygon in polygons
    ])


SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
HOLE = [(4, 4), (6, 4), (6, 6), (4, 6), (4, 4)]


class TestPointLocation(unittest.TestCase):

    def setUp(self):
        self.area = multipolygon([SQUARE])

    def test_point_inside(self):
        self.assertEqual(
            geojson.point_location((5, 5), self.area),
            PointLocation.INSIDE)

    def test_point_outside(self):
        self.assertEqual(
            geojson.point_location((15, 5), self.area),
            PointLocation.OUTSIDE)

    def test_point_on_boundary(self):
        self.assertEqual(
            geojson.point_location((10, 5), self.area),
            PointLocation.BOUNDARY)

    def test_point_on_corner(self):
        self.assertEqual(
            geojson.point_location((0, 0), self.area),
            PointLocation.BOUNDARY)

    def test_point_in_hole_is_outside(self):
        area = multipolygon([SQUARE, HOLE])
        self.assertEqual(
            geojson.point_location((5, 5), area),
            PointLocation.OUTSIDE)

    def test_point_on_hole_boundary_is_boundary(self):
        area = multipolygon([SQUARE, HOLE])
        self.assertEqual(
            geojson.point_location((4, 5), area),
            PointLocation.BOUNDARY)

    def test_point_beyond_hole_is_inside(self):
        area = multipolygon([SQUARE, HOLE])
        self.assertEqual(
            geojson.point_location((2, 5), area),
            PointLocation.INSIDE)


class TestRectangleRelation(unittest.TestCase):

    def setUp(self):
        self.area = multipolygon([SQUARE])

    def test_rectangle_inside(self):
        result = classify_rectangle((1, 1, 3, 3), self.area)
        self.assertEqual(result.relation, Relation.INSIDE)
        self.assertTrue(result.is_inside)
        self.assertEqual(result.reason, "rectangle_inside_area")
        self.assertEqual(result.corner_locations,
                         (PointLocation.INSIDE,) * 4)
        self.assertEqual(result.contact_edges, ())

    def test_rectangle_outside(self):
        result = classify_rectangle((20, 20, 30, 30), self.area)
        self.assertEqual(result.relation, Relation.OUTSIDE)
        self.assertEqual(result.reason, "disjoint")
        self.assertEqual(result.corner_locations,
                         (PointLocation.OUTSIDE,) * 4)

    def test_rectangle_straddling_boundary(self):
        result = classify_rectangle((-1, 4, 11, 6), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertEqual(result.reason, "rectangle_crosses_boundary")

    def test_rectangle_larger_than_area_is_partial(self):
        result = classify_rectangle((-1, -1, 11, 11), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)

    def test_rectangle_bigger_on_one_axis_is_partial(self):
        result = classify_rectangle((2, -1, 8, 11), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)

    def test_area_disjoint_from_rectangle_but_near(self):
        result = classify_rectangle((10.0001, 2, 20, 8), self.area)
        self.assertEqual(result.relation, Relation.OUTSIDE)


class TestBoundaryInclusion(unittest.TestCase):

    def setUp(self):
        self.area = multipolygon([SQUARE])

    def test_rectangle_inside_sharing_one_edge(self):
        result = classify_rectangle((0, 2, 5, 8), self.area)
        self.assertEqual(result.relation, Relation.INSIDE)
        self.assertEqual(result.corner_locations[0],
                         PointLocation.BOUNDARY)
        self.assertEqual(result.corner_locations[3],
                         PointLocation.BOUNDARY)

    def test_rectangle_inside_sharing_two_edges(self):
        result = classify_rectangle((0, 0, 10, 10), self.area)
        self.assertEqual(result.relation, Relation.INSIDE)
        self.assertEqual(result.corner_locations,
                         (PointLocation.BOUNDARY,) * 4)

    def test_covered_union_of_two_polygons(self):
        left = [(0, 0), (5, 0), (5, 10), (0, 10), (0, 0)]
        right = [(5, 0), (10, 0), (10, 10), (5, 10), (5, 0)]
        area = multipolygon([left], [right])
        result = classify_rectangle((1, 1, 9, 9), area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_split_across_two_polygons(self):
        left = [(0, 0), (5, 0), (5, 10), (0, 10), (0, 0)]
        right = [(5, 0), (10, 0), (10, 10), (5, 10), (5, 0)]
        area = multipolygon([left], [right])
        result = classify_rectangle((4, 2, 6, 8), area)
        self.assertEqual(result.relation, Relation.INSIDE)


class TestHoles(unittest.TestCase):

    def setUp(self):
        self.area = multipolygon([SQUARE, HOLE])

    def test_all_corners_inside_with_enclosed_hole_is_partial(self):
        result = classify_rectangle((1, 1, 9, 9), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertEqual(result.reason, "enclosed_hole")
        self.assertEqual(result.corner_locations,
                         (PointLocation.INSIDE,) * 4)
        self.assertEqual(len(result.enclosed_holes), 1)
        self.assertEqual(result.enclosed_holes[0].hole_index, 0)
        self.assertEqual(result.enclosed_holes[0].ring_index, 1)

    def test_rectangle_entirely_inside_hole_is_outside(self):
        result = classify_rectangle((4.5, 4.5, 5.5, 5.5), self.area)
        self.assertEqual(result.relation, Relation.OUTSIDE)
        self.assertEqual(result.reason, "disjoint")

    def test_rectangle_overlaps_hole_and_area(self):
        result = classify_rectangle((3, 3, 5, 5), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)

    def test_rectangle_inside_hole_but_encloses_hole(self):
        # The rectangle follows the hole exactly; every point of the
        # rectangle is on a hole ring, which the rules treat as belonging.
        result = classify_rectangle((4, 4, 6, 6), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)

    def test_rectangle_touching_hole_from_inside_is_inside(self):
        result = classify_rectangle((2, 4, 4, 6), self.area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_hole_outside_rectangle_is_not_reported_enclosed(self):
        result = classify_rectangle((1, 1, 3, 3), self.area)
        self.assertEqual(result.relation, Relation.INSIDE)
        self.assertEqual(result.enclosed_holes, ())


class TestTangency(unittest.TestCase):

    def setUp(self):
        self.area = multipolygon([SQUARE])

    def test_shared_edge_is_tangent(self):
        result = classify_rectangle((10, 2, 20, 8), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertTrue(result.tangent_only)
        self.assertEqual(result.reason, "boundary_tangent")
        self.assertEqual(result.tangent_edges, ("left",))

    def test_shared_full_edge_is_tangent(self):
        result = classify_rectangle((10, 0, 20, 10), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertTrue(result.tangent_only)
        self.assertEqual(result.tangent_edges, ("left",))

    def test_corner_touch_is_tangent(self):
        result = classify_rectangle((10, 10, 20, 20), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertTrue(result.tangent_only)
        self.assertEqual(result.tangent_edges, ("left", "bottom"))

    def test_bottom_edge_tangency_below_area(self):
        result = classify_rectangle((2, 10, 8, 20), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertTrue(result.tangent_only)
        self.assertEqual(result.tangent_edges, ("bottom",))

    def test_corner_touch_from_each_quadrant(self):
        for bounds in ((10, 10, 20, 20), (-10, 10, 0, 20),
                       (-10, -10, 0, 0), (10, -10, 20, 0)):
            result = classify_rectangle(bounds, self.area)
            self.assertEqual(result.relation, Relation.PARTIAL)
            self.assertTrue(result.tangent_only)

    def test_edge_contact_points(self):
        result = classify_rectangle((10, 2, 20, 8), self.area)
        left = result.edge_map["left"]
        self.assertTrue(left.touches_boundary)
        self.assertTrue(left.overlaps_boundary)
        self.assertEqual(left.contact_points,
                         ((10, 2), (10, 8)))
        contact = left.contacts[0]
        self.assertEqual(contact.kind, "overlap")
        self.assertEqual(contact.ring_role, "exterior")
        self.assertEqual(contact.segment, ((10, 0), (10, 10)))

    def test_rectangles_inside_are_not_tangent(self):
        for bounds in ((1, 1, 3, 3), (0, 2, 5, 8), (0, 0, 10, 10)):
            result = classify_rectangle(bounds, self.area)
            self.assertFalse(result.tangent_only)
            self.assertEqual(result.tangent_edges, ())

    def test_pure_crossing_is_not_tangent(self):
        result = classify_rectangle((-1, 4, 11, 6), self.area)
        self.assertFalse(result.tangent_only)
        self.assertEqual(result.tangent_edges, ())


class TestConcaveAreas(unittest.TestCase):

    def setUp(self):
        # An L shape with an open notch in the upper right corner.
        self.shape = [(0, 0), (10, 0), (10, 4), (4, 4),
                      (4, 10), (0, 10), (0, 0)]
        self.area = multipolygon([self.shape])

    def test_rectangle_in_each_arm(self):
        for bounds in ((1, 1, 3, 9), (1, 1, 9, 3), (1, 5, 3, 9)):
            result = classify_rectangle(bounds, self.area)
            self.assertEqual(result.relation, Relation.INSIDE)

    def test_rectangle_bridging_the_notch(self):
        result = classify_rectangle((3, 3, 7, 7), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertEqual(result.reason, "rectangle_crosses_boundary")

    def test_rectangle_outside_the_notch(self):
        result = classify_rectangle((5, 5, 9, 9), self.area)
        self.assertEqual(result.relation, Relation.OUTSIDE)

    def test_rectangle_larger_than_bounding_box(self):
        result = classify_rectangle((-1, -1, 11, 11), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)

    def test_rectangle_tangent_to_concave_corner(self):
        result = classify_rectangle((4, 4, 6, 6), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertTrue(result.tangent_only)


class TestDisjointAreas(unittest.TestCase):

    def setUp(self):
        left = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        right = [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]
        self.area = multipolygon([left], [right])

    def test_rectangle_in_the_gap(self):
        result = classify_rectangle((11, 1, 19, 9), self.area)
        self.assertEqual(result.relation, Relation.OUTSIDE)

    def test_rectangle_spanning_the_gap(self):
        result = classify_rectangle((5, 2, 25, 8), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertFalse(result.tangent_only)

    def test_rectangle_touching_both_areas(self):
        result = classify_rectangle((10, 0, 20, 10), self.area)
        self.assertEqual(result.relation, Relation.PARTIAL)
        self.assertTrue(result.tangent_only)
        self.assertEqual(result.tangent_edges, ("left", "right"))


class TestDegenerateInput(unittest.TestCase):

    def test_ring_with_duplicate_closing_point_only(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        area = multipolygon([square])
        self.assertEqual(
            classify_rectangle((1, 1, 3, 3), area).relation,
            Relation.INSIDE)

    def test_hole_touching_exterior_ring(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        hole = [(0, 4), (2, 4), (2, 6), (0, 6), (0, 4)]
        area = multipolygon([square, hole])
        result = classify_rectangle((1, 1, 3, 3), area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_hole_does_not_split_the_polygon(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        hole = [(0, 4), (2, 4), (2, 6), (0, 6), (0, 4)]
        area = multipolygon([square, hole])
        result = classify_rectangle((-5, 2, 15, 8), area)
        self.assertEqual(result.relation, Relation.PARTIAL)


class TestSimilarScaleCoordinates(unittest.TestCase):

    def test_large_coordinate_magnitudes(self):
        base = [(1200000, 3000000), (1200100, 3000000),
                (1200100, 3000100), (1200000, 3000100),
                (1200000, 3000000)]
        area = multipolygon([base])
        self.assertEqual(
            classify_rectangle((1200010, 3000010,
                                1200090, 3000090), area).relation,
            Relation.INSIDE)
        self.assertEqual(
            classify_rectangle((1200100, 3000010,
                                1200200, 3000090), area).relation,
            Relation.PARTIAL)
        self.assertEqual(
            classify_rectangle((1200200, 3000010,
                                1200300, 3000090), area).relation,
            Relation.OUTSIDE)

    def test_negative_coordinates(self):
        base = [(-20, -20), (-10, -20), (-10, -10), (-20, -10), (-20, -20)]
        area = multipolygon([base])
        self.assertEqual(
            classify_rectangle((-19, -19, -11, -11), area).relation,
            Relation.INSIDE)


class TestRectangleForms(unittest.TestCase):

    def setUp(self):
        self.area = multipolygon([SQUARE])

    def test_bounds_tuple(self):
        result = classify_rectangle((1, 1, 3, 3), self.area)
        self.assertEqual(result.rectangle.bounds, (1.0, 1.0, 3.0, 3.0))

    def test_reversed_bounds_are_normalised(self):
        result = classify_rectangle((3, 3, 1, 1), self.area)
        self.assertEqual(result.rectangle.bounds, (1.0, 1.0, 3.0, 3.0))
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_geojson_polygon(self):
        rectangle = geojson.Polygon([[
            (1, 1), (3, 1), (3, 3), (1, 3), (1, 1)]])
        result = classify_rectangle(rectangle, self.area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_geojson_polygon_with_reversed_ring(self):
        rectangle = geojson.Polygon([[
            (1, 1), (1, 3), (3, 3), (3, 1), (1, 1)]])
        result = classify_rectangle(rectangle, self.area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_feature_wrapped_rectangle(self):
        rectangle = geojson.Feature(geometry=geojson.Polygon([[
            (1, 1), (3, 1), (3, 3), (1, 3), (1, 1)]]))
        result = classify_rectangle(rectangle, self.area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_bbox_mapping(self):
        result = classify_rectangle({"bbox": (1, 1, 3, 3)}, self.area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_rectangle_object(self):
        result = classify_rectangle(
            geojson.Rectangle.from_bounds((1, 1, 3, 3)), self.area)
        self.assertEqual(result.relation, Relation.INSIDE)

    def test_rectangle_from_center(self):
        rectangle = geojson.Rectangle.from_center((2, 2), 2, 2)
        self.assertEqual(rectangle.bounds, (1.0, 1.0, 3.0, 3.0))

    def test_rectangle_geometry_method(self):
        result = self.area.relation_to_rectangle((1, 1, 3, 3))
        self.assertEqual(result.relation, Relation.INSIDE)
        self.assertEqual(self.area.classify_rectangle((1, 1, 3, 3)).value,
                         "inside")

    def test_malformed_rectangle_not_axis_aligned(self):
        ring = [(1, 1), (3, 1), (4, 3), (2, 3), (1, 1)]
        with self.assertRaises(ValueError):
            classify_rectangle(ring, self.area)

    def test_malformed_rectangle_wrong_point_count(self):
        with self.assertRaises(ValueError):
            classify_rectangle([(0, 0), (1, 0), (1, 1)], self.area)

    def test_malformed_rectangle_zero_width(self):
        with self.assertRaises(ValueError):
            classify_rectangle((1, 1, 1, 3), self.area)

    def test_area_must_be_polygon_or_multipolygon(self):
        with self.assertRaises(ValueError):
            classify_rectangle((1, 1, 3, 3), geojson.Point((1, 1)))

    def test_feature_wrapped_area(self):
        area = geojson.Feature(
            geometry=multipolygon([SQUARE, HOLE]))
        result = classify_rectangle((1, 1, 3, 3), area)
        self.assertEqual(result.relation, Relation.INSIDE)


class TestRelationResult(unittest.TestCase):

    def test_result_is_serialisable(self):
        result = classify_rectangle(
            (10, 2, 20, 8), multipolygon([SQUARE]))
        payload = result.as_dict()
        self.assertEqual(payload["relation"], "partial")
        self.assertTrue(payload["tangent_only"])
        self.assertEqual(payload["tangent_edges"], ["left"])
        self.assertEqual(payload["rectangle"], [10.0, 2.0, 20.0, 8.0])
        self.assertEqual(payload["corner_locations"][0], "boundary")
        left = next(edge for edge in payload["edges"]
                    if edge["side"] == "left")
        self.assertEqual(left["contacts"][0]["kind"], "overlap")

    def test_boolean_aliases(self):
        result = classify_rectangle((1, 1, 3, 3), multipolygon([SQUARE]))
        self.assertTrue(result.inside)
        self.assertFalse(result.partial)
        self.assertFalse(result.outside)

    def test_result_type(self):
        self.assertIsInstance(
            classify_rectangle((1, 1, 3, 3), multipolygon([SQUARE])),
            RectangleRelation)

    def test_relation_compares_as_string(self):
        result = classify_rectangle((1, 1, 3, 3), multipolygon([SQUARE]))
        self.assertEqual(result.relation, "inside")
        self.assertEqual(result.relation, Relation.INSIDE)
        self.assertIsInstance(result.relation, str)
