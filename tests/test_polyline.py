from itertools import combinations
from random import uniform, randint
import time

import pytest

import polyline


def test_decode_multiple_points():
    d = polyline.decode('gu`wFnfys@???nKgE??gE?????oK????fE??fE')
    assert d == [
        (40.641, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ]


def test_decode_multiple_points_precision():
    d = polyline.decode('_epolA~ieoOnF??~{Bo}@??o}@?????_|B????n}@??n}@', 6)
    assert d == [
        (40.64112, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ]


def test_decode_official_example():
    d = polyline.decode('_p~iF~ps|U_ulLnnqC_mqNvxq`@')
    assert d == [
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ]


def test_decode_geojson():
    d = polyline.decode('_p~iF~ps|U_ulLnnqC_mqNvxq`@', geojson=True)
    assert d == [
        (-120.200, 38.500),
        (-120.950, 40.700),
        (-126.453, 43.252)
    ]


def test_decode_official_example_precision():
    d = polyline.decode('_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI', 6)
    assert d == [
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ]


def test_decode_single_point():
    d = polyline.decode('gu`wFf`ys@')
    assert d == [
        (40.641, -8.653)
    ]


def test_decode_single_point_precision():
    d = polyline.decode('o}oolAnkcoO', 6)
    assert d == [
        (40.641, -8.653)
    ]


def test_encode_multiple_points():
    e = polyline.encode([
        (40.641, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ])
    assert e == 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'


def test_encode_multiple_points_precision():
    e = polyline.encode([
        (40.64112345, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ], 6)
    assert e == 'eepolA~ieoOtF??~{Bo}@??o}@?????_|B????n}@??n}@'

def test_encode_official_example():
    e = polyline.encode([
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ])
    assert e == '_p~iF~ps|U_ulLnnqC_mqNvxq`@'


def test_encode_geojson():
    e = polyline.encode([
        (-120.200, 38.500),
        (-120.950, 40.700),
        (-126.453, 43.252)
    ], geojson=True)
    assert e == '_p~iF~ps|U_ulLnnqC_mqNvxq`@'


def test_encode_official_example_precision():
    e = polyline.encode([
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ], 6)
    assert e == '_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI'


def test_encode_single_point():
    e = polyline.encode([
        (40.64155, -8.65344)
    ])
    assert e == 'ux`wF~bys@'

    e = polyline.encode([
        (40.641552, -8.653441)
    ])
    assert e == 'ux`wF~bys@'


def test_encode_empty():
    assert polyline.encode([]) == ''
    assert polyline.encode([], geojson=True) == ''
    assert polyline.decode(polyline.encode([])) == []


def test_encode_single_point_rounding():
    e = polyline.encode([
        (0, 0.000006),
        (0, 0.000002)
    ])
    assert e == '?A?@'


def test_rounding_py3_match_py2():
    e = polyline.encode([
        (36.05322, -112.084004),
        (36.053573, -112.083914),
        (36.053845, -112.083965)])
    assert e == 'ss`{E~kbkTeAQw@J'


def test_encode_single_point_precision():
    e = polyline.encode([
        (40.641123, -8.653321)
    ], 6)
    assert e == 'eepolAp_doO'

    e = polyline.encode([
        (40.6411233123, -8.6533214234)
    ], 6)
    assert e == 'eepolAp_doO'


def test_a_variety_of_precisions():
    """uses a generator to create a variety of lat-lon's across the global
        and tests a range of precision settings from 4 to 8"""

    def generator():
        while True:
            coords = []
            for i in range(2, randint(4, 10)):
                lat, lon = uniform(-180.0, 180.0), uniform(-180.0, 180.0)
                coords.append((lat, lon))
            yield coords

    patience = 3  # seconds.
    waypoints, okays = 0, 0

    g = generator()
    start = time.time()
    while time.time() < start + patience:
        precision = randint(4, 8)
        wp = next(g)
        waypoints += len(wp)
        poly = polyline.encode(wp, precision)
        wp2 = polyline.decode(poly, precision)
        if wp == wp2:
            okays += len(wp2)
        else:
            for idx, _ in enumerate(wp):
                dx, dy = abs(wp[idx][0] - wp2[idx][0]), abs(wp[idx][1] - wp2[idx][1])
                if dx > 10 ** -(precision - 1) or dy > 10 ** -(precision - 1):
                    print(f"idx={idx}, dx={dx}, dy={dy}")
                else:
                    okays += 1

    assert okays == waypoints
    print(
        f"encoded and decoded {100 * okays / float(waypoints):.2f}% correctly for {waypoints} "
        f"waypoints @ {round(waypoints / patience, 0)} wp/sec")


def _brute_force_best(coords, parts, allowed):
    """Exhaustively compute the optimal (longest shard, total length) split."""
    best = None
    for cuts in combinations(range(1, len(coords) - 1), parts - 1):
        if not all(cut in allowed for cut in cuts):
            continue
        bounds = [0] + list(cuts) + [len(coords) - 1]
        shards = [polyline.encode(coords[a:b + 1]) for a, b in zip(bounds, bounds[1:])]
        candidate = (max(len(s) for s in shards), sum(len(s) for s in shards))
        if best is None or candidate < best:
            best = candidate
    return best


def test_split_roundtrip_preserves_duplicate_stops():
    coords = [
        (40.641, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ]
    shards = polyline.split(coords, 3)
    assert len(shards) == 3

    decoded = [polyline.decode(shard) for shard in shards]
    # every shard decodes independently from the zero coordinate
    assert decoded[0][0] == coords[0]
    assert decoded[-1][-1] == coords[-1]
    # adjacent shards duplicate their junction point
    for first, second in zip(decoded, decoded[1:]):
        assert first[-1] == second[0]

    # only the artificial junction duplicates are removed on join, the
    # genuinely repeated stops of the original route are preserved
    assert polyline.join(shards) == coords
    assert polyline.encode(polyline.join(shards)) == polyline.encode(coords)


def test_split_minimizes_longest_shard_then_total_length():
    coords = [(uniform(-1.0, 1.0), uniform(-1.0, 1.0)) for _ in range(4)]
    coords += [(uniform(59.0, 61.0), uniform(59.0, 61.0)) for _ in range(6)]
    coords += [(uniform(-0.001, 0.001), uniform(-0.001, 0.001)) for _ in range(5)]
    coords[0] = (0.0, 0.0)
    canonical = polyline.decode(polyline.encode(coords))

    for parts in (2, 3, 4):
        shards = polyline.split(coords, parts)
        assert len(shards) == parts
        assert polyline.join(shards) == canonical

        longest = max(len(shard) for shard in shards)
        total = sum(len(shard) for shard in shards)
        allowed = set(range(1, len(coords) - 1))
        assert (longest, total) == _brute_force_best(coords, parts, allowed)


def test_split_prefers_smaller_total_on_tie():
    coords = [(0.0, 0.0), (0.00001, 0.0), (1.0, 0.0), (1.00001, 0.0)]
    shards = polyline.split(coords, 2)
    # both cuts give a longest shard of the same length, the cut with the
    # smaller total number of characters wins
    assert shards == [polyline.encode(coords[:2]), polyline.encode(coords[1:])]
    assert polyline.join(shards) == coords


def test_split_only_cuts_at_stops():
    coords = [(0.00001 * i, 0.0) for i in range(6)]
    shards = polyline.split(coords, 2, stops=[2])
    assert shards == [polyline.encode(coords[:3]), polyline.encode(coords[2:])]
    assert polyline.join(shards) == polyline.decode(polyline.encode(coords))


def test_split_never_cuts_inside_tunnels():
    coords = [(0.00001 * i, 0.0) for i in range(7)]
    shards = polyline.split(coords, 2, tunnels=[(1, 5)])
    first = polyline.decode(shards[0])
    # cuts strictly inside the tunnel are forbidden, only the portals remain
    canonical = polyline.decode(polyline.encode(coords))
    assert first in (canonical[:2], canonical[:6])
    assert polyline.join(shards) == canonical


def test_split_geojson_roundtrip():
    coords = [(-8.654, 40.641), (-8.656, 40.641), (-8.656, 40.642), (-8.653, 40.642)]
    shards = polyline.split(coords, 2, geojson=True)
    assert polyline.join(shards, geojson=True) == coords


def test_split_single_part():
    coords = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert polyline.split(coords, 1) == [polyline.encode(coords)]


def test_split_errors():
    coords = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    with pytest.raises(ValueError):
        polyline.split(coords, 0)
    with pytest.raises(ValueError):
        polyline.split([], 2)
    with pytest.raises(ValueError):
        # not enough cut candidates for 3 parts
        polyline.split(coords, 3)
    with pytest.raises(ValueError):
        # every candidate cut lies inside a tunnel
        polyline.split(coords * 3, 2, tunnels=[(0, 8)])


def test_join_verifies_junctions():
    shards = polyline.split([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)], 2)
    assert polyline.join(shards) == [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]

    with pytest.raises(ValueError):
        polyline.join([shards[0], polyline.encode([(9.0, 9.0), (10.0, 10.0)])])
    with pytest.raises(ValueError):
        polyline.join([shards[0], ''])
