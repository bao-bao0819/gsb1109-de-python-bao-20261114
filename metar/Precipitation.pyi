from datetime import date, datetime, timedelta
from typing import Iterable, Iterator, Literal, Optional, Sequence, Tuple, Union

from metar.Datatypes import precipitation

PrecipitationUnit = Literal["IN", "CM", "in", "cm"]
RecordKind = Literal[
    "exact", "trace", "at_least", "at_most", "unknown", "=", ">", "<"
]
TimeLike = Union[datetime, date, str, int, float]

EXACT: str
ZERO: str
TRACE: str
UNKNOWN: str
UNKNOWN_UPPER: str
RANGE: str
CONFLICT: str
DEFAULT_TRACE_LIMIT: float

class PrecipitationRecord:
    start: datetime
    end: datetime
    amount: float
    kind: str
    source: Optional[str]
    units: str
    attribution: Optional[str]
    strict: bool
    def __init__(
        self,
        start: TimeLike,
        end: TimeLike,
        amount: Union[float, str, precipitation] = ...,
        kind: RecordKind = ...,
        source: Optional[str] = ...,
        units: Optional[PrecipitationUnit] = ...,
        attribution: Optional[str] = ...,
        strict: Optional[bool] = ...,
    ) -> None: ...
    @classmethod
    def from_metar(cls, metar, attr: str, hours: Optional[float] = ...) -> "PrecipitationRecord": ...
    @property
    def duration_hours(self) -> float: ...
    @property
    def hours(self) -> float: ...
    @property
    def window(self) -> Tuple[datetime, datetime]: ...
    @property
    def group(self) -> Optional[str]: ...
    def value(self, units: Optional[PrecipitationUnit] = ...) -> float: ...
    def total(self, units: Optional[PrecipitationUnit] = ...) -> float: ...
    def covers(self, start: TimeLike, end: TimeLike) -> bool: ...
    def overlaps(self, start: TimeLike, end: TimeLike) -> bool: ...

class PrecipitationConflict:
    records: Tuple[PrecipitationRecord, ...]
    sources: Tuple[str, ...]
    message: str
    interval: Optional[Tuple[datetime, datetime]]
    def __init__(
        self,
        records: Iterable[PrecipitationRecord] = ...,
        message: Optional[str] = ...,
        interval: Optional[Tuple[datetime, datetime]] = ...,
    ) -> None: ...
    @property
    def groups(self) -> Tuple[str, ...]: ...
    @property
    def source_groups(self) -> Tuple[str, ...]: ...
    def __bool__(self) -> bool: ...
    def __iter__(self) -> Iterator[PrecipitationRecord]: ...
    def __len__(self) -> int: ...

class PrecipitationConflictError(Exception):
    conflict: PrecipitationConflict
    sources: Tuple[str, ...]
    records: Tuple[PrecipitationRecord, ...]
    def __init__(self, conflict: PrecipitationConflict) -> None: ...

ConflictError = PrecipitationConflictError

class PrecipitationTotal:
    minimum: Optional[float]
    maximum: Optional[float]
    status: str
    units: PrecipitationUnit
    conflicts: Tuple[PrecipitationConflict, ...]
    records: Tuple[PrecipitationRecord, ...]
    trace_limit: float
    minimum_open: bool
    maximum_open: bool
    start: Optional[datetime]
    end: Optional[datetime]
    def __init__(
        self,
        minimum: Optional[float],
        maximum: Optional[float],
        status: str,
        units: PrecipitationUnit = ...,
        conflicts: Iterable[PrecipitationConflict] = ...,
        records: Iterable[PrecipitationRecord] = ...,
        trace_limit: float = ...,
        minimum_open: bool = ...,
        maximum_open: bool = ...,
        start: Optional[datetime] = ...,
        end: Optional[datetime] = ...,
    ) -> None: ...
    @property
    def min(self) -> Optional[float]: ...
    @property
    def max(self) -> Optional[float]: ...
    @property
    def lower(self) -> Optional[float]: ...
    @property
    def upper(self) -> Optional[float]: ...
    @property
    def conflict(self) -> Optional[PrecipitationConflict]: ...
    @property
    def has_conflict(self) -> bool: ...
    @property
    def is_conflict(self) -> bool: ...
    @property
    def groups(self) -> Tuple[str, ...]: ...
    @property
    def source_groups(self) -> Tuple[str, ...]: ...
    @property
    def sources(self) -> Tuple[str, ...]: ...
    @property
    def conflict_sources(self) -> Tuple[str, ...]: ...
    @property
    def is_zero(self) -> bool: ...
    @property
    def is_trace(self) -> bool: ...
    @property
    def is_trace_only(self) -> bool: ...
    @property
    def is_unknown(self) -> bool: ...
    @property
    def has_data(self) -> bool: ...
    @property
    def has_unknown_maximum(self) -> bool: ...
    @property
    def upper_bound_unknown(self) -> bool: ...
    @property
    def unknown_upper(self) -> bool: ...
    @property
    def is_exact(self) -> bool: ...
    @property
    def is_upper_unbounded(self) -> bool: ...
    @property
    def has_upper_bound(self) -> bool: ...
    @property
    def has_finite_maximum(self) -> bool: ...
    @property
    def exact_zero(self) -> bool: ...
    @property
    def trace_only(self) -> bool: ...
    @property
    def bounds(self) -> Tuple[Optional[float], Optional[float]]: ...
    @property
    def lower_bound(self) -> Optional[float]: ...
    @property
    def upper_bound(self) -> Optional[float]: ...
    @property
    def minimum_possible(self) -> Optional[float]: ...
    @property
    def maximum_possible(self) -> Optional[float]: ...
    @property
    def window(self) -> Tuple[Optional[datetime], Optional[datetime]]: ...
    def value(
        self, units: Optional[PrecipitationUnit] = ...
    ) -> Tuple[Optional[float], Optional[float]]: ...
    def string(self, units: Optional[PrecipitationUnit] = ...) -> str: ...
    def as_dict(self, units: Optional[PrecipitationUnit] = ...) -> dict: ...
    def __iter__(self) -> Iterator[Optional[float]]: ...

