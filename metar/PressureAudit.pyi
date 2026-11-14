from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional, Tuple

class AuditError(ValueError): ...

class Observation:
    id: str
    station: str
    time: datetime
    pressure: float
    generation: Optional[str]
    source: str
    raw: Optional[str]
    pressure_kind: str
    era: Optional[DeviceEra]
    def __init__(
        self,
        id: str,
        station: str,
        time: datetime,
        pressure: float,
        generation: Optional[str] = ...,
        source: str = ...,
        raw: Optional[str] = ...,
        pressure_kind: str = ...,
        era: Optional[DeviceEra] = ...,
    ) -> None: ...

class DeviceEra:
    station: str
    generation: str
    start: datetime
    end: Optional[datetime]
    def __init__(
        self,
        station: str,
        generation: str,
        start: datetime,
        end: Optional[datetime],
    ) -> None: ...
    @property
    def key(self) -> Tuple[str, str]: ...
    def active_at(self, moment: datetime) -> bool: ...

class PairInterval:
    station_a: str
    station_b: str
    low: float
    high: float
    time: Optional[datetime]
    obs_a: Optional[str]
    obs_b: Optional[str]
    id: str
    tolerance: timedelta
    def __init__(
        self,
        station_a: str,
        station_b: str,
        low: float,
        high: float,
        time: Optional[datetime] = ...,
        obs_a: Optional[str] = ...,
        obs_b: Optional[str] = ...,
        id: str = ...,
        tolerance: timedelta = ...,
    ) -> None: ...

class Constraint:
    id: str
    var_a: Tuple[str, str]
    var_b: Tuple[str, str]
    low: float
    high: float
    obs_a: str
    obs_b: str
    pair: str
    reported_diff: float
    source_interval: Tuple[float, float]
    kind: str
    @property
    def observations(self) -> Tuple[str, ...]: ...

class Conflict:
    constraints: Tuple[Constraint, ...]
    observations: Tuple[str, ...]
    stations: Tuple[str, ...]
    amount: float
    message: str
    cycle: Tuple[Tuple[str, str], ...]

class CorrectionRange:
    var: Tuple[str, str]
    low: Optional[float]
    high: Optional[float]
    center: Optional[float]
    width: Optional[float]
    status: str
    relative_low: Optional[float]
    relative_high: Optional[float]
    relative_to: Optional[Tuple[str, str]]
    low_constraint: Optional[str]
    high_constraint: Optional[str]
    low_observations: Tuple[str, ...]
    high_observations: Tuple[str, ...]
    pair_constraints: Tuple[str, ...]

class Component:
    index: int
    variables: Tuple[Tuple[str, str], ...]
    stations: Tuple[str, ...]
    anchored: bool
    calibrated_stations: Tuple[str, ...]
    gauge: Optional[Tuple[str, str]]
    solvable: bool
    conflict: Optional[Conflict]
    ranges: Dict[Tuple[str, str], CorrectionRange]

class AuditResult:
    observations: Dict[str, Observation]
    eras: Dict[Tuple[str, str], DeviceEra]
    constraints: Tuple[Constraint, ...]
    components: Tuple[Component, ...]
    unassigned: Tuple[Observation, ...]
    issues: Tuple[str, ...]
    calibrated: Tuple[str, ...]
    def ranges(self) -> Dict[Tuple[str, str], CorrectionRange]: ...
    def conflicts(self) -> Tuple[Conflict, ...]: ...
    def observations_for(
        self, station: str, generation: str
    ) -> Tuple[Observation, ...]: ...
    def to_dict(self) -> dict: ...

def decode_pressure(code: str, month: Optional[int] = ..., year: Optional[int] = ...) -> float: ...
def audit_pressures(
    observations: Iterable[dict],
    devices: Iterable[dict],
    pairs: Iterable[dict],
    *,
    calibrated: Iterable = ...,
) -> AuditResult: ...
def audit_from_dict(document: dict) -> AuditResult: ...
def format_report(result: AuditResult) -> str: ...
