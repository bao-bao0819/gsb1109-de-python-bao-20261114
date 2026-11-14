"""
A Python implementation of Google's Encoded Polyline Algorithm Format.
"""
import io
import itertools
import json
import math
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple, Union


def _pcitr(iterable):
    return zip(iterable, itertools.islice(iterable, 1, None))


def _py2_round(x):
    # The polyline algorithm uses Python 2's way of rounding
    return int(math.copysign(math.floor(math.fabs(x) + 0.5), x))


class DecodedPoint(tuple):
    """
    A decoded point together with the half-open character range it came from.

    The point itself is a ``(lat, lon)`` tuple, so values compare directly with
    the tuples returned by :func:`decode`.  ``start`` and ``end`` are absolute
    character offsets in the encoded string: the point was produced by
    ``expression[start:end]``.  ``end`` is exclusive.  For ``geojson=True`` the
    tuple is ``(lon, lat)``, exactly like :func:`decode`.

    ``point[2]`` and ``point[3]`` are provided as shortcuts for ``start`` and
    ``end``.
    """

    def __new__(cls, lat: float, lon: float, start: int, end: int) -> 'DecodedPoint':
        point = super().__new__(cls, (lat, lon))
        point.lat = lat
        point.lon = lon
        point.start = start
        point.end = end
        return point

    def __getitem__(self, index):
        if index == 2:
            return self.start
        if index == 3:
            return self.end
        if isinstance(index, slice):
            return self.as_tuple()[index]
        return super().__getitem__(index)

    def __repr__(self) -> str:
        return (f'DecodedPoint(lat={self.lat!r}, lon={self.lon!r}, '
                f'start={self.start!r}, end={self.end!r})')

    def __reduce__(self):
        return (self.__class__, (self.lat, self.lon, self.start, self.end))

    @property
    def lng(self) -> float:
        """Alias for the second coordinate component."""
        return self.lon

    @property
    def latitude(self) -> float:
        """Alias for the first coordinate component."""
        return self.lat

    @property
    def longitude(self) -> float:
        """Alias for the second coordinate component."""
        return self.lon

    @property
    def coordinates(self) -> Tuple[float, float]:
        """The ``(lat, lon)`` pair, in the same order as the point tuple."""
        return (self.lat, self.lon)

    @property
    def position(self) -> int:
        """Absolute offset of the first character of the point."""
        return self.start

    @property
    def offset(self) -> int:
        """Absolute offset of the first character of the point."""
        return self.start

    @property
    def end_offset(self) -> int:
        """Absolute offset just past the last character of the point."""
        return self.end

    @property
    def consumed(self) -> int:
        """Absolute offset just past the last character of the point."""
        return self.end

    @property
    def provenance(self) -> Tuple[int, int]:
        """The half-open character range ``(start, end)``."""
        return (self.start, self.end)

    @property
    def source(self) -> Tuple[int, int]:
        """The half-open character range ``(start, end)``."""
        return (self.start, self.end)

    @property
    def raw(self) -> str:
        """This point's encoded characters, when the source text is retained."""
        return self._raw if hasattr(self, '_raw') else ''

    @property
    def char_range(self) -> Tuple[int, int]:
        """The half-open character range ``(start, end)``."""
        return (self.start, self.end)

    @property
    def begin(self) -> int:
        """Absolute offset of the first character of the point."""
        return self.start

    @property
    def stop(self) -> int:
        """Absolute offset just past the last character of the point."""
        return self.end

    def as_tuple(self) -> Tuple[float, float, int, int]:
        """The point as ``(lat, lon, start, end)``."""
        return (self.lat, self.lon, self.start, self.end)

    def with_source(self, expression: str) -> 'DecodedPoint':
        """Return a copy carrying the point's raw encoded characters."""
        point = self.__class__(self.lat, self.lon, self.start, self.end)
        point._raw = expression[self.start:self.end]
        return point


