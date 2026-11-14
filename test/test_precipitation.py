"""Test precipitation."""
from metar.Datatypes import precipitation


def test_trace():
    """How do we handle trace reports"""
    assert precipitation("0000", "IN").string() == "Trace"
    assert precipitation("0000", "IN").istrace()
    assert not precipitation("0010", "IN").istrace()


def test_trace_remark_groups():
    """P, 6 and 7 groups of zero are trace values, not exact zero."""
    from metar import Metar

    report = Metar.Metar(
        "KEWR 101651Z 00000KT 10SM CLR 20/10 A3000 RMK P0000 60000 70000",
        year=2026,
        month=1,
    )
    assert report.precip_1hr.istrace()
    assert report.precip_6hr.istrace()
    assert report.precip_24hr.istrace()
    assert report.precip_1hr.string() == "Trace"


def test_non_trace_remark_groups_keep_their_amount():
    """A non-zero P group is not scaled twice while preserving trace text."""
    from metar import Metar

    report = Metar.Metar(
        "KEWR 101651Z 00000KT 10SM CLR 20/10 A3000 RMK P0013",
        year=2026,
        month=1,
    )
    assert not report.precip_1hr.istrace()
    assert report.precip_1hr.value() == 0.13
