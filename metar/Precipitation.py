# Copyright (c) 2004,2018 Python-Metar Developers.
# Distributed under the terms of the BSD 2-Clause License.
# SPDX-License-Identifier: BSD-2-Clause
"""Interval estimates for reported cumulative precipitation.

A rain gauge report rarely tells us the amount in every hour.  A METAR may
instead carry one-hour, three-hour, six-hour and twenty-four-hour totals whose
windows overlap.  :class:`PrecipitationStore` keeps those records and answers
questions about arbitrary intervals from them.

The records are treated as constraints on a non-negative hourly rain rate.  In
particular, a six-hour total and a twenty-four-hour total are *not* added
together: they are simultaneous observations of overlapping windows.  The
bounds returned for an interval are the tightest bounds implied by all of the
records that overlap it.

When the records are mutually inconsistent (for example, a six-hour total that
is larger than the twenty-four-hour total containing it), the query does not
silently clamp the impossible hours to zero.  It returns a
:class:`PrecipitationConflict` naming the records that form the contradictory
cycle instead.  ``raise_on_conflict=True`` turns that result into a
:class:`PrecipitationConflictError` for callers that prefer an exception.

The public entry points are :class:`PrecipitationStore` (also exported as
:class:`RainGauge`), :class:`PrecipitationRecord`, :class:`PrecipitationTotal`
and :class:`PrecipitationConflict`.  Times are naive UTC datetimes; ``date``
objects and ISO-8601 strings are accepted and normalized.  Integer interval
endpoints mean hours relative to the store's anchor.
"""
from __future__ import annotations

import datetime as _datetime
import math
from typing import Iterable, Optional

from metar.Datatypes import precipitation

# One inch is 2.54 centimetres.  Stored amounts are always in inches.
_CM = 2.54

# A METAR trace is positive but below the smallest reportable non-zero amount.
# The P/6/7 groups are reported to the nearest 0.01 inch, so a trace is
# greater than zero and less than 0.01 inch.
DEFAULT_TRACE_LIMIT = 0.01

# Values for PrecipitationTotal.status.
EXACT = "exact"
ZERO = "zero"
TRACE = "trace"
UNKNOWN = "unknown"
UNKNOWN_UPPER = "unknown_upper"
RANGE = "range"
CONFLICT = "conflict"


def _coerce_units(units: Optional[str]) -> str:
    if units is None:
        return "IN"
    units = units.upper()
    if units in ("IN", "INCH", "INCHES"):
        return "IN"
    if units in ("CM", "CENTIMETER", "CENTIMETERS", "CENTIMETRE", "CENTIMETRES"):
        return "CM"
    raise ValueError("unrecognized precipitation unit: %r" % (units,))


def _convert_from_inches(value: float, units: str) -> float:
    if units == "CM":
        return value * _CM
    return value


def _convert_to_inches(value: float, units: str) -> float:
    if units == "CM":
        return value / _CM
    return value


def _coerce_time(value, anchor=None):
    """Return a naive UTC datetime for a time argument."""
    if isinstance(value, _datetime.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_datetime.timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, _datetime.date):
        return _datetime.datetime.combine(value, _datetime.time())
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1]
        parsed = _datetime.datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_datetime.timezone.utc).replace(tzinfo=None)
        return parsed
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if anchor is None:
            raise ValueError(
                "an anchor time is required when an interval is given in hours"
            )
        return anchor + _datetime.timedelta(hours=float(value))
    raise TypeError("unsupported time value: %r" % (value,))


def _clean(value: float) -> float:
    if value is None:
        return None
    if abs(value) < 1e-9:
        return 0.0
    # Reported amounts are hundredths of an inch.  Rounding here keeps binary
    # floating point noise from showing up as a fake range.
    return round(float(value), 9)


