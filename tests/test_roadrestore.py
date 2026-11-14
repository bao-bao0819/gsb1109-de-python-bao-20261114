"""道路中心线还原的测试。"""

import json
import random

import pytest

from roadrestore.__main__ import main
from roadrestore import (
    result_as_dict,
    result_as_geojson,
    restore_from_features,
    restore_routes,
    segment_from_feature,
)
from roadrestore.restore import _shift_blocked


def make_feature(seg_id, road, m_start, m_end, coords):
    return {
        "type": "Feature",
        "properties": {
            "id": seg_id,
            "road": road,
            "m_start": m_start,
            "m_end": m_end,
        },
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def chain_features():
    """三条首尾相接、里程连续的线段。"""
    return [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        make_feature("b", "G101", 10, 20, [(1, 0), (2, 0)]),
        make_feature("c", "G101", 20, 30, [(2, 0), (3, 0)]),
    ]


def test_basic_chain_merges_in_mileage_order():
    result = restore_from_features(chain_features())
    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.seg_ids == ["a", "b", "c"]
    assert (route.m_start, route.m_end) == (0, 30)
    assert route.coordinates == [(0, 0), (1, 0), (2, 0), (3, 0)]
    assert result.gaps == []
    assert result.manual_review == []
    assert route.route_id == "R0001"


def test_reversed_segment_is_normalized():
    features = chain_features()
    # c 的几何与里程都是反的。
    features[2]["geometry"]["coordinates"] = [(3, 0), (2, 0)]
    features[2]["properties"]["m_start"], features[2]["properties"]["m_end"] = 30, 20

    segments = [segment_from_feature(f, i) for i, f in enumerate(features)]
    assert segments[2].reversed_geom is True
    assert segments[2].m_start == 20 and segments[2].m_end == 30
    assert segments[2].coords == ((2, 0), (3, 0))

    result = restore_routes(segments)
    assert len(result.routes) == 1
    assert result.routes[0].seg_ids == ["a", "b", "c"]


def test_input_order_does_not_change_numbering_or_review():
    features = chain_features()
    baseline = result_as_dict(restore_from_features(features))

    shuffled = features[:]
    random.Random(42).shuffle(shuffled)
    shuffled_result = result_as_dict(restore_from_features(shuffled))
    assert shuffled_result == baseline

    # 再换几种顺序，结果仍完全一致。
    for seed in range(5):
        order = features[:]
        random.Random(seed).shuffle(order)
        assert result_as_dict(restore_from_features(order)) == baseline


def test_mileage_gap_is_reported_not_merged():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        # 几何上相距 1，里程上缺 10~20。
        make_feature("b", "G101", 20, 30, [(2, 0), (3, 0)]),
    ]
    result = restore_from_features(features, endpoint_tolerance=0.0)
    assert [r.seg_ids for r in result.routes] == [["a"], ["b"]]
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert (gap.m_after, gap.m_before, gap.gap_mileage) == (10, 20, 10)
    assert gap.from_route == "R0001" and gap.to_route == "R0002"


def test_internal_mileage_jump_inside_merged_route_is_gap():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        # 几何完全相接，但里程从 10 跳到 15。
        make_feature("b", "G101", 15, 25, [(1, 0), (2, 0)]),
    ]
    result = restore_from_features(features)
    assert len(result.routes) == 1
    assert result.routes[0].seg_ids == ["a", "b"]
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert (gap.m_after, gap.m_before, gap.gap_mileage) == (10, 15, 5)
    # 缺口位于同一条已还原线路内部。
    assert gap.from_route == gap.to_route == "R0001"


def test_duplicate_segment_id_raises():
    features = chain_features()
    features[1]["properties"]["id"] = "a"
    with pytest.raises(ValueError, match="重复"):
        restore_from_features(features)


def test_endpoint_tolerance_controls_merge():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        make_feature("b", "G101", 10, 20, [(1.5, 0), (2.5, 0)]),
    ]
    # 端点相距 0.5：容差内合并，容差外不合并。
    merged = restore_from_features(features, endpoint_tolerance=0.5)
    assert len(merged.routes) == 1

    separated = restore_from_features(features, endpoint_tolerance=0.4)
    assert len(separated.routes) == 2


def test_mileage_conflict_is_never_force_joined():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        # 端点完全重合，但 b 的里程回退到 5，方向冲突。
        make_feature("b", "G101", 5, 15, [(1, 0), (2, 0)]),
    ]
    result = restore_from_features(features, endpoint_tolerance=1.0)
    assert [r.seg_ids for r in result.routes] == [["a"], ["b"]]
    assert len(result.manual_review) == 1
    item = result.manual_review[0]
    assert item.kind == "mileage_conflict"
    assert item.seg_ids == ("a", "b")
    assert item.route_id == "R0001"


def test_ambiguous_fork_merges_only_up_to_junction():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        make_feature("b", "G101", 10, 20, [(1, 0), (2, 0)]),
        # c 与 b 从同一岔口 (1,0) 出发，里程同样合理。
        make_feature("c", "G101", 10, 20, [(1, 0), (1, 1)]),
    ]
    result = restore_from_features(features)
    assert sorted(r.seg_ids for r in result.routes) == [
        ["a"], ["b"], ["c"]]
    forks = [i for i in result.manual_review
             if i.kind == "ambiguous_fork"]
    assert len(forks) == 1
    assert forks[0].seg_ids == ("a", "b", "c")
    # 岔口处里程恰好相接，不应再报缺口。
    assert result.gaps == []


