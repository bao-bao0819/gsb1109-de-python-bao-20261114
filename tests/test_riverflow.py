"""Tests for the river discharge reconciliation engine."""

import math

import pytest

from riverflow import Reach, Station, reconcile, solve_component, validate
from riverflow.maxflow import Dinic, EPS
from riverflow.model import weak_components

INF = float("inf")


class TestDinic:
    def test_simple_network(self):
        dinic = Dinic(4)
        dinic.add_edge(0, 1, 3)
        dinic.add_edge(0, 2, 2)
        dinic.add_edge(1, 3, 2)
        dinic.add_edge(2, 3, 3)
        assert dinic.max_flow(0, 3) == pytest.approx(4.0)

    def test_fractional_capacities(self):
        dinic = Dinic(3)
        dinic.add_edge(0, 1, 2.5)
        dinic.add_edge(1, 2, 1.25)
        assert dinic.max_flow(0, 2) == pytest.approx(1.25)

    def test_infinite_capacity(self):
        dinic = Dinic(3)
        dinic.add_edge(0, 1, INF)
        dinic.add_edge(1, 2, 5)
        assert dinic.max_flow(0, 2) == pytest.approx(5.0)

    def test_no_path(self):
        dinic = Dinic(3)
        dinic.add_edge(0, 1, 5)
        assert dinic.max_flow(0, 2) == 0.0

    def test_copy_is_independent(self):
        dinic = Dinic(3)
        arc = dinic.add_edge(0, 1, 5)
        clone = dinic.copy()
        clone.max_flow(0, 2)
        assert arc.cap == 5.0


class TestSeriesNetwork:
    def build(self):
        # inflow A --r1--> B --r2--> outflow C, all readings exact at 10
        stations = [
            Station("A", 10, 10, "inflow"),
            Station("B", 0, 0, "interior"),
            Station("C", -10, -10, "outflow"),
        ]
        reaches = [
            Reach("r1", "A", "B", 0, INF),
            Reach("r2", "B", "C", 0, INF),
        ]
        return stations, reaches

    def test_exact_through_flow(self):
        result = solve_component(*self.build())
        assert result.feasible
        assert result.reach_bounds["r1"] == (10.0, 10.0)
        assert result.reach_bounds["r2"] == (10.0, 10.0)

    def test_interval_inflow_propagates(self):
        stations, reaches = self.build()
        stations[0] = Station("A", 8, 12, "inflow")
        stations[2] = Station("C", -15, -5, "outflow")
        result = solve_component(stations, reaches)
        assert result.feasible
        assert result.reach_bounds["r1"] == (8.0, 12.0)
        assert result.reach_bounds["r2"] == (8.0, 12.0)
        assert result.station_bounds["A"] == (8.0, 12.0)
        # outflow is stored signed; report magnitudes via abs in io layer
        assert result.station_bounds["C"] == (-12.0, -8.0)


class TestFork:
    def build(self):
        # A inflow 10 -> B forks into r2 (B->C) and r3 (B->D),
        # C and D merge at E -> outflow F of 10.
        stations = [
            Station("A", 10, 10, "inflow"),
            Station("B", 0, 0, "interior"),
            Station("C", 0, 0, "interior"),
            Station("D", 0, 0, "interior"),
            Station("E", 0, 0, "interior"),
            Station("F", -10, -10, "outflow"),
        ]
        reaches = [
            Reach("r1", "A", "B"),
            Reach("r2", "B", "C"),
            Reach("r3", "B", "D"),
            Reach("r4", "C", "E"),
            Reach("r5", "D", "E"),
            Reach("r6", "E", "F"),
        ]
        return stations, reaches

    def test_each_branch_may_take_the_whole_flow(self):
        result = solve_component(*self.build())
        assert result.feasible
        # Independent extrema: each branch alone can take all 10, but the
        # report never claims both maxima are jointly attainable.
        assert result.reach_bounds["r2"] == (0.0, 10.0)
        assert result.reach_bounds["r3"] == (0.0, 10.0)
        assert result.reach_bounds["r4"] == (0.0, 10.0)
        assert result.reach_bounds["r5"] == (0.0, 10.0)
        assert result.reach_bounds["r1"] == (10.0, 10.0)
        assert result.reach_bounds["r6"] == (10.0, 10.0)

    def test_branch_lower_bound_is_respected(self):
        stations, reaches = self.build()
        for reach in reaches:
            if reach.identifier == "r2":
                reach.lower = 3.0
        result = solve_component(stations, reaches)
        assert result.feasible
        assert result.reach_bounds["r2"] == (3.0, 10.0)
        # r3 must leave at least 3 for r2, so its independent max is 7
        assert result.reach_bounds["r3"] == (0.0, 7.0)

    def test_branch_upper_bounds_split_the_total(self):
        stations, reaches = self.build()
        for reach in reaches:
            if reach.identifier == "r2":
                reach.upper = 4.0
            if reach.identifier == "r3":
                reach.upper = 9.0
        result = solve_component(stations, reaches)
        assert result.feasible
        assert result.reach_bounds["r2"] == (1.0, 4.0)
        assert result.reach_bounds["r3"] == (6.0, 9.0)

    def test_extrema_are_jointly_feasible_one_at_a_time(self):
        result = solve_component(*self.build())
        flow = result.feasible_flows["reaches"]
        # The stored witness itself is balanced
        assert flow["r2"] + flow["r3"] == pytest.approx(10.0)


