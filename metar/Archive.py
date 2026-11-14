# Copyright (c) 2004,2018 Python-Metar Developers.
# Distributed under the terms of the BSD 2-Clause License.
# SPDX-License-Identifier: BSD-2-Clause
"""Reconstruct METAR observations from one or more report archives.

An archive is a collection of *occurrences*: the same observation minute may
appear more than once because it was retransmitted by another feed or because
the station issued a correction.  This module keeps those occurrences apart
while answering two questions that the plain :class:`metar.Metar.Metar` object
cannot answer on its own:

* Which occurrence was visible at a particular reception cutoff?
* Which later correction, if any, replaced the occurrence that was visible?

The selection rules are deliberately conservative:

* A correction (``COR``/``CORR`` or ``CCA`` ... ``CCG``) belongs to a later
  revision level than an uncorrected report.
* Identical reports are retransmissions, not revisions.  Their text may differ
  in whitespace or a trailing ``=``; only the decoded observation matters.
* Two reports at the same revision level with different decoded observations
  are a conflict unless the caller supplies a trustworthy ``sequence`` number
  for them.  Reception time decides what was *visible* at a cutoff; it is not
  treated as evidence of which same-level report was issued later.  Import
  order is never used as a tie-breaker.
* Re-importing an occurrence is idempotent.  An occurrence is identified by its
  source and normalized report text, so replaying a file cannot add a second
  copy that changes a later query.

All reception times are normalized to UTC.  Naive datetimes are interpreted as
UTC, which is the convention used by METAR observation times as well.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

from metar.Metar import Metar as _Metar
from metar.Metar import ParserError

__all__ = [
    "Archive",
    "ArchiveError",
    "Report",
    "Resolution",
    "STATUS_OBSERVED",
    "STATUS_CONFLICT",
    "STATUS_MISSING",
]


class ArchiveError(Exception):
    """Raised when an archive record cannot be decoded or addressed."""


STATUS_OBSERVED = "observed"
STATUS_CONFLICT = "conflict"
STATUS_MISSING = "missing"

_CC_RE = re.compile(r"^CC(?P<letter>[A-G])$")


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Return *value* as an aware UTC datetime (naive means UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_naive_utc(value: datetime) -> datetime:
    """Return *value* as a naive UTC datetime."""
    return _as_utc(value).replace(tzinfo=None)


def _minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _normalize_code(code: str) -> str:
    """Normalize report text for identity and equality checks."""
    return " ".join(code.strip().rstrip("=").split())


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 6)


def _value(obj, units: Optional[str] = None):
    """Return a dimensioned value in a canonical unit, or ``None``."""
    if obj is None:
        return None
    try:
        if units is None:
            return _round(obj.value())
        return _round(obj.value(units))
    except (AttributeError, TypeError, ValueError):
        return None


def _text(value) -> str:
    return "" if value is None else str(value)


def _correction_info(metar: _Metar) -> tuple[int, Optional[str]]:
    """Return ``(revision_level, correction_label)`` for a decoded report.

    ``COR``/``CORR`` are level 1.  ``CCA`` ... ``CCG`` are successive levels
    starting at 2.  An unadorned report is level 0.
    """
    labels = []
    if metar.correction:
        labels.append(str(metar.correction).upper())
    if metar.mod:
        labels.extend(str(metar.mod).upper().split())

    level = 0
    label = None
    for item in labels:
        if item in ("COR", "CORR"):
            if level < 1:
                level = 1
                label = "COR"
        else:
            match = _CC_RE.match(item)
            if match:
                candidate = ord(match.group("letter")) - ord("A") + 2
                if candidate > level:
                    level = candidate
                    label = item
    return level, label


def _fingerprint(metar: _Metar, key: tuple[str, datetime]) -> tuple:
    """Return a canonical representation of the decoded observation.

    Correction markers, source information and reception order are deliberately
    excluded.  Two reports with the same measurements but different provenance
    therefore have the same fingerprint, as do an original and its verbatim
    retransmission.
    """
    station_id, observed_at = key
    weather = tuple(tuple(_text(part) for part in item) for item in metar.weather)
    recent = tuple(tuple(_text(part) for part in item) for item in metar.recent)
    sky = tuple(
        (cover, _value(height, "FT"), _text(cloud))
        for cover, height, cloud in metar.sky
    )
    runway = tuple(
        (name, _value(low, "M"), _value(high, "M"), _text(unit))
        for name, low, high, unit in metar.runway
    )
    return (
        station_id,
        observed_at,
        _value(metar.wind_dir),
        _value(metar.wind_speed, "KT"),
        _value(metar.wind_gust, "KT"),
        _value(metar.wind_dir_from),
        _value(metar.wind_dir_to),
        _value(metar.vis, "M"),
        _value(metar.vis_dir),
        _value(metar.max_vis, "M"),
        _value(metar.max_vis_dir),
        _value(metar.temp, "C"),
        _value(metar.dewpt, "C"),
        _value(metar.press, "HPA"),
        _value(metar.press_sea_level, "HPA"),
        weather,
        recent,
        sky,
        runway,
        tuple(metar.runway_tendencies),
        tuple(metar.windshear),
        _value(metar.wind_speed_peak, "KT"),
        _value(metar.wind_dir_peak),
        _text(metar.peak_wind_time),
        _text(metar.wind_shift_time),
        _value(metar.max_temp_6hr, "C"),
        _value(metar.min_temp_6hr, "C"),
        _value(metar.max_temp_24hr, "C"),
        _value(metar.min_temp_24hr, "C"),
        _value(metar.precip_1hr, "IN"),
        _value(metar.precip_3hr, "IN"),
        _value(metar.precip_6hr, "IN"),
        _value(metar.precip_24hr, "IN"),
        _value(metar.snowdepth, "IN"),
        _value(metar.ice_accretion_1hr, "IN"),
        _value(metar.ice_accretion_3hr, "IN"),
        _value(metar.ice_accretion_6hr, "IN"),
        tuple(metar._trend_groups),
    )


def _stable_report_key(report: "Report") -> tuple:
    return (
        report.received_at,
        report.revision,
        report.source,
        report.normalized_code,
        report.uid,
    )


def _representative(reports: Iterable["Report"]) -> "Report":
    """Pick the earliest, then lexicographically smallest, occurrence."""
    return min(
        reports,
        key=lambda report: (
            report.received_at,
            report.source,
            report.normalized_code,
            report.uid,
        ),
    )


@dataclass
class _Version:
    """One distinct revision of an observation, in issue order."""

    revision: int
    fingerprint: tuple
    first_seen: datetime
    reports: list["Report"]
    sequence: Optional[int] = None
    ambiguous: bool = False


def _version_chain(reports: Iterable["Report"]) -> list[_Version]:
    """Collapse occurrences into distinct versions and order them.

    Revision level is the primary evidence of issue order.  Within one level,
    a caller-supplied ``sequence`` can order the versions; otherwise the
    versions are marked ``ambiguous`` because reception times describe when an
    archive obtained a file, not when the station issued the correction.
    """
    groups: dict[tuple[int, tuple], list[Report]] = {}
    for report in reports:
        groups.setdefault((report.revision, report.fingerprint), []).append(report)
    versions = []
    for (revision, fingerprint), group in groups.items():
        sequences = {report.sequence for report in group if report.sequence is not None}
        versions.append(
            _Version(
                revision=revision,
                fingerprint=fingerprint,
                first_seen=min(report.received_at for report in group),
                reports=sorted(group, key=_stable_report_key),
                sequence=sequences.pop() if len(sequences) == 1 else None,
            )
        )
    versions.sort(key=lambda item: item.revision)
    ordered = []
    for revision in sorted({version.revision for version in versions}):
        same_level = [version for version in versions if version.revision == revision]
        sequences = [version.sequence for version in same_level]
        if len(same_level) > 1 and all(
            sequence is not None for sequence in sequences
        ) and len(set(sequences)) == len(sequences):
            same_level.sort(
                key=lambda item: (item.sequence, repr(item.fingerprint))
            )
        else:
            if len(same_level) > 1:
                for version in same_level:
                    version.ambiguous = True
            same_level.sort(
                key=lambda item: (item.first_seen, repr(item.fingerprint))
            )
        ordered.extend(same_level)
    versions = ordered
    return versions


def _version_of(versions: Sequence[_Version], report: "Report") -> Optional[int]:
    for index, version in enumerate(versions):
        if (
            version.revision == report.revision
            and version.fingerprint == report.fingerprint
        ):
            return index
    return None


def _next_correction(
    versions: Sequence[_Version], report: "Report"
) -> Optional["Report"]:
    """Return the first unambiguous later correction after *report*."""
    index = _version_of(versions, report)
    if index is None:
        return None
    for version in versions[index + 1 :]:
        if version.ambiguous:
            return None
        if version.revision == 0:
            continue
        return _representative(version.reports)
    return None


def _final_replacement(
    versions: Sequence[_Version], report: "Report"
) -> Optional["Report"]:
    """Return the last unambiguous version, if it is a later correction."""
    if not versions:
        return None
    final = versions[-1]
    if final.ambiguous or final.revision == 0:
        return None
    index = _version_of(versions, report)
    if index == len(versions) - 1:
        return None
    replacement = _representative(final.reports)
    if (
        replacement.revision == report.revision
        and replacement.fingerprint == report.fingerprint
    ):
        return None
    return replacement


@dataclass(frozen=True, eq=False)
class Report:
    """One occurrence of a METAR report in an archive.

    A report is immutable.  ``revision`` is 0 for an uncorrected report, 1 for
    ``COR``/``CORR`` and 2..7 for ``CCA``..``CCG``.
    """

    code: str
    metar: _Metar
    station_id: str
    time: datetime
    received_at: datetime
    source: str
    revision: int
    correction: Optional[str]
    normalized_code: str
    fingerprint: tuple
    uid: str
    sequence: Optional[int] = None

    @property
    def raw(self) -> str:
        """The report text as supplied by the caller."""
        return self.code

    @property
    def observed_at(self) -> datetime:
        """The observation minute (naive UTC)."""
        return self.time

    @property
    def is_correction(self) -> bool:
        return self.revision > 0

    @property
    def rank(self) -> int:
        return self.revision

    @property
    def key(self) -> tuple[str, datetime]:
        return (self.station_id, self.time)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, eq=False)
class Resolution:
    """The observation visible for one station/minute at a reception cutoff.

    ``status`` is ``"observed"`` when a single observation can be selected,
    ``"conflict"`` when same-level revisions cannot be ordered, and
    ``"missing"`` when nothing had been received by the cutoff.

    ``successor`` is the resolution of the same observation against the whole
    archive (no cutoff), which lets a caller ask what eventually replaced the
    visible report.  ``superseded_by`` points at the immediate later correction,
    while ``final`` points at the last unambiguous version.
    """

    station_id: str
    time: datetime
    as_of: Optional[datetime]
    status: str
    report: Optional[Report]
    candidates: tuple[Report, ...]
    versions: tuple["_Version", ...] = ()
    later_corrections: tuple[Report, ...] = ()
    successor: Optional["Resolution"] = None

    @property
    def observation(self) -> Optional[Report]:
        return self.report

    @property
    def code(self) -> Optional[str]:
        return self.report.code if self.report is not None else None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OBSERVED

    @property
    def is_observed(self) -> bool:
        return self.status == STATUS_OBSERVED

    @property
    def conflict(self) -> bool:
        return self.status == STATUS_CONFLICT

    @property
    def is_conflict(self) -> bool:
        return self.status == STATUS_CONFLICT

    @property
    def missing(self) -> bool:
        return self.status == STATUS_MISSING

    @property
    def is_missing(self) -> bool:
        return self.status == STATUS_MISSING

    @property
    def variants(self) -> tuple[Report, ...]:
        """One deterministic representative per distinct measurement.

        Variants are ordered by the first reception of each distinct value, so a
        late retransmission does not move an old variant to the front.
        """
        groups: dict[tuple, list[Report]] = {}
        for report in self.candidates:
            groups.setdefault(report.fingerprint, []).append(report)
        return tuple(
            _representative(group)
            for group in sorted(
                groups.values(),
                key=lambda group: (
                    min(report.received_at for report in group),
                    _representative(group).uid,
                ),
            )
        )

    @property
    def superseded_by(self) -> Optional[Report]:
        """The immediate later correction that replaced the visible report."""
        if self.report is None or self.successor is None:
            return None
        return _next_correction(self.successor.versions, self.report)

    @property
    def final(self) -> Optional[Report]:
        """The last unambiguous correction of this observation, if any."""
        if self.report is None:
            return None
        versions = (
            self.successor.versions if self.successor is not None else self.versions
        )
        return _final_replacement(versions, self.report)

    @property
    def replacement(self) -> Optional[Report]:
        return self.superseded_by


class Archive:
    """A collection of METAR occurrences that can be queried by reception time."""

    def __init__(self, month: Optional[int] = None, year: Optional[int] = None):
        self.month = month
        self.year = year
        self._reports: dict[str, Report] = {}
        self._groups: dict[tuple[str, datetime], set[str]] = {}

    def __len__(self) -> int:
        return len(self._reports)

    def __iter__(self) -> Iterator[Report]:
        return iter(self.reports())

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    def add(
        self,
        code: str,
        received_at: datetime,
        *,
        source: Optional[str] = None,
        sequence: Optional[int] = None,
        month: Optional[int] = None,
        year: Optional[int] = None,
    ) -> Report:
        """Add one report occurrence and return its :class:`Report`.

        ``received_at`` is when the archive obtained the report, not the
        observation time.  Re-adding the same source and report text returns
        the existing occurrence; it never creates a second revision.
        """
        if not isinstance(code, str):
            raise ArchiveError("METAR code must be a string")
        source = "" if source is None else str(source)
        if sequence is not None:
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ArchiveError("sequence must be an integer")
            if sequence < 0:
                raise ArchiveError("sequence must not be negative")
        received = _as_utc(received_at)
        if received is None:
            raise ArchiveError("received_at is required")

        try:
            metar = self._parse(
                code,
                month=self.month if month is None else month,
                year=self.year if year is None else year,
                received=received,
            )
        except ParserError as exc:
            raise ArchiveError("cannot decode %r: %s" % (code, exc)) from exc
        if not metar.station_id:
            raise ArchiveError("report has no station identifier: %r" % (code,))
        if not metar.time:
            raise ArchiveError("report has no observation time: %r" % (code,))

        observed = _minute(metar.time)
        key = (metar.station_id, observed)
        normalized = _normalize_code(code)
        revision, correction = _correction_info(metar)
        fingerprint = _fingerprint(metar, key)
        uid = self._uid(source, key, normalized, sequence)

        existing = self._reports.get(uid)
        if existing is not None:
            return existing

        report = Report(
            code=code,
            metar=metar,
            station_id=metar.station_id,
            time=observed,
            received_at=received,
            source=source,
            revision=revision,
            correction=correction,
            normalized_code=normalized,
            fingerprint=fingerprint,
            uid=uid,
            sequence=sequence,
        )
        self._reports[uid] = report
        self._groups.setdefault(key, set()).add(uid)
        return report

    def ingest_text(
        self,
        text: str,
        received_at: datetime,
        *,
        source: Optional[str] = None,
        sequence: Optional[int] = None,
        month: Optional[int] = None,
        year: Optional[int] = None,
    ) -> list[Report]:
        """Add every report-looking line in *text* at one reception time.

        Blank lines, ``#``/``//`` comments, and lines that do not start with an
        uppercase character are skipped.  Date headers in cycle files begin with
        digits, so they fall into the same category as comments.  An uppercase
        line that cannot be decoded raises :class:`ArchiveError` with its line
        number rather than being dropped.
        """
        reports = []
        for line_number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("#")
                or stripped.startswith("//")
                or not stripped[0].isupper()
            ):
                continue
            try:
                report = self.add(
                    stripped,
                    received_at,
                    source=source,
                    sequence=sequence,
                    month=month,
                    year=year,
                )
            except ArchiveError as exc:
                raise ArchiveError(
                    "%s line %d: %s" % (source or "<string>", line_number, exc)
                ) from exc
            reports.append(report)
        return reports

    def ingest_file(
        self,
        path,
        received_at: Optional[datetime] = None,
        *,
        source: Optional[str] = None,
        sequence: Optional[int] = None,
        month: Optional[int] = None,
        year: Optional[int] = None,
    ) -> list[Report]:
        """Read reports from *path*.

        When ``received_at`` is omitted, the file's modification time (UTC) is
        used.  The resolved path is the default source identifier, so replaying
        the same file is idempotent.
        """
        path = Path(path)
        if received_at is None:
            received_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if source is None:
            source = str(path.resolve())
        return self.ingest_text(
        path.read_text(encoding="utf-8-sig"),
            received_at,
            source=source,
            sequence=sequence,
            month=month,
            year=year,
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def reports(
        self,
        station: Optional[str] = None,
        time: Optional[datetime] = None,
        *,
        corrections: Optional[bool] = None,
    ) -> tuple[Report, ...]:
        """Return occurrences in deterministic order."""
        station = station.upper() if station is not None else None
        when = _minute(_as_naive_utc(time)) if time is not None else None
        result = [
            report
            for report in self._reports.values()
            if (station is None or report.station_id == station)
            and (when is None or report.time == when)
            and (corrections is None or report.is_correction == corrections)
        ]
        return tuple(sorted(result, key=_stable_report_key))

    def history(
        self, station: str, time: datetime
    ) -> tuple[Report, ...]:
        """Return every occurrence for one station/minute, oldest first."""
        return self.reports(station, time)

    def lookup(
        self,
        station: str,
        time: datetime,
        as_of: Optional[datetime] = None,
        *,
        cutoff: Optional[datetime] = None,
    ) -> Resolution:
        """Return the observation visible at *as_of*.

        ``as_of`` (or its alias ``cutoff``) is a reception time.  If omitted,
        the whole archive is considered.
        """
        if cutoff is not None:
            if as_of is not None and _as_utc(cutoff) != _as_utc(as_of):
                raise ValueError("as_of and cutoff disagree")
            as_of = cutoff

        station = station.upper()
        when = _minute(_as_naive_utc(time))
        key = (station, when)
        if key not in self._groups:
            return Resolution(
                station_id=station,
                time=when,
                as_of=_as_utc(as_of),
                status=STATUS_MISSING,
                report=None,
                candidates=(),
            )
        return self._resolve(key, as_of, with_successor=True)

    at = lookup
    as_of = lookup

    def visible_at(
        self, as_of: datetime, *, station: Optional[str] = None
    ) -> tuple[Resolution, ...]:
        """Return every observation visible at *as_of*, for one station or all."""
        cutoff = _as_utc(as_of)
        station = station.upper() if station is not None else None
        results = []
        for key in sorted(self._groups, key=lambda item: (item[0], item[1])):
            if station is not None and key[0] != station:
                continue
            if not any(
                self._reports[uid].received_at <= cutoff for uid in self._groups[key]
            ):
                continue
            resolution = self._resolve(key, cutoff, with_successor=True)
            if resolution.status != STATUS_MISSING:
                results.append(resolution)
        return tuple(results)

    def snapshot(self, as_of: datetime, *, station: Optional[str] = None) -> dict:
        """Return ``{(station, time): Resolution}`` visible at *as_of*."""
        return {
            (item.station_id, item.time): item
            for item in self.visible_at(as_of, station=station)
        }

    def conflicts(self, as_of: Optional[datetime] = None) -> tuple[Resolution, ...]:
        """Return unresolved conflicts, optionally only those visible at *as_of*."""
        if as_of is not None:
            return tuple(item for item in self.visible_at(as_of) if item.conflict)
        results = []
        for key in sorted(self._groups, key=lambda item: (item[0], item[1])):
            resolution = self._resolve(key, None, with_successor=False)
            if resolution.conflict:
                results.append(resolution)
        return tuple(results)

    def superseded_by(self, report: Report) -> Optional[Report]:
        """Return the immediate later correction replacing *report*, or ``None``."""
        if not isinstance(report, Report):
            raise TypeError("superseded_by() expects a Report")
        if self._reports.get(report.uid) is not report:
            raise ArchiveError("report does not belong to this archive")
        group = [self._reports[uid] for uid in self._groups.get(report.key, ())]
        return _next_correction(_version_chain(group), report)

    def final_version(self, report: Report) -> Optional[Report]:
        """Return the last unambiguous replacement for *report*."""
        if not isinstance(report, Report):
            raise TypeError("final_version() expects a Report")
        if self._reports.get(report.uid) is not report:
            raise ArchiveError("report does not belong to this archive")
        group = [self._reports[uid] for uid in self._groups.get(report.key, ())]
        return _final_replacement(_version_chain(group), report)

    # ------------------------------------------------------------------
    # Internal resolution
    # ------------------------------------------------------------------
    def _parse(
        self,
        code: str,
        *,
        month: Optional[int],
        year: Optional[int],
        received: datetime,
    ) -> _Metar:
        """Decode *code*, inferring an unstated date from the receipt time.

        A METAR carries only the day of the month.  Anchoring that day to the
        moment the archive received the report keeps historical imports from
        being dated against today's clock.  When the day has not happened yet
        in the received month, the report belongs to the previous month; an
        impossible day (31 February) steps back once more.
        """
        if month is not None or year is not None:
            return _Metar(code, month=month, year=year)

        candidate_year, candidate_month = received.year, received.month
        last_error = None
        for _ in range(13):
            try:
                metar = _Metar(code, month=candidate_month, year=candidate_year)
            except ParserError as exc:
                last_error = exc
                if "day is out of range" not in str(exc):
                    raise
                candidate_year, candidate_month = _previous_month(
                    candidate_year, candidate_month
                )
                continue
            if metar.time is None or metar.time.date() <= received.date():
                return metar
            candidate_year, candidate_month = _previous_month(
                candidate_year, candidate_month
            )
        if last_error is not None:
            raise last_error
        return _Metar(code, month=month, year=year)
        try:
            return _Metar(code, month=month, year=year)
        except ParserError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise ArchiveError("cannot decode %r: %s" % (code, exc)) from exc

    @staticmethod
    def _uid(
        source: str,
        key: tuple[str, datetime],
        normalized_code: str,
        sequence: Optional[int] = None,
    ) -> str:
        sequence_text = "" if sequence is None else str(sequence)
        payload = "\x1f".join(
            (
                source,
                key[0],
                key[1].replace(tzinfo=None).isoformat(),
                normalized_code,
                sequence_text,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _resolve(
        self,
        key: tuple[str, datetime],
        as_of: Optional[datetime],
        *,
        with_successor: bool,
    ) -> Resolution:
        cutoff = _as_utc(as_of)
        all_reports = [self._reports[uid] for uid in self._groups.get(key, ())]
        versions = tuple(_version_chain(all_reports))
        visible = (
            all_reports
            if cutoff is None
            else [report for report in all_reports if report.received_at <= cutoff]
        )
        resolution = self._select(key, cutoff, visible, versions)
        if with_successor:
            final = self._select(key, None, all_reports, versions)
            resolution = Resolution(
                station_id=resolution.station_id,
                time=resolution.time,
                as_of=resolution.as_of,
                status=resolution.status,
                report=resolution.report,
                candidates=resolution.candidates,
                versions=versions,
                later_corrections=tuple(
                    report
                    for report in sorted(all_reports, key=_stable_report_key)
                    if report.is_correction
                    and cutoff is not None
                    and report.received_at > cutoff
                ),
                successor=final,
            )
        return resolution

    def _select(
        self,
        key: tuple[str, datetime],
        cutoff: Optional[datetime],
        visible: Sequence[Report],
        versions: tuple["_Version", ...] = (),
    ) -> Resolution:
        if not visible:
            return Resolution(
                station_id=key[0],
                time=key[1],
                as_of=cutoff,
                status=STATUS_MISSING,
                report=None,
                candidates=(),
                versions=versions,
            )

        visible_versions = _version_chain(visible)
        newest = visible_versions[-1]
        if newest.ambiguous:
            candidates = tuple(
                sorted(
                    (
                        report
                        for version in visible_versions
                        if version.revision == newest.revision
                        for report in version.reports
                    ),
                    key=_stable_report_key,
                )
            )
            return Resolution(
                station_id=key[0],
                time=key[1],
                as_of=cutoff,
                status=STATUS_CONFLICT,
                report=None,
                candidates=candidates,
                versions=versions,
            )
        candidates = tuple(sorted(newest.reports, key=_stable_report_key))
        return Resolution(
            station_id=key[0],
            time=key[1],
            as_of=cutoff,
            status=STATUS_OBSERVED,
            report=_representative(candidates),
            candidates=candidates,
            versions=versions,
        )
