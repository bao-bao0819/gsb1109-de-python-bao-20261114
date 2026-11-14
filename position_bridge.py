"""Bridge position units between two snapshots of a migrated account.

During a historical migration the closing position snapshots and the
transaction ledger arrive at different times.  Given the *confirmed*
split/reverse-split ratios and their effective moments, this module
reconciles the units of each symbol between the two snapshots and
decomposes the movement into:

* trade changes      -- signed units from the transaction ledger
* split adjustments  -- units created/destroyed by confirmed splits
* cash-in-lieu       -- fractional units removed against a cash payment
                        (cash explanation only, never a share trade)
* unexplained residual -- whatever is left over

Conventions
-----------
* Both snapshots are end-of-day.  Events dated on ``start.as_of`` are
  assumed to be already reflected in the start snapshot; events up to
  and including ``end.as_of`` are included in the bridge.
* Split ratios are expressed as new-units per old-unit (2:1 split -> 2,
  1:2 reverse split -> 0.5).
* All decomposed quantities are expressed in *end-of-period* units so
  they are directly comparable with the end snapshot.
* A trade that only carries a date (no time) and falls on the effective
  day of a split is ambiguous: both legal orderings (trade before
  split, split before trade) are projected, and the reachable balance
  interval is computed by interval propagation across all such days.
* Cash-in-lieu of fractional shares is a corporate-action cash
  explanation.  It is validated to be strictly fractional and is never
  booked as a trade, so it cannot be used to flatten the residual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Dict, List, Mapping, Optional, Tuple

ZERO = Decimal("0")
ONE = Decimal("1")

# Scenario names
TRADES_FIRST = "trades_before_splits"
SPLITS_FIRST = "splits_before_trades"


@dataclass(frozen=True)
class Snapshot:
    """End-of-day position snapshot: symbol -> units."""

    as_of: date
    positions: Mapping[str, Decimal]


@dataclass(frozen=True)
class Trade:
    """Signed unit movement; ``time=None`` means the ledger only gave a date."""

    symbol: str
    day: date
    quantity: Decimal  # buy > 0, sell < 0
    time: Optional[time] = None


@dataclass(frozen=True)
class Split:
    """Confirmed split/reverse-split with ratio and effective moment."""

    symbol: str
    effective: datetime
    ratio: Decimal  # new units per old unit

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise ValueError("split ratio must be positive")


@dataclass(frozen=True)
class CashInLieu:
    """Fractional units removed in exchange for a cash payment.

    ``units_removed`` must be a genuine fraction (0, 1): a whole-share
    removal disguised as cash-in-lieu would silently flatten the
    residual, which is exactly what this bridge must not allow.
    """

    symbol: str
    day: date
    units_removed: Decimal
    cash_paid: Decimal

    def __post_init__(self) -> None:
        if not (ZERO < self.units_removed < ONE):
            raise ValueError(
                "cash-in-lieu must remove a fractional unit count (0, 1); "
                "whole-share removals are trades or corporate actions, "
                "not cash-in-lieu"
            )


@dataclass(frozen=True)
class ScenarioProjection:
    """Full decomposition under one legal ordering of ambiguous days."""

    name: str
    trade_change: Decimal  # end-of-period units
    split_adjustment: Decimal  # end-of-period units
    cash_in_lieu_units: Decimal  # end-of-period units (<= 0)
    expected_end: Decimal
    residual: Decimal  # actual end - expected end


@dataclass(frozen=True)
class SymbolBridge:
    symbol: str
    start_units: Decimal
    end_units: Decimal
    scenarios: Tuple[ScenarioProjection, ScenarioProjection]
    reachable_low: Decimal
    reachable_high: Decimal
    cash_in_lieu_cash: Decimal  # cash explanation only
    unexplained_residual: Decimal

    @property
    def explained(self) -> bool:
        return self.unexplained_residual == ZERO


# ---------------------------------------------------------------------------
# event ordering
# ---------------------------------------------------------------------------

# Intraday phases: pre-split ambiguous trades, timed events, post-split
# ambiguous trades, cash-in-lieu (settled end of day, after its split).
_PRE, _TIMED, _POST, _CIL = 0, 1, 2, 3


def _ordered_events(
    symbol: str,
    trades: List[Trade],
    splits: List[Split],
    cils: List[CashInLieu],
    scenario: str,
):
    """Return the deterministic event sequence for one scenario."""
    splits_by_day: Dict[date, List[Split]] = {}
    for split in splits:
        splits_by_day.setdefault(split.effective.date(), []).append(split)

    keyed = []
    for split in splits:
        keyed.append(
            (split.effective.date(), _TIMED, split.effective.time(), "split", split)
        )
    for trade in trades:
        if trade.time is not None:
            keyed.append((trade.day, _TIMED, trade.time, "trade", trade))
        elif splits_by_day.get(trade.day):
            phase = _PRE if scenario == TRADES_FIRST else _POST
            keyed.append((trade.day, phase, time.min, "trade", trade))
        else:
            keyed.append((trade.day, _TIMED, time.min, "trade", trade))
    for cil in cils:
        keyed.append((cil.day, _CIL, time.min, "cil", cil))

    keyed.sort(key=lambda item: (item[0], item[1], item[2]))
    return [(kind, payload) for _, _, _, kind, payload in keyed]


def _project(
    start_units: Decimal,
    events,
) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
    """Walk events; return (expected_end, trade, split_adj, cil) in end units."""
    ratios = [ev[1].ratio if ev[0] == "split" else ONE for ev in events]
    suffix = [ONE] * len(events)
    running = ONE
    for i in range(len(events) - 1, -1, -1):
        suffix[i] = running
        running *= ratios[i]

    balance = start_units
    trade_change = ZERO
    split_adjustment = ZERO
    cil_units = ZERO
    for (kind, payload), factor in zip(events, suffix):
        if kind == "trade":
            balance += payload.quantity
            trade_change += payload.quantity * factor
        elif kind == "split":
            delta = balance * (payload.ratio - ONE)
            balance += delta
            split_adjustment += delta * factor
        else:  # cil
            balance -= payload.units_removed
            cil_units -= payload.units_removed * factor
    return balance, trade_change, split_adjustment, cil_units


def _reachable_interval(
    start_units: Decimal,
    trades: List[Trade],
    splits: List[Split],
    cils: List[CashInLieu],
) -> Tuple[Decimal, Decimal]:
    """Exact [low, high] of reachable end balances.

    Every event is monotone non-decreasing in the running balance, so
    per-day extremes compose: on an ambiguous day the date-only trades
    either all precede or all follow that day's splits.
    """
    days: Dict[date, dict] = {}
    for split in splits:
        day = split.effective.date()
        days.setdefault(day, {"splits": [], "timed": [], "ambiguous": ZERO})
        days[day]["splits"].append(split)
    for trade in trades:
        day = trade.day
        days.setdefault(day, {"splits": [], "timed": [], "ambiguous": ZERO})
        if trade.time is None and days[day]["splits"]:
            days[day]["ambiguous"] += trade.quantity
        else:
            days[day]["timed"].append(trade)
    for cil in cils:
        day = cil.day
        days.setdefault(day, {"splits": [], "timed": [], "ambiguous": ZERO})
        days[day].setdefault("cils", []).append(cil)

    def run_day(balance: Decimal, day_info: dict, trades_first: bool) -> Decimal:
        result = balance
        if trades_first:
            result += day_info["ambiguous"]
        timed = [(s.effective.time(), "split", s) for s in day_info["splits"]]
        timed += [(t.time, "trade", t) for t in day_info["timed"]]
        timed.sort(key=lambda item: (item[0] is not None, item[0] or time.min))
        for _, kind, payload in timed:
            if kind == "split":
                result *= payload.ratio
            else:
                result += payload.quantity
        if not trades_first:
            result += day_info["ambiguous"]
        for cil in day_info.get("cils", []):
            result -= cil.units_removed
        return result

    low = high = start_units
    for day in sorted(days):
        info = days[day]
        if not info["splits"]:
            low = run_day(low, info, True)
            high = run_day(high, info, True)
            continue
        candidates = [
            run_day(low, info, True),
            run_day(low, info, False),
            run_day(high, info, True),
            run_day(high, info, False),
        ]
        low, high = min(candidates), max(candidates)
    return low, high


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def bridge_symbol(
    symbol: str,
    start: Snapshot,
    end: Snapshot,
    trades: List[Trade],
    splits: List[Split],
    cash_in_lieu: List[CashInLieu],
) -> SymbolBridge:
    """Bridge one symbol between the two snapshots."""
    start_units = start.positions.get(symbol, ZERO)
    end_units = end.positions.get(symbol, ZERO)

    in_window = lambda day: start.as_of < day <= end.as_of  # noqa: E731
    trades = [t for t in trades if t.symbol == symbol and in_window(t.day)]
    splits = [
        s for s in splits if s.symbol == symbol and in_window(s.effective.date())
    ]
    cils = [c for c in cash_in_lieu if c.symbol == symbol and in_window(c.day)]

    projections = []
    for scenario in (TRADES_FIRST, SPLITS_FIRST):
        events = _ordered_events(symbol, trades, splits, cils, scenario)
        expected, trade_change, split_adj, cil_units = _project(
            start_units, events
        )
        projections.append(
            ScenarioProjection(
                name=scenario,
                trade_change=trade_change,
                split_adjustment=split_adj,
                cash_in_lieu_units=cil_units,
                expected_end=expected,
                residual=end_units - expected,
            )
        )

    low, high = _reachable_interval(start_units, trades, splits, cils)
    if end_units < low:
        unexplained = end_units - low
    elif end_units > high:
        unexplained = end_units - high
    else:
        unexplained = ZERO

    return SymbolBridge(
        symbol=symbol,
        start_units=start_units,
        end_units=end_units,
        scenarios=(projections[0], projections[1]),
        reachable_low=low,
        reachable_high=high,
        cash_in_lieu_cash=sum((c.cash_paid for c in cils), ZERO),
        unexplained_residual=unexplained,
    )


def bridge(
    start: Snapshot,
    end: Snapshot,
    trades: List[Trade],
    splits: List[Split],
    cash_in_lieu: Optional[List[CashInLieu]] = None,
) -> List[SymbolBridge]:
    """Bridge every symbol appearing in either snapshot or the events."""
    cash_in_lieu = cash_in_lieu or []
    symbols = set(start.positions) | set(end.positions)
    symbols |= {t.symbol for t in trades}
    symbols |= {s.symbol for s in splits}
    symbols |= {c.symbol for c in cash_in_lieu}
    return [
        bridge_symbol(symbol, start, end, trades, splits, cash_in_lieu)
        for symbol in sorted(symbols)
    ]


def render_report(bridges: List[SymbolBridge]) -> str:
    """Human-readable per-symbol bridge with the three decomposed lines."""
    lines = []
    for item in bridges:
        lines.append(f"Symbol: {item.symbol}")
        lines.append(f"  start units:            {item.start_units}")
        lines.append(f"  end units (actual):     {item.end_units}")
        for scenario in item.scenarios:
            lines.append(
                f"  [{scenario.name}]"
                f" trades {scenario.trade_change}"
                f" | split adj {scenario.split_adjustment}"
                f" | cash-in-lieu {scenario.cash_in_lieu_units}"
                f" | expected {scenario.expected_end}"
                f" | residual {scenario.residual}"
            )
        lines.append(
            f"  reachable balance:      [{item.reachable_low}, {item.reachable_high}]"
        )
        if item.cash_in_lieu_cash:
            lines.append(
                f"  cash explanation (cash-in-lieu only, not a trade):"
                f" {item.cash_in_lieu_cash}"
            )
        if item.explained:
            lines.append("  unexplained residual:   0 (within reachable interval)")
        else:
            lines.append(f"  unexplained residual:   {item.unexplained_residual}")
        lines.append("")
    return "\n".join(lines)
