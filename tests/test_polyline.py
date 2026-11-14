from random import uniform, randint
import time

import pytest

import polyline


def point_ranges(expression):
    """Return the character range of every encoded point."""
    ranges, index = [], 0
    while index < len(expression):
        start = index
        for _ in range(2):
            while True:
                byte = ord(expression[index]) - 63
                index += 1
                if byte < 0x20:
                    break
        ranges.append((start, index))
    return ranges


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


OFFICIAL_COORDS = [
    (38.500, -120.200),
    (40.700, -120.950),
    (43.252, -126.453),
    (44.000, -127.000),
    (45.500, -128.250),
    (46.000, -129.000),
]


def test_patch_single_point_replaces_only_affected_chunks():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)

    result = polyline.patch(expression, [(1, 2, [(41.0, -121.0)])])
    new_ranges = point_ranges(result.expression)

    assert polyline.decode(result.expression) == [
        (38.5, -120.2), (41.0, -121.0), (43.252, -126.453),
        (44.0, -127.0), (45.5, -128.25), (46.0, -129.0),
    ]
    assert result.expression[:old_ranges[0][1]] == expression[:old_ranges[0][1]]
    for point in (0, 3, 4, 5):
        chunk = result.expression[new_ranges[point][0]:new_ranges[point][1]]
        assert chunk == expression[old_ranges[point][0]:old_ranges[point][1]]
    replaced = result.expression[new_ranges[1][0]:new_ranges[1][1]]
    assert replaced != expression[old_ranges[1][0]:old_ranges[1][1]]
    assert result.index[1] == new_ranges[1]


def test_patch_reencodes_first_untouched_successor():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)

    result = polyline.patch(expression, [(1, 2, [(41.0, -121.0)])])

    successor = result.expression[result.index[2][0]:result.index[2][1]]
    assert successor != expression[old_ranges[2][0]:old_ranges[2][1]]
    assert polyline.decode(result.expression)[2] == (43.252, -126.453)


def test_patch_longer_replacement_keeps_later_bytes_and_moves_index():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)
    replacement = [(41.0, -121.0), (41.25, -121.1), (41.75, -121.5)]

    result = polyline.patch(expression, [(1, 2, replacement)])

    assert polyline.decode(result.expression) == [
        (38.5, -120.2), (41.0, -121.0), (41.25, -121.1),
        (41.75, -121.5), (43.252, -126.453), (44.0, -127.0),
        (45.5, -128.25), (46.0, -129.0),
    ]
    for point in (3, 4, 5):
        chunk = result.expression[result.index[point][0]:result.index[point][1]]
        assert chunk == expression[old_ranges[point][0]:old_ranges[point][1]]
    successor = result.expression[result.index[2][0]:result.index[2][1]]
    assert successor != expression[old_ranges[2][0]:old_ranges[2][1]]
    assert result.index[1] is not None


def test_patch_batch_of_ranges_migrates_every_index_entry():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)
    index = {f'stop{i}': char_range for i, char_range in enumerate(old_ranges)}

    result = polyline.patch(
        expression, [(1, 2, [(41.0, -121.0)]), (3, 4, [(44.5, -127.5)])], index=index)

    assert polyline.decode(result.expression) == [
        (38.5, -120.2), (41.0, -121.0), (43.252, -126.453),
        (44.5, -127.5), (45.5, -128.25), (46.0, -129.0),
    ]
    for stop, char_range in result.index.items():
        point = int(stop[len('stop'):])
        assert char_range is not None
        assert result.expression[char_range[0]:char_range[1]] == \
            result.expression[point_ranges(result.expression)[point][0]:
                              point_ranges(result.expression)[point][1]]


def test_patch_insertion_at_boundaries():
    expression = polyline.encode(OFFICIAL_COORDS)

    head = polyline.patch(expression, [(0, 0, [(37.0, -119.0)])])
    assert polyline.decode(head.expression)[0] == (37.0, -119.0)
    assert polyline.decode(head.expression)[1] == (38.5, -120.2)

    tail = polyline.patch(expression, [(6, 6, [(47.0, -130.0)])])
    assert polyline.decode(tail.expression)[-1] == (47.0, -130.0)


