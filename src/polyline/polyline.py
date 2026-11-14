"""
A Python implementation of Google's Encoded Polyline Algorithm Format.
"""
import io
import itertools
import math
from typing import Dict, List, NamedTuple, Optional, Tuple


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


class Candidate(NamedTuple):
    """
    A decoding hypothesis for a polyline of unknown origin.

    :param precision: Precision of the encoded coordinates (5 for Google Maps, 6 for OpenStreetMap).
    :param geojson: Whether the sender encoded (lon, lat) tuples instead of (lat, lon).
    """
    precision: int = 5
    geojson: bool = False


#: The four combinations of precision 5/6 and (lat, lon)/(lon, lat) order.
DEFAULT_CANDIDATES: Tuple[Candidate, ...] = (
    Candidate(precision=5, geojson=False),
    Candidate(precision=5, geojson=True),
    Candidate(precision=6, geojson=False),
    Candidate(precision=6, geojson=True),
)

_EARTH_RADIUS_M = 6371000.0


def _distance_m(first: Tuple[float, float], second: Tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (first[0], first[1], second[0], second[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(hav))


def _within_region(lat: float, lon: float, region: Tuple[float, float, float, float]) -> bool:
    min_lat, min_lon, max_lat, max_lon = region
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def disambiguate(expression: str,
                 candidates: Tuple[Candidate, ...] = DEFAULT_CANDIDATES,
                 anchors: Optional[Dict[int, Tuple[float, float, float, float]]] = None,
                 max_step: Optional[float] = None) -> List[Tuple[Candidate, List[Tuple[float, float]]]]:
    """
    Find every decoding configuration that plausibly explains an encoded polyline.

    Each candidate is decoded and checked against the given constraints: decoded coordinates must be
    valid (lat in [-90, 90], lon in [-180, 180]), every anchor point must fall inside its region, and
    the haversine distance between adjacent points must not exceed ``max_step``.

    :param expression: Polyline string of unknown origin.
    :param candidates: Decoding configurations to try. Defaults to all combinations of precision
        5/6 and (lat, lon)/(lon, lat) order.
    :param anchors: Mapping of point index to a ``(min_lat, min_lon, max_lat, max_lon)`` region the
        decoded point at that index must fall into.
    :param max_step: Maximum allowed displacement in meters between adjacent decoded points.
    :return: List of ``(candidate, coordinates)`` pairs that satisfy all constraints, with
        coordinates always in (lat, lon) order. An empty list means no candidate fits; more than one
        match means the segment is ambiguous and should be kept pending rather than committed to a
        default configuration.
    """
    matches = []

    for candidate in candidates:
        try:
            coordinates = decode(expression, precision=candidate.precision, geojson=candidate.geojson)
        except (IndexError, ValueError):
            continue

        if not all(-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 for lat, lon in coordinates):
            continue

        if anchors and not all(
                0 <= index < len(coordinates) and _within_region(*coordinates[index], region)
                for index, region in anchors.items()):
            continue

        if max_step is not None and any(
                _distance_m(prev, curr) > max_step for prev, curr in _pcitr(coordinates)):
            continue

        matches.append((candidate, coordinates))

    return matches
