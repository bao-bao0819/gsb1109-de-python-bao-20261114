"""Tests for out-and-back inspection record matching."""

import unittest

import geojson
from geojson.inspection import (
    Candidate,
    INBOUND,
    InspectionMatcher,
    InspectionRoute,
    Observation,
    OUTBOUND,
    UnmatchedObservationError,
    haversine,
    match_inspection_records,
)


def straight_route(step=0.01, count=10):
    """An east-west route near the equator, roughly 1.11 km per step."""
    return [(index * step, 0.0) for index in range(count + 1)]


class TestHaversine(unittest.TestCase):
    def test_known_distance(self):
        # One degree of longitude at the equator.
        distance = haversine((0.0, 0.0), (1.0, 0.0))
        self.assertAlmostEqual(distance, 111_195.0, delta=20.0)

    def test_same_point(self):
        self.assertEqual(haversine((10.0, 20.0), (10.0, 20.0)), 0.0)


class TestInspectionRoute(unittest.TestCase):
    def test_stations_are_cumulative(self):
        route = InspectionRoute(straight_route())
        self.assertEqual(route.stations[0], 0.0)
        expected_step = haversine((0.0, 0.0), (0.01, 0.0))
        self.assertAlmostEqual(route.length, expected_step * 10, places=6)
        self.assertAlmostEqual(route.stations[5], expected_step * 5, places=6)

    def test_project_snaps_on_route_point(self):
        route = InspectionRoute(straight_route())
        projection = route.project((0.035, 0.0))
        self.assertLess(projection.offset, 1e-6)
        self.assertAlmostEqual(
            projection.station, route.length * 0.35, delta=1e-6
        )

    def test_project_reports_offset(self):
        route = InspectionRoute(straight_route())
        projection = route.project((0.05, 0.001))
        self.assertAlmostEqual(
            projection.offset,
            haversine((0.05, 0.0), (0.05, 0.001)),
            delta=1.0,
        )

    def test_accepts_geojson_linestring(self):
        line = geojson.LineString(straight_route())
        route = InspectionRoute(line)
        self.assertGreater(route.length, 0.0)

    def test_accepts_geojson_multilinestring(self):
        first = straight_route(count=5)
        second = [(0.05 + index * 0.01, 0.0) for index in range(6)]
        multi = geojson.MultiLineString([first, second])
        route = InspectionRoute(multi)
        self.assertAlmostEqual(route.length, InspectionRoute(
            straight_route()).length, places=6)

    def test_rejects_short_route(self):
        with self.assertRaises(ValueError):
            InspectionRoute([(1.0, 2.0)])


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.route = InspectionRoute(straight_route())
        self.length = self.route.length

    def test_outbound_resolved_by_mileage(self):
        observations = [
            Observation("a", (0.02, 0.0), self.length * 0.2),
            Observation("b", (0.05, 0.0), self.length * 0.5),
            Observation("c", (0.08, 0.0), self.length * 0.8),
        ]
        result = InspectionMatcher(self.route, 5.0, 5.0).match(observations)
        self.assertEqual(result.feasible_pass_sets, ((OUTBOUND,) * 3,))
        self.assertTrue(all(not obs.is_ambiguous for obs in result.observations))
        self.assertEqual(
            [obs.pass_names[0] for obs in result.observations],
            [OUTBOUND, OUTBOUND, OUTBOUND],
        )

    def test_inbound_resolved_by_mileage(self):
        observations = [
            Observation("a", (0.08, 0.0), self.length * 1.2),
            Observation("b", (0.05, 0.0), self.length * 1.5),
            Observation("c", (0.02, 0.0), self.length * 1.8),
        ]
        result = InspectionMatcher(self.route, 5.0, 5.0).match(observations)
        self.assertEqual(result.feasible_pass_sets, ((INBOUND,) * 3,))
        self.assertEqual(
            [obs.pass_names[0] for obs in result.observations],
            [INBOUND, INBOUND, INBOUND],
        )

    def test_single_point_two_candidates_resolved_by_following_points(self):
        # Near the turnaround a loose mileage tolerance leaves two pass
        # candidates for the first fix.  Later fixes with inbound-only
        # mileage readings prune every all-outbound interpretation.
        loose_mileage = self.length * 0.25
        observations = [
            Observation("a", (0.09, 0.0), self.length * 1.1),
            Observation("b", (0.06, 0.0), self.length * 1.4),
            Observation("c", (0.03, 0.0), self.length * 1.7),
        ]
        first_candidates = InspectionMatcher(
            self.route, 5.0, loose_mileage
        ).match([observations[0]])
        self.assertEqual(
            len(first_candidates.observations[0].candidates), 2
        )

        result = InspectionMatcher(
            self.route, 5.0, loose_mileage
        ).match(observations)
        # Stations decrease, so no all-outbound chain can cover the record.
        self.assertNotIn((OUTBOUND,) * 3, result.feasible_pass_sets)
        self.assertTrue(
            all(pass_set[-1] == INBOUND
                for pass_set in result.feasible_pass_sets)
        )
        # The following observations are pinned to the inbound pass even
        # though the turnaround fix itself keeps both explanations.
        self.assertEqual(
            result.observations[1].pass_names, (INBOUND,)
        )
        self.assertEqual(
            result.observations[2].pass_names, (INBOUND,)
        )

        # With a mileage tolerance tighter than the outbound residual, the
        # whole sequence resolves to a single deterministic inbound segment.
        tight = InspectionMatcher(
            self.route, 5.0, self.length * 0.15
        ).match(observations)
        self.assertEqual(tight.feasible_pass_sets, ((INBOUND,) * 3,))
        for observation in tight.observations:
            self.assertEqual(len(observation.candidates), 1)
            self.assertEqual(observation.candidates[0].pass_name, INBOUND)

    def test_monotonicity_prunes_inbound_chain(self):
        # Every point fits both passes on its own, but stations increase so
        # the inbound (station-decreasing) chain cannot cover the sequence.
        observations = [
            Observation("a", (0.03, 0.0), self.length),
            Observation("b", (0.06, 0.0), self.length),
            Observation("c", (0.09, 0.0), self.length),
        ]
        result = InspectionMatcher(
            self.route, 5.0, self.length
        ).match(observations)
        self.assertNotIn((INBOUND,) * 3, result.feasible_pass_sets)
        self.assertTrue(
            all(pass_set[0] == OUTBOUND
                for pass_set in result.feasible_pass_sets)
        )

    def test_monotonicity_prunes_outbound_chain(self):
        observations = [
            Observation("a", (0.09, 0.0), self.length),
            Observation("b", (0.06, 0.0), self.length),
            Observation("c", (0.03, 0.0), self.length),
        ]
        result = InspectionMatcher(
            self.route, 5.0, self.length
        ).match(observations)
        self.assertNotIn((OUTBOUND,) * 3, result.feasible_pass_sets)
        self.assertTrue(
            all(pass_set[-1] == INBOUND
                for pass_set in result.feasible_pass_sets)
        )

    def test_round_trip_switches_pass_once(self):
        observations = [
            Observation("o1", (0.02, 0.0), self.length * 0.2),
            Observation("o2", (0.06, 0.0), self.length * 0.6),
            Observation("i1", (0.07, 0.0), self.length * 1.3),
            Observation("i2", (0.03, 0.0), self.length * 1.7),
        ]
        result = InspectionMatcher(self.route, 5.0, 5.0).match(observations)
        self.assertEqual(
            result.feasible_pass_sets,
            ((OUTBOUND, OUTBOUND, INBOUND, INBOUND),),
        )
        passes = [
            observation.pass_names[0] for observation in result.observations
        ]
        self.assertEqual(passes, [OUTBOUND, OUTBOUND, INBOUND, INBOUND])

    def test_reverse_pass_switch_is_impossible(self):
        # Tight mileage pins the first fix to inbound and the second to
        # outbound: an inbound -> outbound ordering can never be a round trip.
        observations = [
            Observation("a", (0.08, 0.0), self.length * 1.2),
            Observation("b", (0.02, 0.0), self.length * 0.2),
        ]
        with self.assertRaises(ValueError):
            InspectionMatcher(self.route, 5.0, 5.0).match(observations)

    def test_ambiguous_record_keeps_both_positions(self):
        # One mid-route observation whose mileage fits neither pass exactly
        # but sits within the tolerance of both: the full record cannot tell
        # the two passes apart.
        observation = Observation("x", (0.05, 0.0), self.length)
        result = InspectionMatcher(
            self.route, 5.0, self.length * 0.5 + 1.0
        ).match([observation])
        self.assertTrue(result.is_ambiguous)
        self.assertEqual(
            result.feasible_pass_sets,
            ((INBOUND,), (OUTBOUND,)),
        )
        matched = result.observations[0]
        self.assertTrue(matched.is_ambiguous)
        self.assertEqual(
            sorted(matched.pass_names), [INBOUND, OUTBOUND]
        )
        stations = {candidate.station for candidate in matched.candidates}
        self.assertEqual(stations, {self.length * 0.5})

    def test_turnaround_point_fits_both_passes(self):
        observation = Observation("turn", (0.1, 0.0), self.length)
        result = InspectionMatcher(
            self.route, 5.0, 5.0
        ).match([observation])
        self.assertTrue(result.is_ambiguous)
        candidates = result.observations[0].candidates
        self.assertEqual(
            sorted(candidate.pass_name for candidate in candidates),
            [INBOUND, OUTBOUND],
        )
        for candidate in candidates:
            self.assertAlmostEqual(
                candidate.implied_mileage, self.length, places=6
            )

    def test_position_tolerance_rejects_fix(self):
        observation = Observation("far", (0.05, 0.05), self.length * 0.5)
        with self.assertRaises(UnmatchedObservationError) as context:
            InspectionMatcher(self.route, 100.0, 5.0).match([observation])
        self.assertEqual(context.exception.identifier, "far")
        self.assertGreater(context.exception.position_residual, 100.0)

    def test_mileage_tolerance_rejects_reading(self):
        observation = Observation("bad-mileage", (0.02, 0.0), self.length)
        with self.assertRaises(UnmatchedObservationError) as context:
            InspectionMatcher(self.route, 5.0, 5.0).match([observation])
        residuals = context.exception.mileage_residuals
        self.assertIn(OUTBOUND, residuals)
        self.assertIn(INBOUND, residuals)

    def test_tolerances_are_independent(self):
        # A noisy fix with a clean mileage reading: generous position
        # tolerance, tight mileage tolerance still resolves the pass.
        observation = Observation(
            "noisy", (0.04, 0.0005), self.length * 0.4
        )
        matcher = InspectionMatcher(self.route, 200.0, 5.0)
        result = matcher.match([observation])
        self.assertEqual(result.feasible_pass_sets, ((OUTBOUND,),))
        candidate = result.observations[0].candidates[0]
        self.assertIsInstance(candidate, Candidate)
        self.assertGreater(candidate.position_residual, 0.0)
        self.assertLessEqual(candidate.position_residual, 200.0)
        self.assertLessEqual(abs(candidate.mileage_residual), 5.0)

    def test_accepts_tuple_records(self):
        records = [("a", (0.01, 0.0), self.length * 0.1)]
        result = match_inspection_records(
            self.route, records, 5.0, 5.0
        )
        self.assertEqual(result.observations[0].identifier, "a")

    def test_empty_record(self):
        result = InspectionMatcher(self.route, 5.0, 5.0).match([])
        self.assertEqual(result.observations, ())
        self.assertEqual(result.feasible_pass_sets, ())
        self.assertFalse(result.is_ambiguous)

    def test_negative_tolerance_rejected(self):
        with self.assertRaises(ValueError):
            InspectionMatcher(self.route, -1.0, 5.0)
        with self.assertRaises(ValueError):
            InspectionMatcher(self.route, 5.0, -1.0)