class PrecipitationRecord:
    """A precipitation amount reported for a half-open interval ``[start, end)``.

    ``kind`` describes how the amount constrains the interval total:

    ``exact``
        The total equals ``amount``.
    ``trace``
        The total is greater than zero and less than the trace limit.
    ``at_least``
        The total is at least ``amount``.  No upper bound is supplied by this
        record.  Pass ``strict=True`` for a strict lower bound.
    ``at_most``
        The total is at most ``amount``.  Pass ``strict=True`` for a strict
        upper bound.
    ``unknown``
        The record carries no usable amount.
    """

    _KIND_ALIASES = {
        "exact": "exact",
        "=": "exact",
        "trace": "trace",
        "at_least": "at_least",
        "atleast": "at_least",
        "lower_bound": "at_least",
        "lower": "at_least",
        "greater_than": "at_least",
        "gt": "at_least",
        ">": "at_least",
        ">=": "at_least",
        "min": "at_least",
        "at_most": "at_most",
        "atmost": "at_most",
        "upper_bound": "at_most",
        "upper": "at_most",
        "less_than": "at_most",
        "lt": "at_most",
        "<": "at_most",
        "<=": "at_most",
        "max": "at_most",
        "unknown": "unknown",
        "none": "unknown",
    }

    def __init__(
        self,
        start,
        end,
        amount=0.0,
        kind="exact",
        source=None,
        units=None,
        attribution=None,
        strict=None,
    ):
        start = _coerce_time(start)
        end = _coerce_time(end)
        if end <= start:
            raise ValueError("precipitation interval must have end after start")

        record_units = _coerce_units(units)
        if isinstance(amount, str):
            text = amount.strip()
            if text.lower() in ("trace", "t"):
                kind = "trace"
                amount = 0.0
            elif len(text) == 5 and text[0] in "PM" and text[1:].isdigit():
                # A complete METAR hourly group, in hundredths of an inch.
                # Four zeros denote a trace.
                digits = text[1:]
                record_units = "IN"
                if digits == "0000":
                    kind = "trace"
                    amount = 0.0
                else:
                    amount = float(digits) / 100.0
                if text[0] == "M":
                    kind = "at_most"
                    strict = True
            elif len(text) == 5 and text[0] in "67" and text[1:].isdigit():
                # A cumulative 6xxxx or 7xxxx group.  Four zeros are trace.
                digits = text[1:]
                record_units = "IN"
                if digits == "0000":
                    kind = "trace"
                    amount = 0.0
                else:
                    amount = float(digits) / 100.0
            elif text.isdigit() and len(text) == 4:
                # A bare four-digit amount in hundredths of an inch.
                record_units = "IN"
                if set(text) == {"0"}:
                    kind = "trace"
                    amount = 0.0
                else:
                    amount = float(text) / 100.0
            else:
                amount = float(text)
        if hasattr(amount, "value") and hasattr(amount, "istrace"):
            precip = amount
            record_units = _coerce_units(getattr(precip, "_units", None))
            if precip.istrace():
                amount = 0.0
                kind = "trace"
            else:
                gtlt = getattr(precip, "_gtlt", None)
                if gtlt == ">":
                    kind = "at_least"
                    strict = True
                elif gtlt == "<":
                    kind = "at_most"
                    strict = True
                amount = precip.value()

        try:
            kind = self._KIND_ALIASES[str(kind).lower()]
        except KeyError:
            raise ValueError("unrecognized precipitation record kind: %r" % (kind,))
        if kind == "trace":
            amount = 0.0
        else:
            amount = float(amount)
        if amount < 0:
            raise ValueError("precipitation amount cannot be negative")
        if strict is None:
            strict = False

        self.start = start
        self.end = end
        self.amount = _convert_to_inches(amount, record_units)
        self.kind = kind
        self.source = source
        self.units = record_units
        self.attribution = attribution
        self.strict = bool(strict)

    @classmethod
    def from_metar(cls, metar, attr, hours=None):
        """Build a record from a METAR precipitation attribute."""
        precip = getattr(metar, attr)
        if hours is None:
            hours = float(attr.rsplit("_", 1)[1].replace("hr", ""))
        return cls(
            metar.time - _datetime.timedelta(hours=hours),
            metar.time,
            precip,
            source=getattr(metar, "station_id", None) or attr,
            attribution=attr,
        )

    @property
    def duration_hours(self):
        """Length of the reported interval in hours."""
        return (self.end - self.start).total_seconds() / 3600.0

    @property
    def hours(self):
        """Alias for :attr:`duration_hours`."""
        return self.duration_hours

    @property
    def window(self):
        """The reported ``(start, end)`` interval."""
        return (self.start, self.end)

    interval = window

    @property
    def group(self):
        """The METAR group/attribute that produced this record, if known."""
        return self.attribution

    def value(self, units=None):
        """Return the reported amount in the requested units."""
        units = self.units if units is None else _coerce_units(units)
        return _convert_from_inches(self.amount, units)

    def total(self, units=None):
        """Alias for :meth:`value`."""
        return self.value(units)

    def covers(self, start, end):
        """Whether this record's window contains the whole query interval."""
        start = _coerce_time(start)
        end = _coerce_time(end)
        return self.start <= start and self.end >= end

    def overlaps(self, start, end):
        """Whether this record's window overlaps the query interval."""
        start = _coerce_time(start)
        end = _coerce_time(end)
        return self.start < end and self.end > start

    def __repr__(self):
        label = self.source if self.source is not None else self.attribution
        suffix = " %r" % (label,) if label is not None else ""
        return "<PrecipitationRecord %s-%s %s %.4fin%s>" % (
            self.start.isoformat(),
            self.end.isoformat(),
            self.kind,
            self.amount,
            suffix,
        )

    def __str__(self):
        amount = "Trace" if self.kind == "trace" else "%.2fin" % self.amount
        label = self.source if self.source is not None else self.attribution
        if label:
            return "%s (%s)" % (amount, label)
        return amount