class PolylineDecodeError(ValueError):
    """Base class for incremental decoding errors."""


class PolylineConflictError(PolylineDecodeError):
    """A re-sent packet disagrees with previously consumed characters."""

    def __init__(self, offset: int, expected: str, received: str, packet_offset: Optional[int] = None):
        self.offset = offset
        self.position = offset
        self.expected = expected
        self.received = received
        self.packet_offset = packet_offset
        message = f"conflicting character at offset {offset}: expected {expected!r}, received {received!r}"
        if packet_offset is not None:
            message += f" (packet offset {packet_offset})"
        super().__init__(message)


class PolylineGapError(PolylineDecodeError):
    """A packet starts past the next character the decoder needs."""

    def __init__(self, offset: int, expected: int):
        self.offset = offset
        self.position = offset
        self.expected = expected
        super().__init__(f"packet starts at offset {offset}, but the decoder needs offset {expected}")


class PolylineIncompleteError(PolylineDecodeError):
    """The input ended in the middle of a point."""

    def __init__(self, position: int, point_start: int, phase: str):
        self.position = position
        self.point_start = point_start
        self.phase = phase
        super().__init__(
            f"incomplete point starting at offset {point_start}: input ended at offset {position} "
            f"while reading the {phase} component"
        )


class PolylineStateError(PolylineDecodeError):
    """A saved decoder state is malformed."""


class DecoderState(dict):
    """
    A snapshot of an :class:`IncrementalDecoder`, usable as a plain dict.

    Keys are also available as attributes, so both ``state['cursor']`` and
    ``state.cursor`` work.  ``to_json``/``from_json`` round-trip it through
    JSON for storage.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name, value):
        self[name] = value

    @property
    def consumed(self) -> int:
        """Number of source characters consumed."""
        return self['cursor']

    @property
    def offset(self) -> int:
        """Number of source characters consumed."""
        return self['cursor']

    @property
    def position(self) -> int:
        """Number of source characters consumed."""
        return self['cursor']

    @property
    def pending(self) -> bool:
        """Whether the state ends part-way through a point."""
        return self['point_start'] < self['cursor']

    @property
    def complete(self) -> bool:
        """Whether the state ends on a point boundary."""
        return not self.pending

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dictionary copy."""
        return dict(self)

    def to_json(self) -> str:
        """Return the state as a JSON string."""
        return json.dumps(self, ensure_ascii=False)

    @classmethod
    def from_json(cls, value: str) -> 'DecoderState':
        """Parse a state from a JSON string."""
        state = json.loads(value)
        if not isinstance(state, dict):
            raise PolylineStateError('state JSON must be an object')
        return cls(state)


class DecodeResult(NamedTuple):
    """The points produced by one :func:`decode_chunk` call and the new state."""

    points: List['DecodedPoint']
    state: DecoderState

    @property
    def cursor(self) -> int:
        """Number of source characters consumed after this packet."""
        return self.state.cursor

    @property
    def consumed(self) -> int:
        """Number of source characters consumed after this packet."""
        return self.state.cursor

    @property
    def position(self) -> int:
        """Number of source characters consumed after this packet."""
        return self.state.cursor

    @property
    def pending(self) -> bool:
        """Whether the stream ends part-way through a point."""
        return self.state.pending

    @property
    def complete(self) -> bool:
        """Whether the stream ends on a point boundary."""
        return self.state.complete