class PrecipitationStore:
    records: list[PrecipitationRecord]
    trace_limit: float
    def __init__(
        self,
        records: Optional[Iterable] = ...,
        trace_limit: float = ...,
    ) -> None: ...
    @property
    def anchor(self) -> Optional[datetime]: ...
    @property
    def latest(self) -> Optional[datetime]: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[PrecipitationRecord]: ...
    def __getitem__(self, index: int) -> PrecipitationRecord: ...
    def add_record(self, record: PrecipitationRecord) -> PrecipitationRecord: ...
    def add_records(self, records: Iterable) -> list[PrecipitationRecord]: ...
    def add(
        self,
        start: Union[TimeLike, PrecipitationRecord],
        end: Optional[TimeLike] = ...,
        amount: Union[float, str, precipitation] = ...,
        kind: RecordKind = ...,
        source: Optional[str] = ...,
        units: Optional[PrecipitationUnit] = ...,
        attribution: Optional[str] = ...,
        strict: Optional[bool] = ...,
        duration: Optional[Union[float, timedelta]] = ...,
    ) -> PrecipitationRecord: ...
    record: ...
    add_observation: ...
    observe: ...
    add_precipitation: ...
    add_many: ...
    def add_metar(self, metar, source: Optional[str] = ...) -> list[PrecipitationRecord]: ...
    @classmethod
    def from_metars(
        cls, metars: Iterable, trace_limit: float = ...
    ) -> "PrecipitationStore": ...
    from_metar: ...
    def clear(self) -> None: ...
    def total(
        self,
        start: Union[TimeLike, Sequence[TimeLike]],
        end: Optional[TimeLike] = ...,
        units: Optional[PrecipitationUnit] = ...,
        anchor: Optional[datetime] = ...,
        trace_limit: Optional[float] = ...,
        raise_on_conflict: bool = ...,
    ) -> PrecipitationTotal: ...
    interval: ...
    query: ...
    query_interval: ...
    for_interval: ...
    accumulated: ...
    between: ...
    amount: ...
    estimate: ...
    range: ...
    interval_total: ...
    get: ...
    compute: ...
    precipitation: ...
    total_precipitation: ...
    def last(
        self,
        hours: float,
        end: Optional[TimeLike] = ...,
        units: Optional[PrecipitationUnit] = ...,
    ) -> PrecipitationTotal: ...

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

def interval_total(
    records: Iterable,
    start: Union[TimeLike, Sequence[TimeLike]],
    end: Optional[TimeLike] = ...,
    units: Optional[PrecipitationUnit] = ...,
    anchor: Optional[datetime] = ...,
    trace_limit: Optional[float] = ...,
) -> PrecipitationTotal: ...
def accumulated_precipitation(
    records: Iterable,
    start: Union[TimeLike, Sequence[TimeLike]],
    end: Optional[TimeLike] = ...,
    units: Optional[PrecipitationUnit] = ...,
    anchor: Optional[datetime] = ...,
    trace_limit: Optional[float] = ...,
) -> PrecipitationTotal: ...
def bounds(
    records: Iterable,
    start: Union[TimeLike, Sequence[TimeLike]],
    end: Optional[TimeLike] = ...,
    units: Optional[PrecipitationUnit] = ...,
    anchor: Optional[datetime] = ...,
    trace_limit: Optional[float] = ...,
) -> Tuple[Optional[float], Optional[float]]: ...
precipitation_interval = interval_total
precip_interval = interval_total
precipitation_between = interval_total
precip_between = interval_total
precipitation_bounds = bounds
precip_bounds = bounds
rain_total = interval_total
rainfall_total = interval_total
estimate_precipitation = interval_total
