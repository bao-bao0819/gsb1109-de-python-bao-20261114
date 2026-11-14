"""Tests for the peak-wind event table built from consecutive METARs."""
from datetime import datetime, timedelta

import pytest

from metar import Metar
from metar import peakwind as pw

MONTH = 1
YEAR = 2026


def make_report(day, hhmm, peak, station="KEWR"):
    """Build a report with a PK WND remark for the given day and time."""
    code = (
        f"METAR {station} {day:02d}{hhmm}Z AUTO 31015G25KT 10SM CLR "
        f"20/15 A2990 RMK AO2 PK WND {peak}"
    )
    return Metar.Metar(code, month=MONTH, year=YEAR)


def make_report_without_peak(day, hhmm, station="KEWR"):
    code = (
        f"METAR {station} {day:02d}{hhmm}Z AUTO 31015KT 10SM CLR "
        f"20/15 A2990 RMK AO2"
    )
    return Metar.Metar(code, month=MONTH, year=YEAR)


def dt(day, hour, minute):
    return datetime(YEAR, MONTH, day, hour, minute)


# --- parser ---------------------------------------------------------------

def test_parser_records_whether_hour_was_reported():
    explicit = make_report(11, "1851", "29028/1817")
    minute_only = make_report(11, "0020", "31028/50")

    assert explicit.peak_wind_hour_reported is True
    assert minute_only.peak_wind_hour_reported is False
    assert explicit.peak_wind_time == dt(11, 18, 17)


def test_minute_only_peak_after_midnight_walks_back_to_previous_day():
    # A 00:20 report mentioning a :50 peak cannot place it in the future.
    report = make_report(11, "0020", "31028/50")
    candidate = pw.build_candidates([report])[0]

    assert candidate.times == (dt(10, 23, 50),)
    assert candidate.is_unique


# --- candidate generation -------------------------------------------------

def test_minute_only_remark_keeps_every_hour_in_lookback_window():
    report = make_report(11, "0200", "31028/30")
    candidate = pw.build_candidates(
        [report], max_lookback=timedelta(hours=2)
    )[0]

    assert candidate.times == (dt(11, 0, 30), dt(11, 1, 30))
    assert not candidate.is_unique
    assert candidate.time == dt(11, 0, 30)


def test_previous_observation_bounds_the_candidates():
    older = make_report_without_peak(11, "0045")
    report = make_report(11, "0200", "31028/30")

    candidate = pw.build_candidates(
        [older, report], max_lookback=timedelta(hours=2)
    )[0]

    # The 00:45 observation rules out the 00:30 gust -- it cannot predate
    # the previous report -- so only 01:30 survives.
    assert candidate.times == (dt(11, 1, 30),)


def test_explicit_hour_remark_is_always_unique():
    report = make_report(11, "0020", "31028/2350")
    candidate = pw.build_candidates(
        [report], max_lookback=timedelta(hours=6)
    )[0]

    assert candidate.hour_reported is True
    assert candidate.times == (dt(10, 23, 50),)


def test_window_shorter_than_minute_offset_keeps_nearest_occurrence():
    report = make_report(11, "0100", "31028/50")
    candidate = pw.build_candidates(
        [report], max_lookback=timedelta(minutes=30)
    )[0]

    # A 30-minute window cannot reach the :50 occurrence, but the remark is
    # still anchored to the nearest time at or before the report.
    assert candidate.times == (dt(11, 0, 50),)


def test_non_positive_lookback_is_rejected():
    with pytest.raises(ValueError):
        pw.build_candidates([], max_lookback=timedelta(0))


# --- event linkage --------------------------------------------------------

def test_next_day_repeat_links_and_resolves_the_event():
    first = make_report(11, "0030", "31028/30")
    repeat = make_report(11, "0130", "31028/30")

    events = pw.peak_wind_events(
        [first, repeat], max_lookback=timedelta(hours=2)
    )

    assert len(events) == 1
    event = events[0]
    assert event.occurrences == (dt(11, 0, 30),)
    assert event.is_resolved
    assert len(event.candidates) == 2
    assert event.first_reported == dt(11, 0, 30)
    assert event.last_reported == dt(11, 1, 30)