class TestReporting(unittest.TestCase):
    def setUp(self):
        self.route = InspectionRoute(straight_route())
        self.length = self.route.length

    def test_one_row_per_observation_when_ambiguous(self):
        observations = [
            Observation("a", (0.05, 0.0), self.length),
            Observation("b", (0.05, 0.0), self.length),
        ]
        result = InspectionMatcher(
            self.route, 5.0, self.length
        ).match(observations)
        self.assertTrue(result.is_ambiguous)
        rows = result.report_rows()
        # Two observations must remain two rows, not four candidate rows.
        self.assertEqual(len(rows), len(observations))
        self.assertEqual([row.identifier for row in rows], ["a", "b"])
        for row in rows:
            self.assertTrue(row.ambiguous)
            self.assertEqual(row.candidate_count, 2)
            self.assertIsNone(row.pass_name)
            self.assertIsNone(row.station)

    def test_resolved_rows_carry_position_and_mileage(self):
        observations = [
            Observation("a", (0.02, 0.0), self.length * 0.2),
            Observation("b", (0.08, 0.0), self.length * 1.2),
        ]
        result = InspectionMatcher(self.route, 5.0, 5.0).match(observations)
        rows = result.report_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row.pass_name for row in rows], [OUTBOUND, INBOUND]
        )
        self.assertTrue(all(not row.ambiguous for row in rows))
        self.assertAlmostEqual(rows[0].station, self.length * 0.2, delta=1.0)
        self.assertAlmostEqual(rows[1].station, self.length * 0.8, delta=1.0)
        self.assertLessEqual(
            max(abs(row.mileage_residual) for row in rows), 5.0
        )

    def test_candidates_for_pass_dedupes_observations(self):
        observation = Observation("x", (0.05, 0.0), self.length)
        result = InspectionMatcher(
            self.route, 5.0, self.length * 0.5 + 1.0
        ).match([observation])
        outbound = result.candidates_for(OUTBOUND)
        inbound = result.candidates_for(INBOUND)
        self.assertEqual(len(outbound), 1)
        self.assertEqual(len(inbound), 1)
        # Both passes describe the same single observation, so totals must
        # never be summed into two inspections.
        total_rows = len(result.report_rows())
        self.assertEqual(total_rows, 1)


if __name__ == "__main__":
    unittest.main()
