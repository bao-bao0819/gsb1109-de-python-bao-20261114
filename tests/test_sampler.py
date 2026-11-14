"""
Tests for the fixed-length recording window selector (pysubs2.sampler).

"""
import dataclasses
import itertools
import random

import pytest

from pysubs2 import SSAEvent
from pysubs2.sampler import (
    CandidateWindow,
    Utterance,
    select_windows,
    utterances_from_events,
)


@dataclasses.dataclass(repr=False, eq=False, order=False)
class TrackEvent(SSAEvent):
    """SSAEvent carrying the shared source number and delivery flags."""

    source_id: int = 0
    forbidden: bool = False
    required: bool = False


def make_utterances(specs):
    return tuple(
        Utterance(
            source_id=source_id,
            start=start,
            end=end,
            forbidden=forbidden,
            required=required,
        )
        for source_id, start, end, forbidden, required in specs
    )


def make_windows(specs):
    return tuple(
        CandidateWindow(window_id=window_id, start=start, end=end)
        for window_id, start, end in specs
    )


def covered_by(utterances, window):
    return frozenset(
        utterance.source_id
        for utterance in utterances
        if utterance.start >= window.start and utterance.end <= window.end
    )


def brute_force_best(utterances, candidates, max_windows, target_sources):
    """Reference solution: enumerate every feasible subset of valid windows."""
    valid = []
    for window in candidates:
        violation_ok = True
        for utterance in utterances:
            if utterance.end <= window.start or utterance.start >= window.end:
                continue
            if utterance.start < window.start or utterance.end > window.end:
                violation_ok = False
            if utterance.forbidden:
                violation_ok = False
        if violation_ok:
            valid.append(window)

    best = -1
    for size in range(min(max_windows, len(valid)) + 1):
        for subset in itertools.combinations(valid, size):
            ordered = sorted(subset, key=lambda window: window.start)
            if any(
                left.end > right.start for left, right in zip(ordered, ordered[1:])
            ):
                continue
            covered = frozenset().union(
                *(covered_by(utterances, window) & target_sources for window in ordered)
            ) if ordered else frozenset()
            best = max(best, len(covered))
    return best


def test_utterances_from_events_merge_tracks():
    events = [
        TrackEvent(start=100, end=900, text="hello", source_id=7),
        TrackEvent(start=50, end=1000, text="hello", source_id=7, forbidden=True),
        TrackEvent(start=2000, end=2500, text="world", source_id=8, required=True),
    ]

    utterances = utterances_from_events(events)

    assert [utterance.source_id for utterance in utterances] == [7, 8]
    merged = utterances[0]
    assert (merged.start, merged.end) == (50, 1000)
    assert merged.forbidden is True
    assert merged.required is False
    assert utterances[1].required is True


def test_event_without_source_id_rejected():
    with pytest.raises(ValueError):
        utterances_from_events([SSAEvent(start=0, end=1000)])


def test_window_cutting_utterance_is_invalid():
    utterances = make_utterances([(1, 0, 1000, False, False)])
    candidates = make_windows([("cut", 500, 1500), ("ok", 0, 1000)])

    result = select_windows(utterances, candidates, max_windows=2)

    assert result.selected_windows == ("ok",)
    assert result.window_violations["cut"].cuts_utterance == frozenset({1})
    assert result.window_violations["ok"].is_valid


def test_window_with_forbidden_dialogue_is_invalid():
    utterances = make_utterances(
        [(1, 0, 500, False, False), (2, 600, 900, True, False)]
    )
    candidates = make_windows([("dirty", 0, 1000), ("clean", 0, 500)])

    result = select_windows(utterances, candidates, max_windows=2)

    assert result.selected_windows == ("clean",)
    assert result.window_violations["dirty"].contains_forbidden == frozenset({2})


def test_same_source_on_two_tracks_counts_once():
    utterances = make_utterances([(1, 100, 400, False, False)])
    candidates = make_windows([("w", 0, 1000)])

    result = select_windows(utterances, candidates, max_windows=1)

    assert result.covered_sources == frozenset({1})
    assert len(result.covered_sources) == 1


def test_seam_split_is_not_covered():
    utterances = make_utterances([(1, 400, 600, False, False)])
    candidates = make_windows([("a", 0, 500), ("b", 500, 1000)])

    result = select_windows(utterances, candidates, max_windows=2)

    assert result.covered_sources == frozenset()
    assert result.selected_windows == ()


def test_touching_windows_are_both_allowed():
    utterances = make_utterances(
        [(1, 0, 400, False, False), (2, 600, 900, False, False)]
    )
    candidates = make_windows([("a", 0, 500), ("b", 500, 1000)])

    result = select_windows(utterances, candidates, max_windows=2)

    assert result.selected_windows == ("a", "b")
    assert result.covered_sources == frozenset({1, 2})


def test_overlapping_windows_never_both_chosen():
    utterances = make_utterances(
        [(1, 0, 400, False, False), (2, 600, 900, False, False)]
    )
    candidates = make_windows(
        [("wide", 0, 1000), ("a", 0, 500), ("b", 500, 1000)]
    )

    result = select_windows(utterances, candidates, max_windows=2)

    assert result.covered_sources == frozenset({1, 2})
    chosen = sorted(
        (window for window in candidates
         if window.window_id in result.selected_windows),
        key=lambda window: window.start,
    )
    for left, right in zip(chosen, chosen[1:]):
        assert left.end <= right.start
    # wide overlaps both split windows, so it can never be combined with them.
    if "wide" in result.selected_windows:
        assert result.selected_windows == ("wide",)


