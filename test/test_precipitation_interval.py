"""Test interval estimates built from overlapping precipitation records."""
import datetime as dt

import pytest

from metar import Metar
from metar.Precipitation import (
    CONFLICT,
    EXACT,
    RANGE,
    TRACE,
    UNKNOWN,
    UNKNOWN_UPPER,
    ZERO,
    ConflictError,
    PrecipitationConflict,
    PrecipitationRecord,
    PrecipitationStore,
    PrecipitationTotal,
)

T0 = dt.datetime(2026, 1, 1, 0, 0)


def hours(n):
    """Return T0 plus n hours."""
    return T0 + dt.timedelta(hours=n)


def store(*records):
    """Build a store from (start_hour, end_hour, amount, source) tuples."""
    result = PrecipitationStore()
    for start, end, amount, source in records:
        result.add(hours(start), hours(end), amount, source=source)
    return result


def test_arbitrary_interval_is_interpolated():
    """An interval between report boundaries gets the implied bounds."""
    s = store((0, 6, 0.60, "6hr"), (6, 12, 0.12, "6hr-2"), (0, 24, 1.00, "24hr"))
    result = s.total(hours(0), hours(24))
    assert result.status == EXACT
    assert result.minimum == 1.00
    assert result.maximum == 1.00

    result = s.total(hours(6), hours(18))
    assert result.status == RANGE
    assert result.minimum == 0.12
    assert result.maximum == 0.40


def test_overlapping_six_and_twenty_four_hours_are_not_added():
    """Six-hour and twenty-four-hour groups constrain the same hours once."""
    s = PrecipitationStore()
    s.add(hours(0), hours(24), 1.20, source="24hr")
    s.add(hours(0), hours(6), 0.50, source="6hr")

    assert s.total(hours(0), hours(24)).string() == "1.20in"
    assert s.total(hours(0), hours(6)).string() == "0.50in"
    rest = s.total(hours(6), hours(24))
    assert rest.minimum == 0.70
    assert rest.maximum == 0.70


def test_zero_record_constrains_contained_hours():
    """An exact zero total forces every contained subinterval to zero too."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 0.0, source="zero-6hr")
    for start, end in ((0, 6), (0, 3), (3, 6)):
        result = s.total(hours(start), hours(end))
        assert result.status == ZERO
        assert result.minimum == result.maximum == 0.0


def test_single_total_leaves_subinterval_bounds_wide():
    """One cumulative total cannot resolve its interior."""
    s = PrecipitationStore()
    s.add(hours(0), hours(24), 1.00, source="24hr")
    result = s.total(hours(3), hours(9))
    assert result.minimum == 0.0
    assert result.maximum == 1.00


def test_unknown_upper_bound_is_distinct_from_zero_and_trace():
    """A lower-bound-only observation reports an open upper end."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 0.30, kind="at_least", source="6hr>=")
    result = s.total(hours(0), hours(6))
    assert result.status == UNKNOWN_UPPER
    assert result.minimum == 0.30
    assert result.maximum is None
    assert result.has_unknown_maximum
    assert not result.is_zero
    assert not result.is_trace


def test_exact_zero_is_not_trace():
    """A zero total is reported as zero, not as trace."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 0.0, source="6hr")
    result = s.total(hours(0), hours(6))
    assert result.status == ZERO
    assert result.is_zero
    assert not result.is_trace
    assert result.minimum == result.maximum == 0.0


def test_trace_only_observation():
    """A trace total stays positive and below the reportable minimum."""
    s = PrecipitationStore(trace_limit=0.01)
    s.add(hours(0), hours(6), 0.0, kind="trace", source="6hr")
    result = s.total(hours(0), hours(6))
    assert result.status == TRACE
    assert result.is_trace
    assert not result.is_zero
    assert result.minimum == 0.0
    assert result.maximum == pytest.approx(0.01)
    assert result.has_unknown_maximum is False


def test_trace_is_not_used_for_a_larger_interval_with_other_rain():
    """A trace inside a wetter twenty-four-hour window is not the answer."""
    s = PrecipitationStore()
    s.add(hours(0), hours(24), 1.00, source="24hr")
    s.add(hours(0), hours(12), 0.0, kind="trace", source="12hr")
    result = s.total(hours(12), hours(24))
    assert not result.is_trace
    assert result.minimum == pytest.approx(0.99)
    assert result.maximum == 1.00


def test_conflict_names_the_sources():
    """A six-hour total larger than its twenty-four-hour window conflicts."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 2.00, source="6hr")
    s.add(hours(0), hours(24), 1.00, source="24hr")

    result = s.total(hours(0), hours(6))
    assert result.status == CONFLICT
    assert result.has_conflict
    assert result.minimum is None
    assert result.maximum is None
    assert result.sources == ("6hr", "24hr")
    assert result.groups == ("6hr", "24hr")
    assert isinstance(result.conflict, PrecipitationConflict)
    assert "negative" in str(result.conflict)


