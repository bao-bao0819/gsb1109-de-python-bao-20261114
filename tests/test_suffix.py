import pytest

from polyline import (
    MakeupPackage,
    ReceiveStatus,
    TripArchive,
    decode_suffix,
    encode_suffix,
    extend_identity,
    prefix_identity,
)

TRIP = 'trip-42'
BASE = [(0.0, 0.0), (1.0, 1.0)]


def make_archive():
    archive = TripArchive(TRIP)
    archive.load_base(BASE)
    return archive


def make_package(points, anchor=BASE[-1], prefix_points=BASE, start_seq=1, **kwargs):
    return MakeupPackage(
        trip_id=TRIP,
        prefix_id=prefix_identity(TRIP, prefix_points),
        start_seq=start_seq,
        suffix=encode_suffix(anchor, points),
        **kwargs,
    )


def test_suffix_roundtrip():
    anchor = (38.5, -120.2)
    points = [(40.7, -120.95), (43.252, -126.453)]
    expression = encode_suffix(anchor, points)
    assert expression == '_ulLnnqC_mqNvxq`@'
    assert decode_suffix(anchor, expression) == points


def test_suffix_roundtrip_geojson():
    anchor = (-120.2, 38.5)
    points = [(-120.95, 40.7), (-126.453, 43.252)]
    expression = encode_suffix(anchor, points, geojson=True)
    assert decode_suffix(anchor, expression, geojson=True) == points


def test_suffix_empty():
    assert encode_suffix((1.0, 1.0), []) == ''
    assert decode_suffix((1.0, 1.0), '') == []


def test_append_when_prefix_matches():
    archive = make_archive()
    package = make_package([(2.0, 2.0), (3.0, 3.0)], package_id='p1')
    result = archive.receive(package)
    assert result.status == ReceiveStatus.APPLIED
    assert result.applied == 2
    assert archive.points == BASE + [(2.0, 2.0), (3.0, 3.0)]
    assert archive.head_prefix_id == extend_identity(prefix_identity(TRIP, BASE),
                                                     [(2.0, 2.0), (3.0, 3.0)])


def test_same_endpoint_different_prefix_not_concatenated():
    revision_a = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
    revision_b = [(0.0, 0.0), (1.5, 1.5), (2.0, 2.0)]
    assert revision_a[-1] == revision_b[-1]
    assert prefix_identity(TRIP, revision_a) != prefix_identity(TRIP, revision_b)

    archive = TripArchive(TRIP)
    archive.load_base(revision_a)
    package = MakeupPackage(
        trip_id=TRIP,
        prefix_id=prefix_identity(TRIP, revision_b),
        start_seq=2,
        suffix=encode_suffix((2.0, 2.0), [(3.0, 3.0)]),
    )
    result = archive.receive(package)
    assert result.status == ReceiveStatus.REJECTED
    assert archive.points == revision_a


def test_out_of_order_suffix_staged_until_prerequisite_arrives():
    archive = make_archive()
    first_points = [(2.0, 2.0), (3.0, 3.0)]
    second_points = [(4.0, 4.0)]
    first = make_package(first_points, package_id='p1')
    second = MakeupPackage(
        trip_id=TRIP,
        prefix_id=extend_identity(prefix_identity(TRIP, BASE), first_points),
        start_seq=3,
        suffix=encode_suffix(first_points[-1], second_points),
        package_id='p2',
    )

    result = archive.receive(second)
    assert result.status == ReceiveStatus.STAGED
    assert archive.points == BASE
    assert len(archive.pending) == 1

    result = archive.receive(first)
    assert result.status == ReceiveStatus.APPLIED
    assert result.applied == 3
    assert archive.points == BASE + first_points + second_points
    assert archive.pending == {}


def test_suffix_staged_until_base_arrives():
    archive = TripArchive(TRIP)
    package = make_package([(2.0, 2.0)], package_id='p1')
    result = archive.receive(package)
    assert result.status == ReceiveStatus.STAGED

    archive.load_base(BASE)
    assert archive.points == BASE + [(2.0, 2.0)]
    assert archive.pending == {}


def test_replay_only_adds_receipt():
    archive = make_archive()
    package = make_package([(2.0, 2.0)], package_id='p1')
    assert archive.receive(package).status == ReceiveStatus.APPLIED

    result = archive.receive(package)
    assert result.status == ReceiveStatus.DUPLICATE
    assert result.applied == 0
    assert archive.points == BASE + [(2.0, 2.0)]
    assert len(archive.receipts) == 2
    assert [receipt.number for receipt in archive.receipts] == [1, 2]

    replayed_content = make_package([(2.0, 2.0)], package_id='p1-new-id')
    assert archive.receive(replayed_content).status == ReceiveStatus.DUPLICATE
    assert len(archive.receipts) == 3


def test_conflicting_suffix_kept_as_branch():
    archive = make_archive()
    first = make_package([(2.0, 2.0)], package_id='p1')
    assert archive.receive(first).status == ReceiveStatus.APPLIED

    conflicting = make_package([(9.0, 9.0)], package_id='p2')
    result = archive.receive(conflicting)
    assert result.status == ReceiveStatus.BRANCHED
    assert archive.points == BASE + [(2.0, 2.0)]

    assert len(archive.branches) == 1
    branch = archive.branches[0]
    assert branch.prefix_id == conflicting.prefix_id
    assert branch.start_seq == 1
    assert branch.decoded == [(9.0, 9.0)]


def test_conflicting_staged_suffixes_branch_on_activation():
    archive = TripArchive(TRIP)
    first = make_package([(2.0, 2.0)], package_id='p1')
    second = make_package([(9.0, 9.0)], package_id='p2')
    assert archive.receive(first).status == ReceiveStatus.STAGED
    assert archive.receive(second).status == ReceiveStatus.STAGED

    archive.load_base(BASE)
    assert archive.points == BASE + [(2.0, 2.0)]
    assert len(archive.branches) == 1
    assert archive.branches[0].decoded == [(9.0, 9.0)]


def test_precision_mismatch_rejected():
    archive = make_archive()
    package = make_package([(2.0, 2.0)], precision=6)
    result = archive.receive(package)
    assert result.status == ReceiveStatus.REJECTED
    assert archive.points == BASE
    assert len(archive.rejections) == 1


def test_foreign_trip_rejected():
    archive = make_archive()
    package = MakeupPackage(
        trip_id='trip-99',
        prefix_id=prefix_identity(TRIP, BASE),
        start_seq=1,
        suffix=encode_suffix(BASE[-1], [(2.0, 2.0)]),
    )
    assert archive.receive(package).status == ReceiveStatus.REJECTED


def test_load_base_twice_raises():
    archive = make_archive()
    with pytest.raises(ValueError):
        archive.load_base(BASE)
