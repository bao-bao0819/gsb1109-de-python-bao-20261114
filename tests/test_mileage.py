import unittest
from fractions import Fraction

import geojson
from geojson.mileage import apportion, apportion_mileage


class ApportionTest(unittest.TestCase):
    def test_splits_segment_by_distance(self):
        result = apportion_mileage([0, 10], [10], bin_width=5)
        self.assertEqual(result["bins"], {0: 5, 1: 5})
        self.assertEqual(result["stops"], [])
        self.assertEqual(result["total"], 10)

    def test_largest_remainder_uses_bin_number_for_ties(self):
        result = apportion_mileage([0, 10], [7], bin_width=5)
        self.assertEqual(result["bins"], {0: 4, 1: 3})

    def test_backtracking_double_counts_distance(self):
        result = apportion_mileage([0, 10, 0], [7, 7], bin_width=5)
        self.assertEqual(result["bins"], {0: 7, 1: 7})
        self.assertEqual(result["total"], 14)

    def test_return_to_original_bin_conserves_units(self):
        result = apportion_mileage([0, 12, 8], [12, 8], bin_width=5)
        self.assertEqual(result["bins"], {0: 5, 1: 9, 2: 6})
        self.assertEqual(result["bins_total"], 20)
        self.assertEqual(result["total"], 20)

    def test_zero_length_segment_on_boundary_is_a_stop(self):
        result = apportion_mileage([0, 5, 5, 10], [3, 4, 3], bin_width=5)
        self.assertEqual(result["bins"], {0: 3, 1: 3})
        self.assertEqual(len(result["stops"]), 1)
        stop = result["stops"][0]
        self.assertEqual(stop["mileage"], 5)
        self.assertEqual(stop["units"], 4)
        self.assertEqual(stop["left_bin"], 0)
        self.assertEqual(stop["right_bin"], 1)
        self.assertEqual(stop["segment"], 1)
        self.assertEqual(result["stops_total"], 4)
        self.assertEqual(result["total"], 10)

    def test_zero_length_segment_inside_bin_is_not_a_stop(self):
        result = apportion_mileage([2, 2, 7], [3, 5], bin_width=5)
        self.assertEqual(result["bins"], {0: 6, 1: 2})
        self.assertEqual(result["stops"], [])

    def test_stop_keeps_route_order(self):
        result = apportion_mileage(
            [0, 5, 5, 10, 10], [1, 1, 1, 1], bin_width=5)
        self.assertEqual([stop["segment"] for stop in result["stops"]], [1, 3])
        self.assertEqual(result["total"], 4)

    def test_stop_wins_remainder_over_bin(self):
        result = apportion_mileage(
            [0, 5, 5], [Fraction(2, 5), Fraction(3, 5)], bin_width=5)
        # The bin has 0.4 units and the stop has 0.6; only the stop rounds up.
        self.assertEqual(result["bins"], {})
        self.assertEqual(result["stops"][0]["units"], 1)
        self.assertEqual(result["total"], 1)

    def test_tie_prefers_lower_mileage_claim(self):
        # Bin 1 and the stop at mileage 5 both have a remainder of one half.
        # The stop sits below bin 1, so it takes the extra unit.
        result = apportion_mileage(
            [0, 5, 5, 10], [1, Fraction(1, 2), Fraction(1, 2)], bin_width=5)
        self.assertEqual(result["bins"], {0: 1})
        self.assertEqual(result["stops"][0]["units"], 1)

    def test_tie_prefers_bin_over_stop_at_same_position(self):
        # Bin 0 is below the stop at mileage 5, so it wins the tie.
        result = apportion_mileage(
            [0, 5, 5], [Fraction(1, 2), Fraction(1, 2)], bin_width=5)
        self.assertEqual(result["bins"], {0: 1})
        self.assertEqual(result["stops"][0]["units"], 0)

    def test_explicit_boundaries(self):
        result = apportion_mileage(
            [0, 9], [9], boundaries=[0, 2, 5, 9])
        self.assertEqual(result["bins"], {0: 2, 1: 3, 2: 4})

    def test_explicit_boundaries_allow_stop_at_last_edge(self):
        result = apportion_mileage(
            [0, 9, 9], [9, 2], boundaries=[0, 2, 5, 9])
        self.assertEqual(result["bins"], {0: 2, 1: 3, 2: 4})
        self.assertEqual(result["stops"][0]["mileage"], 9)
        self.assertIsNone(result["stops"][0]["right_bin"])
        self.assertEqual(result["total"], 11)

    def test_float_input_is_treated_decimal_exactly(self):
        result = apportion_mileage([0, 0.1, 0.3], [0.5, 0.5],
                                   bin_width=0.05)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["bins"], {0: 1})

    def test_total_must_be_whole(self):
        with self.assertRaises(ValueError):
            apportion_mileage([0, 1], [Fraction(1, 2)], bin_width=1)

    def test_negative_emissions_rejected(self):
        with self.assertRaises(ValueError):
            apportion_mileage([0, 1], [-1], bin_width=1)

    def test_mileage_outside_boundaries_rejected(self):
        with self.assertRaises(ValueError):
            apportion_mileage([0, 11], [1], boundaries=[0, 10])

    def test_unsorted_boundaries_rejected(self):
        with self.assertRaises(ValueError):
            apportion_mileage([0, 1], [1], boundaries=[0, 2, 1])

    def test_width_and_boundaries_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            apportion_mileage([0, 1], [1], bin_width=1, boundaries=[0, 1])

    def test_non_positive_width_rejected(self):
        with self.assertRaises(ValueError):
            apportion_mileage([0, 1], [1], bin_width=0)

    def test_non_finite_values_rejected(self):
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                apportion_mileage([0, value], [1], bin_width=1)


