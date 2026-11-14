"""
Checkpointed access to encoded polylines.

A full :func:`polyline.decode` always restarts from the first character.
For a long string that is read back repeatedly, a single scan can instead
persist a limited number of checkpoints.  Each checkpoint records the parser
state (character offset and accumulated coordinates) at a point boundary, so
any single point can be restored by reading at most ``max_read`` characters
from a storage backend that supports ranged reads.
"""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple, runtime_checkable

from .polyline import _trans

# The codec's chunk reader is intentionally reused so checkpoints decode
# exactly the same values as a full polyline.decode run.
# pylint: disable=protected-access


# pylint: disable=invalid-name,too-few-public-methods
@runtime_checkable
class RangeReadable(Protocol):
    """Storage that can return an inclusive-start, exclusive-end substring."""

    def read_range(self, start: int, end: int) -> str:
        """Return the characters in the half-open range ``[start, end)``."""


# pylint: disable=too-few-public-methods,invalid-name
class StringRange:
    """Adapt a plain string to the :class:`RangeReadable` interface."""

    def __init__(self, expression: str):
        self._expression = expression

    def read_range(self, start: int, end: int) -> str:
        """Slice the wrapped string over the half-open range ``[start, end)``."""
        return self._expression[start:end]


# pylint: disable=invalid-name
class OversizedPointError(ValueError):
    """
    Raised when one point's own encoding is longer than ``max_read``.

    No checkpoint placement can help: a point is the smallest unit the
    decoder can resume from, so reading it always costs ``required``
    characters in a single read.

    :ivar point_index: Zero-based index of the point that cannot be served.
    :ivar required: Characters that must be read to decode that one point.
    :ivar limit: The ``max_read`` budget supplied by the caller.
    """

    def __init__(self, point_index: int, required: int, limit: int):
        self.point_index = point_index
        self.required = required
        self.limit = limit
        super().__init__(
            f"point {point_index} needs a single read of {required} "
            f"characters, exceeding max_read={limit}; adding checkpoints "
            f"cannot split a point's own encoding"
        )


@dataclass(frozen=True)
class Checkpoint:
    """Parser state at the boundary immediately before ``point_index``."""

    point_index: int
    char_offset: int
    lat: int
    lng: int


@dataclass(frozen=True)
class Options:
    """Decoding parameters shared by every read made through an index."""

    precision: int
    geojson: bool
    max_read: Optional[int]
    factor: float = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor", float(10 ** self.precision))


