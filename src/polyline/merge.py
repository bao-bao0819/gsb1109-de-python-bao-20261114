"""
Merge independently sampled polyline fragments into one point sequence.

The fragments belong to one route, but they may be supplied in either
direction and their junctions may share more than one sample.  Repeated
coordinates are deliberately kept as distinct samples: a route that visits
the same corner twice must keep both visits.
"""
from dataclasses import dataclass
import numbers
from typing import (
    Any,
    Dict,
    Hashable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from .polyline import decode, encode

Point = Tuple[Any, Any]
MAX_MERGE_FRAGMENTS = 12


@dataclass(frozen=True, eq=False)
class MergeCandidate:
    """
    One shortest merged route.

    :ivar points: The merged sequence of coordinate tuples.
    :ivar fragment_ids: Fragment identifiers in the order used by this route.
    :ivar flipped: Whether each corresponding fragment was reversed.
    :ivar placements: ``(fragment_id, start, stop)`` entries giving the half-open
        slice of ``points`` occupied by every fragment.
    :ivar overlap: Total number of shared samples removed while merging.
    """

    points: Tuple[Point, ...]
    fragment_ids: Tuple[Hashable, ...]
    flipped: Tuple[bool, ...]
    placements: Tuple[Tuple[Hashable, int, int], ...]
    overlap: int = 0

    @property
    def order(self) -> Tuple[Hashable, ...]:
        """Fragment identifiers in route order."""
        return self.fragment_ids

    @property
    def reversed(self) -> Tuple[bool, ...]:
        """Whether each fragment was reversed."""
        return self.flipped

    def __iter__(self):
        return iter(self.points)

    def __len__(self) -> int:
        return len(self.points)

    def __getitem__(self, index):
        return self.points[index]

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, MergeCandidate):
            other = other.points
        if isinstance(other, (list, tuple)):
            try:
                return self.points == tuple(tuple(item) for item in other)
            except TypeError:
                return False
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.points)

    def __repr__(self) -> str:
        return (
            "MergeCandidate("
            f"points={self.points!r}, "
            f"fragment_ids={self.fragment_ids!r}, "
            f"flipped={self.flipped!r})"
        )

    def as_list(self) -> List[Point]:
        """Return the merged points as a mutable list."""
        return list(self.points)

    def encode(self, precision: int = 5, geojson: bool = False) -> str:
        """Encode this candidate in the polyline format."""
        return encode(list(self.points), precision=precision, geojson=geojson)

    @property
    def encoded(self) -> str:
        """Encoded form at the default precision."""
        return self.encode()

    def verify(
        self,
        segments: Any,
        *,
        precision: int = 5,
        geojson: bool = False,
    ) -> bool:
        """
        Check that every numbered fragment occurs at its reported placement.

        This is the audit check meant for ``placements``: it confirms the
        orientation, the exact slice of the merged points, and that no
        fragment was silently dropped.
        """
        fragments = _normalise_fragments(
            segments, precision, geojson, max_segments=None
        )
        if len(fragments) != len(self.fragment_ids):
            return False
        placed = list(zip(self.fragment_ids, self.flipped, self.placements))
        by_id = {}
        for fragment_id, flipped, placement in placed:
            if fragment_id in by_id:
                return False
            by_id[fragment_id] = (flipped, placement)

        for fragment in fragments:
            record = by_id.pop(fragment.fragment_id, None)
            if record is None:
                return False
            flipped, (_, start, stop) = record
            expected = (
                tuple(reversed(fragment.points)) if flipped else fragment.points
            )
            if len(expected) != stop - start:
                return False
            if tuple(self.points[start:stop]) != expected:
                return False
        return not by_id


@dataclass(frozen=True)
class _Fragment:
    fragment_id: Hashable
    points: Tuple[Point, ...]


