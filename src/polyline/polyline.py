"""
A Python implementation of Google's Encoded Polyline Algorithm Format.
"""
import io
import itertools
import math
from typing import Iterable, List, Optional, Tuple


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


def split(coordinates: List[Tuple[float, float]], parts: int, precision: int = 5, geojson: bool = False,
          stops: Optional[Iterable[int]] = None,
          tunnels: Optional[Iterable[Tuple[int, int]]] = None) -> List[str]:
    """
    Split a route into ``parts`` encoded polyline shards, in original point order.

    Each shard is a standalone encoded polyline that decodes from the zero coordinate, so
    shards can be stored and decoded independently. Adjacent shards duplicate their junction
    point, which allows the junction to be verified when the shards are joined again with
    :func:`join`.

    Shards are balanced by actual encoded length: the split minimizes the number of
    characters of the longest shard and, among equally balanced splits, the one with the
    smallest total number of characters is chosen. Because the first point of every shard is
    encoded as a delta from the origin, a shard starting far away from the origin is more
    expensive, so splitting the points into equal counts is generally not the most balanced
    split.

    :param coordinates: List of coordinate tuples, e.g. [(0, 0), (1, 0)]. Unless geojson is
        set to True, the order is expected to be (lat, lon).
    :param parts: Number of shards to produce. Every shard holds at least one point.
    :param precision: Precision of the coordinates to encode. The default value is 5.
    :param geojson: Set to True in order to encode (lon, lat) tuples.
    :param stops: Optional iterable of point indices at which a cut is allowed. If omitted,
        every interior point is a cut candidate.
    :param tunnels: Optional iterable of (start, end) index pairs marking tunnels. A cut
        strictly inside a tunnel (start < cut < end) is never allowed.
    :return: List of ``parts`` encoded polyline strings.
    """
    if parts < 1:
        raise ValueError('parts must be at least 1')

    coordinates = list(coordinates)
    length = len(coordinates)

    if length == 0:
        raise ValueError('cannot split an empty route')

    if parts == 1:
        return [encode(coordinates, precision, geojson)]

    allowed = set(range(1, length - 1))
    if stops is not None:
        allowed &= {int(stop) for stop in stops}
    if tunnels is not None:
        for start, end in tunnels:
            for index in range(max(start + 1, 1), min(end, length - 1)):
                allowed.discard(index)

    # The delta encoding between two consecutive points does not depend on the points
    # before them, so the encoded length of the shard covering points a..b is the length
    # of point a encoded from the origin plus the shared delta lengths of a+1..b.
    head = [len(encode([point], precision, geojson)) for point in coordinates]
    prefix = [0] * length
    prefix[0] = head[0]
    for index in range(1, length):
        pair = len(encode([coordinates[index - 1], coordinates[index]], precision, geojson))
        prefix[index] = prefix[index - 1] + pair - head[index - 1]

    def cost(first, last):
        return head[first] + prefix[last] - prefix[first]

    # dp[p][j] = (longest shard, total length) of the best split of points 0..j into
    # p shards; choice[p][j] = index of the last cut of that split.
    dp = [[None] * length for _ in range(parts + 1)]
    choice = [[None] * length for _ in range(parts + 1)]

    for last in range(length):
        dp[1][last] = (cost(0, last), cost(0, last))

    for part in range(2, parts + 1):
        for last in range(part, length):
            best, best_cut = None, None
            for cut in range(part - 1, last):
                prev = dp[part - 1][cut]
                if prev is None or cut not in allowed:
                    continue
                shard = cost(cut, last)
                candidate = (max(prev[0], shard), prev[1] + shard)
                if best is None or candidate < best:
                    best, best_cut = candidate, cut
            dp[part][last] = best
            choice[part][last] = best_cut

    if dp[parts][length - 1] is None:
        raise ValueError(
            f'cannot split the route into {parts} parts with the given cut constraints')

    cuts = []
    last, part = length - 1, parts
    while part > 1:
        cut = choice[part][last]
        cuts.append(cut)
        last, part = cut, part - 1
    cuts.reverse()

    shards, start = [], 0
    for cut in cuts:
        shards.append(encode(coordinates[start:cut + 1], precision, geojson))
        start = cut
    shards.append(encode(coordinates[start:], precision, geojson))

    return shards


def join(expressions: List[str], precision: int = 5, geojson: bool = False) -> List[Tuple[float, float]]:
    """
    Join shards produced by :func:`split` back into a single list of coordinates.

    The junction point duplicated across adjacent shards is removed exactly once; points
    that were genuinely repeated in the original route are preserved. The duplicated
    junctions are verified and a mismatch raises a ValueError.

    :param expressions: List of encoded polyline strings, in original route order.
    :param precision: Precision of the encoded coordinates. The default value is 5.
    :param geojson: Set output of tuples to (lon, lat).
    :return: List of coordinate tuples in (lat, lon) order, unless geojson is set to True.
    """
    coordinates = []

    for index, expression in enumerate(expressions):
        points = decode(expression, precision, geojson)
        if not points:
            raise ValueError(f'shard {index} is empty')
        if index > 0:
            if coordinates[-1] != points[0]:
                raise ValueError(
                    f'shard {index} does not start at the previous shard\'s last point')
            points = points[1:]
        coordinates.extend(points)

    return coordinates
