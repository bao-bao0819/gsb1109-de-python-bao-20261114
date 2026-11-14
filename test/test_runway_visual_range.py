"""Tests for runway visual range threshold classification."""
import math

import pytest

from metar import Metar
from metar.Datatypes import (
    RVRThresholdVerdict,
    RunwayVisualRange,
    distance,
)

BELOW = RVRThresholdVerdict.DEFINITELY_BELOW
POSSIBLY = RVRThresholdVerdict.POSSIBLY_BELOW
NOT_BELOW = RVRThresholdVerdict.DEFINITELY_NOT_BELOW

STATION = "METAR ZZZZ 010000Z 00000KT 9999 "


def rvr(low, high, unit="M", varying=False):
    return RunwayVisualRange("09", low, high, unit, varying=varying)


@pytest.mark.parametrize(
    "low,high,varying,threshold,expected",
    [
        # Exact point with a closed (inclusive) endpoint.
        ("1000", "1000", False, 1500, BELOW),
        ("1000", "1000", False, 1000, NOT_BELOW),
        ("1000", "1000", False, 800, NOT_BELOW),
        # "M" truncation: the true value is strictly below the reported one.
        ("M1000", "M1000", False, 1000, BELOW),
        ("M1000", "M1000", False, 500, POSSIBLY),
        ("M1000", "M1000", False, 100, POSSIBLY),
        # "P" truncation: the true value is strictly above the reported one.
        ("P1000", "P1000", False, 1000, NOT_BELOW),
        ("P1000", "P1000", False, 1500, POSSIBLY),
        ("P1000", "P1000", False, 5000, POSSIBLY),
        # Explicit low-to-high range with closed endpoints.
        ("0800", "1200", True, 1000, POSSIBLY),
        ("0800", "1200", True, 1200, POSSIBLY),
        ("0800", "1200", True, 800, NOT_BELOW),
        ("0800", "1200", True, 1300, BELOW),
        ("0800", "1200", True, 700, NOT_BELOW),
        # Truncated endpoints inside an explicit range open the named bound.
        ("M0800", "1200", True, 800, NOT_BELOW),
        ("0800", "P1200", True, 1200, BELOW),
    ],
)
def test_value_below_truth_table(low, high, varying, threshold, expected):
    """Open versus closed endpoints decide the verdict at the boundary."""
    assert rvr(low, high, varying=varying).value_below(threshold) == expected


def test_truncation_builds_half_lines():
    """A single truncated value extends to the zero floor or infinity."""
    less_than = rvr("M1000", "M1000")
    assert less_than.low == 0.0
    assert less_than.low_inclusive is True
    assert less_than.high == 1000.0
    assert less_than.high_inclusive is False

    greater_than = rvr("P2000", "P2000")
    assert greater_than.low == 2000.0
    assert greater_than.low_inclusive is False
    assert math.isinf(greater_than.high)
    assert greater_than.high_inclusive is False


def test_explicit_range_keeps_both_bounds():
    """An explicit V-range never collapses to a half line."""
    record = rvr("M0800", "P1200", varying=True)
    assert record.low == 800.0
    assert record.high == 1200.0
    assert record.low_inclusive is False
    assert record.high_inclusive is False


def test_feet_conversion_and_boundary():
    """Thresholds are compared after converting feet to metres."""
    point = rvr("1500", "1500", unit="FT")
    assert point.value_below(1500, "FT") == NOT_BELOW
    assert point.value_below(1499, "FT") == NOT_BELOW
    assert point.value_below(1501, "FT") == BELOW

    # Cross-unit equality on a closed point must still read "not below".
    metres_point = rvr("500", "500", unit="M")
    feet_threshold = distance("500", "M").value("FT")
    assert metres_point.value_below(feet_threshold, "FT") == NOT_BELOW

    # The open upper bound of an M value stays open after unit conversion.
    assert rvr("M500", "M500", unit="M").value_below(feet_threshold, "FT") == BELOW