class PrecipitationConflict:
    """The source records that make a rain-gauge query contradictory."""

    def __init__(self, records=(), message=None, interval=None):
        self.records = tuple(records)
        self.sources = tuple(r.source for r in self.records if r.source is not None)
        self.message = message or "inconsistent precipitation records"
        self.interval = interval

    @property
    def groups(self):
        """Report-group names (``precip_6hr`` etc.) behind the conflict."""
        result = []
        for record in self.records:
            group = record.attribution or record.source
            if group is not None and group not in result:
                result.append(group)
        return tuple(result)

    @property
    def source_groups(self):
        return self.groups

    def __bool__(self):
        return True

    def __iter__(self):
        return iter(self.records)

    def __len__(self):
        return len(self.records)

    def __repr__(self):
        return "<PrecipitationConflict sources=%r>" % (self.sources,)

    def __str__(self):
        if self.sources:
            return "%s: %s" % (self.message, "; ".join(str(s) for s in self.sources))
        if self.records:
            return "%s: %s" % (self.message, "; ".join(str(r) for r in self.records))
        return self.message


class PrecipitationConflictError(Exception):
    """Raised when strict conflict handling is requested and records disagree."""

    def __init__(self, conflict):
        self.conflict = conflict
        self.sources = conflict.sources
        self.records = conflict.records
        super().__init__(str(conflict))


ConflictError = PrecipitationConflictError


