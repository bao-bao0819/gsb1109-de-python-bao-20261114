"""
A Python implementation of Google's Encoded Polyline Algorithm Format.
"""
import io
import itertools
import math
from typing import List, NamedTuple, Optional, Tuple

#: Segment statuses returned by :func:`audit`.
CONFIRMED = 'confirmed'
SUSPICIOUS = 'suspicious'
RESTARTABLE = 'restartable'


def _pcitr(iterable):
    return zip(iterable, itertools.islice(iterable, 1, None))


def _py2_round(x):
    # The polyline algorithm uses Python 2's way of rounding
    return int(math.copysign(math.floor(math.fabs(x) + 0.5), x))


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


def _decode_grid(expression):
    """Decode into per-point (end_offset, lat_grid, lng_grid) cumulative values."""
    points, index, lat, lng, length = [], 0, 0, 0, len(expression)

    while index < length:
        lat_change, index = _trans(expression, index)
        lng_change, index = _trans(expression, index)
        lat += lat_change
        lng += lng_change
        points.append((index, lat, lng))

    return points


class Checkpoint(NamedTuple):
    """
    A trusted record of a single point, taken from an independent source.

    :param offset: Offset in the encoded string right after this point's encoding
        (i.e. where the next point starts).
    :param index: 0-based index of the point in the decoded track.
    :param coord: Absolute coordinate of the point, in (lat, lon) order unless
        ``geojson`` is set when calling :func:`audit`.
    """
    offset: int
    index: int
    coord: Tuple[float, float]


class Segment(NamedTuple):
    """
    A run of consecutive points with a single trust status.

    ``coordinates`` holds the absolute coordinates of points
    ``start_index``..``end_index`` (inclusive) when they are known, and is
    ``None`` for ``SUSPICIOUS`` segments, whose positions cannot be trusted.
    """
    status: str
    start_index: int
    end_index: int
    start_offset: int
    end_offset: int
    coordinates: Optional[List[Tuple[float, float]]]


class AuditReport(NamedTuple):
    """
    Result of :func:`audit`.

    ``track`` has one entry per decoded point; entries are ``None`` for points
    that fall in ``SUSPICIOUS`` segments and therefore cannot be ingested.
    """
    segments: List[Segment]
    track: List[Optional[Tuple[float, float]]]


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


def audit(expression: str, checkpoints: List[Checkpoint], precision: int = 5,
          geojson: bool = False) -> AuditReport:
    """
    Audit a possibly corrupted polyline against trusted checkpoints.

    Because the format is delta-encoded, a single corrupted character shifts the
    absolute position of every subsequent point, even though decoding still
    produces numbers. ``checkpoints`` provides an independent ground truth for a
    few points, allowing the track to be split into:

    - ``CONFIRMED`` segments, where decoding between two trusted anchors lands
      exactly on the closing anchor;
    - ``SUSPICIOUS`` segments, where it does not. Such a segment is only marked
      as a whole: the mismatching end value cannot identify a unique bad
      character, so no repair is attempted and its positions are dropped;
    - a ``RESTARTABLE`` suffix after the last anchor, decoded afresh from that
      anchor's trusted coordinate. If the string contains no checkpoints at
      all, the whole track is restartable from the implicit (0, 0) origin.

    Decoding always resumes from each checkpoint's trusted coordinate, so parts
    of the track that line up again at later anchors remain ingestible.

    :param expression: Polyline string, possibly containing a corrupted stretch.
    :param checkpoints: List of :class:`Checkpoint` records. Offsets must match
        point boundaries of the encoded string.
    :param precision: Precision of the encoded coordinates. The default value is 5.
    :param geojson: Expect and return (lon, lat) tuples instead of (lat, lon).
    :return: An :class:`AuditReport` with the segmented track.
    :raises ValueError: If a checkpoint index or offset does not line up with
        the decoded points.
    """
    factor = float(10 ** precision)
    points = _decode_grid(expression)
    length = len(points)

    # Implicit trusted anchor: the (0, 0) origin the first point is encoded from.
    # Each anchor is (offset, index, lat_grid, lng_grid).
    anchors = [(0, -1, 0, 0)]
    prev_index = -1
    for check in sorted(checkpoints, key=lambda c: c.index):
        if check.index <= prev_index:
            raise ValueError(f'duplicate or unordered checkpoint index {check.index}')
        if not 0 <= check.index < length:
            raise ValueError(f'checkpoint index {check.index} out of range')
        end_offset = points[check.index][0]
        if check.offset != end_offset:
            raise ValueError(
                f'checkpoint offset {check.offset} does not match end of point '
                f'{check.index} at offset {end_offset}')
        lat_c, lng_c = check.coord[::-1] if geojson else check.coord
        anchors.append((check.offset, check.index,
                        _py2_round(lat_c * factor), _py2_round(lng_c * factor)))
        prev_index = check.index

    segments, track = [], [None] * length

    for pos, (a_offset, a_index, a_lat, a_lng) in enumerate(anchors):
        if pos + 1 < len(anchors):
            b_offset, b_index, b_lat, b_lng = anchors[pos + 1]
            decoded = _decode_grid(expression[a_offset:b_offset])
            end_grid = (a_lat + decoded[-1][1], a_lng + decoded[-1][2])
            status = CONFIRMED if end_grid == (b_lat, b_lng) else SUSPICIOUS
            end_index, end_offset = b_index, b_offset
        else:
            decoded = _decode_grid(expression[a_offset:])
            status = RESTARTABLE
            end_index, end_offset = length - 1, len(expression)

        if not decoded:
            continue

        coordinates = [((a_lat + dlat) / factor, (a_lng + dlng) / factor)
                       for _, dlat, dlng in decoded]
        if status != SUSPICIOUS:
            for i, coord in enumerate(coordinates):
                track[a_index + 1 + i] = coord
        else:
            coordinates = None
        segments.append(Segment(status, a_index + 1, end_index, a_offset,
                                end_offset, coordinates))

    if geojson is True:
        segments = [s._replace(coordinates=[t[::-1] for t in s.coordinates]
                               if s.coordinates is not None else None)
                    for s in segments]
        track = [t[::-1] if t is not None else None for t in track]

    return AuditReport(segments, track)