def test_conflict_is_not_masked_by_zero():
    """An impossible hour is never clamped to zero to keep reporting."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 0.00, source="6hr")
    s.add(hours(0), hours(24), 0.00, source="24hr")
    s.add(hours(0), hours(1), 0.01, source="1hr")

    result = s.total(hours(1), hours(2))
    assert result.status == CONFLICT
    assert "1hr" in result.sources
    assert "6hr" in result.sources


def test_conflict_is_visible_from_any_covered_interval():
    """Queries inside a contradictory window report the conflict."""
    s = PrecipitationStore()
    s.add(hours(0), hours(24), 1.00, source="24hr")
    s.add(hours(0), hours(6), 1.50, source="6hr")
    for start in (0, 1, 3):
        result = s.total(hours(start), hours(start + 1))
        assert result.status == CONFLICT
        assert result.sources == ("6hr", "24hr")


def test_units_can_be_cm():
    """Results can be returned in centimetres without changing the records."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 1.00, source="6hr")
    result = s.total(hours(0), hours(6), units="CM")
    assert result.minimum == pytest.approx(2.54)
    assert result.units == "CM"


def test_record_from_precipitation_object_keeps_trace():
    """Records built from precipitation objects preserve trace semantics."""
    report = Metar.Metar(
        "KEWR 101651Z 00000KT 10SM CLR 20/10 A3000 RMK P0000 60000",
        year=2026,
        month=1,
    )
    s = PrecipitationStore()
    s.add_metar(report)
    result = s.total(report.time - dt.timedelta(hours=1), report.time)
    assert result.status == TRACE
    assert {record.source for record in s} == {"precip_1hr", "precip_6hr"}


def test_metar_interval_shortcuts():
    """A report exposes interval and bounds shortcuts."""
    report = Metar.Metar(
        "KEWR 101651Z 00000KT 10SM CLR 20/10 A3000 RMK P0010",
        year=2026,
        month=1,
    )
    result = report.precipitation_interval(
        report.time - dt.timedelta(hours=1), report.time
    )
    assert result.string() == "0.10in"
    assert report.precip_bounds(
        report.time - dt.timedelta(hours=1), report.time
    ) == (0.10, 0.10)


def test_store_from_metar_report():
    """Metar.precipitation_store() seeds a store with all report groups."""
    report = Metar.Metar(
        "KEWR 101651Z 00000KT 10SM CLR 20/10 A3000 RMK P0010 60020 70040",
        year=2026,
        month=1,
    )
    s = report.precipitation_store()
    assert len(s) == 3
    assert {record.group for record in s} == {
        "precip_1hr",
        "precip_6hr",
        "precip_24hr",
    }
    total = s.total(report.time - dt.timedelta(hours=6), report.time)
    assert total.minimum == pytest.approx(0.20)
    assert total.maximum == pytest.approx(0.20)


def test_hour_offsets_use_the_earliest_record_start():
    """Integer interval endpoints are measured from the first record."""
    s = store((0, 6, 0.25, "6hr"))
    result = s.total(0, 6)
    assert result.start == hours(0)
    assert result.end == hours(6)


def test_last_uses_the_latest_record_end():
    """last() counts backwards from the latest observation."""
    s = store((0, 6, 0.25, "6hr"))
    result = s.last(3)
    assert result.start == hours(3)
    assert result.end == hours(6)


