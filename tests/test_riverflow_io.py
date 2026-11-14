"""End-to-end tests for the GeoJSON reconciliation pipeline."""

import json

import pytest

from riverflow.io import (
    build_model,
    reconcile_layers,
    split_features,
)
from riverflow.__main__ import main


def point(identifier, properties):
    return {
        "id": identifier, "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "properties": properties,
    }


def line(identifier, source, target, properties=None):
    return {
        "id": identifier, "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        "properties": {"from": source, "to": target, **(properties or {})},
    }


def fork_features():
    return [
        point("S", {"kind": "inflow", "flow": 10}),
        point("J", {"kind": "interior", "total_flow": 10}),
        point("O1", {"kind": "outflow", "flow_range": [2, 9]}),
        point("O2", {"kind": "outflow"}),
        line("R1", "S", "J"),
        line("R2", "J", "O1"),
        line("R3", "J", "O2"),
    ]


class TestSplitFeatures:
    def test_mixed_layer_split(self):
        stations, reaches, unknown = split_features(fork_features())
        assert {f["id"] for f in stations} == {"S", "J", "O1", "O2"}
        assert {f["id"] for f in reaches} == {"R1", "R2", "R3"}
        assert unknown == []

    def test_endpoint_properties_imply_reach_without_line_geometry(self):
        feature = point("R", {"from": "S", "to": "J"})
        stations, reaches, unknown = split_features([feature])
        assert reaches and not stations and not unknown


class TestPipeline:
    def test_branch_extrema_are_independent(self):
        features = fork_features()
        report = reconcile_layers(
            *split_features(features)[:2])
        props = {f["id"]: f["properties"] for f in features}
        assert props["R2"]["reconcile_status"] == "ok"
        assert props["R3"]["reconcile_status"] == "ok"
        # O1's [2, 9] outflow interval binds R2; R3 takes the complement,
        # and each extremum is attainable on its own.
        assert props["R2"]["flow_min"] == 2.0
        assert props["R2"]["flow_max"] == 9.0
        assert props["R3"]["flow_min"] == 1.0
        assert props["R3"]["flow_max"] == 8.0
        assert report["infeasible_component_count"] == 0

    def test_outflow_magnitudes_are_ordered(self):
        features = fork_features()
        reconcile_layers(*split_features(features)[:2])
        o1 = next(f for f in features if f["id"] == "O1")["properties"]
        assert o1["flow_min"] <= o1["flow_max"]
        assert (o1["flow_min"], o1["flow_max"]) == (2.0, 9.0)

    def test_through_flow_reading_drives_junction(self):
        features = fork_features()
        stations, reaches, _ = split_features(features)
        model_stations, _ = build_model(stations, reaches)
        junction = next(s for s in model_stations if s.identifier == "J")
        assert (junction.pass_lower, junction.pass_upper) == (10.0, 10.0)
        report = reconcile_layers(stations, reaches)
        j_props = next(f for f in features if f["id"] == "J")["properties"]
        assert j_props["flow_min"] == 10.0 and j_props["flow_max"] == 10.0
        assert report["components"][0]["feasible"]

    def test_infeasible_component_is_locatable_others_continue(self):
        features = fork_features()
        # independent, broken water system
        features += [
            point("X1", {"kind": "inflow", "flow": 3}),
            point("X2", {"kind": "outflow", "flow": 7}),
            line("RX", "X1", "X2"),
        ]
        stations, reaches, _ = split_features(features)
        report = reconcile_layers(stations, reaches)
        assert report["infeasible_component_count"] == 1
        props = {f["id"]: f["properties"] for f in features}
        assert props["X1"]["reconcile_status"] == "infeasible"
        assert props["RX"]["reconcile_status"] == "infeasible"
        bad = next(c for c in report["components"] if not c["feasible"])
        assert bad["violation"]["cut"]["gap"] == 4.0
        assert set(bad["violation"]["core"]["station_ids"]) == {"X1", "X2"}
        # healthy component still got full bounds
        assert props["R1"]["reconcile_status"] == "ok"
        assert props["R1"]["flow_min"] == 10.0

    def test_structural_error_is_marked_on_element(self):
        features = [
            point("S", {"kind": "inflow", "flow": 5}),
            line("R1", "S", "ghost"),
        ]
        stations, reaches, _ = split_features(features)
        report = reconcile_layers(stations, reaches)
        assert report["structural_errors"]
        props = reaches[0]["properties"]
        assert props["reconcile_status"] == "error"
        assert "ghost" in props["reconcile_error"]

    def test_chinese_property_names(self):
        features = [
            point("S", {"类型": "入流", "流量": 6}),
            point("O", {"类型": "出流", "流量": 6}),
            line("R", "S", "O"),
        ]
        stations, reaches, _ = split_features(features)
        model_stations, model_reaches = build_model(stations, reaches)
        assert model_stations[0].kind == "inflow"
        assert model_stations[1].kind == "outflow"
        report = reconcile_layers(stations, reaches)
        assert report["infeasible_component_count"] == 0
        assert reaches[0]["properties"]["flow_min"] == 6.0

    def test_report_is_json_serializable(self):
        features = fork_features()
        report = reconcile_layers(*split_features(features)[:2])
        json.dumps(report)  # raises if NaN or other non-serializable content


class TestCli:
    def test_main_writes_output_and_report(self, tmp_path):
        source = tmp_path / "in.geojson"
        output = tmp_path / "out.geojson"
        report = tmp_path / "report.json"
        source.write_text(json.dumps({
            "type": "FeatureCollection", "features": fork_features(),
        }), encoding="utf-8")
        code = main([str(source), str(output), "--report", str(report)])
        assert code == 0
        written = json.loads(output.read_text(encoding="utf-8"))
        assert written["type"] == "FeatureCollection"
        assert len(written["features"]) == 7
        audit = json.loads(report.read_text(encoding="utf-8"))
        assert audit["infeasible_component_count"] == 0

    def test_strict_exit_code_on_infeasible(self, tmp_path):
        features = fork_features() + [
            point("X1", {"kind": "inflow", "flow": 3}),
            point("X2", {"kind": "outflow", "flow": 7}),
            line("RX", "X1", "X2"),
        ]
        source = tmp_path / "in.geojson"
        source.write_text(json.dumps({
            "type": "FeatureCollection", "features": features,
        }), encoding="utf-8")
        code = main([str(source), "--strict"])
        assert code == 2

    def test_separate_station_and_reach_layers(self, tmp_path):
        features = fork_features()
        points = [f for f in features if f["geometry"]["type"] == "Point"]
        lines = [f for f in features if f["geometry"]["type"] == "LineString"]
        station_file = tmp_path / "stations.geojson"
        reach_file = tmp_path / "reaches.geojson"
        station_file.write_text(json.dumps({
            "type": "FeatureCollection", "features": points,
        }), encoding="utf-8")
        reach_file.write_text(json.dumps({
            "type": "FeatureCollection", "features": lines,
        }), encoding="utf-8")
        output = tmp_path / "out.geojson"
        code = main([str(station_file), str(output),
                     "--reaches", str(reach_file)])
        assert code == 0
        written = json.loads(output.read_text(encoding="utf-8"))
        assert len(written["features"]) == 7
        r2 = next(f for f in written["features"] if f["id"] == "R2")
        assert r2["properties"]["flow_min"] == 2.0
        assert r2["properties"]["flow_max"] == 9.0