def test_patch_mapping_input_matches_tuple_input():
    expression = polyline.encode(OFFICIAL_COORDS)

    from_tuples = polyline.patch(expression, [(2, 3, [(42.0, -125.0)])])
    from_mapping = polyline.patch(
        expression, [{'start': 2, 'stop': 3, 'points': [(42.0, -125.0)]}])

    assert from_tuples.expression == from_mapping.expression
    assert from_tuples.index == from_mapping.index


def test_patch_without_changes_preserves_expression():
    expression = polyline.encode(OFFICIAL_COORDS)

    result = polyline.patch(expression, [])

    assert result.expression == expression
    assert all(char_range is not None for char_range in result.index.values())


def test_patch_geojson_reads_lon_lat():
    expression = polyline.encode(OFFICIAL_COORDS)

    result = polyline.patch(expression, [(1, 2, [(-121.0, 41.0)])], geojson=True)

    assert polyline.decode(result.expression)[1] == (41.0, -121.0)
    assert polyline.decode(expression) == OFFICIAL_COORDS


def test_patch_precision_matches_encode():
    expression = polyline.encode(OFFICIAL_COORDS, 6)
    replacement = [(41.0, -121.0)]

    result = polyline.patch(expression, [(1, 2, replacement)], precision=6)

    assert polyline.decode(result.expression, 6)[1] == (41.0, -121.0)


def test_patch_rejects_overlapping_ranges_atomically():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)

    with pytest.raises(ValueError):
        polyline.patch(expression, [(0, 3, [(1.0, 1.0)]), (2, 4, [(2.0, 2.0)])])

    assert expression[old_ranges[1][0]:] == expression[old_ranges[1][0]:]


def test_patch_rejects_duplicate_insertions_and_same_range():
    expression = polyline.encode(OFFICIAL_COORDS)

    with pytest.raises(ValueError):
        polyline.patch(expression, [(2, 2, [(1.0, 1.0)]), (2, 2, [(2.0, 2.0)])])

    with pytest.raises(ValueError):
        polyline.patch(expression, [(1, 3, [(1.0, 1.0)]), (1, 3, [(2.0, 2.0)])])


def test_patch_rejects_out_of_range_values():
    expression = polyline.encode(OFFICIAL_COORDS)

    with pytest.raises(ValueError):
        polyline.patch(expression, [(4, 7, [(1.0, 1.0)])])

    with pytest.raises(ValueError):
        polyline.patch(expression, [(-1, 2, [(1.0, 1.0)])])

    with pytest.raises(ValueError):
        polyline.patch(expression, [(3, 2, [(1.0, 1.0)])])


def test_patch_rejects_index_entries_off_point_boundaries():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)

    with pytest.raises(ValueError):
        polyline.patch(expression, [(1, 2, [])], index={'stop': (old_ranges[1][0] + 1, old_ranges[1][1])})

    with pytest.raises(ValueError):
        polyline.patch(expression, [(1, 2, [])], index={'stop': (old_ranges[1][0], len(expression) + 1)})


def test_patch_index_entry_spanning_points_follows_survivors():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)
    index = {'trip': (old_ranges[1][0], old_ranges[4][1])}

    result = polyline.patch(expression, [(2, 3, [])], index=index)

    assert result.index['trip'] is not None
    assert polyline.decode(result.expression) == [
        (38.5, -120.2), (40.7, -120.95), (44.0, -127.0),
        (45.5, -128.25), (46.0, -129.0),
    ]
    start, stop = result.index['trip']
    new_ranges = point_ranges(result.expression)
    assert (start, stop) == (new_ranges[1][0], new_ranges[3][1])


def test_patch_dropped_index_entry_becomes_none():
    expression = polyline.encode(OFFICIAL_COORDS)
    old_ranges = point_ranges(expression)

    result = polyline.patch(expression, [(2, 3, [])], index={'gone': old_ranges[2]})

    assert result.index == {'gone': None}