class FeedResult(list):
    """
    The list of complete points produced by :meth:`IncrementalDecoder.feed`.

    It behaves as a normal list.  Extra attributes describe how the packet was
    reconciled with the data that had already been consumed.
    """

    def __init__(self, points: Iterable[DecodedPoint], position: int, packet_offset: int,
                 new_characters: int, duplicate_characters: int):
        super().__init__(points)
        self.position = position
        self.packet_offset = packet_offset
        self.new_characters = new_characters
        self.duplicate_characters = duplicate_characters

    @property
    def points(self) -> List[DecodedPoint]:
        """The complete points produced by the packet."""
        return list(self)

    @property
    def positions(self) -> List[Tuple[int, int]]:
        """Half-open source ranges of the complete points produced by the packet."""
        return [point.provenance for point in self]

    @property
    def ranges(self) -> List[Tuple[int, int]]:
        """Alias for :attr:`positions`."""
        return self.positions

    @property
    def consumed(self) -> int:
        """Total number of source characters consumed after this packet."""
        return self.position

    @property
    def cursor(self) -> int:
        """Total number of source characters consumed after this packet."""
        return self.position

    @property
    def duplicate(self) -> int:
        """Number of already-consumed characters re-sent in this packet."""
        return self.duplicate_characters

    @property
    def new(self) -> int:
        """Number of genuinely new characters in this packet."""
        return self.new_characters


