"""Tests for the per-instrument pressure offset audit."""
import json

import pytest

from metar import PressureAudit


def doc(observations, devices, pairs, calibrated=()):
    return {
        "observations": observations,
        "devices": devices,
        "pairs": pairs,
        "calibrated": list(calibrated),
    }


def era(station, generation, start, end=None):
    record = {"station": station, "generation": generation, "start": start}
    if end is not None:
        record["end"] = end
    return record


def obs(obs_id, station, when, pressure):
    return {"id": obs_id, "station": station, "time": when, "pressure": pressure}


def pair(pair_id, a, b, when, low, high):
    return {
        "id": pair_id,
        "station_a": a,
        "station_b": b,
        "time": when,
        "low": low,
        "high": high,
    }


def test_decode_pressure_prefers_report_over_slp():
    assert PressureAudit.decode_pressure("KEWR 111851Z 22015KT 10SM CLR 22/12 A2992") == pytest.approx(
        1013.21, abs=0.01
    )
    # SLP114 is the only pressure in this report: 1011.4 hPa.
    assert PressureAudit.decode_pressure(
        "METAR KCOF 191855Z 18015G22KT 7SM SCT300 28/18 SLP114"
    ) == pytest.approx(1011.4)


def test_decode_pressure_rejects_report_without_pressure():
    with pytest.raises(PressureAudit.AuditError):
        PressureAudit.decode_pressure("KEWR 111851Z 22015KT 10SM CLR 22/12")


def test_observation_metar_is_decoded():
    result = PressureAudit.audit_pressures(
        [
            {
                "id": "m1",
                "station": "AAA",
                "time": "2024-01-01T00:00:00Z",
                "metar": "AAA 010000Z 00000KT 10SM CLR 10/05 Q1013",
            }
        ],
        [era("AAA", "g1", "2024-01-01T00:00:00Z")],
        [],
    )
    assert result.observations["m1"].pressure == pytest.approx(1013.0)
    assert result.observations["m1"].era.key == ("AAA", "g1")


def test_generation_dates_assign_observations():
    observations = [
        obs("old", "AAA", "2024-01-10T00:00:00Z", 1000.0),
        obs("new", "AAA", "2024-02-10T00:00:00Z", 1000.0),
    ]
    devices = [
        era("AAA", "gen-1", "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"),
        era("AAA", "gen-2", "2024-02-01T00:00:00Z"),
    ]
    result = PressureAudit.audit_pressures(observations, devices, [])
    assert result.observations["old"].era.generation == "gen-1"
    assert result.observations["new"].era.generation == "gen-2"


def test_explicit_generation_must_match_dates():
    observations = [
        {
            "id": "wrong",
            "station": "AAA",
            "time": "2024-01-10T00:00:00Z",
            "pressure": 1000.0,
            "generation": "gen-2",
        }
    ]
    devices = [
        era("AAA", "gen-1", "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"),
        era("AAA", "gen-2", "2024-02-01T00:00:00Z"),
    ]
    result = PressureAudit.audit_pressures(observations, devices, [])
    assert list(result.observations) == []
    assert [o.id for o in result.unassigned] == ["wrong"]
    assert any("outside that generation's dates" in issue for issue in result.issues)


def test_overlapping_eras_are_not_guessed():
    observations = [obs("o", "AAA", "2024-01-15T00:00:00Z", 1000.0)]
    devices = [
        era("AAA", "g1", "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"),
        era("AAA", "g2", "2024-01-10T00:00:00Z"),
    ]
    result = PressureAudit.audit_pressures(observations, devices, [])
    assert list(result.observations) == []
    assert any("overlapping device eras" in issue for issue in result.issues)