class PrecipitationTotal:
    """Bounds on an interval total implied by the available records.

    ``minimum`` and ``maximum`` are expressed in :attr:`units`.  ``maximum`` is
    ``None`` when the observations do not put any finite upper bound on the
    interval.  A conflict is exposed through :attr:`conflict` /
    :attr:`conflicts` rather than represented as a numeric result.
    """

    def __init__(
        self,
        minimum,
        maximum,
        status,
        units="IN",
        conflicts=(),
        records=(),
        trace_limit=DEFAULT_TRACE_LIMIT,
        minimum_open=False,
        maximum_open=False,
        start=None,
        end=None,
    ):
        self.minimum = minimum
        self.maximum = maximum
        self.status = status
        self.units = _coerce_units(units)
        self.conflicts = tuple(conflicts)
        self.records = tuple(records)
        self.trace_limit = trace_limit
        self.minimum_open = bool(minimum_open)
        self.maximum_open = bool(maximum_open)
        self.start = start
        self.end = end

    @property
    def min(self):
        return self.minimum

    @property
    def max(self):
        return self.maximum

    @property
    def lower(self):
        return self.minimum

    @property
    def upper(self):
        return self.maximum

    @property
    def conflict(self):
        return self.conflicts[0] if self.conflicts else None

    @property
    def has_conflict(self):
        return bool(self.conflicts)

    @property
    def is_conflict(self):
        return self.has_conflict

    @property
    def groups(self):
        """Report-group names (``precip_6hr`` etc.) behind the conflict."""
        result = []
        for conflict in self.conflicts:
            for group in conflict.groups:
                if group not in result:
                    result.append(group)
        return tuple(result)

    @property
    def source_groups(self):
        return self.groups

    @property
    def sources(self):
        """Source labels from the conflicting records, in order."""
        result = []
        for conflict in self.conflicts:
            for source in conflict.sources:
                if source not in result:
                    result.append(source)
        return tuple(result)

    @property
    def conflict_sources(self):
        return self.sources

    @property
    def is_zero(self):
        return not self.has_conflict and self.minimum == 0 and self.maximum == 0

    @property
    def is_trace(self):
        return self.status == TRACE

    @property
    def is_trace_only(self):
        return self.status == TRACE

    @property
    def is_unknown(self):
        return self.status == UNKNOWN

    @property
    def has_data(self):
        return self.status != UNKNOWN

    @property
    def has_unknown_maximum(self):
        return not self.has_conflict and self.maximum is None

    @property
    def upper_bound_unknown(self):
        return self.has_unknown_maximum

    @property
    def unknown_upper(self):
        return self.has_unknown_maximum

    @property
    def is_exact(self):
        return (
            not self.has_conflict
            and self.maximum is not None
            and self.minimum == self.maximum
        )

    @property
    def is_upper_unbounded(self):
        return self.has_unknown_maximum

    is_unknown_upper = is_upper_unbounded

    @property
    def has_upper_bound(self):
        return not self.has_conflict and self.maximum is not None

    @property
    def has_finite_maximum(self):
        return self.has_upper_bound

    @property
    def exact_zero(self):
        return self.is_zero

    @property
    def trace_only(self):
        return self.is_trace

    @property
    def bounds(self):
        """Return ``(minimum, maximum)`` in the result's units."""
        return self.minimum, self.maximum

    @property
    def lower_bound(self):
        return self.minimum

    @property
    def upper_bound(self):
        return self.maximum

    @property
    def minimum_possible(self):
        return self.minimum

    @property
    def maximum_possible(self):
        return self.maximum

    @property
    def window(self):
        """The queried ``(start, end)`` interval."""
        return (self.start, self.end)

    def value(self, units=None):
        """Return ``(minimum, maximum)`` in the requested units."""
        if units is None:
            return self.minimum, self.maximum
        units = _coerce_units(units)
        minimum = _convert_from_inches(
            _convert_to_inches(self.minimum, self.units), units
        )
        if self.maximum is None:
            return minimum, None
        maximum = _convert_from_inches(
            _convert_to_inches(self.maximum, self.units), units
        )
        return minimum, maximum

    def string(self, units=None):
        """Return a compact human-readable description of the result."""
        units = self.units if units is None else _coerce_units(units)
        unit_text = "cm" if units == "CM" else "in"
        if self.has_conflict:
            return str(self.conflict)
        minimum, maximum = self.value(units)
        if maximum is None:
            return "at least %.2f%s (upper bound unknown)" % (minimum, unit_text)
        if self.is_zero:
            return "0.00%s" % unit_text
        if self.status == TRACE:
            return "Trace (0.00%s to %.2f%s)" % (unit_text, maximum, unit_text)
        if minimum == maximum:
            return "%.2f%s" % (minimum, unit_text)
        return "%.2f%s to %.2f%s" % (minimum, unit_text, maximum, unit_text)

    def as_dict(self, units=None):
        """Return a plain dictionary summary of the result."""
        minimum, maximum = self.value(units)
        return {
            "start": self.start,
            "end": self.end,
            "minimum": minimum,
            "maximum": maximum,
            "status": self.status,
            "units": self.units if units is None else _coerce_units(units),
            "trace_limit": self.trace_limit,
            "sources": self.sources,
            "groups": self.groups,
            "has_conflict": self.has_conflict,
            "minimum_open": self.minimum_open,
            "maximum_open": self.maximum_open,
        }

    def __str__(self):
        return self.string()

    def __repr__(self):
        return "<PrecipitationTotal %s %s..%s %s>" % (
            self.status,
            self.minimum,
            self.maximum,
            self.units,
        )

    def __iter__(self):
        yield self.minimum
        yield self.maximum