class ApportionLineStringTest(unittest.TestCase):
    def test_reads_explicit_readings(self):
        line = geojson.LineString([(0, 0), (0, 2), (0, 5)])
        result = apportion(line, mileage=[0, 2, 5], emissions=[2, 3],
                           bin_width=1)
        self.assertEqual(result["bins"], {0: 1, 1: 1, 2: 1, 3: 1, 4: 1})
        self.assertEqual(result["total"], 5)

    def test_reads_attached_geometry_members(self):
        line = geojson.LineString(
            [(0, 0), (0, 10)], mileage=[0, 10], emissions=[4])
        result = apportion(line, bin_width=5)
        self.assertEqual(result["bins"], {0: 2, 1: 2})

    def test_reads_feature_properties(self):
        feature = geojson.Feature(
            geometry=geojson.LineString([(0, 0), (0, 10)]),
            properties={"odometer": [0, 10], "emission_totals": [8]})
        result = apportion(feature, bin_width=5)
        self.assertEqual(result["bins"], {0: 4, 1: 4})

    def test_reads_mileage_from_coordinate_axis(self):
        line = geojson.LineString([(0, 0, 0), (0, 0, 10)])
        result = apportion(line, emissions=[6], bin_width=5, mileage_axis=2)
        self.assertEqual(result["bins"], {0: 3, 1: 3})

    def test_wrong_vertex_count_rejected(self):
        line = geojson.LineString([(0, 0), (0, 10)])
        with self.assertRaises(ValueError):
            apportion(line, mileage=[0, 5, 10], emissions=[4, 6],
                      bin_width=5)

    def test_wrong_segment_count_rejected(self):
        line = geojson.LineString([(0, 0), (0, 10)])
        with self.assertRaises(ValueError):
            apportion(line, mileage=[0, 10], emissions=[4, 6], bin_width=5)

    def test_missing_readings_rejected(self):
        line = geojson.LineString([(0, 0), (0, 10)])
        with self.assertRaises(ValueError):
            apportion(line, emissions=[4], bin_width=5)
        with self.assertRaises(ValueError):
            apportion(line, mileage=[0, 10], bin_width=5)

    def test_non_linestring_rejected(self):
        with self.assertRaises(ValueError):
            apportion(geojson.Point((0, 0)), mileage=[0], emissions=[])

    def test_plain_position_sequence(self):
        result = apportion([(0, 0), (0, 10)], mileage=[0, 10],
                           emissions=[4], bin_width=5)
        self.assertEqual(result["bins"], {0: 2, 1: 2})


class ConservationTest(unittest.TestCase):
    def test_random_walks_conserve_batch_total(self):
        import random
        rng = random.Random(20261114)
        for _ in range(200):
            readings = [rng.randint(0, 40)]
            emissions = []
            for _ in range(rng.randint(1, 12)):
                readings.append(max(0, readings[-1] + rng.randint(-12, 12)))
                emissions.append(rng.randint(0, 20))
            result = apportion_mileage(readings, emissions, bin_width=3)
            self.assertEqual(result["total"], sum(emissions))
            self.assertEqual(
                result["bins_total"] + result["stops_total"],
                sum(emissions))
            self.assertEqual(result["bins_total"],
                             sum(result["bins"].values()))

    def test_long_backtrack_conserves_units(self):
        readings = [0, 7, 3, 11, 11, 6, 14, 2]
        emissions = [5, 7, 9, 4, 6, 8, 3]
        result = apportion_mileage(readings, emissions, bin_width=4)
        self.assertEqual(result["total"], sum(emissions))
        self.assertEqual(
            result["bins_total"] + result["stops_total"], sum(emissions))


if __name__ == "__main__":
    unittest.main()