# pylint: disable=invalid-name
class PolylineIndex:
    """
    Checkpoints built from one full scan, reusable over ranged storage.

    Instances are normally created with :meth:`build`; persist
    :attr:`checkpoints` and reattach them to a :class:`RangeReadable`
    backend with :meth:`from_source` when the original string is no longer
    held in memory.
    """

    def __init__(
        self,
        source: RangeReadable,
        checkpoints: List[Checkpoint],
        point_count: int,
        total_length: int,
        precision: int = 5,
        geojson: bool = False,
        max_read: Optional[int] = None,
    ):
        self._source = source
        self._checkpoints = list(checkpoints)
        self._point_starts = [cp.point_index for cp in self._checkpoints]
        self._point_count = point_count
        self._total_length = total_length
        self._options = Options(precision, geojson, max_read)

    @classmethod
    def build(
        cls,
        expression: str,
        max_read: int,
        precision: int = 5,
        geojson: bool = False,
    ) -> "PolylineIndex":
        """
        Scan ``expression`` once and place the minimum number of checkpoints.

        Checkpoints are placed greedily: each one covers as many points as
        possible while keeping every single-point read at or below
        ``max_read`` characters.  This farthest-reach strategy yields the
        minimum checkpoint count for the given read budget.

        :raises OversizedPointError: if a point's own two-value encoding is
            longer than ``max_read``.
        :raises ValueError: if ``max_read`` is not a positive integer.
        """
        if not isinstance(max_read, int) or max_read < 1:
            raise ValueError("max_read must be a positive integer")

        checkpoints = [Checkpoint(0, 0, 0, 0)]
        segment_offset = 0
        lat = 0
        lng = 0
        index = 0
        point_index = 0
        length = len(expression)

        while index < length:
            point_start = index
            lat_change, index = _trans(expression, index)
            lng_change, index = _trans(expression, index)

            point_length = index - point_start
            if point_length > max_read:
                raise OversizedPointError(point_index, point_length, max_read)

            if index - segment_offset > max_read:
                checkpoints.append(Checkpoint(point_index, point_start, lat, lng))
                segment_offset = point_start

            lat += lat_change
            lng += lng_change
            point_index += 1

        return cls(
            StringRange(expression),
            checkpoints,
            point_index,
            length,
            precision,
            geojson,
            max_read,
        )

    @classmethod
    def from_source(
        cls,
        source: RangeReadable,
        checkpoints: List[Checkpoint],
        point_count: int,
        total_length: int,
        precision: int = 5,
        geojson: bool = False,
        max_read: Optional[int] = None,
    ) -> "PolylineIndex":
        """Reattach persisted checkpoints to a ranged storage backend."""
        return cls(
            source,
            checkpoints,
            point_count,
            total_length,
            precision,
            geojson,
            max_read,
        )

    @property
    def checkpoints(self) -> Tuple[Checkpoint, ...]:
        """All checkpoints, ordered by their point index."""
        return tuple(self._checkpoints)

    @property
    def point_count(self) -> int:
        """Number of points encoded in the polyline."""
        return self._point_count

    @property
    def precision(self) -> int:
        """Coordinate precision used when formatting decoded values."""
        return self._options.precision

    @property
    def geojson(self) -> bool:
        """Whether coordinates are returned in (lon, lat) order."""
        return self._options.geojson

    @property
    def max_read(self) -> Optional[int]:
        """Read budget the checkpoints were built for, if recorded."""
        return self._options.max_read

    def to_dict(self) -> dict:
        """Serialize the checkpoints and the parameters needed to reuse them."""
        return {
            "checkpoints": [
                {
                    "point_index": cp.point_index,
                    "char_offset": cp.char_offset,
                    "lat": cp.lat,
                    "lng": cp.lng,
                }
                for cp in self._checkpoints
            ],
            "point_count": self._point_count,
            "total_length": self._total_length,
            "precision": self._options.precision,
            "geojson": self._options.geojson,
            "max_read": self._options.max_read,
        }

    @classmethod
    def from_dict(cls, source: RangeReadable, data: dict) -> "PolylineIndex":
        """Rebuild an index from :meth:`to_dict` output and a storage backend."""
        checkpoints = [Checkpoint(**item) for item in data["checkpoints"]]
        return cls(
            source,
            checkpoints,
            data["point_count"],
            data["total_length"],
            data.get("precision", 5),
            data.get("geojson", False),
            data.get("max_read"),
        )

    def _checkpoint_for(self, point_index: int) -> Checkpoint:
        if not 0 <= point_index < self._point_count:
            raise IndexError(
                f"point index {point_index} out of range "
                f"({self._point_count} points)"
            )
        position = bisect_right(self._point_starts, point_index) - 1
        return self._checkpoints[position]

    def _segment_end(self, point_index: int) -> int:
        position = bisect_left(self._point_starts, point_index)
        if position < len(self._checkpoints):
            return self._checkpoints[position].char_offset
        return self._total_length

    def _format(self, lat: int, lng: int) -> Tuple[float, float]:
        coordinate = (lat / self._options.factor, lng / self._options.factor)
        return coordinate[::-1] if self._options.geojson else coordinate

    def decode_point(self, point_index: int) -> Tuple[float, float]:
        """
        Decode a single point without reading the whole polyline.

        Only the checkpoint segment containing the point is fetched, and its
        length never exceeds the ``max_read`` given to :meth:`build`.
        """
        checkpoint = self._checkpoint_for(point_index)
        end = self._segment_end(point_index + 1)
        chunk = self._source.read_range(checkpoint.char_offset, end)

        index = 0
        lat = checkpoint.lat
        lng = checkpoint.lng
        for _ in range(point_index - checkpoint.point_index + 1):
            lat_change, index = _trans(chunk, index)
            lng_change, index = _trans(chunk, index)
            lat += lat_change
            lng += lng_change

        return self._format(lat, lng)

    def decode_slice(self, start: int = 0, stop: Optional[int] = None) -> List[Tuple[float, float]]:
        """
        Decode a contiguous range of points using slice semantics.

        Negative indices count from the end and out-of-range bounds are
        clamped, mirroring ``range(start, stop)``.  Checkpoint segments are
        fetched one at a time, so each ranged read stays within
        ``max_read``; points within a segment are simply read sequentially.
        """
        if stop is None:
            stop = self._point_count
        if start < 0:
            start = max(0, self._point_count + start)
        if stop < 0:
            stop = max(0, self._point_count + stop)
        stop = min(stop, self._point_count)
        if start >= stop:
            return []

        first_position = bisect_right(self._point_starts, start) - 1
        coordinates = []
        lat = self._checkpoints[first_position].lat
        lng = self._checkpoints[first_position].lng
        current = self._checkpoints[first_position].point_index

        position = first_position
        while current < stop:
            checkpoint = self._checkpoints[position]
            if position + 1 < len(self._checkpoints):
                segment_end = self._checkpoints[position + 1].char_offset
            else:
                segment_end = self._total_length
            chunk = self._source.read_range(checkpoint.char_offset, segment_end)

            index = 0
            segment_last = (
                self._checkpoints[position + 1].point_index
                if position + 1 < len(self._checkpoints)
                else self._point_count
            )
            while current < min(stop, segment_last):
                lat_change, index = _trans(chunk, index)
                lng_change, index = _trans(chunk, index)
                lat += lat_change
                lng += lng_change
                if current >= start:
                    coordinates.append(self._format(lat, lng))
                current += 1
            position += 1

        return coordinates
