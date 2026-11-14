"""Calibration of frame-only MicroDVD transcripts against known broadcast times.

MicroDVD files store every cue as a pair of frame numbers. When the frame-rate
declaration is missing and the video has an unknown lead-in offset, the cues
cannot be converted to wall-clock times without two pieces of information:

* the frame-rate ``fps`` used to count frames,
* the lead-in *offset* (in milliseconds) between frame time ``0`` and the
  start of the broadcast timeline.

A registration table of a few cues with approximate broadcast times gives
*time windows* that a correct ``(fps, offset)`` pair must satisfy. For a cue
whose start frame ``F`` is matched to a broadcast time window
``[t - tol, t + tol]`` (all in milliseconds), the offset must obey::

    t - tol - round(F * 1000 / fps) <= offset <= t + tol - round(F * 1000 / fps)

Intersecting these inequalities over all retained checks yields the range of
feasible offsets for each candidate frame-rate. Checks flagged as suspicious
may be dropped one at a time; every feasible interpretation is returned so the
import operator can compare them rather than having a single answer selected.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from math import inf
from os import PathLike
from pathlib import Path
from re import Match
from typing import overload

from ..time import frames_to_ms, timestamp_to_ms
from ..formats.microdvd import MICRODVD_LINE

__all__ = [
    "CalibrationResult",
    "OffsetRange",
    "TimingCheck",
    "calibrate_microdvd",
    "format_calibration_report",
    "make_timing_checks",
    "match_check_frames",
    "nearest_event_frame",
    "parse_timing_check_spec",
    "read_microdvd_frames",
    "read_microdvd_frames_from_text",
    "timestamp_field_to_ms",
]

#: Accept either a path to a MicroDVD file or an already extracted list of start frames.
PathOrFrames = str | PathLike[str] | Sequence[int]


@dataclass(frozen=True)
class TimingCheck:
    """One broadcast-time entry from the registration table.

    Attributes:
        index: Stable 0-based position of the check within the table.
        frame: Start frame number of the cue, as written in the MicroDVD file.
        expected_ms: Approximate broadcast time of the cue, in milliseconds.
        tolerance_ms: Half-width of the accepted broadcast time window, in milliseconds.
        suspicious: Whether the entry is marked as suspicious (eligible for one-off exclusion).
        label: Optional human-readable label of the entry, eg. a cue identifier.
    """

    index: int
    frame: int
    expected_ms: float
    tolerance_ms: float
    suspicious: bool = False
    label: str | None = None

    def __post_init__(self) -> None:
        if self.frame < 0:
            raise ValueError(f"Timing check frame must be non-negative (got {self.frame})")
        if self.tolerance_ms < 0:
            raise ValueError(
                f"Timing check tolerance must be non-negative (got {self.tolerance_ms})"
            )

    @property
    def window_lower_ms(self) -> float:
        return self.expected_ms - self.tolerance_ms

    @property
    def window_upper_ms(self) -> float:
        return self.expected_ms + self.tolerance_ms

    def describe(self) -> str:
        label = f" {self.label!r}" if self.label else ""
        flag = " [suspicious]" if self.suspicious else ""
        return (
            f"#{self.index}{label} frame={self.frame} "
            f"window={self.window_lower_ms:g}..{self.window_upper_ms:g} ms{flag}"
        )


@dataclass(frozen=True)
class OffsetRange:
    """Inclusive range of feasible lead-in offsets, in milliseconds.

    Sign convention: ``broadcast_time_ms = frame_time_ms + offset_ms``.
    A negative offset means the frame-0 timeline starts before broadcast time 0.
    """

    lower_ms: float
    upper_ms: float

    def __post_init__(self) -> None:
        if self.lower_ms > self.upper_ms:
            raise ValueError(
                f"Offset range lower bound must not exceed upper bound "
                f"({self.lower_ms:g} > {self.upper_ms:g})"
            )


@dataclass(frozen=True)
class CalibrationResult:
    """One feasible interpretation: a frame-rate and its offset range.

    Attributes:
        fps: Candidate frame-rate for which the offset range is feasible.
        offset_range: Inclusive range of lead-in offsets satisfying every retained check.
        satisfied: Indices of checks retained and satisfied by this interpretation.
        excluded: Checks dropped from this interpretation (at most one, always suspicious).
    """

    fps: float
    offset_range: OffsetRange
    satisfied: tuple[int, ...]
    excluded: tuple[TimingCheck, ...]

    @property
    def excludes_suspicious_check(self) -> bool:
        return bool(self.excluded)


def read_microdvd_frames_from_text(text: str) -> list[int]:
    """Extract cue start frames from MicroDVD file content.

    Lines that do not match the MicroDVD grammar are ignored, as is the
    customary frame-rate declaration line ``{1}{1}<fps>``.

    Returns:
        Start frame numbers of the cues, in file order.
    """
    frames: list[int] = []
    for line in text.splitlines():
        match: Match[str] | None = MICRODVD_LINE.match(line)
        if match is None:
            continue

        fstart, fend, cue_text = match.groups()
        if fstart == "1" and fend == "1":
            try:
                float(cue_text.strip())
            except ValueError:
                pass
            else:
                # Frame-rate declaration line, not a real cue.
                continue

        frames.append(int(fstart))
    return frames


def read_microdvd_frames(
    path: str | PathLike[str],
    encoding: str = "utf-8",
    errors: str = "surrogateescape",
) -> list[int]:
    """Extract cue start frames directly from a MicroDVD file.

    Arguments:
        path: Path to the ``.sub`` file.
        encoding: Character encoding of the file.
        errors: Codec error handling, passed to :meth:`pathlib.Path.open`.
    """
    text = Path(path).read_text(encoding=encoding, errors=errors)
    return read_microdvd_frames_from_text(text)


def nearest_event_frame(event_frames: Sequence[int], frame: int) -> int:
    """Return the cue start frame closest to ``frame``.

    Raises:
        ValueError: When there are no cue frames to match against.
    """
    if not event_frames:
        raise ValueError("No MicroDVD cues available to match timing checks against")
    return min(event_frames, key=lambda event_frame: abs(event_frame - frame))


def match_check_frames(
    event_frames: Sequence[int],
    checks: Sequence[TimingCheck],
) -> dict[int, int]:
    """Map each check index to the nearest cue start frame in the transcript."""
    return {
        check.index: nearest_event_frame(event_frames, check.frame) for check in checks
    }


@overload
def make_timing_checks(
    rows: Sequence[tuple[int, float, float]],
    *,
    suspicious: Collection[int] = (),
) -> list[TimingCheck]: ...


@overload
def make_timing_checks(
    rows: Sequence[tuple[int, float, float, str | None]],
    *,
    suspicious: Collection[int] = (),
) -> list[TimingCheck]: ...


def make_timing_checks(
    rows: Sequence[tuple],
    *,
    suspicious: Collection[int] = (),
) -> list[TimingCheck]:
    """Build :class:`TimingCheck` objects from plain table rows.

    Arguments:
        rows: Rows of ``(frame, expected_ms, tolerance_ms)`` or
            ``(frame, expected_ms, tolerance_ms, label)``; row position becomes
            the check :attr:`TimingCheck.index`.
        suspicious: Indices of rows to flag as suspicious.

    Raises:
        ValueError: On malformed rows or a suspicious index outside the table.
    """
    checks: list[TimingCheck] = []
    suspicious_set = set(suspicious)
    unknown = suspicious_set - set(range(len(rows)))
    if unknown:
        raise ValueError(f"Suspicious indices without matching checks: {sorted(unknown)}")

    for index, row in enumerate(rows):
        if len(row) == 3:
            frame, expected_ms, tolerance_ms = row
            label = None
        elif len(row) == 4:
            frame, expected_ms, tolerance_ms, label = row
        else:
            raise ValueError(
                f"Timing check row must have 3 or 4 fields (got {len(row)}): {row!r}"
            )

        checks.append(
            TimingCheck(
                index=index,
                frame=int(frame),
                expected_ms=float(expected_ms),
                tolerance_ms=float(tolerance_ms),
                suspicious=index in suspicious_set,
                label=label,
            )
        )
    return checks


def _offset_interval_for_fps(
    fps: float,
    checks: Sequence[TimingCheck],
    matched_frames: dict[int, int],
) -> OffsetRange | None:
    lower = -inf
    upper = inf

    for check in checks:
        frame_time_ms = frames_to_ms(matched_frames[check.index], fps)
        lower = max(lower, check.window_lower_ms - frame_time_ms)
        upper = min(upper, check.window_upper_ms - frame_time_ms)

    if lower > upper:
        return None
    return OffsetRange(lower_ms=lower, upper_ms=upper)


def calibrate_microdvd(
    source: PathOrFrames,
    candidate_fps: Sequence[float],
    timing_checks: Sequence[TimingCheck],
    *,
    encoding: str = "utf-8",
    errors: str = "surrogateescape",
) -> list[CalibrationResult]:
    """Find frame-rates and lead-in offset ranges compatible with all timing windows.

    Each candidate frame-rate is tested against the timing checks in two kinds of
    runs: first using every check, then once more with each *suspicious* check
    excluded on its own. A run is reported as a :class:`CalibrationResult` only
    when the offset inequalities intersect (i.e. all retained time windows can be
    satisfied simultaneously). All feasible interpretations are returned in
    candidate frame-rate order; nothing is filtered or ranked away.

    Arguments:
        source: Path to a frame-only MicroDVD file, or a sequence of cue start frames.
        candidate_fps: Frame-rates from the registration table worth testing.
        timing_checks: Broadcast time windows to satisfy, from :func:`make_timing_checks`.
        encoding: File encoding, used only when ``source`` is a path.
        errors: Codec error handling, used only when ``source`` is a path.

    Returns:
        Feasible interpretations, each carrying its frame-rate, offset range and
        the checks it satisfies (plus any single suspicious check it excludes).

    Raises:
        ValueError: If frame-rates or checks are missing/invalid.
    """
    if not candidate_fps:
        raise ValueError("At least one candidate frame-rate is required")
    if any(fps <= 0 for fps in candidate_fps):
        raise ValueError("Candidate frame-rates must be positive numbers")
    if not timing_checks:
        raise ValueError("At least one timing check is required")

    indices = [check.index for check in timing_checks]
    if len(set(indices)) != len(indices):
        raise ValueError("Timing check indices must be unique")

    # Drop repeated frame-rates while keeping the registration-table order.
    candidate_fps = list(dict.fromkeys(candidate_fps))

    if isinstance(source, (str, PathLike)):
        event_frames = read_microdvd_frames(source, encoding=encoding, errors=errors)
    else:
        event_frames = list(source)

    matched_frames = match_check_frames(event_frames, timing_checks)
    checks_by_index = {check.index: check for check in timing_checks}

    # First run keeps every check; follow with one run per suspicious check dropped alone.
    exclusion_sets: list[tuple[int, ...]] = [()]
    exclusion_sets.extend(
        (check.index,) for check in timing_checks if check.suspicious
    )

    results: list[CalibrationResult] = []
    for fps in candidate_fps:
        for excluded_indices in exclusion_sets:
            excluded_set = set(excluded_indices)
            retained = [check for check in timing_checks if check.index not in excluded_set]
            if not retained:
                # Dropping the only check would leave no time window to satisfy.
                continue
            offset_range = _offset_interval_for_fps(fps, retained, matched_frames)
            if offset_range is None:
                continue

            results.append(
                CalibrationResult(
                    fps=fps,
                    offset_range=offset_range,
                    satisfied=tuple(check.index for check in retained),
                    excluded=tuple(checks_by_index[index] for index in excluded_indices),
                )
            )

    return results


def parse_timing_check_spec(spec: str, index: int) -> TimingCheck:
    """Parse one CLI-style timing-check specification.

    Grammar: ``FRAME:EXPECTED[,TOLERANCE][?]``. ``FRAME`` is the cue start
    frame; ``EXPECTED`` is either a bare millisecond count or an
    ``H:MM:SS[.mmm]`` timestamp (colons are unambiguous because the frame
    separator is the first colon and the tolerance separator is a comma);
    ``TOLERANCE`` is a millisecond half-width (default 0); a trailing ``?``
    flags the check as suspicious.
    """
    suspicious = spec.endswith("?")
    if suspicious:
        spec = spec[:-1]

    head, separator, tail = spec.partition(",")
    if separator:
        tolerance_ms = float(tail)
    else:
        tolerance_ms = 0.0

    frame_field, colon, expected_field = head.partition(":")
    if not colon or not frame_field:
        raise ValueError(
            f"Timing check {spec!r} must look like FRAME:EXPECTED[,TOLERANCE][?]"
        )

    frame = int(frame_field)
    if _is_float(expected_field):
        expected_ms = float(expected_field)
    else:
        expected_ms = float(timestamp_field_to_ms(expected_field))

    return TimingCheck(
        index=index,
        frame=frame,
        expected_ms=expected_ms,
        tolerance_ms=tolerance_ms,
        suspicious=suspicious,
    )


def _is_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


# Re-exported for callers that want timestamp parsing alongside calibration.
def timestamp_field_to_ms(field: str) -> int:
    """Convert an ``H:MM:SS[.mmm]`` (or comma decimals) field to milliseconds."""
    from ..time import TIMESTAMP, TIMESTAMP_SHORT

    match = TIMESTAMP.match(field) or TIMESTAMP_SHORT.match(field)
    if match is None:
        raise ValueError(f"Could not parse broadcast time {field!r}")
    return timestamp_to_ms(match.groups())


def _fmt_ms(value: float) -> str:
    if value == inf or value == -inf:
        return "unbounded"
    return f"{value:g} ms"


def format_calibration_report(
    results: Sequence[CalibrationResult],
    checks: Sequence[TimingCheck],
    matched_frames: dict[int, int] | None = None,
) -> str:
    """Render calibration results as a human-readable report.

    Every feasible interpretation gets its own block, including the exact
    suspicious check (if any) that was excluded, so the import operator can see
    all alternatives instead of a single picked answer. When ``matched_frames``
    is given, each check line also names the cue frame it was matched to
    (highlighted whenever the transcript has no cue at the requested frame).
    """
    lines: list[str] = []
    lines.append(f"Timing checks ({len(checks)}):")
    for check in checks:
        line = f"  - {check.describe()}"
        if matched_frames is not None:
            matched = matched_frames[check.index]
            line += f" -> matched frame {matched}"
            if matched != check.frame:
                line += " (nearest cue; no cue at requested frame)"
        lines.append(line)

    if not results:
        lines.append("")
        lines.append("No frame-rate satisfies the timing windows "
                     "(even after excluding one suspicious check).")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"Feasible interpretations ({len(results)}):")
    for number, result in enumerate(results, 1):
        offset_range = result.offset_range
        lines.append(f"  [{number}] fps={result.fps:g}")
        lines.append(
            "      offset: "
            f"{_fmt_ms(offset_range.lower_ms)} .. {_fmt_ms(offset_range.upper_ms)}"
        )
        lines.append(f"      satisfied checks: {', '.join(map(str, result.satisfied))}")
        if result.excluded:
            excluded = ", ".join(check.describe() for check in result.excluded)
            lines.append(f"      excluded suspicious check: {excluded}")
        else:
            lines.append("      excluded suspicious check: none")

    return "\n".join(lines)