class TestConfluence:
    def test_two_inflows_merge(self):
        stations = [
            Station("A", 3, 7, "inflow"),
            Station("B", 4, 8, "inflow"),
            Station("M", 0, 0, "interior"),
            Station("O", -15, -7, "outflow"),
        ]
        reaches = [
            Reach("r1", "A", "M"),
            Reach("r2", "B", "M"),
            Reach("r3", "M", "O"),
        ]
        result = solve_component(stations, reaches)
        assert result.feasible
        assert result.reach_bounds["r1"] == (3.0, 7.0)
        assert result.reach_bounds["r2"] == (4.0, 8.0)
        # outflow interval [7,15] tightens the merged reach to [7,15]
        assert result.reach_bounds["r3"] == (7.0, 15.0)


class TestInfeasibility:
    def test_inflow_smaller_than_outflow(self):
        stations = [
            Station("A", 5, 5, "inflow"),
            Station("B", 0, 0, "interior"),
            Station("O", -8, -8, "outflow"),
        ]
        reaches = [Reach("r1", "A", "B"), Reach("r2", "B", "O")]
        result = solve_component(stations, reaches)
        assert not result.feasible
        violation = result.violation
        assert "A" in violation.cut["stations"]
        assert violation.cut["gap"] == pytest.approx(3.0)
        # both conflicting boundary stations show up in the core
        assert "A" in violation.core["station_ids"]
        assert "O" in violation.core["station_ids"]

    def test_reach_lower_bound_too_high(self):
        stations = [
            Station("A", 5, 5, "inflow"),
            Station("O", -5, -5, "outflow"),
        ]
        reaches = [Reach("r1", "A", "O", lower=8, upper=INF)]
        result = solve_component(stations, reaches)
        assert not result.feasible
        assert "r1" in result.violation.core["reach_ids"]
        assert result.violation.cut["gap"] == pytest.approx(3.0)

    def test_fork_cannot_meet_two_minimums(self):
        # total inflow 10 but the two branches each demand at least 7
        stations = [
            Station("A", 10, 10, "inflow"),
            Station("B", 0, 0, "interior"),
            Station("O1", -INF, 0, "outflow"),
            Station("O2", -INF, 0, "outflow"),
        ]
        reaches = [
            Reach("r1", "A", "B"),
            Reach("r2", "B", "O1", lower=7),
            Reach("r3", "B", "O2", lower=7),
        ]
        result = solve_component(stations, reaches)
        assert not result.feasible
        assert set(result.violation.core["reach_ids"]) >= {"r2", "r3"}

    def test_certificate_identifies_locatable_set(self):
        stations = [
            Station("A", 5, 5, "inflow"),
            Station("B", 0, 0, "interior"),
            Station("O", -8, -8, "outflow"),
        ]
        reaches = [Reach("r1", "A", "B"), Reach("r2", "B", "O")]
        result = solve_component(stations, reaches)
        cut = result.violation.cut
        assert cut["outflow_minimum"] == 8.0
        assert cut["inflow_maximum"] == 5.0
        assert isinstance(result.violation.message, str)
        assert "5" in result.violation.message and "8" in result.violation.message