class IncrementalDecoder:
    """
    A resumable decoder for polyline strings received in arbitrary packets.

    Packets may split the encoded string after any character, including in the
    middle of a coordinate's variable-length integer.  Only complete
    ``(latitude, longitude)`` points are emitted; a trailing partial value
    remains in the decoder state.

    Every :meth:`feed` call takes the packet's absolute character offset
    (``offset``).  If a packet starts before the current cursor, its overlap
    with already-consumed characters is checked character by character.  A
    mismatch raises :class:`PolylineConflictError`.  A matching overlap is a
    harmless re-send: it is skipped and decoding continues with the genuinely
    new suffix.

    :meth:`save_state` returns a JSON-serialisable snapshot, including the
    consumed raw characters and any partly decoded coordinate.  Passing that
    snapshot to :meth:`restore_state` or :meth:`from_state` resumes at exactly
    the saved character boundary.
    """

    STATE_VERSION = 1

    def __init__(self, precision: int = 5, geojson: bool = False,
                 state: Optional[Union[Dict[str, Any], str]] = None, on_point=None):
        """
        Create a decoder.

        :param precision: Precision of the encoded coordinates.
        :param geojson: Emit ``(lon, lat)`` points, as :func:`decode` does.
        :param state: Optional state produced by :meth:`save_state`.
        :param on_point: Optional callback invoked with each complete point.
        """
        self._precision = precision
        self._geojson = geojson
        self._factor = float(10 ** precision)
        self._on_point = on_point
        self._reset_parser()
        if state is not None:
            self.restore_state(state)

    def _reset_parser(self):
        self._cursor = 0
        self._raw_chars: Optional[List[str]] = []
        self._lat = 0
        self._lng = 0
        self._phase = 'lat'
        self._lat_change: Optional[int] = None
        self._partial = 0
        self._shift = 0
        self._point_start = 0
        self._points: List[DecodedPoint] = []
        self._conflicts: List[Dict[str, Any]] = []

    @property
    def precision(self) -> int:
        """Coordinate precision used by this decoder."""
        return self._precision

    @property
    def geojson(self) -> bool:
        """Whether points are emitted as ``(lon, lat)``."""
        return self._geojson

    @property
    def cursor(self) -> int:
        """Number of original characters consumed so far."""
        return self._cursor

    @property
    def position(self) -> int:
        """Number of original characters consumed so far."""
        return self._cursor

    @property
    def consumed(self) -> int:
        """Number of original characters consumed so far."""
        return self._cursor

    @property
    def offset(self) -> int:
        """Number of original characters consumed so far."""
        return self._cursor

    @property
    def pending(self) -> bool:
        """Whether the decoder is part-way through a point."""
        return self._point_start < self._cursor

    @property
    def points(self) -> List[DecodedPoint]:
        """All complete points emitted so far, in source order."""
        return list(self._points)

    @property
    def conflicts(self) -> List[Dict[str, Any]]:
        """Conflict reports collected before an exception was raised."""
        return [dict(item) for item in self._conflicts]

    @property
    def raw(self) -> Optional[str]:
        """The consumed source characters, if they are available."""
        return None if self._raw_chars is None else ''.join(self._raw_chars)

    @property
    def state(self) -> DecoderState:
        """A fresh snapshot of the resumable read state."""
        return self.save_state()

    def feed(self, packet: Union[str, Sequence[Any]], offset: Optional[int] = None,
             packet_offset: Optional[int] = None) -> FeedResult:
        """
        Consume one packet and return the complete points it finished.

        :param packet: Encoded characters, or a ``(offset, characters)`` pair.
        :param offset: Absolute offset of the packet's first character.  If
            omitted, the packet is assumed to continue at the current cursor.
        :param packet_offset: Alias for ``offset``.
        :return: A :class:`FeedResult` list containing only newly completed
            points.  Partial input is retained for the next call.
        """
        if (isinstance(packet, (tuple, list))
                and len(packet) == 2
                and isinstance(packet[0], int)
                and isinstance(packet[1], str)
                and offset is None
                and packet_offset is None):
            packet, offset = packet[1], packet[0]

        if isinstance(packet, int) and isinstance(offset, str):
            packet, offset = offset, packet

        if packet_offset is not None:
            if offset is not None and offset != packet_offset:
                raise ValueError('offset and packet_offset disagree')
            offset = packet_offset

        if not isinstance(packet, str):
            raise TypeError('packet must be a string')

        if offset is None:
            offset = self._cursor
        if not isinstance(offset, int):
            raise TypeError('offset must be an integer')
        if offset < 0:
            raise PolylineGapError(offset, self._cursor)
        if offset > self._cursor:
            raise PolylineGapError(offset, self._cursor)

        packet_end = offset + len(packet)
        overlap_end = min(packet_end, self._cursor)
        if offset < overlap_end:
            self._verify_overlap(packet, offset, overlap_end)

        if packet_end <= self._cursor:
            return FeedResult([], self._cursor, offset, 0, len(packet))

        new_text = packet[self._cursor - offset:]
        new_points = self._consume(new_text)
        return FeedResult(new_points, self._cursor, offset, len(new_text), overlap_end - offset)

    def _verify_overlap(self, packet: str, offset: int, overlap_end: int):
        if self._raw_chars is None:
            return
        for position in range(offset, overlap_end):
            received = packet[position - offset]
            expected = self._raw_chars[position]
            if expected != received:
                conflict = {
                    'position': position,
                    'offset': position,
                    'expected': expected,
                    'received': received,
                    'packet_offset': offset,
                }
                self._conflicts.append(conflict)
                raise PolylineConflictError(position, expected, received, offset)

    def _consume(self, text: str) -> List[DecodedPoint]:
        points: List[DecodedPoint] = []
        for character in text:
            if self._raw_chars is not None:
                self._raw_chars.append(character)
            self._cursor += 1

            byte = ord(character) - 63
            self._partial |= (byte & 0x1f) << self._shift
            self._shift += 5

            if byte >= 0x20:
                continue

            value = self._partial
            self._partial = 0
            self._shift = 0
            change = ~(value >> 1) if value & 1 else (value >> 1)

            if self._phase == 'lat':
                self._lat_change = change
                self._phase = 'lng'
            else:
                self._lat += self._lat_change
                self._lng += change
                lat, lng = self._lat / self._factor, self._lng / self._factor
                if self._geojson:
                    lat, lng = lng, lat
                point = DecodedPoint(lat, lng, self._point_start, self._cursor)
                points.append(point)
                self._points.append(point)
                if self._on_point is not None:
                    self._on_point(point)
                self._point_start = self._cursor
                self._phase = 'lat'
                self._lat_change = None

        return points

    def finish(self) -> None:
        """Assert that the received input ends on a point boundary."""
        if self.pending:
            raise PolylineIncompleteError(self._cursor, self._point_start, self._phase)

    def finalize(self) -> None:
        """Alias for :meth:`finish`."""
        self.finish()

    def reset(self) -> None:
        """Discard all input and begin again at offset zero."""
        self._reset_parser()

    def save_state(self, as_json: bool = False) -> Union[DecoderState, str]:
        """
        Return a serialisable snapshot of the incremental read state.

        The snapshot contains the absolute cursor, the consumed source text,
        accumulated latitude/longitude, any partially decoded coordinate, and
        the points emitted so far.
        """
        state: Dict[str, Any] = {
            'version': self.STATE_VERSION,
            'precision': self._precision,
            'geojson': self._geojson,
            'cursor': self._cursor,
            'raw': None if self._raw_chars is None else ''.join(self._raw_chars),
            'latitude': self._lat,
            'longitude': self._lng,
            'phase': self._phase,
            'lat_change': self._lat_change,
            'partial': self._partial,
            'shift': self._shift,
            'point_start': self._point_start,
            'points': [point.as_tuple() for point in self._points],
            'conflicts': [dict(item) for item in self._conflicts],
        }
        snapshot = DecoderState(state)
        return snapshot.to_json() if as_json else snapshot

    def get_state(self) -> DecoderState:
        """Alias for :meth:`save_state` without JSON encoding."""
        return self.save_state()

    def dump_state(self) -> str:
        """Return the state as a JSON string."""
        return self.save_state(as_json=True)

    @property
    def state_dict(self) -> DecoderState:
        """A fresh, attribute-accessible snapshot of the read state."""
        return self.save_state()

    def save(self, as_json: bool = False) -> Union[DecoderState, str]:
        """Alias for :meth:`save_state`."""
        return self.save_state(as_json)

    def restore_state(self, state: Union[Dict[str, Any], str]) -> 'IncrementalDecoder':
        """
        Restore a snapshot produced by :meth:`save_state`.

        Restoring is in-place; the decoder's previous input is discarded.
        """
        if isinstance(state, DecoderState):
            state = dict(state)
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except ValueError as error:
                raise PolylineStateError(f'invalid JSON state: {error}') from error
        if not isinstance(state, dict):
            raise PolylineStateError('state must be a mapping or a JSON object string')
        if state.get('version', self.STATE_VERSION) != self.STATE_VERSION:
            raise PolylineStateError(f"unsupported state version {state.get('version')!r}")

        raw = state.get('raw')
        if raw is not None and not isinstance(raw, str):
            raise PolylineStateError('state raw must be a string or None')

        try:
            cursor = int(state.get('cursor', len(raw) if raw is not None else 0))
            lat = int(state.get('latitude', 0))
            lng = int(state.get('longitude', 0))
            phase = state.get('phase', 'lat')
            lat_change = state.get('lat_change')
            partial = int(state.get('partial', 0))
            shift = int(state.get('shift', 0))
            point_start = int(state.get('point_start', 0))
        except (TypeError, ValueError) as error:
            raise PolylineStateError(f'invalid state value: {error}') from error

        if cursor < 0 or point_start < 0 or point_start > cursor:
            raise PolylineStateError('state cursor and point_start are inconsistent')
        if raw is None and cursor:
            raise PolylineStateError('state raw is required to resume a non-empty input')
        if raw is not None and len(raw) != cursor:
            raise PolylineStateError('state raw length does not match cursor')
        if phase not in ('lat', 'lng'):
            raise PolylineStateError(f"unknown state phase {phase!r}")
        if lat_change is not None:
            lat_change = int(lat_change)

        precision = int(state.get('precision', self._precision))
        geojson = bool(state.get('geojson', self._geojson))

        points = []
        for value in state.get('points', []):
            try:
                points.append(DecodedPoint(*value[:4]))
            except (TypeError, ValueError) as error:
                raise PolylineStateError(f'invalid point in state: {error}') from error

        conflicts = state.get('conflicts', [])
        if not isinstance(conflicts, list):
            raise PolylineStateError('state conflicts must be a list')

        self._precision = precision
        self._geojson = geojson
        self._factor = float(10 ** precision)
        self._cursor = cursor
        self._raw_chars = None if raw is None else list(raw)
        self._lat = lat
        self._lng = lng
        self._phase = phase
        self._lat_change = lat_change
        self._partial = partial
        self._shift = shift
        self._point_start = point_start
        self._points = points
        self._conflicts = [dict(item) for item in conflicts]
        return self

    def set_state(self, state: Union[Dict[str, Any], str]) -> 'IncrementalDecoder':
        """Alias for :meth:`restore_state`."""
        return self.restore_state(state)

    @classmethod
    def from_state(cls, state: Union[Dict[str, Any], str]) -> 'IncrementalDecoder':
        """Create a decoder directly from a saved state snapshot."""
        return cls(state=state)

    @classmethod
    def resume(cls, state: Union[Dict[str, Any], str]) -> 'IncrementalDecoder':
        """Alias for :meth:`from_state`."""
        return cls.from_state(state)