def test_fork_result_is_stable_under_shuffle():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        make_feature("b", "G101", 10, 20, [(1, 0), (2, 0)]),
        make_feature("c", "G101", 10, 20, [(1, 0), (1, 1)]),
    ]
    baseline = result_as_dict(restore_from_features(features))
    for seed in range(6):
        order = features[:]
        random.Random(seed).shuffle(order)
        assert result_as_dict(restore_from_features(order)) == baseline


def test_two_candidates_with_different_distances_are_not_guessed():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        make_feature("b", "G101", 10, 20, [(1, 0), (2, 0)]),
        # c 的起点距 a 末端 0.5，虽比 b 远但仍在容差内，不能据此二选一。
        make_feature("c", "G101", 10, 20, [(1.5, 0), (2.5, 0)]),
    ]
    result = restore_from_features(features, endpoint_tolerance=1.0)
    assert [r.seg_ids for r in result.routes] == [["a"], ["b"], ["c"]]
    kinds = {item.kind for item in result.manual_review}
    assert "ambiguous_fork" in kinds
    # b 尾端与 c 头端在容差内而里程回退，同样不硬接。
    conflict_pairs = {item.seg_ids for item in result.manual_review
                      if item.kind == "mileage_conflict"}
    assert ("b", "c") in conflict_pairs


def test_fork_blocking_does_not_stop_independent_merges():
    features = [
        make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)]),
        make_feature("b", "G101", 10, 20, [(1, 0), (2, 0)]),
        make_feature("c", "G101", 10, 20, [(1, 0), (1, 1)]),
        # 与岔口无关的另一条链，应照常合并。
        make_feature("d", "G101", 30, 40, [(5, 0), (6, 0)]),
        make_feature("e", "G101", 40, 50, [(6, 0), (7, 0)]),
    ]
    result = restore_from_features(features)
    assert sorted(r.seg_ids for r in result.routes) == [
        ["a"], ["b"], ["c"], ["d", "e"]]
    assert len([i for i in result.manual_review
                if i.kind == "ambiguous_fork"]) == 1


def test_roads_are_grouped_independently():
    features = [
        make_feature("x1", "G202", 0, 5, [(0, 0), (1, 0)]),
        make_feature("y1", "G101", 0, 5, [(0, 9), (1, 9)]),
        make_feature("x2", "G202", 5, 10, [(1, 0), (2, 0)]),
        make_feature("y2", "G101", 5, 10, [(1, 9), (2, 9)]),
    ]
    result = restore_from_features(features)
    assert len(result.routes) == 2
    # 编号按道路号排序：G101 在前。
    assert result.routes[0].road == "G101"
    assert result.routes[0].seg_ids == ["y1", "y2"]
    assert result.routes[1].road == "G202"
    assert result.routes[1].seg_ids == ["x1", "x2"]
    assert [r.route_id for r in result.routes] == ["R0001", "R0002"]


def test_serialization_structures_are_json_ready():
    result = restore_from_features(chain_features())
    plain = result_as_dict(result)
    json.dumps(plain, ensure_ascii=False)
    assert plain["routes"][0]["segment_ids"] == ["a", "b", "c"]

    fc = result_as_geojson(result)
    json.dumps(fc, ensure_ascii=False)
    assert fc["type"] == "FeatureCollection"
    assert fc["features"][0]["geometry"]["type"] == "LineString"
    assert "gaps" in fc["properties"]


def test_missing_mileage_raises():
    feature = make_feature("a", "G101", 0, 10, [(0, 0), (1, 0)])
    del feature["properties"]["m_end"]
    with pytest.raises(ValueError):
        segment_from_feature(feature, 0)


def test_non_linestring_raises():
    feature = {
        "type": "Feature",
        "properties": {"id": "p", "m_start": 0, "m_end": 1},
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }
    with pytest.raises(ValueError):
        segment_from_feature(feature, 0)


def test_negative_tolerance_raises():
    with pytest.raises(ValueError):
        restore_from_features(chain_features(), endpoint_tolerance=-1.0)


def test_shift_blocked_when_tail_chain_comes_first():
    blocked = {(0, "head"), (1, "tail"), (2, "head")}
    shifted = _shift_blocked(blocked, tail_idx=0, head_idx=1)
    # 合并链留在 0：尾链头端阻塞保留，被并链尾端成为新尾端，
    # 原下标 2 的链删除头链后前移到 1。
    assert shifted == {(0, "head"), (0, "tail"), (1, "head")}


def test_shift_blocked_when_head_chain_comes_first():
    blocked = {(0, "tail"), (1, "head"), (2, "tail")}
    shifted = _shift_blocked(blocked, tail_idx=1, head_idx=0)
    # 删除下标 0 后合并链落在 0：头链尾端阻塞迁为新链尾端阻塞，
    # 尾链头端阻塞迁为新链头端阻塞；原下标 2 的链前移到 1。
    assert shifted == {(0, "head"), (0, "tail"), (1, "tail")}


def test_cli_report_and_geojson_output(tmp_path, capsys):
    collection = {"type": "FeatureCollection",
                  "features": chain_features()}
    input_path = tmp_path / "lines.geojson"
    input_path.write_text(json.dumps(collection), encoding="utf-8")

    assert main([str(input_path), "-t", "0.1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["routes"][0]["segment_ids"] == ["a", "b", "c"]

    output_path = tmp_path / "out.geojson"
    assert main([str(input_path), "--format", "geojson",
                 "-o", str(output_path)]) == 0
    fc = json.loads(output_path.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection"
    assert fc["features"][0]["geometry"]["coordinates"] == [
        [0, 0], [1, 0], [2, 0], [3, 0]]