def _is_number(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _is_point(value: Any) -> bool:
    return (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and all(_is_number(item) for item in value)
    )


def _as_point(value: Any) -> Point:
    if not _is_point(value):
        raise ValueError(f"Expected a coordinate pair, got {value!r}")
    return tuple(value)


def _as_point_sequence(value: Any) -> Tuple[Point, ...]:
    if _is_point(value):
        return (_as_point(value),)
    try:
        values = list(value)
    except TypeError as exc:
        raise ValueError(f"Expected a sequence of coordinate pairs, got {value!r}") from exc
    return tuple(_as_point(item) for item in values)


def _payload_points(
    payload: Any, precision: int, geojson: bool
) -> Tuple[Point, ...]:
    if isinstance(payload, str):
        return tuple(decode(payload, precision=precision, geojson=geojson))
    return _as_point_sequence(payload)


def _looks_like_payload(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if _is_point(value):
        return True
    try:
        return all(_is_point(item) for item in value)
    except TypeError:
        return False


def _normalise_fragments(
    segments: Any,
    precision: int,
    geojson: bool,
    max_segments: Optional[int] = MAX_MERGE_FRAGMENTS,
) -> List[_Fragment]:
    if isinstance(segments, Mapping):
        raw = [
            _Fragment(fragment_id, _payload_points(payload, precision, geojson))
            for fragment_id, payload in segments.items()
        ]
    elif isinstance(segments, str) or _is_point(segments):
        raw = [_Fragment(0, _payload_points(segments, precision, geojson))]
    else:
        try:
            items = list(segments)
        except TypeError as exc:
            raise ValueError(
                "segments must be a mapping, a polyline, or a sequence of fragments"
            ) from exc

        if not items:
            raw = []
        elif all(_is_point(item) for item in items):
            raw = [_Fragment(0, tuple(_as_point(item) for item in items))]
        else:
            raw = []
            for index, item in enumerate(items):
                if isinstance(item, _Fragment):
                    raw.append(item)
                    continue
                if (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and not _is_point(item[0])
                    and _looks_like_payload(item[1])
                ):
                    fragment_id, payload = item
                    raw.append(
                        _Fragment(
                            fragment_id,
                            _payload_points(payload, precision, geojson),
                        )
                    )
                    continue
                raw.append(
                    _Fragment(index, _payload_points(item, precision, geojson))
                )

    if max_segments is not None and len(raw) > max_segments:
        raise ValueError(
            f"A merge batch may contain at most {max_segments} fragments"
        )
    identifiers = [fragment.fragment_id for fragment in raw]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Fragment identifiers must be unique within a batch")
    return raw


def _proper_overlap(left: Tuple[Point, ...], right: Tuple[Point, ...]) -> int:
    """
    Longest suffix of ``left`` that is a prefix of ``right``.

    A multi-point fragment is never allowed to vanish completely into a
    junction.  Otherwise an out-and-back leg whose coordinates repeat the
    outward leg would be folded away, and the second visit to those corners
    would be lost.  A one-point fragment is the sole exception: it represents
    a hand-off sample that can sit inside the shared junction.
    """
    limit = min(len(left), len(right))
    if limit > 1:
        limit -= 1
    for size in range(limit, 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def _orientations(fragments: Sequence[_Fragment]) -> List[Tuple[Tuple[Point, ...], Tuple[Point, ...]]]:
    return [(fragment.points, tuple(reversed(fragment.points))) for fragment in fragments]


def _overlap_matrix(
    orientations: Sequence[Tuple[Tuple[Point, ...], Tuple[Point, ...]]]
) -> List[List[List[List[int]]]]:
    count = len(orientations)
    overlaps = [
        [[[0, 0] for _ in range(count)] for _ in range(2)]
        for _ in range(count)
    ]
    for left in range(count):
        for left_side in (0, 1):
            for right in range(count):
                if left == right:
                    continue
                for right_side in (0, 1):
                    overlaps[left][left_side][right][right_side] = _proper_overlap(
                        orientations[left][left_side],
                        orientations[right][right_side],
                    )
    return overlaps


def _compute_scores(
    overlaps: Sequence[Sequence[Sequence[Sequence[int]]]]
) -> List[List[List[int]]]:
    count = len(overlaps)
    size = 1 << count
    scores = [[[-1, -1] for _ in range(count)] for _ in range(size)]
    for index in range(count):
        scores[1 << index][index][0] = 0
        scores[1 << index][index][1] = 0

    for mask in range(size):
        for last in range(count):
            if not mask & (1 << last):
                continue
            for last_side in (0, 1):
                base = scores[mask][last][last_side]
                if base < 0:
                    continue
                for following in range(count):
                    if mask & (1 << following):
                        continue
                    next_mask = mask | (1 << following)
                    for following_side in (0, 1):
                        candidate = (
                            base
                            + overlaps[last][last_side][following][following_side]
                        )
                        if candidate > scores[next_mask][following][following_side]:
                            scores[next_mask][following][following_side] = candidate
    return scores


def _best_path(
    scores: Sequence[Sequence[Sequence[int]]],
    overlaps: Sequence[Sequence[Sequence[Sequence[int]]]],
    full_mask: int,
    final: int,
    final_side: int,
) -> List[Tuple[int, int]]:
    path = [(final, final_side)]
    mask = full_mask
    last, last_side = final, final_side
    while mask != (1 << last):
        target = scores[mask][last][last_side]
        previous_mask = mask ^ (1 << last)
        found = None
        for previous in range(len(scores[0])):
            if not previous_mask & (1 << previous):
                continue
            for previous_side in (0, 1):
                base = scores[previous_mask][previous][previous_side]
                if (
                    base >= 0
                    and base
                    + overlaps[previous][previous_side][last][last_side]
                    == target
                ):
                    found = (previous, previous_side)
                    break
            if found is not None:
                break
        if found is None:
            raise RuntimeError("Could not reconstruct an optimal merge")
        last, last_side = found
        path.append((last, last_side))
        mask = previous_mask
    path.reverse()
    return path


def _build_candidate(
    fragments: Sequence[_Fragment],
    orientations: Sequence[Tuple[Tuple[Point, ...], Tuple[Point, ...]]],
    overlaps: Sequence[Sequence[Sequence[Sequence[int]]]],
    path: Sequence[Tuple[int, int]],
) -> MergeCandidate:
    points: List[Point] = []
    fragment_ids: List[Hashable] = []
    flipped: List[bool] = []
    placements: List[Tuple[Hashable, int, int]] = []
    total_overlap = 0

    for position, (index, side) in enumerate(path):
        oriented = orientations[index][side]
        fragment = fragments[index]
        shared = 0
        if position:
            previous_index, previous_side = path[position - 1]
            shared = overlaps[previous_index][previous_side][index][side]
            total_overlap += shared
        start = len(points) - shared
        points.extend(oriented[shared:])
        stop = len(points)
        if not oriented:
            start = stop
        fragment_ids.append(fragment.fragment_id)
        flipped.append(bool(side))
        placements.append((fragment.fragment_id, start, stop))

    return MergeCandidate(
        points=tuple(points),
        fragment_ids=tuple(fragment_ids),
        flipped=tuple(flipped),
        placements=tuple(placements),
        overlap=total_overlap,
    )


def _reverse_candidate(candidate: MergeCandidate) -> MergeCandidate:
    length = len(candidate.points)
    placements = tuple(
        (fragment_id, length - stop, length - start)
        for fragment_id, start, stop in reversed(candidate.placements)
    )
    return MergeCandidate(
        points=tuple(reversed(candidate.points)),
        fragment_ids=tuple(reversed(candidate.fragment_ids)),
        flipped=tuple(not value for value in reversed(candidate.flipped)),
        placements=placements,
        overlap=candidate.overlap,
    )


def _orient_pair(
    candidates: Sequence[MergeCandidate],
    start_point: Optional[Point],
    fallback_min: bool = False,
) -> List[MergeCandidate]:
    """
    Collapse pairs that differ only by reversing the complete route.

    If ``start_point`` is supplied, the member of each pair whose first point
    is at that location is retained.  A closed route can legitimately start at
    the same point in both directions; in that case both directions remain.
    When neither direction starts at ``start_point`` the pair is kept intact
    rather than silently picking a direction at the far end.  With
    ``fallback_min`` the lexicographically smaller direction is used instead,
    which gives the implicit orientation a stable result.
    """
    by_points: Dict[Tuple[Point, ...], MergeCandidate] = {}
    for candidate in candidates:
        previous = by_points.get(candidate.points)
        if previous is None:
            by_points[candidate.points] = candidate

    groups: Dict[Tuple[Point, ...], List[MergeCandidate]] = {}
    for candidate in by_points.values():
        reverse_points = tuple(reversed(candidate.points))
        key = min(candidate.points, reverse_points)
        groups.setdefault(key, []).append(candidate)

    result: List[MergeCandidate] = []
    for group in groups.values():
        unique: List[MergeCandidate] = []
        seen: Set[Tuple[Point, ...]] = set()
        for candidate in group:
            if candidate.points not in seen:
                unique.append(candidate)
                seen.add(candidate.points)
        if len(unique) == 1:
            result.extend(unique)
            continue
        if start_point is None:
            result.extend(
                [min(unique, key=lambda item: item.points)]
                if fallback_min
                else unique
            )
            continue
        exact = [item for item in unique if item.points[0] == start_point]
        if exact:
            result.extend(exact)
        elif fallback_min:
            result.append(min(unique, key=lambda item: item.points))
        else:
            result.extend(unique)
    return result


def _resolve_start(
    start_point: Optional[Point],
    start: Optional[Point],
    origin: Optional[Point],
    caller: str,
) -> Optional[Point]:
    supplied = [
        value
        for value in (start_point, start, origin)
        if value is not None
    ]
    if not supplied:
        return None
    first = _as_point(supplied[0])
    for value in supplied[1:]:
        if _as_point(value) != first:
            raise ValueError(f"{caller} received conflicting starting points")
    return first


def merge_candidates(
    segments: Any,
    start_point: Optional[Point] = None,
    *,
    start: Optional[Point] = None,
    origin: Optional[Point] = None,
    precision: int = 5,
    geojson: bool = False,
    max_segments: int = MAX_MERGE_FRAGMENTS,
    include_reversals: bool = False,
    max_candidates: int = 256,
) -> List[MergeCandidate]:
    """
    Return every distinct shortest merged route for a batch of fragments.

    :param segments: A mapping of fragment id to fragment, a sequence of
        fragments, or one fragment.  A fragment is either an encoded polyline
        string or a sequence of coordinate pairs.
    :param start_point: Optional starting coordinate used to orient a route
        that would otherwise be equally valid in both directions.
    :param start: Alias for ``start_point``.
    :param origin: Alias for ``start_point``.
    :param precision: Precision used for encoded fragments.
    :param geojson: Set to True when coordinates are (lon, lat).
    :param max_segments: Stricter batch limit; it can never exceed 12.
    :param include_reversals: Keep both directions when two candidates differ
        only by reversing the complete route.
    :param max_candidates: Safety limit for the number of enumerated routes.
    :return: Distinct candidates in deterministic order.
    """
    start_point = _resolve_start(
        start_point, start, origin, "merge_candidates"
    )
    if max_segments < 1:
        raise ValueError("max_segments must be at least 1")
    if max_segments > MAX_MERGE_FRAGMENTS:
        raise ValueError(
            f"max_segments cannot exceed {MAX_MERGE_FRAGMENTS} fragments"
        )
    fragments = _normalise_fragments(
        segments, precision, geojson, max_segments=max_segments
    )
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least 1")

    empty_ids = [
        fragment.fragment_id for fragment in fragments if not fragment.points
    ]
    active = [fragment for fragment in fragments if fragment.points]
    if not active:
        return [
            MergeCandidate(
                points=(),
                fragment_ids=tuple(empty_ids),
                flipped=tuple(False for _ in empty_ids),
                placements=tuple((fragment_id, 0, 0) for fragment_id in empty_ids),
                overlap=0,
            )
        ]

    orientations = _orientations(active)
    overlaps = _overlap_matrix(orientations)
    scores = _compute_scores(overlaps)
    full_mask = (1 << len(active)) - 1

    best_score = max(
        scores[full_mask][last][side]
        for last in range(len(active))
        for side in (0, 1)
    )
    final_states = [
        (last, side)
        for last in range(len(active))
        for side in (0, 1)
        if scores[full_mask][last][side] == best_score
    ]

    candidates: List[MergeCandidate] = []
    seen: Set[Tuple[Point, ...]] = set()
    spent: Set[Tuple[int, int, int, Tuple[Point, ...]]] = set()

    def walk(
        mask: int,
        last: int,
        side: int,
        suffix: List[Tuple[int, int]],
        tail: Tuple[Point, ...],
    ) -> None:
        if len(candidates) >= max_candidates:
            return
        if mask == (1 << last):
            path = [(last, side)]
            path.extend(reversed(suffix))
            candidate = _build_candidate(active, orientations, overlaps, path)
            if empty_ids:
                candidate = MergeCandidate(
                    points=candidate.points,
                    fragment_ids=candidate.fragment_ids + tuple(empty_ids),
                    flipped=candidate.flipped + tuple(False for _ in empty_ids),
                    placements=candidate.placements
                    + tuple((fragment_id, 0, 0) for fragment_id in empty_ids),
                    overlap=candidate.overlap,
                )
            if candidate.points not in seen:
                seen.add(candidate.points)
                candidates.append(candidate)
            return

        state = (mask, last, side, tail)
        if state in spent:
            return
        spent.add(state)

        previous_mask = mask ^ (1 << last)
        target = scores[mask][last][side]
        for previous in range(len(active)):
            if not previous_mask & (1 << previous):
                continue
            for previous_side in (0, 1):
                base = scores[previous_mask][previous][previous_side]
                if (
                    base >= 0
                    and base
                    + overlaps[previous][previous_side][last][side]
                    == target
                ):
                    shared = overlaps[previous][previous_side][last][side]
                    new_tail = orientations[previous][previous_side] + tail[shared:]
                    walk(
                        previous_mask,
                        previous,
                        previous_side,
                        suffix + [(last, side)],
                        new_tail,
                    )
                    if len(candidates) >= max_candidates:
                        return

    for final, final_side in final_states:
        walk(
            full_mask,
            final,
            final_side,
            [],
            orientations[final][final_side],
        )
        if len(candidates) >= max_candidates:
            break

    if not candidates:
        path = _best_path(scores, overlaps, full_mask, *final_states[0])
        candidates = [
            _build_candidate(active, orientations, overlaps, path)
        ]

    if include_reversals:
        expanded = list(candidates)
        known = {candidate.points for candidate in candidates}
        for candidate in candidates:
            reverse = _reverse_candidate(candidate)
            if reverse.points not in known:
                known.add(reverse.points)
                expanded.append(reverse)
        candidates = expanded

    if start_point is not None:
        candidates = _orient_pair(candidates, start_point)
    elif not include_reversals:
        candidates = _orient_pair(candidates, None, fallback_min=True)
    candidates.sort(
        key=lambda item: (
            0 if start_point is not None and item.points and item.points[0] == start_point else 1,
            len(item.points),
            item.points,
        )
    )
    return candidates


def merge(
    segments: Any,
    start_point: Optional[Point] = None,
    *,
    start: Optional[Point] = None,
    origin: Optional[Point] = None,
    precision: int = 5,
    geojson: bool = False,
    max_segments: int = MAX_MERGE_FRAGMENTS,
    return_candidates: bool = False,
    return_all: bool = False,
    include_reversals: bool = False,
    max_candidates: int = 256,
) -> Any:
    """
    Merge numbered polyline fragments into a shortest sampled point sequence.

    Each fragment may be used in either direction.  Shared junction samples,
    including repeated coordinate runs, are aligned by their longest proper
    suffix/prefix overlap.  A whole fragment is never swallowed by a junction,
    so an out-and-back leg remains present.

    :param segments: Mapping of fragment id to fragment, sequence of fragments,
        or a single fragment.  Fragments may be encoded polylines or point
        sequences.
    :param start_point: Optional starting coordinate for orienting the route.
    :param start: Alias for ``start_point``.
    :param origin: Alias for ``start_point``.
    :param precision: Precision used for encoded fragments.
    :param geojson: Set to True when coordinates are (lon, lat).
    :param max_segments: Stricter batch limit; it can never exceed 12.
    :param return_candidates: Return :class:`MergeCandidate` objects instead of
        only the merged points.
    :param return_all: Alias for ``return_candidates``.
    :param include_reversals: With candidates, keep routes that differ only by
        a complete reversal.
    :param max_candidates: Safety limit for candidate enumeration.
    :return: A list of points, or a list of candidates when requested.
    """
    candidates = merge_candidates(
        segments,
        start_point,
        start=start,
        origin=origin,
        precision=precision,
        geojson=geojson,
        max_segments=max_segments,
        include_reversals=include_reversals,
        max_candidates=max_candidates,
    )
    if return_candidates or return_all:
        return candidates
    return candidates[0].as_list() if candidates else []


def merge_encoded(
    segments: Any,
    start_point: Optional[Point] = None,
    *,
    start: Optional[Point] = None,
    origin: Optional[Point] = None,
    precision: int = 5,
    geojson: bool = False,
    max_segments: int = MAX_MERGE_FRAGMENTS,
) -> str:
    """Merge fragments and return the shortest result as an encoded polyline."""
    return encode(
        merge(
            segments,
            start_point,
            start=start,
            origin=origin,
            precision=precision,
            geojson=geojson,
            max_segments=max_segments,
        ),
        precision=precision,
        geojson=geojson,
    )


merge_polylines = merge
merge_segments = merge
merge_all = merge_candidates
shortest_merged_route = merge