def decode_with_provenance(expression: str, precision: int = 5, geojson: bool = False) -> List[DecodedPoint]:
    """
    Decode a complete polyline string, retaining each point's character range.

    The values are identical to :func:`decode`; each result additionally
    records the half-open ``[start, end)`` range it was decoded from.

    :param expression: Polyline string.
    :param precision: Precision of the encoded coordinates.
    :param geojson: Emit ``(lon, lat)`` points.
    :return: List of :class:`DecodedPoint` objects.
    """
    decoder = IncrementalDecoder(precision=precision, geojson=geojson)
    decoder.feed(expression, 0)
    decoder.finish()
    return decoder.points


def decode_incremental(packets: Iterable[Union[str, Sequence[Any]]], precision: int = 5,
                       geojson: bool = False, offsets: Optional[Iterable[Optional[int]]] = None,
                       state: Optional[Union[Dict[str, Any], str]] = None) -> List[DecodedPoint]:
    """
    Decode an encoded string delivered as an iterable of packets.

    :param packets: Packet strings, or ``(offset, characters)`` pairs.
    :param precision: Precision of the encoded coordinates.
    :param geojson: Emit ``(lon, lat)`` points.
    :param offsets: Optional per-packet absolute offsets.  When omitted, each
        packet is expected to continue at the current cursor.
    :param state: Optional saved state to resume from before the first packet.
    :return: All complete points, in source order.
    """
    decoder = IncrementalDecoder(precision=precision, geojson=geojson, state=state)
    if offsets is None:
        for packet in packets:
            decoder.feed(packet)
    else:
        for packet, offset in zip(packets, offsets):
            decoder.feed(packet, offset)
    decoder.finish()
    return decoder.points


