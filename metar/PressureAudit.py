"""Audit fixed per-instrument pressure corrections from simultaneous neighbours.

The archive supplies intervals for pressure differences at neighbouring
stations observed at the same time.  A station is served by one instrument
generation at a time; after a replacement it may carry a new fixed bias.  This
module turns those pair intervals into one correction variable per
``(station, generation)`` and solves the resulting difference constraints.

There is deliberately no variable per observation or per hour.  For generation
``g`` at station ``s`` the corrected pressure is::

    corrected(t) = reported(t) + correction[s, g]

with the *same* correction at every ``t`` in that generation's active period.
Two hours that disagree therefore produce an infeasible set; they can never be
"fixed" by giving the same instrument two offsets.

Input
-----
A plain dictionary with four keys (the first three are lists of dictionaries):

``observations``
    ``id``, ``station``, ``time``, and either ``pressure`` in hPa or a raw
    ``metar`` code.  Optional ``generation`` is checked against the dates, not
    trusted blindly.  For a ``metar`` the ordinary pressure group is preferred;
    SLP is used only when that is all the report contains.
``devices``
    ``station``, ``generation``, ``start``, and optionally ``end``.  The end
    defaults to the next generation's start; the newest generation stays open.
    Every observation must fit exactly one active generation.  Ambiguous or
    uncovered observations are reported, never assigned a convenient offset.
``pairs``
    ``station_a``, ``station_b``, ``low``, ``high``, and either ``time`` (the
    nearest simultaneous reports are used, within ``tolerance_seconds``) or
    explicit ``obs_a``/``obs_b``.  Bounds apply to the *corrected*
    ``P(station_a) - P(station_b)`` in hPa.
``calibrated``
    Station ids (or ``{"station": ..., "generation": ...}`` objects) whose
    correction is known to be zero.  A station entry without ``generation``
    pins every generation at that station.

Results
-------
Each connected component of the pair graph is solved independently.  A
component containing a calibrated generation yields absolute correction ranges.
A component without one has no external level: only differences relative to the
component's gauge are identified, and ``low``/``high`` are left ``None``.

When constraints are infeasible, a negative-late search finds a tight subset and
returns its constraint and observation ids.  A contradiction that only appears
when walking around three stations is reported as the three observations and
the three pair intervals, not as a fault of whichever station happens to be
listed first.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from metar.Metar import Metar

__all__ = [
    "AuditError",
    "Observation",
    "DeviceEra",
    "PairInterval",
    "Constraint",
    "Conflict",
    "CorrectionRange",
    "Component",
    "AuditResult",
    "decode_pressure",
    "audit_pressures",
    "audit_from_dict",
    "format_report",
]

_EPS = 1e-9


class AuditError(ValueError):
    """The archive input cannot be audited unambiguously."""


# ---------------------------------------------------------------------------
# scalar parsing


_TIME_RE = re.compile(
    r"""^\s*
        (?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})
        (?:[T ]
            (?P<hour>\d{2}):(?P<minute>\d{2})
            (?::(?P<second>\d{2})(?:\.(?P<fraction>\d+))?)?
        )?
        (?P<zone>Z|[+-]\d{2}:?\d{2})?
        \s*$""",
    re.VERBOSE,
)


def _parse_time(value, field_name: str = "time") -> datetime:
    """Parse an ISO timestamp and return a naive UTC ``datetime``."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if not isinstance(value, str):
        raise AuditError("%s must be a datetime or string, got %r" % (field_name, value))
    match = _TIME_RE.match(value)
    if not match:
        raise AuditError("unrecognized %s %r (use ISO 8601)" % (field_name, value))
    parts = match.groupdict()
    fraction = parts["fraction"] or ""
    microsecond = int((fraction + "000000")[:6]) if fraction else 0
    moment = datetime(
        int(parts["year"]),
        int(parts["month"]),
        int(parts["day"]),
        int(parts["hour"] or 0),
        int(parts["minute"] or 0),
        int(parts["second"] or 0),
        microsecond,
    )
    zone = parts["zone"]
    if zone:
        if zone == "Z":
            offset = timedelta(0)
        else:
            sign = 1 if zone[0] == "+" else -1
            digits = zone[1:].replace(":", "")
            offset = sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
        moment -= offset
    return moment