def test_anchored_component_gives_absolute_ranges():
    observations = [
        obs("c0", "CAL", "2024-01-01T00:00:00Z", 1000.0),
        obs("s0", "S1", "2024-01-01T00:00:00Z", 1001.0),
        obs("c1", "CAL", "2024-01-01T06:00:00Z", 1000.0),
        obs("s1", "S1", "2024-01-01T06:00:00Z", 1001.2),
    ]
    devices = [
        era("CAL", "g", "2024-01-01T00:00:00Z"),
        era("S1", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [
        pair("p0", "CAL", "S1", "2024-01-01T00:00:00Z", -0.5, 0.5),
        pair("p1", "CAL", "S1", "2024-01-01T06:00:00Z", -0.5, 0.5),
    ]
    result = PressureAudit.audit_pressures(observations, devices, pairs, calibrated=["CAL"])
    ranges = result.ranges()
    assert ranges[("CAL", "g")].low == 0.0
    assert ranges[("CAL", "g")].high == 0.0
    # corrected S1 - corrected CAL = reported(1.0..1.2) + correction(S1)
    assert ranges[("S1", "g")].low == pytest.approx(-1.5)
    assert ranges[("S1", "g")].high == pytest.approx(-0.7)
    assert not result.conflicts()


def test_two_generations_get_independent_ranges():
    observations = [
        obs("c0", "CAL", "2024-01-10T00:00:00Z", 1000.0),
        obs("a0", "AAA", "2024-01-10T00:00:00Z", 1001.0),
        obs("c1", "CAL", "2024-02-10T00:00:00Z", 1000.0),
        obs("a1", "AAA", "2024-02-10T00:00:00Z", 998.0),
    ]
    devices = [
        era("CAL", "g", "2024-01-01T00:00:00Z"),
        era("AAA", "old", "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"),
        era("AAA", "new", "2024-02-01T00:00:00Z"),
    ]
    pairs = [
        pair("p0", "CAL", "AAA", "2024-01-10T00:00:00Z", -0.25, 0.25),
        pair("p1", "CAL", "AAA", "2024-02-10T00:00:00Z", -0.25, 0.25),
    ]
    result = PressureAudit.audit_pressures(observations, devices, pairs, calibrated=["CAL"])
    ranges = result.ranges()
    assert ranges[("AAA", "old")].center == pytest.approx(-1.0)
    assert ranges[("AAA", "new")].center == pytest.approx(2.0)
    assert ranges[("AAA", "old")].width == pytest.approx(0.5)


def test_unanchored_component_reports_only_relative_differences():
    observations = [
        obs("a0", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("b0", "BBB", "2024-01-01T00:00:00Z", 1001.0),
        obs("a1", "AAA", "2024-01-02T00:00:00Z", 1000.2),
        obs("b1", "BBB", "2024-01-02T00:00:00Z", 1001.1),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [
        pair("p0", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.5, 0.5),
        pair("p1", "AAA", "BBB", "2024-01-02T00:00:00Z", -0.5, 0.5),
    ]
    result = PressureAudit.audit_pressures(observations, devices, pairs)
    assert not result.conflicts()
    ranges = result.ranges()
    assert {item.status for item in ranges.values()} == {"relative"}
    assert all(item.low is None and item.high is None for item in ranges.values())
    gauge = result.components[0].gauge
    other = ("BBB", "g") if gauge == ("AAA", "g") else ("AAA", "g")
    item = ranges[other]
    # correction(other) - correction(gauge) = -reported(other - gauge)
    assert item.relative_low is not None and item.relative_high is not None
    assert item.relative_to == gauge
    payload = result.to_dict()
    assert {row["status"] for row in payload["ranges"]} == {"relative"}
    assert payload["relative"]


def test_isolated_station_without_reference_is_unidentified():
    result = PressureAudit.audit_pressures(
        [obs("x", "XXX", "2024-01-01T00:00:00Z", 1000.0)],
        [era("XXX", "g", "2024-01-01T00:00:00Z")],
        [],
    )
    assert result.ranges()[("XXX", "g")].status == "unidentified"


def test_calibrated_generation_object_pins_only_that_generation():
    observations = [
        obs("c0", "CAL", "2024-01-10T00:00:00Z", 1000.0),
        obs("s0", "S1", "2024-01-10T00:00:00Z", 1001.0),
    ]
    devices = [
        era("CAL", "old", "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"),
        era("CAL", "new", "2024-02-01T00:00:00Z"),
        era("S1", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [pair("p", "CAL", "S1", "2024-01-10T00:00:00Z", -0.2, 0.2)]
    result = PressureAudit.audit_pressures(
        observations,
        devices,
        pairs,
        calibrated=[{"station": "CAL", "generation": "old"}],
    )
    ranges = result.ranges()
    assert ranges[("CAL", "old")].low == 0.0
    assert ranges[("CAL", "new")].status == "unidentified"
    assert ranges[("S1", "g")].status == "bounded"


def test_three_station_cycle_points_at_observations():
    document = {
        "observations": [
            obs("a", "AAA", "2024-01-01T00:00:00Z", 1000.0),
            obs("b", "BBB", "2024-01-01T00:00:00Z", 1000.0),
            obs("c", "CCC", "2024-01-01T00:00:00Z", 1000.0),
        ],
        "devices": [
            era("AAA", "g", "2024-01-01T00:00:00Z"),
            era("BBB", "g", "2024-01-01T00:00:00Z"),
            era("CCC", "g", "2024-01-01T00:00:00Z"),
        ],
        "pairs": [
            pair("ab", "AAA", "BBB", "2024-01-01T00:00:00Z", 0.0, 0.0),
            pair("bc", "BBB", "CCC", "2024-01-01T00:00:00Z", 0.0, 0.0),
            pair("ca", "CCC", "AAA", "2024-01-01T00:00:00Z", 1.0, 2.0),
        ],
        "calibrated": ["AAA"],
    }
    result = PressureAudit.audit_from_dict(document)
    conflicts = result.conflicts()
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert set(conflict.stations) == {"AAA", "BBB", "CCC"}
    assert set(conflict.observations) == {"a", "b", "c"}
    assert {c.id for c in conflict.constraints} == {"ab", "bc", "ca"}
    assert conflict.amount == pytest.approx(1.0)
    assert result.ranges()[("BBB", "g")].status == "conflict"


def test_conflict_from_three_stations_is_located_at_the_hour():
    observations = []
    pairs = []
    for hour in range(3):
        when = "2024-05-01T%02d:00:00Z" % hour
        observations.extend(
            [
                obs("p%d" % hour, "P", when, 1001.0 + hour * 0.1),
                obs("q%d" % hour, "Q", when, 1000.5 + hour * 0.1),
                obs("r%d" % hour, "R", when, 1000.0 + hour * 0.1),
            ]
        )
        if hour == 1:
            pairs.extend(
                [
                    pair("pq1", "P", "Q", when, 0.2, 0.8),
                    pair("qr1", "Q", "R", when, 0.2, 0.8),
                    pair("rp1", "R", "P", when, -0.2, 0.2),
                ]
            )
    devices = [era(s, "g", "2024-01-01T00:00:00Z") for s in ("P", "Q", "R")]
    result = PressureAudit.audit_pressures(observations, devices, pairs)
    conflict = result.conflicts()[0]
    assert set(conflict.observations) == {"p1", "q1", "r1"}
    assert "P -> Q -> R -> P" in conflict.message


def test_same_generation_cannot_take_per_hour_offsets():
    observations = [
        obs("a0", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("b0", "BBB", "2024-01-01T00:00:00Z", 1000.0),
        obs("a1", "AAA", "2024-01-01T06:00:00Z", 1001.0),
        obs("b1", "BBB", "2024-01-01T06:00:00Z", 1000.0),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [
        pair("p0", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.1, 0.1),
        pair("p1", "AAA", "BBB", "2024-01-01T06:00:00Z", -0.1, 0.1),
    ]
    result = PressureAudit.audit_pressures(observations, devices, pairs, calibrated=["BBB"])
    conflict = result.conflicts()[0]
    assert {c.id for c in conflict.constraints} == {"p0", "p1"}
    assert conflict.amount == pytest.approx(0.8)
    assert len(result.constraints) == 2
    # One variable per generation, not one per observation.
    assert {c.var_a for c in result.constraints} == {("AAA", "g")}
    assert {c.var_b for c in result.constraints} == {("BBB", "g")}


def test_non_simultaneous_pair_is_rejected_not_bent():
    observations = [
        obs("a", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("b", "BBB", "2024-01-01T00:10:00Z", 1000.0),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [pair("p", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.1, 0.1)]
    result = PressureAudit.audit_pressures(observations, devices, pairs, calibrated=["AAA"])
    assert result.constraints == ()
    assert any("no BBB observation within" in issue for issue in result.issues)


def test_close_observations_are_matched_within_tolerance():
    observations = [
        obs("a", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("b", "BBB", "2024-01-01T00:00:30Z", 1000.0),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [pair("p", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.1, 0.1)]
    result = PressureAudit.audit_pressures(observations, devices, pairs, calibrated=["AAA"])
    assert len(result.constraints) == 1
    assert result.constraints[0].obs_b == "b"


def test_ambiguous_nearest_observation_is_rejected():
    observations = [
        obs("a1", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("b1", "BBB", "2024-01-01T00:00:00Z", 1000.0),
        obs("b2", "BBB", "2024-01-01T00:00:00Z", 1000.1),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [pair("p", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.1, 0.1)]
    result = PressureAudit.audit_pressures(observations, devices, pairs, calibrated=["AAA"])
    assert result.constraints == ()
    assert any("ambiguous" in issue for issue in result.issues)


def test_slp_and_report_pressure_mix_is_flagged():
    observations = [
        obs("a", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        {
            "id": "b",
            "station": "BBB",
            "time": "2024-01-01T00:00:00Z",
            "metar": "BBB 010000Z 00000KT 9999 SCT030 10/05 SLP998",
        },
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [pair("p", "AAA", "BBB", "2024-01-01T00:00:00Z", -100.0, 100.0)]
    result = PressureAudit.audit_pressures(observations, devices, pairs)
    assert any("mixes report and sea-level pressure" in issue for issue in result.issues)


def test_timezone_offsets_are_normalized():
    observations = [
        obs("a", "AAA", "2024-01-01T08:00:00+08:00", 1000.0),
        obs("b", "BBB", "2024-01-01T00:00:00Z", 1000.0),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [pair("p", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.1, 0.1)]
    result = PressureAudit.audit_pressures(observations, devices, pairs)
    assert len(result.constraints) == 1


def test_duplicate_observation_id_is_an_error():
    records = [
        obs("dup", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("dup", "AAA", "2024-01-01T01:00:00Z", 1000.0),
    ]
    with pytest.raises(PressureAudit.AuditError):
        PressureAudit.audit_pressures(records, [era("AAA", "g", "2024-01-01T00:00:00Z")], [])


def test_missing_input_section_is_an_error():
    with pytest.raises(PressureAudit.AuditError):
        PressureAudit.audit_from_dict({"observations": [], "devices": []})


def test_cli_text_and_json(capsys, tmp_path):
    path = tmp_path / "audit.json"
    path.write_text(
        json.dumps(
            doc(
                [
                    obs("c", "CAL", "2024-01-01T00:00:00Z", 1000.0),
                    obs("s", "S1", "2024-01-01T00:00:00Z", 1001.0),
                ],
                [
                    era("CAL", "g", "2024-01-01T00:00:00Z"),
                    era("S1", "g", "2024-01-01T00:00:00Z"),
                ],
                [pair("p", "CAL", "S1", "2024-01-01T00:00:00Z", -0.5, -0.1)],
                calibrated=["CAL"],
            )
        ),
        encoding="utf-8",
    )
    assert PressureAudit.main([str(path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    item = [row for row in payload["ranges"] if row["station"] == "S1"][0]
    assert item["status"] == "bounded"

    assert PressureAudit.main([str(path), "--format", "text"]) == 0
    text = capsys.readouterr().out
    assert "S1" in text and "bounded" in text


def test_cli_strict_exit_code_for_conflict(capsys, tmp_path):
    path = tmp_path / "conflict.json"
    path.write_text(
        json.dumps(
            doc(
                [
                    obs("a", "AAA", "2024-01-01T00:00:00Z", 1000.0),
                    obs("b", "BBB", "2024-01-01T00:00:00Z", 1000.0),
                    obs("a6", "AAA", "2024-01-01T06:00:00Z", 1000.0),
                    obs("b6", "BBB", "2024-01-01T06:00:00Z", 1000.0),
                ],
                [
                    era("AAA", "g", "2024-01-01T00:00:00Z"),
                    era("BBB", "g", "2024-01-01T00:00:00Z"),
                ],
                [
                    pair("p0", "AAA", "BBB", "2024-01-01T00:00:00Z", 2.0, 3.0),
                    pair("p1", "AAA", "BBB", "2024-01-01T06:00:00Z", -3.0, -2.0),
                ],
                calibrated=["AAA"],
            )
        ),
        encoding="utf-8",
    )
    assert PressureAudit.main([str(path), "--strict"]) == 1
    capsys.readouterr()


def test_tolerance_is_configurable():
    pair_record = pair("p", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.1, 0.1)
    pair_record["tolerance_seconds"] = 0
    observations = [
        obs("a", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("b", "BBB", "2024-01-01T00:00:01Z", 1000.0),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    result = PressureAudit.audit_pressures(observations, devices, [pair_record])
    assert result.constraints == ()


def test_relative_difference_interval_is_reported():
    observations = [
        obs("a0", "AAA", "2024-01-01T00:00:00Z", 1000.0),
        obs("b0", "BBB", "2024-01-01T00:00:00Z", 1000.0),
    ]
    devices = [
        era("AAA", "g", "2024-01-01T00:00:00Z"),
        era("BBB", "g", "2024-01-01T00:00:00Z"),
    ]
    pairs = [pair("p", "AAA", "BBB", "2024-01-01T00:00:00Z", -0.1, 0.3)]
    result = PressureAudit.audit_pressures(observations, devices, pairs)
    row = result.to_dict()["relative"][0]
    assert (row["low"], row["high"]) == pytest.approx((-0.1, 0.3))
