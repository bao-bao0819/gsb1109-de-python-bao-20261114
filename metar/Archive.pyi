from datetime import datetime
from typing import Iterator, Optional, Sequence, Tuple

from metar.Metar import Metar

STATUS_OBSERVED: str
STATUS_CONFLICT: str
STATUS_MISSING: str

class ArchiveError(Exception): ...

class Report:
    code: str
    metar: Metar
    station_id: str
    time: datetime
    received_at: datetime
    source: str
    revision: int
    correction: Optional[str]
    normalized_code: str
    fingerprint: tuple
    uid: str
    sequence: Optional[int]
    @property
    def raw(self) -> str: ...
    @property
    def observed_at(self) -> datetime: ...
    @property
    def is_correction(self) -> bool: ...
    @property
    def rank(self) -> int: ...
    @property
    def key(self) -> Tuple[str, datetime]: ...

class Resolution:
    station_id: str
    time: datetime
    as_of: Optional[datetime]
    status: str
    report: Optional[Report]
    candidates: Tuple[Report, ...]
    versions: tuple
    later_corrections: Tuple[Report, ...]
    successor: Optional["Resolution"]
    @property
    def observation(self) -> Optional[Report]: ...
    @property
    def code(self) -> Optional[str]: ...
    @property
    def ok(self) -> bool: ...
    @property
    def is_observed(self) -> bool: ...
    @property
    def conflict(self) -> bool: ...
    @property
    def is_conflict(self) -> bool: ...
    @property
    def missing(self) -> bool: ...
    @property
    def is_missing(self) -> bool: ...
    @property
    def variants(self) -> Tuple[Report, ...]: ...
    @property
    def superseded_by(self) -> Optional[Report]: ...
    @property
    def final(self) -> Optional[Report]: ...
    @property
    def replacement(self) -> Optional[Report]: ...

class Archive:
    month: Optional[int]
    year: Optional[int]
    def __init__(
        self, month: Optional[int] = ..., year: Optional[int] = ...
    ) -> None: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Report]: ...
    def add(
        self,
        code: str,
        received_at: datetime,
        *,
        source: Optional[str] = ...,
        sequence: Optional[int] = ...,
        month: Optional[int] = ...,
        year: Optional[int] = ...,
    ) -> Report: ...
    def ingest_text(
        self,
        text: str,
        received_at: datetime,
        *,
        source: Optional[str] = ...,
        sequence: Optional[int] = ...,
        month: Optional[int] = ...,
        year: Optional[int] = ...,
    ) -> list[Report]: ...
    def ingest_file(
        self,
        path,
        received_at: Optional[datetime] = ...,
        *,
        source: Optional[str] = ...,
        sequence: Optional[int] = ...,
        month: Optional[int] = ...,
        year: Optional[int] = ...,
    ) -> list[Report]: ...
    def reports(
        self,
        station: Optional[str] = ...,
        time: Optional[datetime] = ...,
        *,
        corrections: Optional[bool] = ...,
    ) -> Tuple[Report, ...]: ...
    def history(self, station: str, time: datetime) -> Tuple[Report, ...]: ...
    def lookup(
        self,
        station: str,
        time: datetime,
        as_of: Optional[datetime] = ...,
        *,
        cutoff: Optional[datetime] = ...,
    ) -> Resolution: ...
    def as_of(
        self,
        station: str,
        time: datetime,
        as_of: Optional[datetime] = ...,
        *,
        cutoff: Optional[datetime] = ...,
    ) -> Resolution: ...
    def visible_at(
        self, as_of: datetime, *, station: Optional[str] = ...
    ) -> Tuple[Resolution, ...]: ...
    def snapshot(
        self, as_of: datetime, *, station: Optional[str] = ...
    ) -> dict: ...
    def conflicts(self, as_of: Optional[datetime] = ...) -> Tuple[Resolution, ...]: ...
    def superseded_by(self, report: Report) -> Optional[Report]: ...
    def final_version(self, report: Report) -> Optional[Report]: ...
