from datetime import date, datetime, time
from decimal import Decimal

import pytest

from position_bridge import (
    CashInLieu,
    Snapshot,
    Split,
    Trade,
    bridge,
    bridge_symbol,
    render_report,
)

D = Decimal
START = Snapshot(date(2026, 1, 31), {"ACME": D("100")})


def test_plain_trade_bridge():
    end = Snapshot(date(2026, 2, 28), {"ACME": D("110")})
    trades = [Trade("ACME", date(2026, 2, 10), D("10"))]
    result = bridge_symbol("ACME", START, end, trades, [], [])
    for scenario in result.scenarios:
        assert scenario.trade_change == D("10")
        assert scenario.split_adjustment == 0
        assert scenario.expected_end == D("110")
        assert scenario.residual == 0
    assert result.explained
    assert (result.reachable_low, result.reachable_high) == (D("110"), D("110"))


def test_plain_split_bridge():
    end = Snapshot(date(2026, 2, 28), {"ACME": D("200")})
    splits = [Split("ACME", datetime(2026, 2, 10, 9, 30), D("2"))]
    result = bridge_symbol("ACME", START, end, [], splits, [])
    for scenario in result.scenarios:
        assert scenario.split_adjustment == D("100")
        assert scenario.trade_change == 0
        assert scenario.residual == 0
    assert result.explained


def test_same_day_buy_and_split_both_legal_orderings():
    # Buy 10 on the split day; the ledger only gave a date.
    splits = [Split("ACME", datetime(2026, 2, 10, 9, 30), D("2"))]
    trades = [Trade("ACME", date(2026, 2, 10), D("10"))]

    # Trades before split: (100 + 10) * 2 = 220
    end = Snapshot(date(2026, 2, 28), {"ACME": D("220")})
    result = bridge_symbol("ACME", START, end, trades, splits, [])
    pre, post = result.scenarios
    assert pre.expected_end == D("220")
    assert pre.residual == 0
    # Split before trades: 100 * 2 + 10 = 210
    assert post.expected_end == D("210")
    assert post.residual == D("10")
    assert (result.reachable_low, result.reachable_high) == (D("210"), D("220"))
    assert result.explained  # 220 is reachable

    # An actual balance strictly inside the interval is also explained.
    end = Snapshot(date(2026, 2, 28), {"ACME": D("215")})
    result = bridge_symbol("ACME", START, end, trades, splits, [])
    assert result.explained
    assert result.unexplained_residual == 0

    # 225 cannot be reached by any legal ordering: tail difference of 5.
    end = Snapshot(date(2026, 2, 28), {"ACME": D("225")})
    result = bridge_symbol("ACME", START, end, trades, splits, [])
    assert not result.explained
    assert result.unexplained_residual == D("5")


def test_timed_trade_on_split_day_is_deterministic():
    splits = [Split("ACME", datetime(2026, 2, 10, 9, 30), D("2"))]
    trades = [Trade("ACME", date(2026, 2, 10), D("10"), time=time(14, 0))]
    end = Snapshot(date(2026, 2, 28), {"ACME": D("210")})
    result = bridge_symbol("ACME", START, end, trades, splits, [])
    for scenario in result.scenarios:
        assert scenario.expected_end == D("210")
        assert scenario.residual == 0
    assert (result.reachable_low, result.reachable_high) == (D("210"), D("210"))


def test_cash_in_lieu_is_not_a_trade():
    # 1:2 reverse split of 3 units -> 1.5, fraction 0.5 cashed out for 25.
    start = Snapshot(date(2026, 1, 31), {"ACME": D("3")})
    end = Snapshot(date(2026, 2, 28), {"ACME": D("1")})
    splits = [Split("ACME", datetime(2026, 2, 10, 9, 30), D("0.5"))]
    cils = [CashInLieu("ACME", date(2026, 2, 10), D("0.5"), D("25"))]
    result = bridge_symbol("ACME", start, end, [], splits, cils)
    for scenario in result.scenarios:
        assert scenario.trade_change == 0  # never disguised as a trade
        assert scenario.split_adjustment == D("-1.5")
        assert scenario.cash_in_lieu_units == D("-0.5")
        assert scenario.expected_end == D("1")
        assert scenario.residual == 0
    assert result.cash_in_lieu_cash == D("25")  # cash explanation only
    assert result.explained


def test_cash_in_lieu_rejects_whole_share_removal():
    with pytest.raises(ValueError):
        CashInLieu("ACME", date(2026, 2, 10), D("1"), D("50"))
    with pytest.raises(ValueError):
        CashInLieu("ACME", date(2026, 2, 10), D("0"), D("50"))


def test_unexplained_residual_without_events():
    end = Snapshot(date(2026, 2, 28), {"ACME": D("97")})
    result = bridge_symbol("ACME", START, end, [], [], [])
    assert result.unexplained_residual == D("-3")
    assert not result.explained


def test_events_outside_window_are_ignored():
    end = Snapshot(date(2026, 2, 28), {"ACME": D("100")})
    trades = [
        Trade("ACME", date(2026, 1, 31), D("5")),  # in start snapshot
        Trade("ACME", date(2026, 3, 1), D("7")),  # after end snapshot
    ]
    result = bridge_symbol("ACME", START, end, trades, [], [])
    assert result.explained
    assert result.scenarios[0].trade_change == 0


def test_bridge_covers_all_symbols_and_report():
    start = Snapshot(date(2026, 1, 31), {"ACME": D("100"), "BETA": D("3")})
    end = Snapshot(date(2026, 2, 28), {"ACME": D("220"), "BETA": D("1")})
    trades = [Trade("ACME", date(2026, 2, 10), D("10"))]
    splits = [
        Split("ACME", datetime(2026, 2, 10, 9, 30), D("2")),
        Split("BETA", datetime(2026, 2, 12, 9, 30), D("0.5")),
    ]
    cils = [CashInLieu("BETA", date(2026, 2, 12), D("0.5"), D("25"))]
    results = bridge(start, end, trades, splits, cils)
    assert [item.symbol for item in results] == ["ACME", "BETA"]
    assert all(item.explained for item in results)
    report = render_report(results)
    assert "cash explanation" in report
    assert "unexplained residual:   0" in report