def test_budget_k_is_respected():
    utterances = make_utterances(
        [(index, index * 1000, index * 1000 + 400, False, False)
         for index in range(5)]
    )
    candidates = make_windows(
        [(f"w{index}", index * 1000, index * 1000 + 500) for index in range(5)]
    )

    result = select_windows(utterances, candidates, max_windows=2)

    assert len(result.selected_windows) <= 2
    assert len(result.covered_sources) == 2


def test_zero_budget_covers_nothing():
    utterances = make_utterances([(1, 0, 400, False, False)])
    candidates = make_windows([("w", 0, 500)])

    result = select_windows(utterances, candidates, max_windows=0)

    assert result.selected_windows == ()
    assert result.covered_sources == frozenset()


def test_forbidden_utterance_is_not_a_target():
    utterances = make_utterances([(1, 0, 400, True, False)])
    candidates = make_windows([("w", 0, 500)])

    result = select_windows(utterances, candidates, max_windows=1)

    assert result.covered_sources == frozenset()


def test_required_missing_no_complete_window():
    utterances = make_utterances([(1, 400, 600, False, True)])
    candidates = make_windows([("a", 0, 500), ("b", 500, 1000)])

    result = select_windows(utterances, candidates, max_windows=2)

    assert not result.is_feasible
    reason = result.missing_required[0]
    assert reason.source_id == 1
    assert reason.no_complete_window is True
    assert reason.excluded_by_windows == {}
    assert reason.dropped_by_budget is False


def test_required_missing_lists_excluding_windows():
    utterances = make_utterances(
        [(1, 0, 400, False, True), (2, 300, 900, True, False)]
    )
    # Only candidate covering utterance 1 also holds forbidden utterance 2.
    candidates = make_windows([("dirty", 0, 1000)])

    result = select_windows(utterances, candidates, max_windows=1)

    reason = result.missing_required[0]
    assert reason.source_id == 1
    assert reason.no_complete_window is False
    assert reason.dropped_by_budget is False
    violation = reason.excluded_by_windows["dirty"]
    assert violation.contains_forbidden == frozenset({2})


def test_required_missing_due_to_budget():
    utterances = make_utterances(
        [
            (1, 0, 400, False, True),
            (2, 1000, 1100, False, False),
            (3, 1200, 1400, False, False),
        ]
    )
    # w1 covers only the required utterance; w2 covers two, so the budget
    # forces the required one out of the optimal selection.
    candidates = make_windows([("w1", 0, 500), ("w2", 1000, 1500)])

    result = select_windows(utterances, candidates, max_windows=1)

    reason = next(
        reason for reason in result.missing_required if reason.source_id == 1
    )
    assert reason.dropped_by_budget is True
    assert reason.no_complete_window is False
    assert reason.excluded_by_windows == {}


def test_explicit_target_subset():
    utterances = make_utterances(
        [(1, 0, 400, False, False), (2, 600, 900, False, False)]
    )
    candidates = make_windows([("w", 0, 1000)])

    result = select_windows(
        utterances, candidates, max_windows=1, target_sources=frozenset({2})
    )

    assert result.covered_sources == frozenset({2})


def test_invalid_arguments():
    utterances = make_utterances([(1, 0, 400, False, False)])
    candidates = make_windows([("w", 0, 500)])

    with pytest.raises(ValueError):
        select_windows(utterances, candidates, max_windows=-1)
    with pytest.raises(ValueError):
        select_windows(
            utterances,
            candidates + candidates,
            max_windows=1,
        )
    with pytest.raises(ValueError):
        select_windows(
            utterances, candidates, max_windows=1, target_sources=frozenset({99})
        )


@pytest.mark.parametrize("seed", range(40))
def test_optimizer_matches_brute_force(seed):
    rng = random.Random(seed)
    utterance_count = rng.randint(1, 7)
    specs = []
    cursor = 0
    for source_id in range(utterance_count):
        start = cursor + rng.randint(0, 300)
        end = start + rng.randint(50, 400)
        specs.append((source_id, start, end, rng.random() < 0.2, rng.random() < 0.3))
        cursor = start
    utterances = make_utterances(specs)

    window_count = rng.randint(1, 9)
    window_specs = []
    for index in range(window_count):
        start = rng.randint(0, 2500)
        end = start + rng.randint(100, 800)
        window_specs.append((f"w{index}", start, end))
    candidates = make_windows(window_specs)
    max_windows = rng.randint(1, 4)
    target_sources = frozenset(
        utterance.source_id
        for utterance in utterances
        if not utterance.forbidden
    )

    result = select_windows(utterances, candidates, max_windows)

    assert len(result.selected_windows) <= max_windows
    chosen = [
        window for window in candidates
        if window.window_id in result.selected_windows
    ]
    for left, right in zip(sorted(chosen, key=lambda w: w.start),
                           sorted(chosen, key=lambda w: w.start)[1:]):
        assert left.end <= right.start
    for window in chosen:
        assert result.window_violations[window.window_id].is_valid

    expected = brute_force_best(
        utterances, candidates, max_windows, target_sources
    )
    assert len(result.covered_sources) == expected
