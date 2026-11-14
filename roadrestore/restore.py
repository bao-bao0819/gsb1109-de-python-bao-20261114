"""按道路号与里程还原被切碎、方向混杂的道路中心线。

输入是若干条线段（LineString），每条带道路号、起止里程。
输出三类结果：

* ``routes``        —— 能确定的连续段（线段的有序合并）；
* ``gaps``          —— 同一道路上里程对不上的缺口；
* ``manual_review`` —— 里程方向冲突或岔口多出路等需人工判断之处。

接线规则：

1. 先把每条线段在内存中翻成里程递增方向，原几何保持不变；
2. 仅当两端点平面距离 <= 容差，且后段里程不小于前段里程时才接续；
3. 两端相近但里程方向冲突（重叠/回退）时不硬接，记入人工判断；
4. 一个岔口有两条同样合理的出路时，主链只合并到岔口之前，
   岔口整体记入人工判断；
5. 一切编号与输出顺序只由数据本身的排序键决定，与输入顺序无关。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

_EPS = 1e-9


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RoadSegment:
    """一条原始中心线线段，方向已规范化为里程递增。

    ``reversed_geom`` 表示保存下来的坐标相对原始几何是否翻转过。
    """

    seg_id: str
    road: str
    m_start: float
    m_end: float
    coords: tuple[tuple[float, float], ...]
    source_index: int
    reversed_geom: bool = False

    @property
    def start_point(self) -> tuple[float, float]:
        return self.coords[0]

    @property
    def end_point(self) -> tuple[float, float]:
        return self.coords[-1]

    @property
    def length_m(self) -> float:
        return self.m_end - self.m_start


@dataclass
class Route:
    """一条已还原的连续线路。"""

    route_id: str
    road: str
    segments: list[RoadSegment] = field(default_factory=list)

    @property
    def m_start(self) -> float:
        return self.segments[0].m_start

    @property
    def m_end(self) -> float:
        return self.segments[-1].m_end

    @property
    def seg_ids(self) -> list[str]:
        return [seg.seg_id for seg in self.segments]

    @property
    def coordinates(self) -> list[tuple[float, float]]:
        """拼接后的坐标串，相邻段共享端点只保留一个。"""
        coords: list[tuple[float, float]] = [self.segments[0].coords[0]]
        for seg in self.segments:
            coords.extend(seg.coords[1:])
        return coords


@dataclass(frozen=True)
class MileageGap:
    """同一道路上两条线路之间的里程缺口。"""

    road: str
    from_route: str
    to_route: str
    m_after: float
    m_before: float

    @property
    def gap_mileage(self) -> float:
        return self.m_before - self.m_after


@dataclass(frozen=True)
class ReviewItem:
    """需要人工判断的位置。

    ``kind`` 取值：

    * ``mileage_conflict`` —— 端点相近但里程方向冲突，不能硬接；
    * ``ambiguous_fork``   —— 岔口存在多条同样合理的出路（或来路）；
    """

    road: str
    kind: str
    route_id: str
    detail: str
    seg_ids: tuple[str, ...] = ()
    at_mileage: float | None = None


@dataclass
class RestoreResult:
    routes: list[Route]
    gaps: list[MileageGap]
    manual_review: list[ReviewItem]


# --------------------------------------------------------------------------- #
# 输入适配
# --------------------------------------------------------------------------- #

def _coord_pair(value: Sequence[float]) -> tuple[float, float]:
    return (float(value[0]), float(value[1]))


def segment_from_feature(feature: dict, source_index: int) -> RoadSegment:
    """从 GeoJSON Feature 构造线段。

    识别的属性名（均可带下划线/大小写变体）：

    * 道路号：``road`` / ``road_id`` / ``road_no`` / ``dlbm`` / ``road_code``；
    * 里程：``m_start``/``m_end``、``start_m``/``end_m``、
      ``mileage_start``/``mileage_end``、``qszh``/``zzzh``。
    """

    props = dict(feature.get("properties") or {})
    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "LineString":
        raise ValueError(f"第 {source_index} 个要素不是 LineString")

    road = _pick(props, ("road", "road_id", "road_no", "road_code", "dlbm"),
                 default=f"ROAD_{source_index}")
    m_start = _pick(props, ("m_start", "start_m", "mileage_start",
                            "start_mileage", "qszh", "start_zh"))
    m_end = _pick(props, ("m_end", "end_m", "mileage_end",
                          "end_mileage", "zzzh", "end_zh"))
    if m_start is None or m_end is None:
        raise ValueError(f"线段 {props.get('id', source_index)} 缺少里程字段")
    m_start, m_end = float(m_start), float(m_end)

    coords = tuple(_coord_pair(c) for c in geometry["coordinates"])
    if len(coords) < 2:
        raise ValueError(f"道路 {road} 的线段至少需要两个坐标点")

    seg_id = str(_pick(props, ("id", "seg_id", "segment_id"),
                       default=f"seg_{source_index}"))
    reversed_geom = False
    if m_start > m_end:
        m_start, m_end = m_end, m_start
        coords = tuple(reversed(coords))
        reversed_geom = True

    return RoadSegment(
        seg_id=seg_id, road=str(road), m_start=m_start, m_end=m_end,
        coords=coords, source_index=source_index,
        reversed_geom=reversed_geom,
    )


def _pick(props: dict, names: Iterable[str], default=None):
    lowered = {str(k).lower(): k for k in props}
    for name in names:
        key = lowered.get(name.lower())
        if key is not None:
            return props[key]
    return default


def segments_from_features(features: Iterable[dict]) -> list[RoadSegment]:
    return [segment_from_feature(f, i) for i, f in enumerate(features)]


# --------------------------------------------------------------------------- #
# 几何与接线
# --------------------------------------------------------------------------- #

def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _seg_sort_key(seg: RoadSegment) -> tuple:
    # 道路号按字符串排；同道路按里程起、止、id 排，保证与输入顺序无关。
    return (seg.road, seg.m_start, seg.m_end, seg.seg_id)


def restore_routes(
    segments: Iterable[RoadSegment],
    endpoint_tolerance: float = 0.0,
) -> RestoreResult:
    """把线段还原为连续线路。

    :param segments: 已规范化的线段集合，顺序任意。
    :param endpoint_tolerance: 端点平面距离容差（与坐标同单位）。
    """

    if endpoint_tolerance < 0:
        raise ValueError("endpoint_tolerance 不能为负数")

    segments = list(segments)
    seen_ids: set[str] = set()
    for seg in segments:
        if seg.seg_id in seen_ids:
            raise ValueError(f"线段 id 重复：{seg.seg_id}")
        seen_ids.add(seg.seg_id)

    ordered = sorted(segments, key=_seg_sort_key)
    by_road: dict[str, list[RoadSegment]] = {}
    for seg in ordered:
        by_road.setdefault(seg.road, []).append(seg)

    routes: list[Route] = []
    review: list[ReviewItem] = []

    for road in sorted(by_road):
        road_routes, road_review = _restore_one_road(
            road, by_road[road], endpoint_tolerance)
        routes.extend(road_routes)
        review.extend(road_review)

    # 线路编号只依赖道路号与里程，重排输入后保持稳定。
    routes.sort(key=lambda r: (r.road, r.m_start, r.m_end,
                               r.segments[0].seg_id))
    for index, route in enumerate(routes, start=1):
        route.route_id = f"R{index:04d}"

    _backfill_route_ids(routes, review)
    gaps = _find_gaps(routes)
    return RestoreResult(routes=routes, gaps=gaps, manual_review=review)


def _backfill_route_ids(routes: list[Route],
                        review: list[ReviewItem]) -> None:
    """冲突/岔口上报于编号之前，此处按涉及线段回填所属线路号。"""

    seg_to_route = {seg.seg_id: route.route_id
                    for route in routes for seg in route.segments}
    for index, item in enumerate(review):
        route_id = seg_to_route.get(item.seg_ids[0], "")
        review[index] = ReviewItem(
            road=item.road, kind=item.kind, route_id=route_id,
            detail=item.detail, seg_ids=item.seg_ids,
            at_mileage=item.at_mileage,
        )
    review.sort(key=lambda item: (
        item.road, item.kind,
        item.at_mileage if item.at_mileage is not None else float("-inf"),
        item.route_id, item.seg_ids))


def _restore_one_road(
    road: str,
    segments: list[RoadSegment],
    tolerance: float,
) -> tuple[list[Route], list[ReviewItem]]:
    """单条道路内部的接线。"""

    # 每条线段先自成一条链，反复尝试把“尾链 + 头链”合并。
    chains: list[list[RoadSegment]] = [[seg] for seg in segments]
    review: list[ReviewItem] = []
    # 已上报的冲突/岔口，按相关线段 id 去重，避免同一问题重复记录。
    reported: set[tuple] = set()
    # 因岔口歧义被阻塞的链端点（链下标, 'head'|'tail'）。
    # 阻塞只影响该端点，不影响同链另一端或其他链继续合并。
    blocked: set[tuple[int, str]] = set()

    while True:
        head, tail = _chain_endpoints(chains)
        decision = _find_best_merge(chains, head, tail, tolerance,
                                    blocked, reported, road, review)
        if decision[0] == "none":
            break
        if decision[0] == "blocked":
            # 岔口端点已阻塞，其余链之间仍可继续合并。
            continue
        _, tail_idx, head_idx = decision
        chains[tail_idx] = chains[tail_idx] + chains[head_idx]
        # 删除链后同步调整阻塞端点的下标。
        blocked = _shift_blocked(blocked, tail_idx, head_idx)
        del chains[head_idx]

    routes = [Route(route_id="", road=road, segments=chain)
              for chain in chains]
    return routes, review


def _shift_blocked(
    blocked: set[tuple[int, str]],
    tail_idx: int,
    head_idx: int,
) -> set[tuple[int, str]]:
    """合并 ``tail_idx + head_idx`` 并删除 ``head_idx`` 后迁移阻塞下标。

    合并链的新下标取决于两条链的相对位置；尾链的头端仍是新链头端，
    被并链的尾端成为新链尾端，两个相接端则变成内部点。
    """

    merged_index = tail_idx if tail_idx < head_idx else tail_idx - 1

    def new_index(idx: int) -> int:
        if idx in (tail_idx, head_idx):
            return merged_index
        return idx if idx < head_idx else idx - 1

    shifted: set[tuple[int, str]] = set()
    for idx, side in blocked:
        if idx == tail_idx:
            if side == "head":
                shifted.add((merged_index, "head"))
            # 尾链的尾端在合并后成为内部点，阻塞自然消失。
        elif idx == head_idx:
            if side == "tail":
                shifted.add((merged_index, "tail"))
        else:
            shifted.add((new_index(idx), side))
    return shifted


def _chain_endpoints(
    chains: list[list[RoadSegment]],
) -> tuple[dict[int, tuple[float, float]], dict[int, tuple[float, float]]]:
    head = {i: chain[0].start_point for i, chain in enumerate(chains)}
    tail = {i: chain[-1].end_point for i, chain in enumerate(chains)}
    return head, tail


def _find_best_merge(
    chains: list[list[RoadSegment]],
    head: dict[int, tuple[float, float]],
    tail: dict[int, tuple[float, float]],
    tolerance: float,
    blocked: set[tuple[int, str]],
    reported: set[tuple],
    road: str,
    review: list[ReviewItem],
) -> tuple:
    """在所有未阻塞的“尾 -> 头”候选里找唯一最优的一次合并。

    返回三元组：

    * ``("none",)``                       —— 没有可确定的合并；
    * ``("blocked",)``                    —— 岔口歧义，相关端点已阻塞；
    * ``("merge", 尾链下标, 头链下标)``   —— 执行这次合并。
    """

    edges: list[tuple[float, float, int, int]] = []

    for tail_idx, tail_chain in enumerate(chains):
        if (tail_idx, "tail") in blocked:
            continue
        tail_seg = tail_chain[-1]
        for head_idx, head_chain in enumerate(chains):
            if tail_idx == head_idx or (head_idx, "head") in blocked:
                continue
            head_seg = head_chain[0]
            dist = _distance(tail[tail_idx], head[head_idx])
            if dist > tolerance:
                continue
            if head_seg.m_start >= tail_seg.m_end:
                # 里程单调递增（恰好相接也允许）：合理接续。
                edges.append((dist, head_seg.m_start, tail_idx, head_idx))
            else:
                # 端点相近但里程重叠/回退：方向冲突，禁止硬接。
                _report_conflict(tail_seg, head_seg, road, reported, review)

    if not edges:
        return ("none",)

    ambiguous = _detect_ambiguity(chains, edges, road, reported,
                                  review, blocked)
    if ambiguous:
        # 岔口/汇入口端点已阻塞，其余链之间仍可继续合并。
        return ("blocked",)

    edges.sort()
    best = edges[0]
    return ("merge", best[2], best[3])


def _detect_ambiguity(
    chains: list[list[RoadSegment]],
    edges: list[tuple[float, float, int, int]],
    road: str,
    reported: set[tuple],
    review: list[ReviewItem],
    blocked: set[tuple[int, str]],
) -> bool:
    """识别岔口（一尾多头）与汇入口（一头多尾）。

    采用保守规则：一个尾端只要存在两个里程合法的候选头端，数据本身
    无法说明谁是正线，即视为岔口歧义，不按距离远近猜测；汇入口对称。
    能进入 ``edges`` 的边都已通过距离容差与里程方向检查。
    """

    found = False

    # 一尾多头：岔口。
    by_tail: dict[int, set[int]] = {}
    for _, _, tail_idx, head_idx in edges:
        by_tail.setdefault(tail_idx, set()).add(head_idx)
    for tail_idx, head_indices in by_tail.items():
        if len(head_indices) < 2:
            continue
        _report_fork(chains, tail_idx, tuple(sorted(head_indices)), road,
                     reported, review)
        blocked.add((tail_idx, "tail"))
        for head_idx in head_indices:
            blocked.add((head_idx, "head"))
        found = True

    # 一头多尾：汇入口。
    by_head: dict[int, set[int]] = {}
    for _, _, tail_idx, head_idx in edges:
        by_head.setdefault(head_idx, set()).add(tail_idx)
    for head_idx, tail_indices in by_head.items():
        if len(tail_indices) < 2:
            continue
        _report_merge(chains, head_idx, tuple(sorted(tail_indices)), road,
                      reported, review)
        blocked.add((head_idx, "head"))
        for tail_idx in tail_indices:
            blocked.add((tail_idx, "tail"))
        found = True

    return found


def _report_conflict(
    tail_seg: RoadSegment,
    head_seg: RoadSegment,
    road: str,
    reported: set[tuple],
    review: list[ReviewItem],
) -> None:
    key = ("conflict",) + tuple(sorted((tail_seg.seg_id, head_seg.seg_id)))
    if key in reported:
        return
    reported.add(key)
    review.append(ReviewItem(
        road=road,
        kind="mileage_conflict",
        route_id="",
        detail=(f"线段 {tail_seg.seg_id} 终点里程 {tail_seg.m_end:g} "
                f"与线段 {head_seg.seg_id} 起点里程 {head_seg.m_start:g} "
                "方向冲突，端点虽在容差内但禁止硬接"),
        seg_ids=(tail_seg.seg_id, head_seg.seg_id),
        at_mileage=tail_seg.m_end,
    ))


def _report_fork(
    chains: list[list[RoadSegment]],
    tail_idx: int,
    head_pair: tuple[int, int],
    road: str,
    reported: set[tuple],
    review: list[ReviewItem],
) -> None:
    tail_seg = chains[tail_idx][-1]
    outlet_ids = tuple(sorted(chains[i][0].seg_id for i in head_pair))
    key = ("fork", tail_seg.seg_id, outlet_ids)
    if key in reported:
        return
    reported.add(key)
    review.append(ReviewItem(
        road=road,
        kind="ambiguous_fork",
        route_id="",
        detail=(f"线段 {tail_seg.seg_id} 末端岔口存在 "
                f"{len(outlet_ids)} 条同样合理的出路 "
                f"({', '.join(outlet_ids)})，主链只合并到岔口之前"),
        seg_ids=(tail_seg.seg_id,) + outlet_ids,
        at_mileage=tail_seg.m_end,
    ))


def _report_merge(
    chains: list[list[RoadSegment]],
    head_idx: int,
    tail_pair: tuple[int, int],
    road: str,
    reported: set[tuple],
    review: list[ReviewItem],
) -> None:
    head_seg = chains[head_idx][0]
    inlet_ids = tuple(sorted(chains[i][-1].seg_id for i in tail_pair))
    key = ("merge", head_seg.seg_id, inlet_ids)
    if key in reported:
        return
    reported.add(key)
    review.append(ReviewItem(
        road=road,
        kind="ambiguous_fork",
        route_id="",
        detail=(f"线段 {head_seg.seg_id} 起端汇入口存在 "
                f"{len(inlet_ids)} 条同样合理的来路 "
                f"({', '.join(inlet_ids)})，主链只合并到汇入口之后"),
        seg_ids=inlet_ids + (head_seg.seg_id,),
        at_mileage=head_seg.m_start,
    ))


def _find_gaps(routes: list[Route]) -> list[MileageGap]:
    """找出每条道路上所有里程不连续处。

    既包括两条线路之间的缺口，也包括一条已合并线路内部相邻线段
    几何相接但里程跳变的缺口（此时 from/to 线路号相同）。
    """

    gaps: list[MileageGap] = []
    by_road: dict[str, list[Route]] = {}
    for route in routes:
        by_road.setdefault(route.road, []).append(route)

    for road, road_routes in by_road.items():
        road_routes.sort(key=lambda r: (r.m_start, r.m_end))
        prev_route_id = ""
        prev_end: float | None = None
        for route in road_routes:
            for seg in route.segments:
                if prev_end is not None and seg.m_start > prev_end + _EPS:
                    gaps.append(MileageGap(
                        road=road,
                        from_route=prev_route_id,
                        to_route=route.route_id,
                        m_after=prev_end,
                        m_before=seg.m_start,
                    ))
                prev_end = seg.m_end
                prev_route_id = route.route_id
    gaps.sort(key=lambda g: (g.road, g.m_after))
    return gaps


# --------------------------------------------------------------------------- #
# 便捷入口
# --------------------------------------------------------------------------- #

def restore_from_features(
    features: Iterable[dict],
    endpoint_tolerance: float = 0.0,
) -> RestoreResult:
    """直接从 GeoJSON Feature 列表还原线路。"""

    segments = segments_from_features(features)
    return restore_routes(segments, endpoint_tolerance)


# --------------------------------------------------------------------------- #
# 结果序列化
# --------------------------------------------------------------------------- #

def result_as_dict(result: RestoreResult) -> dict:
    """把还原结果转成可直接 ``json.dumps`` 的普通字典。"""

    return {
        "routes": [
            {
                "route_id": route.route_id,
                "road": route.road,
                "m_start": route.m_start,
                "m_end": route.m_end,
                "segment_ids": route.seg_ids,
                "coordinates": [list(point) for point in route.coordinates],
            }
            for route in result.routes
        ],
        "gaps": [
            {
                "road": gap.road,
                "from_route": gap.from_route,
                "to_route": gap.to_route,
                "m_after": gap.m_after,
                "m_before": gap.m_before,
                "gap_mileage": gap.gap_mileage,
            }
            for gap in result.gaps
        ],
        "manual_review": [
            {
                "road": item.road,
                "kind": item.kind,
                "route_id": item.route_id,
                "at_mileage": item.at_mileage,
                "segment_ids": list(item.seg_ids),
                "detail": item.detail,
            }
            for item in result.manual_review
        ],
    }


def result_as_geojson(result: RestoreResult) -> dict:
    """把已还原线路输出为 GeoJSON FeatureCollection。

    缺口与人工项放进集合级 ``properties``，线路本身为 LineString 要素。
    """

    features = []
    for route in result.routes:
        features.append({
            "type": "Feature",
            "properties": {
                "route_id": route.route_id,
                "road": route.road,
                "m_start": route.m_start,
                "m_end": route.m_end,
                "segment_ids": route.seg_ids,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [list(point) for point in route.coordinates],
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "gaps": [
                {
                    "road": gap.road,
                    "from_route": gap.from_route,
                    "to_route": gap.to_route,
                    "m_after": gap.m_after,
                    "m_before": gap.m_before,
                    "gap_mileage": gap.gap_mileage,
                }
                for gap in result.gaps
            ],
            "manual_review": [
                {
                    "road": item.road,
                    "kind": item.kind,
                    "route_id": item.route_id,
                    "at_mileage": item.at_mileage,
                    "segment_ids": list(item.seg_ids),
                    "detail": item.detail,
                }
                for item in result.manual_review
            ],
        },
    }