class _Edge:
    __slots__ = ("u", "v", "weight", "record", "strict", "reason")

    def __init__(self, u, v, weight, record=None, strict=False, reason=None):
        self.u = u
        self.v = v
        self.weight = weight
        self.record = record
        self.strict = strict
        self.reason = reason

    def __repr__(self):
        return "<_Edge %s->%s %.6f %r>" % (self.u, self.v, self.weight, self.reason)


class PrecipitationStore:
    """A collection of overlapping cumulative precipitation observations.

    Add records with :meth:`add` or :meth:`add_metar`, then query any interval
    with :meth:`total` (also available as :meth:`interval`, :meth:`query` and
    :meth:`accumulated`).  Integer interval endpoints are interpreted as hours
    relative to :attr:`anchor`, which defaults to the earliest record start.
    """

    def __init__(self, records=None, trace_limit=DEFAULT_TRACE_LIMIT):
        if trace_limit is None or float(trace_limit) <= 0:
            raise ValueError("trace_limit must be positive")
        self.records = []
        self.trace_limit = float(trace_limit)
        if records:
            self.add_records(records)

    @property
    def anchor(self):
        """Earliest record start, used for hour-offset interval arguments."""
        if not self.records:
            return None
        return min(record.start for record in self.records)

    @property
    def latest(self):
        """Latest record end."""
        if not self.records:
            return None
        return max(record.end for record in self.records)

    def __len__(self):
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def __getitem__(self, index):
        return self.records[index]

    def add_record(self, record):
        if not isinstance(record, PrecipitationRecord):
            raise TypeError("expected a PrecipitationRecord")
        self.records.append(record)
        return record

    def add_records(self, records):
        """Add a batch of records, METAR reports, mappings or tuples."""
        added = []
        for record in records:
            if isinstance(record, PrecipitationRecord):
                added.append(self.add_record(record))
            elif hasattr(record, "precip_1hr") or hasattr(record, "time"):
                added.extend(self.add_metar(record))
            elif isinstance(record, dict):
                added.append(self.add(**record))
            elif isinstance(record, (tuple, list)):
                if len(record) == 4:
                    start, end, amount, source = record
                    added.append(self.add(start, end, amount, source=source))
                else:
                    added.append(self.add(*record))
            else:
                raise TypeError("unsupported precipitation record: %r" % (record,))
        return added

    def add(
        self,
        start,
        end=None,
        amount=0.0,
        kind="exact",
        source=None,
        units=None,
        attribution=None,
        strict=None,
        duration=None,
    ):
        """Add a record using the same arguments as :class:`PrecipitationRecord`.

        ``duration`` is an optional convenience: it may be an hour count or a
        ``timedelta`` and, when given, supplies ``end`` if it is omitted.
        """
        if isinstance(start, PrecipitationRecord):
            return self.add_record(start)
        if end is None and duration is not None:
            start_time = _coerce_time(start)
            if isinstance(duration, _datetime.timedelta):
                end = start_time + duration
            else:
                end = start_time + _datetime.timedelta(hours=float(duration))
        record = PrecipitationRecord(
            start,
            end,
            amount,
            kind,
            source=source,
            units=units,
            attribution=attribution,
            strict=strict,
        )
        return self.add_record(record)

    record = add
    add_observation = add
    observe = add
    add_precipitation = add
    add_many = add_records

    def add_metar(self, metar, source=None):
        """Add all cumulative precipitation records carried by a METAR report.

        Returns the records that were added.  ``metar`` may also be an iterable
        of METAR objects.  Each METAR's own ``precip_*`` attributes supply the
        amount and the report time supplies the window end.  A six-hour group
        is only recognized as such when the parser put it in ``precip_6hr``;
        the store does not infer a window from the report cycle.
        """
        if not hasattr(metar, "precip_1hr") and isinstance(metar, Iterable):
            added = []
            for item in metar:
                added.extend(self.add_metar(item, source=source))
            return added

        time = getattr(metar, "time", None)
        if time is None:
            return []

        provided_source = source is not None
        added = []
        specs = (
            ("precip_1hr", 1.0),
            ("precip_3hr", 3.0),
            ("precip_6hr", 6.0),
            ("precip_24hr", 24.0),
        )
        for attr, hours in specs:
            precip = getattr(metar, attr, None)
            if precip is None:
                continue
            if provided_source:
                label = "%s %s" % (source, attr)
            else:
                label = attr
            record = PrecipitationRecord(
                time - _datetime.timedelta(hours=hours),
                time,
                precip,
                source=label,
                attribution=attr,
            )
            self.add_record(record)
            added.append(record)
        return added

    @classmethod
    def from_metars(cls, metars, trace_limit=DEFAULT_TRACE_LIMIT):
        """Build a store from one METAR or an iterable of METARs."""
        store = cls(trace_limit=trace_limit)
        store.add_metar(metars)
        return store

    from_metar = from_metars

    def clear(self):
        self.records = []

    def _coerce_interval(self, start, end=None, anchor=None):
        if end is None and isinstance(start, (tuple, list)) and len(start) == 2:
            start, end = start
        if end is None:
            raise TypeError("an interval requires both start and end")
        if anchor is None:
            anchor = self.anchor
        elif isinstance(anchor, str):
            if anchor.lower() in ("latest", "end", "last"):
                anchor = self.latest
            elif anchor.lower() in ("earliest", "start", "first"):
                anchor = self.anchor
        return _coerce_time(start, anchor), _coerce_time(end, anchor)

    def _relevant_records(self, start, end):
        """Records whose overlap chain can constrain the query interval.

        Records that do not overlap the query, directly or through a chain of
        overlapping records, can only supply monotonic information.  That does
        not tighten the difference between two times, so they are left out of
        the constraint graph.
        """
        selected = [r for r in self.records if r.overlaps(start, end)]
        changed = True
        while changed:
            changed = False
            for candidate in self.records:
                if candidate in selected:
                    continue
                if any(candidate.overlaps(r.start, r.end) for r in selected):
                    selected.append(candidate)
                    changed = True
        return selected

    def total(
        self,
        start,
        end=None,
        units=None,
        anchor=None,
        trace_limit=None,
        raise_on_conflict=False,
    ):
        """Return bounds for the total over ``[start, end)``.

        ``start`` and ``end`` may be datetimes, ISO-8601 strings, dates, or
        numbers of hours relative to ``anchor`` (default: earliest record
        start).  A two-element sequence may also be passed as ``start``.

        The result's ``minimum`` and ``maximum`` are in ``units``.  A missing
        finite upper bound is reported as ``maximum is None``; the result's
        ``status`` distinguishes ``"zero"``, ``"trace"``, ``"unknown"``,
        ``"unknown_upper"``, ``"range"``, ``"exact"`` and ``"conflict"``.
        """
        start, end = self._coerce_interval(start, end, anchor)
        if end <= start:
            raise ValueError("precipitation interval must have end after start")
        units = _coerce_units(units)
        trace_limit = self.trace_limit if trace_limit is None else float(trace_limit)
        if trace_limit <= 0:
            raise ValueError("trace_limit must be positive")

        relevant = self._relevant_records(start, end)
        nodes = sorted(
            {start, end} | {r.start for r in relevant} | {r.end for r in relevant}
        )
        index = {node: i for i, node in enumerate(nodes)}
        edges = []

        # Cumulative precipitation cannot decrease with time.
        for i in range(len(nodes) - 1):
            edges.append(_Edge(i + 1, i, 0.0, reason="monotonic"))

        for record in relevant:
            u = index[record.start]
            v = index[record.end]
            amount = record.amount
            if record.kind == "exact":
                edges.append(_Edge(u, v, amount, record, reason="exact"))
                edges.append(_Edge(v, u, -amount, record, reason="exact"))
            elif record.kind == "trace":
                edges.append(_Edge(v, u, 0.0, record, strict=True, reason="trace>0"))
                edges.append(
                    _Edge(
                        u,
                        v,
                        trace_limit,
                        record,
                        strict=True,
                        reason="trace<limit",
                    )
                )
            elif record.kind == "at_least":
                edges.append(
                    _Edge(
                        v,
                        u,
                        -amount,
                        record,
                        strict=record.strict,
                        reason="at_least",
                    )
                )
            elif record.kind == "at_most":
                edges.append(
                    _Edge(
                        u,
                        v,
                        amount,
                        record,
                        strict=record.strict,
                        reason="at_most",
                    )
                )

        conflict = self._find_conflict(nodes, edges)
        if conflict is not None:
            conflict.interval = (start, end)
            if raise_on_conflict:
                raise PrecipitationConflictError(conflict)
            return PrecipitationTotal(
                None,
                None,
                CONFLICT,
                units,
                conflicts=(conflict,),
                records=relevant,
                trace_limit=trace_limit,
                start=start,
                end=end,
            )

        start_index = index[start]
        end_index = index[end]

        path_max = _shortest_path(nodes, edges, start_index, end_index)
        path_min = _shortest_path(nodes, edges, end_index, start_index)

        maximum = None
        maximum_open = False
        if path_max is not None and math.isfinite(path_max[0]):
            maximum = _clean(path_max[0])
            maximum_open = any(edge.strict for edge in path_max[1])

        if path_min is None or not math.isfinite(path_min[0]):
            minimum = 0.0
            minimum_open = False
        else:
            minimum = _clean(-path_min[0])
            minimum_open = any(edge.strict for edge in path_min[1])
            if minimum < 0:
                # Negative cycles are handled above; a tiny negative value
                # here is floating point noise.
                minimum = 0.0

        status = _classify(minimum, maximum, relevant, start, end, trace_limit)
        return PrecipitationTotal(
            _convert_from_inches(minimum, units),
            None if maximum is None else _convert_from_inches(maximum, units),
            status,
            units,
            records=relevant,
            trace_limit=trace_limit,
            minimum_open=minimum_open,
            maximum_open=maximum_open,
            start=start,
            end=end,
        )

    interval = total
    query = total
    query_interval = total
    for_interval = total
    accumulated = total
    between = total
    amount = total
    estimate = total
    range = total
    interval_total = total
    get = total
    compute = total
    precipitation = total
    total_precipitation = total

    def _find_conflict(self, nodes, edges):
        cycle = _find_negative_cycle(len(nodes), edges)
        message = "hourly precipitation would be negative"
        if cycle is None:
            cycle = _find_negative_cycle(
                len(nodes), edges, strict_penalty=1e-9, tolerance=1e-10
            )
            message = "strict precipitation bounds cannot be satisfied"
        if cycle is None:
            return None
        return PrecipitationConflict(
            _records_from_cycle(cycle),
            message=message,
        )

    def last(self, hours, end=None, units=None):
        """Return bounds for the ``hours``-hour interval ending at ``end``."""
        end_time = self.latest if end is None else _coerce_time(end, self.anchor)
        if end_time is None:
            raise ValueError("cannot query an empty store without an explicit end")
        start_time = end_time - _datetime.timedelta(hours=float(hours))
        return self.total(start_time, end_time, units=units)