def test_repeat_after_midnight_resolves_which_day_the_gust_belongs_to():
    # A 00:50 report's minute-only :50 gust could still be on the 10th.
    first = make_report(11, "0050", "31028/50")
    ambiguous = pw.peak_wind_events(
        [first], max_lookback=timedelta(hours=2)
    )[0]
    assert ambiguous.occurrences == (
        dt(10, 22, 50),
        dt(10, 23, 50),
        dt(11, 0, 50),
    )

    # The next report's coverage starts at 00:50, so repeating the gust pins
    # it to 00:50 on the 11th rather than the 10th.
    repeat = make_report(11, "0150", "31028/50")
    resolved = pw.peak_wind_events(
        [first, repeat], max_lookback=timedelta(hours=2)
    )[0]

    assert len(resolved.candidates) == 2
    assert resolved.occurrences == (dt(11, 0, 50),)
    assert resolved.is_resolved


def test_identical_speed_but_disjoint_hours_remain_separate_events():
    first = make_report(10, "1000", "27020/0959")
    second = make_report(10, "1200", "27020/1159")

    events = pw.peak_wind_events([first, second])

    assert len(events) == 2
    assert [event.time for event in events] == [
        dt(10, 9, 59),
        dt(10, 11, 59),
    ]
    assert all(event.speed == 20.0 for event in events)


def test_unresolved_event_exposes_its_alternatives():
    report = make_report(11, "0200", "31028/30")
    events = pw.peak_wind_events(
        [report], max_lookback=timedelta(hours=2)
    )

    assert len(events) == 1
    assert not events[0].is_resolved
    assert events[0].occurrences == (dt(11, 0, 30), dt(11, 1, 30))


def test_events_are_sorted_by_occurrence_time():
    late_gust = make_report(11, "0100", "31028/50")
    early_gust = make_report(10, "1000", "27020/0959")

    events = pw.peak_wind_events([late_gust, early_gust])

    assert [event.time for event in events] == [
        dt(10, 9, 59),
        dt(11, 0, 50),
    ]


def test_station_filter_ignores_other_stations():
    own = make_report(10, "1000", "27020/0959", station="KEWR")
    other = make_report(10, "1000", "29030/0959", station="KJFK")

    events = pw.peak_wind_events([own, other], station_id="KEWR")

    assert len(events) == 1
    assert events[0].station_id == "KEWR"


def test_table_rows_carry_the_documented_columns():
    report = make_report(11, "0020", "31028/50")
    row = pw.peak_wind_table([report])[0]

    time, station, direction, speed_value, resolved, occurrences, count = row
    assert time == dt(10, 23, 50)
    assert station == "KEWR"
    assert direction == 310.0
    assert speed_value == 28.0
    assert resolved is True
    assert occurrences == (dt(10, 23, 50),)
    assert count == 1


# --- backfill -------------------------------------------------------------

def test_backfill_reports_which_event_becomes_unique():
    current = make_report(11, "0200", "31028/30")
    late = make_report(11, "0030", "31028/30")

    before = pw.peak_wind_events(
        [current], max_lookback=timedelta(hours=2)
    )
    assert not before[0].is_resolved

    result = pw.backfill(
        [current], [late], max_lookback=timedelta(hours=2)
    )

    assert len(result.newly_resolved) == 1
    resolved = result.newly_resolved[0]
    assert resolved.occurrences == (dt(11, 0, 30),)
    assert resolved.is_resolved
    assert result.new_events == ()
    assert result.still_ambiguous == ()
    assert result.added_reports == (late,)


def test_backfill_keeps_unrelated_ambiguous_events_ambiguous():
    ambiguous = make_report(11, "0500", "31028/30")
    late = make_report(11, "0030", "27020/0959")

    result = pw.backfill(
        [ambiguous], [late], max_lookback=timedelta(hours=2)
    )

    assert result.newly_resolved == ()
    assert len(result.new_events) == 1
    assert len(result.still_ambiguous) == 1
    assert result.still_ambiguous[0].speed == 28.0


def test_backfill_accepts_reports_given_out_of_order():
    current = make_report(11, "0200", "31028/30")
    late = make_report(11, "0030", "31028/30")

    result = pw.backfill(
        [current], [late], max_lookback=timedelta(hours=2)
    )

    assert [event.time for event in result.after] == [dt(11, 0, 30)]
