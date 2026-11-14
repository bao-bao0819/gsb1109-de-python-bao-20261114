import enum
from typing import Literal, Optional, Tuple, Union

GreaterOrLess = Literal[">", "<"]
Value = Union[str, float]

TemperatureUnit = Literal["F", "C", "K", "f", "c", "k"]

class temperature:
    _units: TemperatureUnit
    _value: float

    def __init__(self, value: Value, units: TemperatureUnit = "C") -> None: ...
    def __str__(self) -> str: ...
    def value(self, units: Optional[TemperatureUnit] = None) -> float: ...
    def string(self, units: Optional[TemperatureUnit] = None) -> str: ...

PressureUnit = Literal["MB", "HPA", "IN", "mb", "hPa", "in"]

class pressure:
    _units: PressureUnit
    _value: float

    def __init__(self, value: Value, units: PressureUnit = "HPA") -> None: ...
    def __str__(self) -> str: ...
    def value(self, units: Optional[PressureUnit] = None) -> float: ...
    def string(self, units: Optional[PressureUnit] = None) -> str: ...

SpeedUnit = Literal["KT", "MPS", "KMH", "MPH", "kt", "mps", "kmh", "mph"]

class speed:
    _units: SpeedUnit
    _value: float
    _gtlt: GreaterOrLess

    def __init__(
        self,
        value: Value,
        units: Optional[SpeedUnit] = None,
        gtlt: Optional[GreaterOrLess] = None,
    ) -> None: ...
    def __str__(self) -> str: ...
    def value(self, units: Optional[SpeedUnit] = None) -> float: ...
    def string(self, units: Optional[SpeedUnit] = None) -> str: ...

DistanceUnit = Literal[
    "SM", "MI", "M", "KM", "FT", "IN", "sm", "mi", "m", "km", "ft", "in"
]

class distance:
    _units: DistanceUnit
    _value: float
    _gtlt: GreaterOrLess
    _num: Optional[int]
    _den: Optional[int]

    def __init__(
        self,
        value: Value,
        units: Optional[DistanceUnit] = None,
        gtlt: Optional[GreaterOrLess] = None,
    ) -> None: ...
    def __str__(self) -> str: ...
    def value(self, units: Optional[DistanceUnit] = None) -> float: ...
    def string(self, units: Optional[DistanceUnit] = None) -> str: ...

class RVRThresholdVerdict(enum.Enum):
    DEFINITELY_BELOW: RVRThresholdVerdict
    POSSIBLY_BELOW: RVRThresholdVerdict
    DEFINITELY_NOT_BELOW: RVRThresholdVerdict
    def __str__(self) -> str: ...

class RunwayVisualRange:
    name: str
    code: Optional[str]
    unit: DistanceUnit
    tendency: Optional[Literal["U", "D", "N"]]
    low: float
    high: float
    low_inclusive: bool
    high_inclusive: bool
    FLOOR: float
    CEILING: float

    def __init__(
        self,
        name: str,
        low: Value,
        high: Value,
        unit: Optional[DistanceUnit] = ...,
        code: Optional[str] = ...,
        tendency: Optional[Literal["U", "D", "N"]] = ...,
        varying: bool = ...,
    ) -> None: ...
    def value_below(
        self,
        threshold: Union[Value, distance],
        units: Optional[DistanceUnit] = ...,
    ) -> RVRThresholdVerdict: ...
    def bounds(self, units: Optional[DistanceUnit] = None) -> Tuple[float, float]: ...
    def __repr__(self) -> str: ...
    def __str__(self) -> str: ...
    def string(self, units: Optional[DistanceUnit] = None) -> str: ...

CompassDirection = Literal[
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]

class direction:
    _compass: Optional[CompassDirection]
    _degrees: float

    def __init__(
        self,
        d: Union[CompassDirection, Value],
    ): ...
    def __str__(self) -> str: ...
    def value(self) -> float: ...
    def string(self) -> str: ...
    def compass(self) -> CompassDirection: ...

PrecipitationUnit = Literal["IN", "CM", "in", "cm"]

class precipitation(object):
    _units: PrecipitationUnit
    _value: float
    _gtlt: GreaterOrLess
    _istrace: bool

    def __init__(
        self,
        value: Value,
        units: Optional[PrecipitationUnit] = None,
        gtlt: Optional[GreaterOrLess] = None,
    ) -> None: ...
    def __str__(self) -> str: ...
    def value(self, units: Optional[PrecipitationUnit] = None) -> float: ...
    def string(self, units: Optional[PrecipitationUnit] = None) -> str: ...
    def istrace(self) -> bool: ...

class position:
    latitude: Optional[float]
    longitude: Optional[float]

    def __init__(
        self, latitude: Optional[float] = None, longitude: Optional[float] = None
    ): ...
    def __str__(self) -> str: ...
    def getdirection(self, position2: "position") -> direction: ...
