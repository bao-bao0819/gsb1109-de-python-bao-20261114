"""
Selection of fixed-length recording windows that cover the most target dialogue.

The sampler can only deliver whole, fixed-length candidate windows. A window is
deliverable only when it does not cut through any dialogue event and does not
contain any dialogue marked as forbidden for external delivery. Given parsed
multi-track subtitles (the same utterance on different tracks shares a source
number and counts once) and a list of candidate windows, this module chooses a
set of pairwise non-overlapping windows, at most ``K`` of them, that covers the
maximum number of distinct target utterances.

A target utterance is counted as covered only when one selected window contains
it completely; an utterance split across the seam of two windows is not covered.
When a required (must-keep) utterance cannot be preserved at all, the result
reports which candidate windows exclude it.

All timestamps are in milliseconds, matching :class:`pysubs2.SSAEvent`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence

from .ssaevent import SSAEvent


@dataclasses.dataclass(frozen=True)
class Utterance:
    """
    One distinct dialogue utterance, identified by its shared source number.

    Multiple subtitle tracks may carry the same utterance; they are merged by
    :attr:`source_id` and the time span is the union of all track copies.

    Attributes:
        source_id: Shared source number of the utterance (unique per utterance).
        start: Earliest start across all track copies, in milliseconds.
        end: Latest end across all track copies, in milliseconds.
        text: Utterance text (from the first track seen).
        forbidden: True when at least one track marks the utterance as
            forbidden for external delivery.
        required: True when the utterance must be kept if at all possible.
    """

    source_id: int
    start: int
    end: int
    text: str = ""
    forbidden: bool = False
    required: bool = False


@dataclasses.dataclass(frozen=True)
class CandidateWindow:
    """
    A fixed-length recording window that the sampler could deliver.

    Attributes:
        window_id: Unique identifier of the candidate window.
        start: Window start in milliseconds.
        end: Window end in milliseconds.
    """

    window_id: str
    start: int
    end: int


@dataclasses.dataclass(frozen=True)
class WindowViolation:
    """
    Reason why a candidate window cannot be delivered.

    Attributes:
        window_id: The offending candidate window.
        cuts_utterance: Source IDs of utterances whose span the window cuts
            through (the window starts or ends inside the utterance).
        contains_forbidden: Source IDs of forbidden utterances the window
            contains.
    """

    window_id: str
    cuts_utterance: frozenset[int]
    contains_forbidden: frozenset[int]

    @property
    def is_valid(self) -> bool:
        return not self.cuts_utterance and not self.contains_forbidden


@dataclasses.dataclass(frozen=True)
class ExclusionReason:
    """
    Why a required utterance is not covered by the selected windows.

    Attributes:
        source_id: The required utterance that could not be kept.
        no_complete_window: True when no single candidate window contains the
            whole utterance (it can only be split across a seam).
        excluded_by_windows: Candidate windows that would otherwise cover the
            utterance but are undeliverable, with the reason for each.
        forbidden: True when the utterance itself is marked forbidden, so it
            can never be delivered.
        dropped_by_budget: True when a deliverable window does cover the
            utterance but it was not selected because of the ``K`` limit.
    """

    source_id: int
    no_complete_window: bool
    excluded_by_windows: Mapping[str, WindowViolation]
    forbidden: bool
    dropped_by_budget: bool


@dataclasses.dataclass(frozen=True)
class SelectionResult:
    """
    Outcome of a window selection.

    Attributes:
        selected_windows: Window IDs of the chosen pairwise non-overlapping
            windows, ordered by start time.
        covered_sources: Source IDs of distinct target utterances fully covered
            by the selected windows.
        window_violations: Undeliverable candidate windows with their reasons.
        missing_required: Required utterances that are not covered, with the
            candidate windows that exclude them.
    """

    selected_windows: tuple[str, ...]
    covered_sources: frozenset[int]
    window_violations: Mapping[str, WindowViolation]
    missing_required: tuple[ExclusionReason, ...]

    @property
    def is_feasible(self) -> bool:
        """True when every required utterance is covered."""
        return not self.missing_required


def _event_source_id(event: SSAEvent) -> int:
    """Return the shared source number carried by a parsed subtitle event."""
    source_id = getattr(event, "source_id", None)
    if source_id is not None:
        return int(source_id)
    raise ValueError(
        "SSAEvent has no source_id; build Utterance objects explicitly or "
        "annotate events with the shared source number"
    )


def utterances_from_events(events: Iterable[SSAEvent]) -> tuple[Utterance, ...]:
    """
    Merge parsed multi-track subtitle events into distinct utterances.

    Events sharing a source number describe the same utterance across tracks;
    the merged span is the union of the copies, and the utterance is forbidden
    or required when any copy is marked as such. Events with no source number
    raise :class:`ValueError`.
    """
    merged: dict[int, Utterance] = {}
    for event in events:
        source_id = _event_source_id(event)
        forbidden = bool(getattr(event, "forbidden", False))
        required = bool(getattr(event, "required", False))
        current = merged.get(source_id)
        if current is None:
            merged[source_id] = Utterance(
                source_id=source_id,
                start=int(event.start),
                end=int(event.end),
                text=event.plaintext,
                forbidden=forbidden,
                required=required,
            )
        else:
            merged[source_id] = Utterance(
                source_id=source_id,
                start=min(current.start, int(event.start)),
                end=max(current.end, int(event.end)),
                text=current.text or event.plaintext,
                forbidden=current.forbidden or forbidden,
                required=current.required or required,
            )
    return tuple(sorted(merged.values(), key=lambda utterance: utterance.source_id))


def _window_violation(
    window: CandidateWindow, utterances: Sequence[Utterance]
) -> WindowViolation:
    cuts: set[int] = set()
    forbidden_inside: set[int] = set()
    for utterance in utterances:
        if utterance.end <= window.start or utterance.start >= window.end:
            continue
        if utterance.start < window.start or utterance.end > window.end:
            cuts.add(utterance.source_id)
        if utterance.forbidden:
            forbidden_inside.add(utterance.source_id)
    return WindowViolation(
        window_id=window.window_id,
        cuts_utterance=frozenset(cuts),
        contains_forbidden=frozenset(forbidden_inside),
    )


def _cover_sets(
    windows: Sequence[CandidateWindow],
    target_sources: frozenset[int],
    utterance_by_id: Mapping[int, Utterance],
) -> list[frozenset[int]]:
    cover_sets: list[frozenset[int]] = []
    for window in windows:
        covered = {
            source_id
            for source_id in target_sources
            if (utterance := utterance_by_id[source_id]).start >= window.start
            and utterance.end <= window.end
        }
        cover_sets.append(frozenset(covered))
    return cover_sets


def select_windows(
    utterances: Sequence[Utterance],
    candidates: Sequence[CandidateWindow],
    max_windows: int,
    target_sources: frozenset[int] | None = None,
) -> SelectionResult:
    """
    Select pairwise non-overlapping windows covering the most target utterances.

    Args:
        utterances: Distinct dialogue utterances (merge multi-track copies with
            :func:`utterances_from_events` first).
        candidates: Fixed-length candidate recording windows.
        max_windows: Maximum number of windows to deliver (``K``); must be >= 0.
        target_sources: Source IDs counted toward coverage. When ``None``, every
            non-forbidden utterance is a target.

    Returns:
        A :class:`SelectionResult` with the chosen windows, the covered target
        source IDs, the undeliverable candidates and, for any required
        utterance that cannot be kept, the candidate windows that exclude it.

    A window is invalid when it cuts through an utterance or contains a
    forbidden utterance; invalid windows are never selected. Touching windows
    (one ends exactly when the next starts) are allowed because they do not
    overlap. An utterance split across such a seam is not covered, since no
    single window contains it entirely.
    """
    if max_windows < 0:
        raise ValueError("max_windows must be non-negative")
    if len({window.window_id for window in candidates}) != len(candidates):
        raise ValueError("candidate window IDs must be unique")

    utterance_by_id = {utterance.source_id: utterance for utterance in utterances}
    if target_sources is None:
        target_sources = frozenset(
            source_id
            for source_id, utterance in utterance_by_id.items()
            if not utterance.forbidden
        )
    unknown = target_sources.difference(utterance_by_id)
    if unknown:
        raise ValueError(f"unknown target source IDs: {sorted(unknown)}")

    violations = {
        window.window_id: _window_violation(window, utterances)
        for window in candidates
    }
    valid_windows = [
        window for window in candidates if violations[window.window_id].is_valid
    ]
    valid_windows.sort(key=lambda window: (window.end, window.start, window.window_id))

    cover_sets = _cover_sets(valid_windows, target_sources, utterance_by_id)

    selected_indices = _optimize(valid_windows, cover_sets, max_windows)
    selected = [valid_windows[index] for index in selected_indices]
    selected_ids = tuple(window.window_id for window in selected)

    covered: set[int] = set()
    for index in selected_indices:
        covered.update(cover_sets[index])

    missing = _missing_required(
        utterance_by_id,
        target_sources,
        candidates,
        violations,
        frozenset(covered),
    )

    return SelectionResult(
        selected_windows=selected_ids,
        covered_sources=frozenset(covered),
        window_violations=violations,
        missing_required=missing,
    )


def _optimize(
    windows: Sequence[CandidateWindow],
    cover_sets: Sequence[frozenset[int]],
    max_windows: int,
) -> list[int]:
    """
    Weighted interval scheduling under a budget of at most ``max_windows``.

    Windows are sorted by end time. ``prev[i]`` is the last window that ends no
    later than window ``i`` starts, so chosen windows never overlap. The DP
    maximizes the number of distinct target utterances covered; parent pointers
    reconstruct the chosen window indices.
    """
    count = len(windows)
    if count == 0 or max_windows == 0:
        return []

    previous: list[int] = []
    for index, window in enumerate(windows):
        predecessor = -1
        low, high = 0, index
        while low < high:
            middle = (low + high) // 2
            if windows[middle].end <= window.start:
                predecessor = middle
                low = middle + 1
            else:
                high = middle
        previous.append(predecessor)

    budget = min(max_windows, count)
    # score[k][i] = max covered targets using at most k windows among [:i+1].
    score = [[0] * count for _ in range(budget + 1)]
    # parent[k][i] = (next budget, next index, window i was taken).
    parent: list[list[tuple[int, int, bool] | None]] = [
        [None] * count for _ in range(budget + 1)
    ]

    for used in range(1, budget + 1):
        for index in range(count):
            skip_score = score[used][index - 1] if index > 0 else 0
            predecessor = previous[index]
            take_score = (
                score[used - 1][predecessor] if predecessor >= 0 else 0
            ) + len(cover_sets[index])

            if take_score > skip_score:
                score[used][index] = take_score
                parent[used][index] = (used - 1, predecessor, True)
            else:
                score[used][index] = skip_score
                parent[used][index] = (used, index - 1, False)

    chosen: list[int] = []
    used, index = budget, count - 1
    while index >= 0 and used > 0:
        next_used, next_index, took = parent[used][index]  # type: ignore[misc]
        if took:
            chosen.append(index)
        used, index = next_used, next_index
    chosen.reverse()
    return chosen


def _missing_required(
    utterance_by_id: Mapping[int, Utterance],
    target_sources: frozenset[int],
    candidates: Sequence[CandidateWindow],
    violations: Mapping[str, WindowViolation],
    covered: frozenset[int],
) -> tuple[ExclusionReason, ...]:
    reasons: list[ExclusionReason] = []
    required_ids = sorted(
        source_id
        for source_id in target_sources
        if utterance_by_id[source_id].required
    )
    for source_id in required_ids:
        if source_id in covered:
            continue
        utterance = utterance_by_id[source_id]
        excluding: dict[str, WindowViolation] = {}
        valid_cover_exists = False
        any_cover_exists = False
        for window in candidates:
            contains_completely = (
                utterance.start >= window.start and utterance.end <= window.end
            )
            if not contains_completely:
                continue
            any_cover_exists = True
            violation = violations[window.window_id]
            if violation.is_valid:
                valid_cover_exists = True
            else:
                excluding[window.window_id] = violation
        reasons.append(
            ExclusionReason(
                source_id=source_id,
                no_complete_window=not any_cover_exists,
                excluded_by_windows=excluding,
                forbidden=utterance.forbidden,
                dropped_by_budget=valid_cover_exists,
            )
        )
    return tuple(reasons)