def _classify(minimum, maximum, records, start, end, trace_limit):
    if not records:
        return UNKNOWN
    if maximum is None:
        return UNKNOWN_UPPER
    if minimum == 0 and maximum == 0:
        return ZERO

    covering = [r for r in records if r.covers(start, end)]
    has_trace = any(r.kind == "trace" for r in covering)
    if has_trace and minimum <= 0 and maximum <= trace_limit + 1e-12:
        return TRACE
    if maximum == minimum:
        return EXACT
    return RANGE


def _find_negative_cycle(node_count, edges, strict_penalty=0.0, tolerance=1e-12):
    """Return a list of edges forming a negative cycle, or ``None``.

    When ``strict_penalty`` is positive, strict edges receive a tiny extra
    negative weight.  This turns a zero-length cycle containing a strict
    inequality into an ordinary negative cycle, so an impossible "trace but
    also zero" pair is reported instead of quietly accepted.
    """
    if node_count == 0:
        return None
    distance = [0.0] * node_count
    predecessor = [None] * node_count
    last_changed = None

    for _ in range(node_count + 1):
        changed = None
        for edge in edges:
            weight = edge.weight - (strict_penalty if edge.strict else 0.0)
            candidate = distance[edge.u] + weight
            if candidate < distance[edge.v] - tolerance:
                distance[edge.v] = candidate
                predecessor[edge.v] = edge
                changed = edge.v
                last_changed = edge.v
        if changed is None:
            return None

    if last_changed is None:
        return None

    node = last_changed
    for _ in range(node_count):
        edge = predecessor[node]
        if edge is None:
            return None
        node = edge.u

    cycle = []
    seen = {}
    while node not in seen:
        seen[node] = len(cycle)
        edge = predecessor[node]
        if edge is None:
            return None
        cycle.append(edge)
        node = edge.u
    cycle = cycle[seen[node]:]
    cycle.reverse()
    return cycle