decode_chunks = decode_incremental
decode_packets = decode_incremental
decode_stream = decode_incremental


def decode_chunk(packet: Union[str, Sequence[Any]], state: Optional[Union[Dict[str, Any], str]] = None,
                 offset: Optional[int] = None, precision: int = 5, geojson: bool = False,
                 finish: bool = False, packet_offset: Optional[int] = None) -> DecodeResult:
    """
    Consume one packet against a saved state and return the new state.

    This is the stateless form of :meth:`IncrementalDecoder.feed`, useful when
    the caller persists the state between packets (for example in a database)::

        result = polyline.decode_chunk(packet, saved_state, offset=100)
        save(result.state)
        points = result.points

    When ``state`` is ``None`` the decoder starts at offset zero, so pass the
    packet's absolute offset only if it is not the first packet.

    :param packet: Encoded characters, or a ``(offset, characters)`` pair.
    :param state: State from a previous call, or ``None`` to start.
    :param offset: Absolute offset of the packet's first character.
    :param precision: Precision of the encoded coordinates.
    :param geojson: Emit ``(lon, lat)`` points.
    :param finish: If true, assert the input ends on a point boundary.
    :param packet_offset: Alias for ``offset``.
    :return: The complete points produced by the packet and the new state.
    """
    if packet_offset is not None:
        if offset is not None and offset != packet_offset:
            raise ValueError('offset and packet_offset disagree')
        offset = packet_offset

    decoder = IncrementalDecoder(precision=precision, geojson=geojson, state=state)
    points = decoder.feed(packet, offset)
    if finish:
        decoder.finish()
    return DecodeResult(list(points), decoder.save_state())