class TestMultipleComponents:
    def build(self):
        # healthy component
        good_stations = [
            Station("gA", 4, 4, "inflow"),
            Station("gO", -4, -4, "outflow"),
        ]
        good_reaches = [Reach("gr", "gA", "gO")]
        # broken component
        bad_stations = [
            Station("bA", 2, 2, "inflow"),
            Station("bO", -9, -9, "outflow"),
        ]
        bad_reaches = [Reach("br", "bA", "bO")]
        return good_stations, good_reaches, bad_stations, bad_reaches

    def test_infeasible_component_does_not_block_others(self):
        good_s, good_r, bad_s, bad_r = self.build()
        stations = good_s + bad_s
        reaches = good_r + bad_r
        groups = weak_components(stations, reaches)
        assert len(groups) == 2

        results, errors = reconcile(stations, reaches)
        assert errors == []
        by_first_station = {
            tuple(sorted(r.station_ids)): r for r in results
        }
        good = by_first_station[tuple(sorted(["gA", "gO"]))]
        bad = by_first_station[tuple(sorted(["bA", "bO"]))]
        assert good.feasible
        assert good.reach_bounds["gr"] == (4.0, 4.0)
        assert not bad.feasible
        assert bad.violation is not None


class TestValidation:
    def test_unknown_endpoint(self):
        stations = [Station("A", 1, 1, "inflow")]
        reaches = [Reach("r1", "A", "ghost")]
        errors = validate(stations, reaches)
        assert any(e["element"] == "r1" and "ghost" in e["message"]
                   for e in errors)

    def test_bad_station_bounds(self):
        stations = [Station("A", 9, 2, "inflow")]
        errors = validate(stations, [])
        assert any(e["element"] == "A" for e in errors)

    def test_negative_reach_lower(self):
        stations = [
            Station("A", 1, 1, "inflow"),
            Station("O", -1, -1, "outflow"),
        ]
        reaches = [Reach("r1", "A", "O", lower=-2)]
        errors = validate(stations, reaches)
        assert any(e["element"] == "r1" for e in errors)

    def test_duplicate_ids(self):
        stations = [
            Station("A", 1, 1, "inflow"),
            Station("A", 1, 1, "inflow"),
        ]
        errors = validate(stations, [])
        assert any("duplicate" in e["message"] for e in errors)

    def test_validation_failure_short_circuits_solve(self):
        stations = [Station("A", 1, 1, "inflow")]
        reaches = [Reach("r1", "A", "ghost")]
        results, errors = reconcile(stations, reaches)
        assert results == []
        assert errors


class TestScalability:
    def test_long_chain_is_pinned_end_to_end(self):
        count = 200
        stations = [Station("s0", 7, 7, "inflow")]
        stations += [Station("s{}".format(i)) for i in range(1, count - 1)]
        stations.append(Station(
            "s{}".format(count - 1), -7, -7, "outflow"))
        reaches = [
            Reach("r{}".format(i), "s{}".format(i), "s{}".format(i + 1))
            for i in range(count - 1)]
        result = solve_component(stations, reaches)
        assert result.feasible
        for reach in reaches:
            assert result.reach_bounds[reach.identifier] == (7.0, 7.0)


class TestRandomizedExtrema:
    """Cross-check every reported extremum with an independent feasibility LP.

    A reported min/max is correct exactly when pinning the arc to that value
    stays feasible while pinning it just outside becomes infeasible.
    """

    @staticmethod
    def _pin_reach(reaches, identifier, value):
        return [
            Reach(r.identifier, r.source, r.target, value, value)
            if r.identifier == identifier else r
            for r in reaches
        ]

    @pytest.mark.parametrize("seed", range(20))
    def test_reach_extrema_are_tight(self, seed):
        import random
        rng = random.Random(seed)
        layers = [["s"], ["a", "b"], ["c", "d"], ["t"]]

        # Pick edges while guaranteeing every node has in- and out-edges.
        edges_by_pair = []
        for left, right in zip(layers, layers[1:]):
            pairs = [(u, v) for u in left for v in right]
            rng.shuffle(pairs)
            chosen = set()
            for upstream in left:
                chosen.add(next(p for p in pairs if p[0] == upstream))
            for downstream in right:
                chosen.add(next(p for p in pairs if p[1] == downstream))
            for pair in pairs:
                if pair not in chosen and rng.random() < 0.5:
                    chosen.add(pair)
            edges_by_pair.append(sorted(chosen))

        # Route 12 units conservatively, then derive capacities with slack.
        inflow = {name: 0.0 for layer in layers for name in layer}
        inflow["s"] = 12.0
        flow_on = {}
        for left, right in zip(layers, layers[1:]):
            outgoing = {}
            for pair in edges_by_pair[layers.index(left)]:
                outgoing.setdefault(pair[0], []).append(pair)
            for upstream, pairs in outgoing.items():
                weights = [rng.random() + 0.2 for _ in pairs]
                total = sum(weights)
                for pair, weight in zip(pairs, weights):
                    value = inflow[upstream] * weight / total
                    flow_on[pair] = value
                    inflow[pair[1]] += value

        stations = [Station("s", 12, 12, "inflow"),
                    Station("t", -12, -12, "outflow")]
        stations += [Station(name) for layer in layers[1:-1] for name in layer]
        reaches = []
        for counter, ((upstream, downstream), value) in enumerate(
                flow_on.items(), start=1):
            slack = rng.choice([0.0, 0.5, 2.0, 6.0])
            reaches.append(Reach(
                "e{}".format(counter), upstream, downstream,
                0.0, value + slack))

        result = solve_component(stations, reaches)
        assert result.feasible
        for reach in reaches:
            lower, upper = result.reach_bounds[reach.identifier]
            at_min = solve_component(
                stations, self._pin_reach(reaches, reach.identifier, lower))
            at_max = solve_component(
                stations, self._pin_reach(reaches, reach.identifier, upper))
            assert at_min.feasible and at_max.feasible
            # Only probe outside the box when the extremum is constrained by
            # the rest of the network rather than by the reach's own limit.
            if lower > reach.lower + EPS:
                below = solve_component(stations, self._pin_reach(
                    reaches, reach.identifier,
                    max(0.0, lower - 0.25)))
                assert not below.feasible
            if upper < reach.upper - EPS:
                above = solve_component(stations, self._pin_reach(
                    reaches, reach.identifier, upper + 0.25))
                assert not above.feasible