def test_empty_store_reports_unknown():
    """No records means no finite bound and no claim of zero."""
    result = PrecipitationStore().total(hours(0), hours(6))
    assert result.status == UNKNOWN
    assert result.minimum == 0.0
    assert result.maximum is None
    assert not result.is_zero
    assert not result.is_trace


def test_result_dictionary_summary():
    """Results can be serialized to a plain dictionary."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 0.50, source="6hr")
    result = s.total(hours(0), hours(6))
    data = result.as_dict()
    assert data["minimum"] == data["maximum"] == 0.50
    assert data["status"] == EXACT
    assert data["sources"] == ()
    assert data["start"] == hours(0)

    conflict = store((0, 6, 2.0, "6hr"), (0, 24, 1.0, "24hr")).total(
        hours(0), hours(6)
    )
    assert conflict.as_dict()["has_conflict"] is True
    assert conflict.as_dict()["groups"] == ("6hr", "24hr")


def test_bad_interval_is_rejected():
    """An empty or reversed interval is a caller error."""
    s = PrecipitationStore()
    with pytest.raises(ValueError):
        s.total(hours(1), hours(1))
    with pytest.raises(ValueError):
        s.total(hours(2), hours(1))


def test_trace_covering_part_of_the_window():
    """A trace bound applies to any subinterval it contains."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 0.0, kind="trace", source="trace-6hr")
    result = s.total(hours(3), hours(6))
    assert result.status == TRACE
    assert result.maximum == pytest.approx(0.01)

    beyond = s.total(hours(3), hours(9))
    assert beyond.status == UNKNOWN_UPPER
    assert beyond.maximum is None


def test_from_metars_collects_multiple_reports():
    """A sequence of reports is combined into one store."""
    first = Metar.Metar(
        "KEWR 101651Z 00000KT 10SM CLR 20/10 A3000 RMK P0010",
        year=2026,
        month=1,
    )
    second = Metar.Metar(
        "KEWR 101751Z 00000KT 10SM CLR 20/10 A3000 RMK P0020",
        year=2026,
        month=1,
    )
    s = PrecipitationStore.from_metars([first, second])
    assert len(s) == 2
    assert s.total(
        second.time - dt.timedelta(hours=1), second.time
    ).string() == "0.20in"


def test_store_accepts_tuple_records_and_metars():
    """Constructors accept tuples and whole METAR reports."""
    s = PrecipitationStore([(hours(0), hours(6), 0.5, "6hr")])
    assert s.total(hours(0), hours(6)).string() == "0.50in"

    report = Metar.Metar(
        "KEWR 101651Z 00000KT 10SM CLR 20/10 A3000 RMK P0010",
        year=2026,
        month=1,
    )
    combined = PrecipitationStore([report])
    assert len(combined) == 1
    assert combined.records[0].group == "precip_1hr"


def test_conflict_can_be_raised_instead_of_returned():
    """Callers that cannot render a conflict can opt into an exception."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 2.00, source="6hr")
    s.add(hours(0), hours(24), 1.00, source="24hr")
    with pytest.raises(ConflictError) as error:
        s.total(hours(0), hours(6), raise_on_conflict=True)
    assert error.value.sources == ("6hr", "24hr")


def test_strict_conflict_is_reported():
    """A positive trace inside an exact zero window is contradictory."""
    s = PrecipitationStore()
    s.add(hours(0), hours(6), 0.0, kind="trace", source="trace-6hr")
    s.add(hours(0), hours(24), 0.0, source="zero-24hr")
    result = s.total(hours(0), hours(6))
    assert result.status == CONFLICT
    assert set(result.sources) == {"trace-6hr", "zero-24hr"}


def test_bad_record_kind_is_rejected():
    """Unknown record kinds fail loudly instead of being ignored."""
    with pytest.raises(ValueError):
        PrecipitationRecord(hours(0), hours(1), 0.0, kind="sideways")


def test_negative_amount_is_rejected():
    """Negative rain amounts are invalid input."""
    with pytest.raises(ValueError):
        PrecipitationRecord(hours(0), hours(1), -0.1)