def _records_from_cycle(cycle):
    records = []
    for edge in cycle:
        if edge.record is not None and all(edge.record is not r for r in records):
            records.append(edge.record)
    return tuple(records)


def _shortest_path(nodes, edges, source, target):
    """Bellman-Ford shortest path; returns ``(distance, path_edges)``."""
    if source == target:
        return 0.0, []
    distance = [math.inf] * len(nodes)
    predecessor = [None] * len(nodes)
    distance[source] = 0.0
    node_count = len(nodes)

    for _ in range(node_count - 1):
        changed = False
        for edge in edges:
            if not math.isfinite(distance[edge.u]):
                continue
            candidate = distance[edge.u] + edge.weight
            if candidate < distance[edge.v] - 1e-12:
                distance[edge.v] = candidate
                predecessor[edge.v] = edge
                changed = True
        if not changed:
            break

    if not math.isfinite(distance[target]):
        return None

    path = []
    node = target
    guard = 0
    while node != source and guard <= node_count:
        edge = predecessor[node]
        if edge is None:
            return None
        path.append(edge)
        node = edge.u
        guard += 1
    if node != source:
        return None
    path.reverse()
    return distance[target], path


def interval_total(
    records, start, end=None, units=None, anchor=None, trace_limit=None
):
    """Build a store from ``records`` and return the total for an interval.

    ``records`` may contain :class:`PrecipitationRecord` objects, METAR
    reports, or ``(start, end, amount[, source])`` tuples.
    """
    kwargs = {} if trace_limit is None else {"trace_limit": trace_limit}
    return PrecipitationStore(records, **kwargs).total(
        start, end, units=units, anchor=anchor
    )


