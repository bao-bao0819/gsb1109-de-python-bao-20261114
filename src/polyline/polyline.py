"""
A Python implementation of Google's Encoded Polyline Algorithm Format.
"""
import io
import itertools
import math
from collections.abc import Mapping
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple


def _pcitr(iterable):
    return zip(iterable, itertools.islice(iterable, 1, None))


def _py2_round(x):
    # The polyline algorithm uses Python 2's way of rounding
    return int(math.copysign(math.floor(math.fabs(x) + 0.5), x))


def _write(output, curr_value, prev_value, factor):
    curr_value = _py2_round(curr_value * factor)
    prev_value = _py2_round(prev_value * factor)
    _write_int(output, curr_value, prev_value)


def _write_int(output, curr_value, prev_value):
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


def _decode_scaled(expression: str) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Decode a polyline into scaled integer coordinates and per-point char ranges."""
    coordinates, ranges, index, lat, lng = [], [], 0, 0, 0

    while index < len(expression):
        start = index
        lat_change, index = _trans(expression, index)
        lng_change, index = _trans(expression, index)
        lat += lat_change
        lng += lng_change
        coordinates.append((lat, lng))
        ranges.append((start, index))

    return coordinates, ranges


def _write_point(output, curr_value, prev_value):
    _write_int(output, curr_value[0], prev_value[0])
    _write_int(output, curr_value[1], prev_value[1])


class PointPatch(NamedTuple):
    """A replacement of old points ``start`` up to (excluding) ``stop``."""

    start: int
    stop: int
    points: Sequence[Tuple[float, float]]


class PatchResult(NamedTuple):
    """The patched polyline and the migration of the old character index."""

    expression: str
    index: Dict[Any, Optional[Tuple[int, int]]]

    @property
    def new_expression(self) -> str:
        """The encoded polyline after applying the patches."""
        return self.expression

    @property
    def migrated_index(self) -> Dict[Any, Optional[Tuple[int, int]]]:
        """Old index mapped to the new string; ``None`` where the entry was removed."""
        return self.index


def _parse_point(value: Any) -> Tuple[float, float]:
    try:
        lat, lng = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f'point {value!r} must be a (lat, lng) pair') from exc
    return float(lat), float(lng)


def _parse_patch(edit: Any) -> PointPatch:
    if isinstance(edit, PointPatch):
        return PointPatch(edit.start, edit.stop, tuple(_parse_point(item) for item in edit.points))
    if isinstance(edit, Mapping):
        missing = [key for key in ('start', 'points') if key not in edit]
        if missing:
            raise ValueError(f"patch is missing {', '.join(map(repr, missing))}")
        start = int(edit['start'])
        stop = edit['stop'] if 'stop' in edit else edit.get('end', start)
        points = edit['points']
    else:
        try:
            start, stop, points = edit
        except (TypeError, ValueError) as exc:
            raise ValueError('a patch must provide start, stop and points') from exc
        start, stop = int(start), int(stop)

    if isinstance(points, tuple) and len(points) == 2 and not isinstance(points[0], (tuple, list)):
        points = [points]
    return PointPatch(start, int(stop), tuple(_parse_point(item) for item in points))


def _conflict(previous: PointPatch, current: PointPatch) -> bool:
    if previous.start == previous.stop and current.start == current.stop:
        return previous.start == current.start
    if previous.start == previous.stop:
        return current.start < previous.start < current.stop
    if current.start == current.stop:
        return previous.start < current.start < previous.stop
    return current.start < previous.stop


def _validate_patches(patches: Sequence[PointPatch], point_count: int) -> None:
    for entry in patches:
        if entry.start < 0 or entry.stop < entry.start or entry.stop > point_count:
            raise ValueError(
                f'patch range [{entry.start}, {entry.stop}) is outside 0..{point_count}')

    ordered = sorted(patches, key=lambda entry: (entry.start, entry.stop))
    for previous, current in _pcitr(ordered):
        if _conflict(previous, current):
            raise ValueError(
                f'patch ranges [{previous.start}, {previous.stop}) and '
                f'[{current.start}, {current.stop}) overlap in the old points')


def _rounded_points(points: Sequence[Tuple[float, float]], factor: int) -> List[Tuple[int, int]]:
    return [(_py2_round(lat * factor), _py2_round(lng * factor)) for lat, lng in points]


def _normalise_index(index: Mapping[Any, Tuple[int, int]]) -> Dict[Any, Tuple[int, int]]:
    normalised = {}
    for key, char_range in index.items():
        try:
            start, stop = char_range
        except (TypeError, ValueError) as exc:
            raise ValueError(f'index entry {key!r} must be a (start, stop) pair') from exc
        start, stop = int(start), int(stop)
        if start < 0 or stop < start:
            raise ValueError(f'index entry {key!r} has an invalid range')
        normalised[key] = (start, stop)
    return normalised


def _validate_index(
        index: Mapping[Any, Tuple[int, int]], old_ranges: Sequence[Tuple[int, int]]) -> None:
    length = old_ranges[-1][1] if old_ranges else 0
    for key, (start, stop) in index.items():
        if stop > length:
            raise ValueError(f'index entry {key!r} ends outside the polyline')
        if start == stop:
            continue
        covered = [
            point for point, (old_start, old_end) in enumerate(old_ranges)
            if old_start < stop and start < old_end
        ]
        aligned = (
            covered
            and covered == list(range(covered[0], covered[-1] + 1))
            and old_ranges[covered[0]][0] == start
            and old_ranges[covered[-1]][1] == stop
        )
        if not aligned:
            raise ValueError(
                f'index entry {key!r} [{start}, {stop}) does not follow point boundaries')


def _map_index_entry(
        char_range: Tuple[int, int], old_ranges: Sequence[Tuple[int, int]],
        old_spans: Mapping[int, Optional[Tuple[int, int]]], length: int
        ) -> Optional[Tuple[int, int]]:
    start, stop = char_range
    if stop > length:
        raise ValueError(f'index range [{start}, {stop}) is outside the polyline')

    if start == stop:
        boundary = sum(1 for _, old_end in old_ranges if old_end <= start)
        span = old_spans.get(boundary)
        offset = span[0] if span is not None else length
        return offset, offset

    touched = [
        point for point, (old_start, old_end) in enumerate(old_ranges)
        if old_start < stop and start < old_end
    ]
    surviving = [point for point in touched if old_spans.get(point) is not None]
    if not surviving:
        return None
    first = old_spans[surviving[0]]
    last = old_spans[surviving[-1]]
    if first is None or last is None:
        return None
    return first[0], last[1]


def patch(expression: str, patches: Sequence[Any], index: Optional[Mapping[Any, Tuple[int, int]]] = None,
          precision: int = 5, geojson: bool = False) -> PatchResult:
    """
    Replace several point ranges of an encoded polyline in one atomic batch.

    Each patch is described in the old point numbering, either as a
    :class:`PointPatch`, as a ``(start, stop, points)`` triple, or as a mapping
    with ``start``, ``stop`` and ``points`` keys. ``start`` is inclusive and
    ``stop`` exclusive, so an empty range inserts points. Coordinates use the
    same order and rounding as :func:`encode`.

    Only the replaced points and the first untouched point after each range are
    re-encoded, because their delta changes. Every later point is copied
    verbatim from ``expression``, so unrelated parts keep their exact bytes.

    All ranges are validated before any output is produced. Overlapping
    patches raise ``ValueError`` and no partial result is returned.

    :param expression: The original encoded polyline string.
    :param patches: The point ranges to replace, numbered against the old points.
    :param index: Optional mapping of stop identifiers to ``(start, stop)``
        character ranges in the old string. Ranges must follow point boundaries.
        When omitted, every old point number is migrated.
    :param precision: Precision of the encoded coordinates. The default is 5.
    :param geojson: Set to True when the patch coordinates are (lon, lat) tuples.
    :return: A :class:`PatchResult` with the new string and the migrated index.
        Entries that no longer exist map to ``None``.
    """
    normalised = [_parse_patch(entry) for entry in patches]
    old_points, old_ranges = _decode_scaled(expression)
    _validate_patches(normalised, len(old_points))
    supplied_index = _normalise_index(index) if index is not None else None
    if supplied_index is not None:
        _validate_index(supplied_index, old_ranges)

    factor = int(10 ** precision)
    ordered = sorted(normalised, key=lambda entry: (entry.start, entry.stop))

    sequence: List[Tuple[str, Any]] = []
    cursor = 0
    for number, entry in enumerate(ordered):
        while cursor < entry.start:
            sequence.append(('old', cursor))
            cursor += 1
        replacements = _rounded_points(entry.points, factor)
        if geojson is True:
            replacements = [(lng, lat) for lat, lng in replacements]
        sequence.extend(('new', (number, coordinate)) for coordinate in replacements)
        cursor = entry.stop

    while cursor < len(old_points):
        sequence.append(('old', cursor))
        cursor += 1

    output = io.StringIO()
    old_spans: Dict[int, Optional[Tuple[int, int]]] = {}
    replaced_items: Dict[int, List[Tuple[int, int]]] = {}
    previous = (0, 0)
    previous_source: Optional[int] = None

    for kind, value in sequence:
        start = output.tell()
        if kind == 'old':
            source = int(value)
            if previous_source == source - 1:
                output.write(expression[old_ranges[source][0]:old_ranges[source][1]])
            else:
                _write_point(output, old_points[source], previous)
            old_spans[source] = (start, output.tell())
            previous = old_points[source]
            previous_source = source
        else:
            number, coordinate = value
            _write_point(output, coordinate, previous)
            replaced_items.setdefault(number, []).append((start, output.tell()))
            previous = coordinate
            previous_source = None

    for number, entry in enumerate(ordered):
        items = replaced_items.get(number, [])
        span = (items[0][0], items[-1][1]) if items else None
        for source in range(entry.start, entry.stop):
            old_spans[source] = span

    if supplied_index is None:
        migrated: Dict[Any, Optional[Tuple[int, int]]] = {
            point: old_spans.get(point) for point in range(len(old_points))}
    else:
        migrated = {
            key: _map_index_entry(char_range, old_ranges, old_spans, len(expression))
            for key, char_range in supplied_index.items()}

    return PatchResult(output.getvalue(), migrated)
