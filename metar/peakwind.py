"""Build a chronological table of peak-wind events from consecutive METARs.

A ``PK WND`` remark reports the strongest gust seen since the previous report.
It carries a direction, a speed and an occurrence time, but that time is not
always fully dated:

* ``PK WND 29028/1817`` gives a full ``HHMM`` time, so the issuing report's
  own date stamps it unambiguously.
* ``PK WND 29028/17`` gives minutes only. The hour is not reported, so the
  gust could sit in the issuing report's hour or in an earlier hour.

For a minute-only remark this module keeps *every* hour that is consistent
with three pieces of information instead of guessing a single one:

1. the station clock -- the issuing report's observation time, which the gust
   cannot follow;
2. the previous available observation -- a peak repeated by a later report is
   the same physical gust and shares its occurrence time;
3. a maximum look-back window (one hour by default, the METAR convention).

The same gust mentioned again in a later report is linked into one event. Two
reports with identical wind speed but no shared possible occurrence time stay
separate events, because equal speed does not make them the same gust.

Feeding a late, older report back into the sequence can remove the remaining
hour alternatives; :func:`backfill` reports which events become unique
because of it.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

from metar import Metar

# A PK WND remark describes the peak within the hour ending at the report.
# Stations that retain peaks longer can pass a larger window explicitly.
DEFAULT_MAX_LOOKBACK = timedelta(hours=1)


@dataclass(frozen=True)
class PeakWindCandidate:
    """One ``PK WND`` remark and every occurrence time it could describe.

    Attributes
    ----------
    station_id:
        ICAO station identifier of the issuing report.
    direction:
        Peak wind direction in degrees.
    speed:
        Peak wind speed in knots.
    minute:
        Reported minute of the occurrence (0-59).
    hour_reported:
        Whether the remark carried an explicit hour. When ``False`` the hour
        is inferred and ``times`` may hold more than one alternative.
    times:
        Every occurrence datetime consistent with the report, ascending. A
        single element means the attribution is already unique.
    source_time:
        Observation time of the report carrying the remark.
    source_index:
        Position of that report in the time-sorted input sequence.
    """

    station_id: Optional[str]
    direction: float
    speed: float
    minute: int
    hour_reported: bool
    times: Tuple[datetime, ...]
    source_time: datetime
    source_index: int

    @property
    def time(self) -> datetime:
        """The earliest possible occurrence time."""
        return self.times[0]

    @property
    def is_unique(self) -> bool:
        """Whether exactly one occurrence time is possible."""
        return len(self.times) == 1


@dataclass
class PeakWindEvent:
    """A physical peak-wind gust, possibly mentioned by several reports.

    Attributes
    ----------
    station_id:
        ICAO station identifier.
    direction:
        Peak wind direction in degrees.
    speed:
        Peak wind speed in knots.
    occurrences:
        Distinct possible occurrence datetimes, ascending. One element means
        the event's attribution is resolved.
    candidates:
        The linked remarks, in report order.
    first_reported:
        Observation time of the first report mentioning the gust.
    last_reported:
        Observation time of the most recent report mentioning it.
    """

    station_id: Optional[str]
    direction: float
    speed: float
    occurrences: Tuple[datetime, ...]
    candidates: List[PeakWindCandidate] = field(default_factory=list)
    first_reported: Optional[datetime] = None
    last_reported: Optional[datetime] = None

    @property
    def time(self) -> datetime:
        """The earliest possible occurrence time."""
        return self.occurrences[0]

    @property
    def is_resolved(self) -> bool:
        """Whether the occurrence time is attributed uniquely."""
        return len(self.occurrences) == 1

    def row(self) -> Tuple:
        """A plain tuple suitable for tabular output."""
        return (
            self.time,
            self.station_id,
            self.direction,
            self.speed,
            self.is_resolved,
            self.occurrences,
            len(self.candidates),
        )


@dataclass
class BackfillResult:
    """Effect of inserting one or more late, older reports.

    Attributes
    ----------
    before:
        Events derived from the reports that were already available.
    after:
        Events derived from the full sequence, sorted by occurrence time.
    added_reports:
        The late reports that were inserted.
    newly_resolved:
        Events that were ambiguous before and have a unique occurrence now.
    new_events:
        Events introduced by the late reports themselves.
    still_ambiguous:
        Events that keep more than one possible occurrence time.
    """

    before: Tuple[PeakWindEvent, ...]
    after: Tuple[PeakWindEvent, ...]
    added_reports: Tuple[Metar.Metar, ...]
    newly_resolved: Tuple[PeakWindEvent, ...]
    new_events: Tuple[PeakWindEvent, ...]
    still_ambiguous: Tuple[PeakWindEvent, ...]


def _candidate_times(
    report: Metar.Metar,
    max_lookback: timedelta,
    previous_time: Optional[datetime],
) -> Tuple[datetime, ...]:
    """Return every occurrence time a report's peak could describe.

    The occurrence cannot follow the station clock (the report's own
    observation time), cannot predate the previous available observation --
    the peak covers the period since that report -- and cannot be older than
    the maximum look-back window.
    """
    peak_time = report.peak_wind_time
    minute = peak_time.minute
    if report.peak_wind_hour_reported:
        # A full HHMM time is fully dated by the issuing report's clock.
        return (peak_time,)

    earliest = report.time - max_lookback
    if previous_time is not None:
        earliest = max(earliest, previous_time)

    # Minutes only: walk back from the report's own hour, keeping every
    # occurrence inside the window and no later than the observation itself.
    candidate = report.time.replace(minute=minute, second=0, microsecond=0)
    while candidate > report.time:
        candidate -= timedelta(hours=1)
    nearest = candidate
    times = []
    while candidate >= earliest:
        times.append(candidate)
        candidate -= timedelta(hours=1)
    if not times:
        # A window shorter than the minute offset cannot erase the remark:
        # the occurrence at or just before the report stays the best fit.
        times = [nearest]
    return tuple(reversed(times))


def _sorted_reports(reports: Iterable[Metar.Metar]) -> List[Metar.Metar]:
    return sorted(reports, key=lambda report: report.time)


def build_candidates(
    reports: Sequence[Metar.Metar],
    max_lookback: timedelta = DEFAULT_MAX_LOOKBACK,
    station_id: Optional[str] = None,
) -> List[PeakWindCandidate]:
    """Extract one candidate per ``PK WND`` remark in the reports.

    Reports are ordered by observation time. ``max_lookback`` bounds how far
    before its issuing report a minute-only peak may be placed. The previous
    report for the same station supplies a second lower bound, since a peak
    cannot predate the previous available observation. When ``station_id`` is
    given, reports for other stations are ignored.
    """
    if max_lookback <= timedelta(0):
        raise ValueError("max_lookback must be a positive duration")
    candidates = []
    previous_by_station = {}
    for index, report in enumerate(_sorted_reports(reports)):
        if station_id is not None and report.station_id != station_id:
            continue
        previous_time = previous_by_station.get(report.station_id)
        if report.wind_speed_peak is not None:
            candidates.append(
                PeakWindCandidate(
                    station_id=report.station_id,
                    direction=report.wind_dir_peak.value(),
                    speed=report.wind_speed_peak.value(),
                    minute=report.peak_wind_time.minute,
                    hour_reported=bool(report.peak_wind_hour_reported),
                    times=_candidate_times(
                        report, max_lookback, previous_time
                    ),
                    source_time=report.time,
                    source_index=index,
                )
            )
        previous_by_station[report.station_id] = report.time
    return candidates


def _link_candidates(
    candidates: Sequence[PeakWindCandidate],
) -> List[PeakWindEvent]:
    """Link repeated mentions of the same gust into events."""
    events: List[PeakWindEvent] = []
    for candidate in candidates:
        possible = set(candidate.times)
        best = None
        best_overlap = 0
        for event in events:
            if event.station_id != candidate.station_id:
                continue
            if event.direction != candidate.direction:
                continue
            if event.speed != candidate.speed:
                continue
            overlap = len(possible.intersection(event.occurrences))
            if overlap == 0:
                continue
            # Same gust: share an occurrence time. On a tie, prefer the gust
            # mentioned most recently, since consecutive reports repeat the
            # same peak rather than an older one.
            if overlap > best_overlap or (
                overlap == best_overlap
                and best is not None
                and event.last_reported > best.last_reported
            ):
                best = event
                best_overlap = overlap
        if best is None:
            events.append(
                PeakWindEvent(
                    station_id=candidate.station_id,
                    direction=candidate.direction,
                    speed=candidate.speed,
                    occurrences=tuple(sorted(possible)),
                    candidates=[candidate],
                    first_reported=candidate.source_time,
                    last_reported=candidate.source_time,
                )
            )
        else:
            best.candidates.append(candidate)
            # Both remarks describe one physical gust, so its time must be
            # consistent with every report that mentions it: intersect.
            best.occurrences = tuple(
                sorted(set(best.occurrences).intersection(possible))
            )
            best.last_reported = candidate.source_time
    return events


def peak_wind_events(
    reports: Sequence[Metar.Metar],
    max_lookback: timedelta = DEFAULT_MAX_LOOKBACK,
    station_id: Optional[str] = None,
) -> List[PeakWindEvent]:
    """Return peak-wind events sorted by occurrence time.

    Every report mentioning the same gust contributes to one event. Events
    whose hour could not be pinned down carry several possible occurrences;
    check ``PeakWindEvent.is_resolved`` or the length of ``occurrences``.
    """
    candidates = build_candidates(reports, max_lookback, station_id)
    events = _link_candidates(candidates)
    return sorted(
        events,
        key=lambda event: (
            event.time,
            event.station_id or "",
            event.speed,
            event.direction,
        ),
    )


def peak_wind_table(
    reports: Sequence[Metar.Metar],
    max_lookback: timedelta = DEFAULT_MAX_LOOKBACK,
    station_id: Optional[str] = None,
) -> List[Tuple]:
    """Return the events as plain rows, ordered by occurrence time.

    Columns are ``(time, station_id, direction, speed, is_resolved,
    occurrences, report_count)``.
    """
    return [
        event.row()
        for event in peak_wind_events(reports, max_lookback, station_id)
    ]


def _matches_prior(
    event: PeakWindEvent, prior: Sequence[PeakWindEvent]
) -> List[PeakWindEvent]:
    """Prior events for the same gust: same wind values and a shared time."""
    times = set(event.occurrences)
    return [
        old
        for old in prior
        if old.station_id == event.station_id
        and old.direction == event.direction
        and old.speed == event.speed
        and times.intersection(old.occurrences)
    ]


def backfill(
    previous_reports: Sequence[Metar.Metar],
    late_reports: Sequence[Metar.Metar],
    max_lookback: timedelta = DEFAULT_MAX_LOOKBACK,
    station_id: Optional[str] = None,
) -> BackfillResult:
    """Insert late, older reports and explain which attributions settle.

    The late reports are merged into the time order and the events are
    rebuilt. The result separates events the late data made unique from
    events it introduces itself and events that stay ambiguous.
    """
    before_events = peak_wind_events(
        previous_reports, max_lookback, station_id
    )
    combined = _sorted_reports(list(previous_reports) + list(late_reports))
    after_events = peak_wind_events(combined, max_lookback, station_id)

    newly_resolved = []
    new_events = []
    still_ambiguous = []
    for event in after_events:
        prior = _matches_prior(event, before_events)
        if not prior:
            new_events.append(event)
        elif event.is_resolved and any(not old.is_resolved for old in prior):
            newly_resolved.append(event)
        if not event.is_resolved:
            still_ambiguous.append(event)

    return BackfillResult(
        before=tuple(before_events),
        after=tuple(after_events),
        added_reports=tuple(_sorted_reports(late_reports)),
        newly_resolved=tuple(newly_resolved),
        new_events=tuple(new_events),
        still_ambiguous=tuple(still_ambiguous),
    )