def test_threshold_accepts_distance_object():
    threshold = distance("1000", "M")
    assert rvr("0800", "0800").value_below(threshold) == BELOW
    assert rvr("1200", "1200").value_below(threshold) == NOT_BELOW


def test_bounds_and_string_rendering():
    record = rvr("0800", "1200", varying=True)
    low_ft, high_ft = record.bounds("FT")
    assert low_ft == pytest.approx(distance("800", "M").value("FT"))
    assert high_ft == pytest.approx(distance("1200", "M").value("FT"))

    assert rvr("1500", "1500").string() == "on runway 09, 1500 meters"
    assert rvr("M1000", "M1000").string() == "on runway 09, less than 1000 meters"
    assert rvr("P2000", "P2000").string() == "on runway 09, greater than 2000 meters"
    assert (
        rvr("0800", "1200", varying=True).string()
        == "on runway 09, from 800 meters to 1200 meters"
    )


def test_enum_labels():
    assert str(BELOW) == "definitely below"
    assert str(POSSIBLY) == "possibly below"
    assert str(NOT_BELOW) == "definitely not below"


def test_report_keeps_raw_group_direction_and_tendency():
    report = Metar.Metar(STATION + "R27L/0400N R27R/P2000FTU")
    records = report.runway_visual_ranges
    assert [record.name for record in records] == ["27L", "27R"]
    assert [record.code for record in records] == ["R27L/0400N", "R27R/P2000FTU"]
    assert [record.tendency for record in records] == ["N", "U"]
    assert records[0].unit == "M"
    assert records[1].unit == "FT"
    assert report.runway_tendencies == ["N", "U"]


def test_report_parses_truncation_and_varying_ranges():
    report = Metar.Metar(STATION + "R09/M0500 R27/0800V1200")
    less_than, varying = report.runway_visual_ranges
    assert less_than.high == 500.0 and less_than.high_inclusive is False
    assert varying.low == 800.0 and varying.high == 1200.0
    assert report.rvr_threshold_verdict("09", 500) == BELOW
    assert report.rvr_threshold_verdict("27", 1000) == POSSIBLY


def test_two_directions_are_classified_independently():
    report = Metar.Metar(STATION + "R27L/0400 R27R/1200")
    verdicts = report.rvr_threshold_verdicts(1000)
    assert [r.name for r in verdicts[BELOW]] == ["27L"]
    assert verdicts[POSSIBLY] == []
    assert [r.name for r in verdicts[NOT_BELOW]] == ["27R"]
    assert verdicts["missing"] == []


def test_missing_direction_is_excluded_from_denominator():
    report = Metar.Metar(STATION + "R28L///// R28R/1500")
    assert report.runway_missing == ["28L"]
    assert report.runway_visual_range_record("28L") is None
    assert report.rvr_threshold_verdict("28L", 1000) is None

    verdicts = report.rvr_threshold_verdicts(1000)
    evaluated = (
        len(verdicts[BELOW]) + len(verdicts[POSSIBLY]) + len(verdicts[NOT_BELOW])
    )
    assert evaluated == 1
    assert [r.name for r in verdicts[NOT_BELOW]] == ["28R"]
    assert verdicts["missing"] == ["28L"]


def test_rvrno_leaves_no_record_or_missing_direction():
    report = Metar.Metar(STATION + "RVRNO R28L/1500")
    assert report.runway_missing == []
    assert [r.name for r in report.runway_visual_ranges] == ["28L"]


def test_legacy_runway_records_still_aligned():
    report = Metar.Metar(
        STATION + "R04L/1700N R15/1800 R22L/1800U R22R/1600D"
    )
    assert report.runway_tendencies == ["N", None, "U", "D"]
    assert len(report.runway) == len(report.runway_visual_ranges) == 4
    assert [r.name for r in report.runway_visual_ranges] == [
        row[0] for row in report.runway
    ]