def accumulated_precipitation(
    records, start, end=None, units=None, anchor=None, trace_limit=None
):
    """Alias for :func:`interval_total` with a more explicit name."""
    return interval_total(
        records, start, end, units=units, anchor=anchor, trace_limit=trace_limit
    )


def bounds(records, start, end=None, units=None, anchor=None, trace_limit=None):
    """Return ``(minimum, maximum)`` for ``records`` over an interval."""
    return interval_total(
        records, start, end, units=units, anchor=anchor, trace_limit=trace_limit
    ).value()


# Friendly aliases.  These names are all the same implementation; the aliases
# make the module convenient for callers who think of the container as a rain
# gauge rather than a generic store.
PrecipRecord = PrecipitationRecord
PrecipitationObservation = PrecipitationRecord
Observation = PrecipitationRecord
PrecipObservation = PrecipitationRecord

PrecipitationRange = PrecipitationTotal
PrecipitationEstimate = PrecipitationTotal
PrecipitationInterval = PrecipitationTotal
IntervalTotal = PrecipitationTotal
PrecipTotal = PrecipitationTotal
PrecipInterval = PrecipitationTotal
Accumulation = PrecipitationTotal

Conflict = PrecipitationConflict

PrecipStore = PrecipitationStore
RainGauge = PrecipitationStore
RainGaugeStore = PrecipitationStore
Gauge = PrecipitationStore
PrecipitationGauge = PrecipitationStore
PrecipGauge = PrecipitationStore
PrecipitationAccumulator = PrecipitationStore
PrecipAccumulator = PrecipitationStore
Accumulator = PrecipitationStore
RainGaugeAccumulator = PrecipitationStore
RainGaugeObservation = PrecipitationRecord

precipitation_interval = interval_total
precip_interval = interval_total
precipitation_between = interval_total
precip_between = interval_total
precipitation_bounds = bounds
precip_bounds = bounds
rain_total = interval_total
rainfall_total = interval_total
estimate_precipitation = interval_total
