from random import uniform, randint
import time

import polyline
from polyline import Checkpoint

import pytest


AUDIT_TRACK = [
    (40.641, -8.654),
    (40.642, -8.655),
    (40.643, -8.654),
    (40.644, -8.656),
    (40.645, -8.655),
    (40.646, -8.657),
    (40.647, -8.656),
    (40.648, -8.658)
]
AUDIT_EXPRESSION = polyline.encode(AUDIT_TRACK)
# Per-point end offsets in AUDIT_EXPRESSION.
AUDIT_OFFSETS = [10, 14, 18, 22, 26, 30, 34, 38]
# Corruption of the first char of point 3 ('g' -> '_' at offset 18): every
# subsequent absolute position decoded naively is shifted.
AUDIT_CORRUPTED = AUDIT_EXPRESSION[:18] + '_' + AUDIT_EXPRESSION[19:]


def _checkpoints(*indices):
    return [Checkpoint(offset=AUDIT_OFFSETS[i], index=i, coord=AUDIT_TRACK[i])
            for i in indices]


def test_audit_clean_track_fully_confirmed():
    report = polyline.audit(AUDIT_EXPRESSION, _checkpoints(2, 5, 7))
    assert [s.status for s in report.segments] == [
        polyline.CONFIRMED, polyline.CONFIRMED, polyline.CONFIRMED
    ]
    assert report.track == AUDIT_TRACK


def test_audit_corruption_marks_single_suspicious_segment():
    report = polyline.audit(AUDIT_CORRUPTED, _checkpoints(1, 4, 7))
    statuses = [(s.status, s.start_index, s.end_index) for s in report.segments]
    assert statuses == [
        (polyline.CONFIRMED, 0, 1),
        (polyline.SUSPICIOUS, 2, 4),
        (polyline.CONFIRMED, 5, 7)
    ]
    # The suspicious segment carries no positions.
    assert report.segments[1].coordinates is None
    # Points before and after the corrupted stretch stay ingestible.
    assert report.track == AUDIT_TRACK[:2] + [None] * 3 + AUDIT_TRACK[5:]


def test_audit_naive_decode_drifts_but_audit_resyncs():
    # Plain decoding of the corrupted string still yields numbers,
    # but every position after the corruption is off.
    assert polyline.decode(AUDIT_CORRUPTED) != AUDIT_TRACK
    report = polyline.audit(AUDIT_CORRUPTED, _checkpoints(1, 4, 7))
    # Decoding resumes from each checkpoint, so the tail lines up again.
    assert report.segments[2].coordinates == AUDIT_TRACK[5:]


def test_audit_restartable_suffix_after_last_checkpoint():
    report = polyline.audit(AUDIT_CORRUPTED, _checkpoints(1, 3))
    statuses = [(s.status, s.start_index, s.end_index) for s in report.segments]
    assert statuses == [
        (polyline.CONFIRMED, 0, 1),
        (polyline.SUSPICIOUS, 2, 3),
        (polyline.RESTARTABLE, 4, 7)
    ]
    assert report.segments[2].coordinates == AUDIT_TRACK[4:]
    assert report.track == AUDIT_TRACK[:2] + [None] * 2 + AUDIT_TRACK[4:]


def test_audit_without_checkpoints_is_restartable_from_origin():
    report = polyline.audit(AUDIT_EXPRESSION, [])
    assert len(report.segments) == 1
    segment = report.segments[0]
    assert segment.status == polyline.RESTARTABLE
    assert (segment.start_index, segment.end_index) == (0, len(AUDIT_TRACK) - 1)
    assert report.track == AUDIT_TRACK


def test_audit_geojson():
    checkpoints = [Checkpoint(offset=AUDIT_OFFSETS[3], index=3,
                              coord=(-8.656, 40.644))]
    report = polyline.audit(AUDIT_EXPRESSION, checkpoints, geojson=True)
    assert report.segments[0].status == polyline.CONFIRMED
    assert report.track[0] == (-8.654, 40.641)
    assert report.track[3] == (-8.656, 40.644)


def test_audit_checkpoint_offset_must_match_point_boundary():
    with pytest.raises(ValueError):
        polyline.audit(AUDIT_EXPRESSION,
                       [Checkpoint(offset=3, index=1, coord=AUDIT_TRACK[1])])


def test_audit_checkpoint_index_out_of_range():
    with pytest.raises(ValueError):
        polyline.audit(AUDIT_EXPRESSION,
                       [Checkpoint(offset=99, index=99, coord=(0, 0))])


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