def _number(value, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise AuditError("%s must be numeric, got %r" % (field_name, value))
    if math.isnan(number) or math.isinf(number):
        raise AuditError("%s must be finite" % field_name)
    return number


def _pressure_from_metar(code: str, month=None, year=None) -> Tuple[float, str]:
    """Decode a METAR report's pressure, in hPa.

    The ordinary pressure group (altimeter/QNH) is preferred.  SLP is used only
    when it is the sole pressure in the report; it is labelled so mixing SLP
    with the ordinary group can be surfaced as a caveat.
    """
    try:
        report = Metar(code, month=month, year=year, strict=False)
    except Exception as exc:
        raise AuditError("cannot decode METAR %r: %s" % (code, exc)) from exc
    if report.press is not None:
        return float(report.press.value("HPA")), "report"
    if report.press_sea_level is not None:
        return float(report.press_sea_level.value("HPA")), "sea-level"
    raise AuditError("METAR %r reports no usable pressure group" % (code,))


def decode_pressure(code: str, month=None, year=None) -> float:
    """Return the pressure decoded from a METAR code, in hPa."""
    return _pressure_from_metar(code, month=month, year=year)[0]


# ---------------------------------------------------------------------------
# records


@dataclass(frozen=True)
class Observation:
    """One pressure observation by one station."""

    id: str
    station: str
    time: datetime
    pressure: float
    generation: Optional[str] = None
    source: str = ""
    raw: Optional[str] = None
    pressure_kind: str = "report"
    era: Optional["DeviceEra"] = None


@dataclass(frozen=True)
class DeviceEra:
    """A generation of instrument at one station, active on [start, end)."""

    station: str
    generation: str
    start: datetime
    end: Optional[datetime]

    @property
    def key(self) -> Tuple[str, str]:
        return (self.station, self.generation)

    def active_at(self, moment: datetime) -> bool:
        if moment < self.start:
            return False
        return self.end is None or moment < self.end

    def overlaps(self, other: "DeviceEra") -> bool:
        if self.end is None:
            return other.start >= self.start
        return other.start < self.end


@dataclass(frozen=True)
class PairInterval:
    """Allowed interval for a simultaneous neighbouring pressure difference."""

    station_a: str
    station_b: str
    low: float
    high: float
    time: Optional[datetime] = None
    obs_a: Optional[str] = None
    obs_b: Optional[str] = None
    id: str = ""
    tolerance: timedelta = timedelta(minutes=1)


@dataclass(frozen=True)
class Constraint:
    """A difference constraint ``low <= x_a - x_b <= high``.

    ``x`` is added to reported pressure.  A pair interval on ``P_a - P_b`` with
    reported difference ``D`` gives ``low - D <= x_a - x_b <= high - D``.
    """

    id: str
    var_a: Tuple[str, str]
    var_b: Tuple[str, str]
    low: float
    high: float
    obs_a: str = ""
    obs_b: str = ""
    pair: str = ""
    reported_diff: float = 0.0
    source_interval: Tuple[float, float] = (0.0, 0.0)
    kind: str = "pair"

    @property
    def observations(self) -> Tuple[str, ...]:
        return tuple(obs for obs in (self.obs_a, self.obs_b) if obs)


@dataclass(frozen=True)
class Conflict:
    """An infeasible subset, given as a negative cycle of constraints."""

    constraints: Tuple[Constraint, ...]
    observations: Tuple[str, ...]
    stations: Tuple[str, ...]
    amount: float
    message: str
    cycle: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class CorrectionRange:
    """Identified correction interval for one station-generation.

    ``status`` is one of:

    ``bounded``
        The component is anchored by a calibrated generation; ``low``/``high``
        are absolute corrections in hPa.
    ``relative``
        The component has no reference.  ``low``/``high`` are ``None``;
        ``relative_low``/``relative_high`` bound ``correction[var] -
        correction[gauge]``.
    ``unidentified``
        No constraints touch this generation and no reference exists.
    ``conflict``
        The component's constraints are infeasible.
    """

    var: Tuple[str, str]
    low: Optional[float]
    high: Optional[float]
    center: Optional[float]
    width: Optional[float]
    status: str
    relative_low: Optional[float] = None
    relative_high: Optional[float] = None
    relative_to: Optional[Tuple[str, str]] = None
    low_constraint: Optional[str] = None
    high_constraint: Optional[str] = None
    low_observations: Tuple[str, ...] = ()
    high_observations: Tuple[str, ...] = ()
    pair_constraints: Tuple[str, ...] = ()


@dataclass
class Component:
    """A connected group of station-generations."""

    index: int
    variables: Tuple[Tuple[str, str], ...]
    stations: Tuple[str, ...]
    anchored: bool
    calibrated_stations: Tuple[str, ...]
    gauge: Optional[Tuple[str, str]]
    solvable: bool
    conflict: Optional[Conflict] = None
    ranges: Dict[Tuple[str, str], CorrectionRange] = field(default_factory=dict)


@dataclass
class AuditResult:
    """Outcome of :func:`audit_pressures`."""

    observations: Dict[str, Observation]
    eras: Dict[Tuple[str, str], DeviceEra]
    constraints: Tuple[Constraint, ...]
    components: Tuple[Component, ...]
    unassigned: Tuple[Observation, ...]
    issues: Tuple[str, ...]
    calibrated: Tuple[str, ...]

    def ranges(self) -> Dict[Tuple[str, str], CorrectionRange]:
        result: Dict[Tuple[str, str], CorrectionRange] = {}
        for component in self.components:
            result.update(component.ranges)
        return result

    def conflicts(self) -> Tuple[Conflict, ...]:
        return tuple(c.conflict for c in self.components if c.conflict is not None)

    def observations_for(
        self, station: str, generation: str
    ) -> Tuple[Observation, ...]:
        era = self.eras.get((station, generation))
        if era is None:
            return ()
        return tuple(
            sorted(
                (obs for obs in self.observations.values() if obs.era == era),
                key=lambda obs: (obs.time, obs.id),
            )
        )

    def to_dict(self) -> dict:
        """Return a JSON-safe view of every range, component, and conflict."""
        ranges = []
        for component in self.components:
            for var in component.variables:
                item = component.ranges[var]
                ranges.append(
                    {
                        "station": var[0],
                        "generation": var[1],
                        "status": item.status,
                        "low": _round(item.low),
                        "high": _round(item.high),
                        "center": _round(item.center),
                        "width": _round(item.width),
                        "relative_low": _round(item.relative_low),
                        "relative_high": _round(item.relative_high),
                        "relative_to": list(item.relative_to) if item.relative_to else None,
                        "component": component.index,
                        "anchored": component.anchored,
                        "calibrated": var[0] in component.calibrated_stations,
                        "pair_constraints": list(item.pair_constraints),
                        "low_constraint": item.low_constraint,
                        "high_constraint": item.high_constraint,
                        "low_observations": list(item.low_observations),
                        "high_observations": list(item.high_observations),
                    }
                )
        components = [
            {
                "index": component.index,
                "stations": list(component.stations),
                "variables": [list(var) for var in component.variables],
                "anchored": component.anchored,
                "calibrated_stations": list(component.calibrated_stations),
                "solvable": component.solvable,
                "gauge": list(component.gauge) if component.gauge else None,
            }
            for component in self.components
        ]
        constraints = [
            {
                "id": c.id,
                "kind": c.kind,
                "a": list(c.var_a),
                "b": list(c.var_b),
                "low": _round(c.low),
                "high": _round(c.high),
                "obs_a": c.obs_a,
                "obs_b": c.obs_b,
                "pair": c.pair,
                "reported_diff": _round(c.reported_diff),
                "source_interval": [_round(v) for v in c.source_interval],
            }
            for c in self.constraints
        ]
        relative = []
        for component in self.components:
            if component.anchored or not component.solvable:
                continue
            bounds: Dict[Tuple[Tuple[str, str], Tuple[str, str]], List[float]] = {}
            for constraint in self.constraints:
                if constraint.var_a not in component.ranges:
                    continue
                key = (constraint.var_a, constraint.var_b)
                if key not in bounds:
                    bounds[key] = [constraint.low, constraint.high]
                else:
                    bounds[key][0] = max(bounds[key][0], constraint.low)
                    bounds[key][1] = min(bounds[key][1], constraint.high)
            for (var_a, var_b), (low, high) in sorted(bounds.items()):
                relative.append(
                    {
                        "a": list(var_a),
                        "b": list(var_b),
                        "low": _round(low),
                        "high": _round(high),
                        "component": component.index,
                    }
                )
        conflicts = [
            {
                "cycle": [list(var) for var in conflict.cycle],
                "stations": list(conflict.stations),
                "observations": list(conflict.observations),
                "amount": _round(conflict.amount),
                "message": conflict.message,
                "constraints": [_constraint_dict(c) for c in conflict.constraints],
            }
            for conflict in self.conflicts()
        ]
        return {
            "convention": {
                "corrected_pressure": "reported + correction",
                "pair_interval": "low <= corrected(station_a) - corrected(station_b) <= high",
                "relative": "correction[a] - correction[b]",
            },
            "calibrated": list(self.calibrated),
            "ranges": ranges,
            "relative": relative,
            "components": components,
            "constraints": constraints,
            "conflicts": conflicts,
            "unassigned": [
                {
                    "id": obs.id,
                    "station": obs.station,
                    "time": obs.time.isoformat(),
                    "pressure": _round(obs.pressure),
                }
                for obs in self.unassigned
            ],
            "issues": list(self.issues),
        }


def _constraint_dict(constraint: Constraint) -> dict:
    return {
        "id": constraint.id,
        "kind": constraint.kind,
        "a": list(constraint.var_a),
        "b": list(constraint.var_b),
        "low": _round(constraint.low),
        "high": _round(constraint.high),
        "obs_a": constraint.obs_a,
        "obs_b": constraint.obs_b,
        "pair": constraint.pair,
        "reported_diff": _round(constraint.reported_diff),
        "source_interval": [_round(v) for v in constraint.source_interval],
    }


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if abs(value) < 5e-4:
        return 0.0
    return round(value, 3)


# ---------------------------------------------------------------------------
# record construction


def _make_observation(record: dict, index: int) -> Observation:
    if not isinstance(record, dict):
        raise AuditError("observation %d must be an object" % index)
    station = str(record.get("station") or "").strip().upper()
    if not station:
        raise AuditError("observation %d has no station" % index)
    obs_id = str(record.get("id") or "obs-%04d" % index)
    raw = None
    if record.get("pressure") is not None:
        value = _number(record["pressure"], "pressure of observation %s" % obs_id)
        kind = str(record.get("pressure_kind") or "report").lower()
        if kind not in ("report", "sea-level"):
            raise AuditError(
                "pressure_kind of %s must be 'report' or 'sea-level'" % obs_id
            )
    elif record.get("metar"):
        raw = str(record["metar"])
        value, kind = _pressure_from_metar(
            raw, month=record.get("month"), year=record.get("year")
        )
    else:
        raise AuditError("observation %s has neither pressure nor metar" % obs_id)

    if record.get("time") is not None:
        when = _parse_time(record["time"], "time of observation %s" % obs_id)
    elif raw is not None:
        decoded = Metar(
            raw, month=record.get("month"), year=record.get("year"), strict=False
        )
        if decoded.time is None:
            raise AuditError("METAR %r has no observation time" % raw)
        when = decoded.time
    else:
        raise AuditError("observation %s has no time" % obs_id)

    generation = record.get("generation")
    return Observation(
        id=obs_id,
        station=station,
        time=when,
        pressure=value,
        generation=str(generation) if generation is not None else None,
        source=str(record.get("source") or ""),
        raw=raw,
        pressure_kind=kind,
    )


def _make_eras(records: Iterable[dict]) -> Tuple[Dict[Tuple[str, str], DeviceEra], List[str]]:
    """Build eras, deriving omitted ends from the next generation's start."""
    by_key: Dict[Tuple[str, str], DeviceEra] = {}
    by_station: Dict[str, List[dict]] = {}
    issues: List[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise AuditError("device era %d must be an object" % index)
        station = str(record.get("station") or "").strip().upper()
        generation = str(record.get("generation") or "").strip()
        if not station or not generation:
            raise AuditError("device era %d needs station and generation" % index)
        start = _parse_time(record.get("start"), "start of %s/%s" % (station, generation))
        end = None
        if record.get("end") is not None:
            end = _parse_time(record.get("end"), "end of %s/%s" % (station, generation))
            if end <= start:
                raise AuditError(
                    "device era %s/%s ends before it starts" % (station, generation)
                )
        if (station, generation) in by_key:
            raise AuditError("duplicate device era for %s/%s" % (station, generation))
        by_station.setdefault(station, []).append(
            {
                "generation": generation,
                "start": start,
                "end": end,
            }
        )
    for station, items in by_station.items():
        items.sort(key=lambda item: (item["start"], item["generation"]))
        starts = [item["start"] for item in items]
        if len(set(starts)) != len(starts):
            issues.append(
                "device eras at %s share a start date; observations there are "
                "ambiguous" % station
            )
    result: Dict[Tuple[str, str], DeviceEra] = {}
    for station, items in by_station.items():
        for position, item in enumerate(items):
            end = item["end"]
            if end is None and position + 1 < len(items):
                end = items[position + 1]["start"]
            era = DeviceEra(
                station=station,
                generation=item["generation"],
                start=item["start"],
                end=end,
            )
            result[era.key] = era
    return result, issues


def _make_pair(record: dict, index: int) -> PairInterval:
    if not isinstance(record, dict):
        raise AuditError("pair %d must be an object" % index)
    station_a = str(record.get("station_a") or record.get("a") or "").strip().upper()
    station_b = str(record.get("station_b") or record.get("b") or "").strip().upper()
    if not station_a or not station_b:
        raise AuditError("pair %d needs station_a and station_b" % index)
    if station_a == station_b:
        raise AuditError("pair %d compares %s with itself" % (index, station_a))
    low = _number(record.get("low"), "low of pair %d" % index)
    high = _number(record.get("high"), "high of pair %d" % index)
    if low > high:
        raise AuditError("pair %d has low > high" % index)
    moment = None
    if record.get("time") is not None:
        moment = _parse_time(record["time"], "time of pair %d" % index)
    tolerance = timedelta(minutes=1)
    if record.get("tolerance_seconds") is not None:
        tolerance = timedelta(
            seconds=_number(record["tolerance_seconds"], "tolerance of pair %d" % index)
        )
        if tolerance < timedelta(0):
            raise AuditError("pair %d has negative tolerance" % index)
    return PairInterval(
        station_a=station_a,
        station_b=station_b,
        low=low,
        high=high,
        time=moment,
        obs_a=record.get("obs_a"),
        obs_b=record.get("obs_b"),
        id=str(record.get("id") or "pair-%04d" % index),
        tolerance=tolerance,
    )


# ---------------------------------------------------------------------------
# observation matching


class _StationIndex:
    """Nearest-in-time lookup within one station, with tie detection."""

    def __init__(self, observations: Sequence[Observation], station: str):
        items = sorted(
            (obs for obs in observations if obs.station == station),
            key=lambda obs: (obs.time, obs.id),
        )
        self.times = [obs.time for obs in items]
        self.items = items

    def within(self, moment: datetime, tolerance: timedelta) -> List[Observation]:
        if not self.items:
            return []
        position = bisect.bisect_left(self.times, moment)
        candidates = []
        # Walk out from the insertion point until both sides are out of range;
        # this keeps every simultaneous duplicate, not just the first.
        left = position - 1
        while left >= 0 and moment - self.times[left] <= tolerance:
            candidates.append(self.items[left])
            left -= 1
        right = position
        while right < len(self.items) and self.times[right] - moment <= tolerance:
            candidates.append(self.items[right])
            right += 1
        return candidates

    def nearest(self, moment: datetime, tolerance: timedelta, pair_id: str) -> Observation:
        candidates = self.within(moment, tolerance)
        if not candidates:
            raise AuditError(
                "pair %s has no %s observation within %s of %s"
                % (pair_id, self.items[0].station if self.items else "station",
                   tolerance, moment.isoformat())
            )
        best = min(candidates, key=lambda obs: (abs(obs.time - moment), obs.id))
        closest = [obs for obs in candidates if abs(obs.time - moment) == abs(best.time - moment)]
        if len(closest) > 1:
            raise AuditError(
                "pair %s is ambiguous: %s has several observations at %s (%s)"
                % (
                    pair_id,
                    best.station,
                    moment.isoformat(),
                    ", ".join(obs.id for obs in closest),
                )
            )
        return best


def _build_constraints(
    observations: Sequence[Observation],
    pairs: Sequence[PairInterval],
    by_id: Dict[str, Observation],
    issues: List[str],
) -> List[Constraint]:
    stations = sorted({obs.station for obs in observations})
    index = {station: _StationIndex(observations, station) for station in stations}
    constraints: List[Constraint] = []
    for pair in pairs:
        try:
            if pair.obs_a:
                if pair.obs_a not in by_id:
                    raise AuditError(
                        "pair %s names unknown or unassigned observation %s"
                        % (pair.id, pair.obs_a)
                    )
                obs_a = by_id[pair.obs_a]
            elif pair.time is not None:
                obs_a = _nearest(index, pair.station_a, pair.time, pair.tolerance, pair.id)
            else:
                raise AuditError("pair %s needs time or obs_a" % pair.id)

            if pair.obs_b:
                if pair.obs_b not in by_id:
                    raise AuditError(
                        "pair %s names unknown or unassigned observation %s"
                        % (pair.id, pair.obs_b)
                    )
                obs_b = by_id[pair.obs_b]
            elif pair.time is not None:
                obs_b = _nearest(index, pair.station_b, pair.time, pair.tolerance, pair.id)
            else:
                raise AuditError("pair %s needs time or obs_b" % pair.id)
        except AuditError as exc:
            issues.append(str(exc))
            continue

        if obs_a.station != pair.station_a or obs_b.station != pair.station_b:
            raise AuditError(
                "pair %s names observations from stations other than %s and %s"
                % (pair.id, pair.station_a, pair.station_b)
            )
        if abs(obs_a.time - obs_b.time) > pair.tolerance:
            issues.append(
                "pair %s is not simultaneous (%.1f s apart)"
                % (pair.id, abs(obs_a.time - obs_b.time).total_seconds())
            )
            continue
        if obs_a.era is None or obs_b.era is None:
            issues.append(
                "pair %s touches an observation with no usable instrument era"
                % pair.id
            )
            continue
        if obs_a.pressure_kind != obs_b.pressure_kind:
            issues.append(
                "pair %s mixes %s and %s pressure; the difference may reflect "
                "reduction method, not instrument bias"
                % (pair.id, obs_a.pressure_kind, obs_b.pressure_kind)
            )
        reported = obs_a.pressure - obs_b.pressure
        constraints.append(
            Constraint(
                id=pair.id,
                var_a=obs_a.era.key,
                var_b=obs_b.era.key,
                low=pair.low - reported,
                high=pair.high - reported,
                obs_a=obs_a.id,
                obs_b=obs_b.id,
                pair=pair.id,
                reported_diff=reported,
                source_interval=(pair.low, pair.high),
                kind="pair",
            )
        )
    return constraints


def _nearest(
    index: Dict[str, _StationIndex],
    station: str,
    moment: datetime,
    tolerance: timedelta,
    pair_id: str,
) -> Observation:
    if station not in index:
        raise AuditError("pair %s references unknown station %s" % (pair_id, station))
    return index[station].nearest(moment, tolerance, pair_id)


# ---------------------------------------------------------------------------
# difference-constraint graph


class _Graph:
    """Edges encode ``x_to <= x_from + weight``."""

    def __init__(self, variables: Sequence[Tuple[str, str]]):
        self.variables = list(variables)
        self.index = {var: i for i, var in enumerate(self.variables)}
        self.edges: List[Tuple[int, int, float, Optional[Constraint]]] = []

    @property
    def size(self) -> int:
        return len(self.variables)

    def add(self, frm, to, weight, constraint=None):
        self.edges.append((self.index[frm], self.index[to], weight, constraint))

    def reversed_edges(self):
        return [(v, u, w, c) for (u, v, w, c) in self.edges]


def _bellman_ford(graph: _Graph, source: int, reverse: bool = False):
    edges = graph.reversed_edges() if reverse else graph.edges
    distance = [math.inf] * graph.size
    parent: List[Optional[Tuple[int, int]]] = [None] * graph.size
    distance[source] = 0.0
    for _ in range(max(0, graph.size - 1)):
        changed = False
        for edge_index, (u, v, weight, _constraint) in enumerate(edges):
            if distance[u] != math.inf and distance[u] + weight < distance[v] - _EPS:
                distance[v] = distance[u] + weight
                parent[v] = (u, edge_index)
                changed = True
        if not changed:
            break
    for u, v, weight, _constraint in edges:
        if distance[u] != math.inf and distance[u] + weight < distance[v] - _EPS:
            raise AuditError("internal error: infeasible component reached the range solver")
    return distance, parent


def _path_evidence(graph: _Graph, parent, target: int, source: int, reverse: bool):
    edges = graph.reversed_edges() if reverse else graph.edges
    constraint_id: Optional[str] = None
    observations: Tuple[str, ...] = ()
    node = target
    seen = set()
    while node != source:
        if node in seen:
            break
        seen.add(node)
        step = parent[node]
        if step is None:
            break
        previous, edge_index = step
        constraint = edges[edge_index][3]
        if constraint is not None and constraint.kind == "pair":
            if constraint_id is None:
                constraint_id = constraint.id
                observations = constraint.observations
        node = previous
    return constraint_id, observations


def _negative_cycle(graph: _Graph) -> Optional[List[int]]:
    """Return edge indices of a negative cycle, or ``None`` when feasible."""
    n = graph.size
    if n == 0 or not graph.edges:
        return None
    distance = [0.0] * n
    parent: List[Optional[Tuple[int, int]]] = [None] * n
    last_relaxed: Optional[int] = None
    for _ in range(n):
        last_relaxed = None
        for edge_index, (u, v, weight, _constraint) in enumerate(graph.edges):
            if distance[u] + weight < distance[v] - _EPS:
                distance[v] = distance[u] + weight
                parent[v] = (u, edge_index)
                last_relaxed = v
        if last_relaxed is None:
            return None
    if last_relaxed is None:
        return None

    # A vertex relaxed on the nth pass has a predecessor chain that enters a
    # cycle.  Walk back n times to be inside it, then collect until it repeats.
    node = last_relaxed
    for _ in range(n):
        step = parent[node]
        if step is None:
            return None
        node = step[0]
    start = node
    collected: List[int] = []
    while True:
        step = parent[node]
        if step is None:
            return None
        collected.append(step[1])
        node = step[0]
        if node == start:
            break
    collected.reverse()
    if sum(graph.edges[i][2] for i in collected) < -_EPS:
        return collected
    return None


def _find_conflict(constraints: Sequence[Constraint]) -> Optional[Conflict]:
    """Locate a negative cycle, if the constraints are infeasible."""
    if not constraints:
        return None
    variables = sorted({c.var_a for c in constraints} | {c.var_b for c in constraints})
    graph = _Graph(variables)
    for constraint in constraints:
        # x_a - x_b <= high  ->  x_a <= x_b + high
        graph.add(constraint.var_b, constraint.var_a, constraint.high, constraint)
        # x_a - x_b >= low   ->  x_b <= x_a - low
        graph.add(constraint.var_a, constraint.var_b, -constraint.low, constraint)
    edge_indices = _negative_cycle(graph)
    if edge_indices is None:
        return None

    amount = max(0.0, -sum(graph.edges[i][2] for i in edge_indices))
    cycle_edges = [graph.edges[i] for i in edge_indices]
    cycle_nodes = [graph.variables[edge[0]] for edge in cycle_edges]
    if cycle_edges:
        cycle_nodes.append(graph.variables[cycle_edges[-1][1]])

    chosen: List[Constraint] = []
    seen = set()
    for _u, _v, _weight, edge_constraint in cycle_edges:
        if edge_constraint is not None and edge_constraint.id not in seen:
            seen.add(edge_constraint.id)
            chosen.append(edge_constraint)
    if not chosen:
        return None

    observations: List[str] = []
    for constraint in chosen:
        for obs_id in constraint.observations:
            if obs_id not in observations:
                observations.append(obs_id)
    stations: List[str] = []
    for var in cycle_nodes:
        if var[0] not in stations:
            stations.append(var[0])

    loop = " -> ".join(var[0] for var in cycle_nodes)
    calibration = [c.id for c in chosen if c.kind == "calibration"]
    detail = " (including calibration pins %s)" % ", ".join(calibration) if calibration else ""
    message = (
        "no fixed correction per instrument generation satisfies the cycle %s%s; "
        "the intervals are short by %.2f hPa at their tightest. Inspect "
        "observations %s."
        % (loop, detail, amount, ", ".join(observations) if observations else "on the cycle")
    )
    return Conflict(
        constraints=tuple(chosen),
        observations=tuple(observations),
        stations=tuple(stations),
        amount=amount,
        message=message,
        cycle=tuple(cycle_nodes),
    )


# ---------------------------------------------------------------------------
# components


def _connected_components(
    variables: Sequence[Tuple[str, str]], constraints: Sequence[Constraint]
) -> List[List[Tuple[str, str]]]:
    adjacency: Dict[Tuple[str, str], set] = {var: set() for var in variables}
    for constraint in constraints:
        adjacency[constraint.var_a].add(constraint.var_b)
        adjacency[constraint.var_b].add(constraint.var_a)
    seen = set()
    result: List[List[Tuple[str, str]]] = []
    for start in variables:
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        group = []
        while stack:
            var = stack.pop()
            group.append(var)
            for neighbour in adjacency[var]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        result.append(sorted(group))
    return result


def _calibration_constraints(
    variables: Sequence[Tuple[str, str]], pinned: Sequence[Tuple[str, str]]
) -> List[Constraint]:
    anchors = sorted(var for var in variables if var in set(pinned))
    if len(anchors) < 2:
        return []
    reference = anchors[0]
    return [
        Constraint(
            id="calibration:%s/%s" % var,
            var_a=var,
            var_b=reference,
            low=0.0,
            high=0.0,
            kind="calibration",
        )
        for var in anchors[1:]
    ]


def _solve_component(
    variables: Sequence[Tuple[str, str]],
    constraints: Sequence[Constraint],
    pinned: Sequence[Tuple[str, str]],
    index: int,
) -> Component:
    stations = tuple(sorted({var[0] for var in variables}))
    pinned_vars = [var for var in variables if var in set(pinned)]
    anchored = bool(pinned_vars)
    calibrated_stations = tuple(sorted({var[0] for var in pinned_vars}))
    calibration = _calibration_constraints(variables, pinned_vars)
    all_constraints = list(constraints) + calibration

    conflict = _find_conflict(all_constraints)
    if conflict is not None:
        conflict_ranges = {
            var: CorrectionRange(
                var=var,
                low=None,
                high=None,
                center=None,
                width=None,
                status="conflict",
                pair_constraints=tuple(
                    c.id for c in constraints if var in (c.var_a, c.var_b)
                ),
            )
            for var in variables
        }
        return Component(
            index=index,
            variables=tuple(variables),
            stations=stations,
            anchored=anchored,
            calibrated_stations=calibrated_stations,
            gauge=None,
            solvable=False,
            conflict=conflict,
            ranges=conflict_ranges,
        )

    if not constraints and not anchored:
        free_ranges = {
            var: CorrectionRange(
                var=var,
                low=None,
                high=None,
                center=None,
                width=None,
                status="unidentified",
            )
            for var in variables
        }
        return Component(
            index=index,
            variables=tuple(variables),
            stations=stations,
            anchored=False,
            calibrated_stations=(),
            gauge=None,
            solvable=True,
            ranges=free_ranges,
        )

    anchor = pinned_vars[0] if anchored else variables[0]
    status = "bounded" if anchored else "relative"

    graph = _Graph(variables)
    for constraint in all_constraints:
        graph.add(constraint.var_b, constraint.var_a, constraint.high, constraint)
        graph.add(constraint.var_a, constraint.var_b, -constraint.low, constraint)
    upper, upper_parent = _bellman_ford(graph, graph.index[anchor])
    lower_distance, lower_parent = _bellman_ford(
        graph, graph.index[anchor], reverse=True
    )

    ranges: Dict[Tuple[str, str], CorrectionRange] = {}
    for var in variables:
        position = graph.index[var]
        if math.isinf(upper[position]) or math.isinf(lower_distance[position]):
            raise AuditError(
                "component %d is disconnected while solving %s/%s"
                % (index, var[0], var[1])
            )
        high = upper[position]
        low = -lower_distance[position]
        low_constraint, low_observations = _path_evidence(
            graph, lower_parent, position, graph.index[anchor], reverse=True
        )
        high_constraint, high_observations = _path_evidence(
            graph, upper_parent, position, graph.index[anchor], reverse=False
        )
        pair_constraints: List[str] = []
        for constraint in constraints:
            if var in (constraint.var_a, constraint.var_b) and constraint.id not in pair_constraints:
                pair_constraints.append(constraint.id)
        if anchored:
            ranges[var] = CorrectionRange(
                var=var,
                low=low,
                high=high,
                center=(low + high) / 2.0,
                width=high - low,
                status=status,
                low_constraint=low_constraint,
                high_constraint=high_constraint,
                low_observations=low_observations,
                high_observations=high_observations,
                pair_constraints=tuple(pair_constraints),
            )
        else:
            ranges[var] = CorrectionRange(
                var=var,
                low=None,
                high=None,
                center=(low + high) / 2.0,
                width=high - low,
                status=status,
                relative_low=low,
                relative_high=high,
                relative_to=anchor,
                low_constraint=low_constraint,
                high_constraint=high_constraint,
                low_observations=low_observations,
                high_observations=high_observations,
                pair_constraints=tuple(pair_constraints),
            )
    return Component(
        index=index,
        variables=tuple(variables),
        stations=stations,
        anchored=anchored,
        calibrated_stations=calibrated_stations,
        gauge=anchor,
        solvable=True,
        ranges=ranges,
    )


# ---------------------------------------------------------------------------
# public entry points


def _calibration_spec(
    entries: Iterable,
    eras: Dict[Tuple[str, str], DeviceEra],
) -> Tuple[Tuple[Tuple[str, str], ...], Tuple[str, ...], List[str]]:
    """Resolve calibrated entries to pinned station-generations."""
    issues: List[str] = []
    pinned: List[Tuple[str, str]] = []
    stations: List[str] = []
    all_eras = set(eras)
    for entry in entries:
        generation = None
        if isinstance(entry, str):
            station = entry.strip().upper()
        elif isinstance(entry, dict):
            station = str(entry.get("station") or "").strip().upper()
            if entry.get("generation") is not None:
                generation = str(entry["generation"])
        else:
            raise AuditError("calibrated entry must be a station id or object")
        if not station:
            raise AuditError("calibrated entry needs a station")
        if station not in stations:
            stations.append(station)
        if generation is None:
            keys = sorted(key for key in all_eras if key[0] == station)
            if not keys:
                issues.append("calibrated station %s has no device era" % station)
            for key in keys:
                if key not in pinned:
                    pinned.append(key)
        else:
            key = (station, generation)
            if key not in all_eras:
                issues.append(
                    "calibrated generation %s/%s has no device era"
                    % (station, generation)
                )
            elif key not in pinned:
                pinned.append(key)
    return tuple(sorted(pinned)), tuple(sorted(stations)), issues


def audit_pressures(
    observations: Iterable[dict],
    devices: Iterable[dict],
    pairs: Iterable[dict],
    *,
    calibrated: Iterable = (),
) -> AuditResult:
    """Audit one fixed correction range per station and instrument generation."""
    issues: List[str] = []
    eras, era_issues = _make_eras(devices)
    issues.extend(era_issues)
    calibrated_vars, calibrated_stations, calibration_issues = _calibration_spec(
        calibrated, eras
    )
    issues.extend(calibration_issues)

    parsed: List[Observation] = []
    seen_ids = set()
    for index, record in enumerate(observations):
        obs = _make_observation(record, index)
        if obs.id in seen_ids:
            raise AuditError("duplicate observation id %s" % obs.id)
        seen_ids.add(obs.id)
        parsed.append(obs)

    by_station: Dict[str, List[DeviceEra]] = {}
    for era in eras.values():
        by_station.setdefault(era.station, []).append(era)
    for station in by_station:
        by_station[station].sort(key=lambda era: (era.start, era.generation))

    assigned: List[Observation] = []
    unassigned: List[Observation] = []
    for obs in parsed:
        active = [era for era in by_station.get(obs.station, ()) if era.active_at(obs.time)]
        if obs.generation is not None:
            names = [era for era in active if era.generation == obs.generation]
            known = any(
                era.generation == obs.generation
                for era in by_station.get(obs.station, ())
            )
            if not names:
                if known:
                    issues.append(
                        "observation %s names generation %s but %s is outside that "
                        "generation's dates"
                        % (obs.id, obs.generation, obs.time.isoformat())
                    )
                else:
                    issues.append(
                        "observation %s names unknown generation %s at %s"
                        % (obs.id, obs.generation, obs.station)
                    )
                unassigned.append(obs)
                continue
            active = names
        if len(active) == 1:
            assigned.append(_with_era(obs, active[0]))
        elif not active:
            issues.append(
                "observation %s at %s matches no device era at %s"
                % (obs.id, obs.time.isoformat(), obs.station)
            )
            unassigned.append(obs)
        else:
            issues.append(
                "observation %s at %s matches %d overlapping device eras at %s (%s)"
                % (
                    obs.id,
                    obs.time.isoformat(),
                    len(active),
                    obs.station,
                    ", ".join(era.generation for era in active),
                )
            )
            unassigned.append(obs)

    assigned_by_id = {obs.id: obs for obs in assigned}
    by_id = {obs.id: assigned_by_id.get(obs.id, obs) for obs in parsed}
    lookup = [assigned_by_id.get(obs.id, obs) for obs in parsed]
    pair_records = [_make_pair(record, index) for index, record in enumerate(pairs)]
    seen_pairs = set()
    for pair in pair_records:
        if pair.id in seen_pairs:
            raise AuditError("duplicate pair id %s" % pair.id)
        seen_pairs.add(pair.id)
    # Selection sees every parsed report so an unassigned report is named in
    # the issue; the era check below then rejects it explicitly.
    constraints = _build_constraints(lookup, pair_records, by_id, issues)

    variables = sorted(eras)
    components: List[Component] = []
    for group in _connected_components(variables, constraints):
        group_set = set(group)
        group_constraints = [
            c for c in constraints if c.var_a in group_set and c.var_b in group_set
        ]
        components.append(
            _solve_component(group, group_constraints, calibrated_vars, len(components))
        )
    components.sort(key=lambda component: (component.stations, component.variables))
    for position, component in enumerate(components):
        component.index = position

    return AuditResult(
        observations=assigned_by_id,
        eras=eras,
        constraints=tuple(constraints),
        components=tuple(components),
        unassigned=tuple(unassigned),
        issues=tuple(issues),
        calibrated=calibrated_stations,
    )


def _with_era(obs: Observation, era: DeviceEra) -> Observation:
    return Observation(
        id=obs.id,
        station=obs.station,
        time=obs.time,
        pressure=obs.pressure,
        generation=obs.generation,
        source=obs.source,
        raw=obs.raw,
        pressure_kind=obs.pressure_kind,
        era=era,
    )


def audit_from_dict(document: dict) -> AuditResult:
    """Run an audit from one JSON-shaped document."""
    if not isinstance(document, dict):
        raise AuditError("audit input must be an object")
    for key in ("observations", "devices", "pairs"):
        if key not in document:
            raise AuditError("audit input is missing %r" % key)
    return audit_pressures(
        document["observations"],
        document["devices"],
        document["pairs"],
        calibrated=document.get("calibrated", ()),
    )


# ---------------------------------------------------------------------------
# output


def format_report(result: AuditResult) -> str:
    """Render a compact human-readable audit report."""
    lines: List[str] = []
    ranges = result.ranges()
    lines.append(
        "station  generation  status        correction range (hPa)  basis"
    )
    for var in sorted(ranges):
        item = ranges[var]
        if item.status == "bounded" and item.low is not None and item.high is not None:
            bounds = "[%+.2f, %+.2f]" % (item.low, item.high)
            basis = "absolute"
        elif (
            item.status == "relative"
            and item.relative_low is not None
            and item.relative_high is not None
        ):
            bounds = "[%+.2f, %+.2f]" % (item.relative_low, item.relative_high)
            reference = item.relative_to or ("?", "?")
            basis = "relative to %s/%s" % (reference[0], reference[1])
        else:
            bounds = "-"
            basis = item.status
        lines.append(
            "%-8s %-11s %-13s %-23s %s"
            % (var[0], var[1], item.status, bounds, basis)
        )
    for component in result.components:
        if component.conflict is not None:
            lines.append("")
            lines.append("CONFLICT: " + component.conflict.message)
            for constraint in component.conflict.constraints:
                lines.append(
                    "  %-18s %s/%s vs %s/%s  obs %s/%s  reported diff %+.2f hPa"
                    % (
                        constraint.id,
                        constraint.var_a[0],
                        constraint.var_a[1],
                        constraint.var_b[0],
                        constraint.var_b[1],
                        constraint.obs_a or "-",
                        constraint.obs_b or "-",
                        constraint.reported_diff,
                    )
                )
    if result.unassigned:
        lines.append("")
        lines.append(
            "unassigned observations: " + ", ".join(obs.id for obs in result.unassigned)
        )
    if result.issues:
        lines.append("")
        lines.append("issues:")
        lines.extend("  - " + issue for issue in result.issues)
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Read an audit document and print the result."""
    parser = argparse.ArgumentParser(
        description="Audit fixed corrections per station and instrument generation."
    )
    parser.add_argument(
        "path", nargs="?", help="JSON audit document; read standard input when omitted"
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="output format (default: json)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any component is in conflict",
    )
    args = parser.parse_args(argv)
    try:
        if args.path:
            with open(args.path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        else:
            document = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as exc:
        print("cannot read audit document: %s" % exc, file=sys.stderr)
        return 2
    try:
        result = audit_from_dict(document)
    except AuditError as exc:
        print("audit error: %s" % exc, file=sys.stderr)
        return 2
    if args.format == "text":
        print(format_report(result))
    else:
        json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    if args.strict and result.conflicts():
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