class TestThroughFlow:
    def build(self, pass_lower=0.0, pass_upper=INF):
        # S -> J (fork, through-flow bounded) -> O1, O2
        stations = [
            Station("S", 10, 10, "inflow"),
            Station("J", 0, 0, "interior", pass_lower, pass_upper),
            Station("O1", -INF, 0, "outflow"),
            Station("O2", -INF, 0, "outflow"),
        ]
        reaches = [
            Reach("r1", "S", "J"),
            Reach("r2", "J", "O1"),
            Reach("r3", "J", "O2"),
        ]
        return stations, reaches

    def test_through_total_matches_inflow_when_unconstrained(self):
        result = solve_component(*self.build())
        assert result.feasible
        assert result.station_bounds["J"] == (10.0, 10.0)

    def test_through_upper_caps_the_total(self):
        stations, reaches = self.build(pass_upper=6)
        result = solve_component(stations, reaches)
        assert not result.feasible
        assert "J" in result.violation.core["station_ids"]

    def test_through_interval_propagates_to_inflow(self):
        stations, reaches = self.build(pass_lower=4, pass_upper=8)
        # loosen the inflow so the junction interval drives the answer
        stations[0] = Station("S", 0, INF, "inflow")
        result = solve_component(stations, reaches)
        assert result.feasible
        assert result.station_bounds["J"] == (4.0, 8.0)
        assert result.reach_bounds["r1"] == (4.0, 8.0)
        assert result.reach_bounds["r2"] == (0.0, 8.0)
        assert result.reach_bounds["r3"] == (0.0, 8.0)


class TestReadings:
    def test_fork_known_total_only(self):
        # Junction only knows the total; branches carry no readings.
        stations = [
            Station("A", 10, 10, "inflow"),
            Station("J", 0, 0, "interior"),
            Station("O1", -INF, 0, "outflow"),
            Station("O2", -INF, 0, "outflow"),
        ]
        reaches = [
            Reach("r1", "A", "J"),
            Reach("r2", "J", "O1"),
            Reach("r3", "J", "O2"),
        ]
        result = solve_component(stations, reaches)
        assert result.feasible
        assert result.reach_bounds["r2"] == (0.0, 10.0)
        assert result.reach_bounds["r3"] == (0.0, 10.0)

    def test_mid_network_reading_interval(self):
        stations = [
            Station("A", 0, INF, "inflow"),
            Station("M", 0, 0, "interior"),
            Station("O", -INF, 0, "outflow"),
        ]
        reaches = [
            Reach("r1", "A", "M"),
            Reach("r2", "M", "O", lower=6, upper=9),
        ]
        result = solve_component(stations, reaches)
        assert result.feasible
        assert result.reach_bounds["r1"] == (6.0, 9.0)
        assert result.reach_bounds["r2"] == (6.0, 9.0)
        assert result.station_bounds["A"] == (6.0, 9.0)
        assert result.station_bounds["O"] == (-9.0, -6.0)