def decode_state(expression: str, offset: int = 0, state: Optional[Union[Dict[str, Any], str]] = None,
                 precision: int = 5, geojson: bool = False,
                 finish: bool = True) -> DecodeResult:
    """
    Decode one packet at its absolute offset, returning points and state.

    This is an explicit-name alias for :func:`decode_chunk`; packets may end
    anywhere, including inside a coordinate value.
    """
    return decode_chunk(expression, state=state, offset=offset, precision=precision,
                        geojson=geojson, finish=finish)


def _write(output, curr_value, prev_value, factor):
    curr_value = _py2_round(curr_value * factor)
    prev_value = _py2_round(prev_value * factor)
    coord = curr_value - prev_value
    coord <<= 1
    coord = coord if coord >= 0 else ~coord

    while coord >= 0x20:
        output.write(chr((0x20 | (coord & 0x1f)) + 63))
        coord >>= 5

    output.write(chr(coord + 63))


def _trans(value, index):
    byte, result, shift = None, 0, 0

    comp = None
    while byte is None or byte >= 0x20:
        byte = ord(value[index]) - 63
        index += 1
        result |= (byte & 0x1f) << shift
        shift += 5
        comp = result & 1

    return ~(result >> 1) if comp else (result >> 1), index


def decode(expression: str, precision: int = 5, geojson: bool = False) -> List[Tuple[float, float]]:
    """
    Decode a polyline string into a set of coordinates.

    :param expression: Polyline string, e.g. 'u{~vFvyys@fS]'.
    :param precision: Precision of the encoded coordinates. Google Maps uses 5, OpenStreetMap uses 6.
        The default value is 5.
    :param geojson: Set output of tuples to (lon, lat), as per https://tools.ietf.org/html/rfc7946#section-3.1.1
    :return: List of coordinate tuples in (lat, lon) order, unless geojson is set to True.
    """
    coordinates, index, lat, lng, length, factor = [], 0, 0, 0, len(expression), float(10 ** precision)

    while index < length:
        lat_change, index = _trans(expression, index)
        lng_change, index = _trans(expression, index)
        lat += lat_change
        lng += lng_change
        coordinates.append((lat / factor, lng / factor))

    if geojson is True:
        coordinates = [t[::-1] for t in coordinates]

    return coordinates


def encode(coordinates: List[Tuple[float, float]], precision: int = 5, geojson: bool = False) -> str:
    """
    Encode a set of coordinates in a polyline string.

    :param coordinates: List of coordinate tuples, e.g. [(0, 0), (1, 0)]. Unless geojson is set to True, the order
        is expected to be (lat, lon).
    :param precision: Precision of the coordinates to encode. Google Maps uses 5, OpenStreetMap uses 6.
        The default value is 5.
    :param geojson: Set to True in order to encode (lon, lat) tuples.
    :return: The encoded polyline string.
    """
    if len(coordinates) == 0:
        return ''

    if geojson is True:
        coordinates = [t[::-1] for t in coordinates]

    output, factor = io.StringIO(), int(10 ** precision)

    _write(output, coordinates[0][0], 0, factor)
    _write(output, coordinates[0][1], 0, factor)

    for prev, curr in _pcitr(coordinates):
        _write(output, curr[0], prev[0], factor)
        _write(output, curr[1], prev[1], factor)

    return output.getvalue()
