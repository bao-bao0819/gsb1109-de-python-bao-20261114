from textwrap import dedent

import pytest

from pysubs2.calibration import (
    CalibrationResult,
    OffsetRange,
    TimingCheck,
    calibrate_microdvd,
    format_calibration_report,
    make_timing_checks,
    match_check_frames,
    nearest_event_frame,
    parse_timing_check_spec,
    read_microdvd_frames,
    read_microdvd_frames_from_text,
    timestamp_field_to_ms,
)

FRAME_ONLY_SUB = dedent("""\
    garbage line without frames
    {1}{1}25.0
    {0}{50}First cue.
    {250}{300}Second cue.
    {500}{550}Third cue.
    {750}{800}Fourth cue.
""")


def test_read_microdvd_frames_from_text_skips_declaration_and_junk() -> None:
    assert read_microdvd_frames_from_text(FRAME_ONLY_SUB) == [0, 250, 500, 750]


def test_read_microdvd_frames_from_file(tmp_path) -> None:
    path = tmp_path / "transcript.sub"
    path.write_text(FRAME_ONLY_SUB, encoding="utf-8")
    assert read_microdvd_frames(path) == [0, 250, 500, 750]


def test_nearest_event_frame() -> None:
    frames = [0, 250, 500, 750]
    assert nearest_event_frame(frames, 240) == 250
    assert nearest_event_frame(frames, 10) == 0
    with pytest.raises(ValueError):
        nearest_event_frame([], 10)


def test_match_check_frames_flags_approximate_match() -> None:
    frames = [0, 250, 500, 750]
    checks = make_timing_checks([(240, 12000, 100), (500, 22000, 100)])
    matched = match_check_frames(frames, checks)
    assert matched == {0: 250, 1: 500}
    report = format_calibration_report([], checks, matched)
    assert "frame=240" in report
    assert "-> matched frame 250 (nearest cue; no cue at requested frame)" in report
    assert "-> matched frame 500" in report
    assert report.count("nearest cue; no cue at requested frame") == 1


def test_make_timing_checks_rows_and_suspicious() -> None:
    checks = make_timing_checks(
        [(250, 12000, 100, None), (500, 22000, 100, "mid")],
        suspicious=[1],
    )
    assert checks[0] == TimingCheck(0, 250, 12000, 100, False, None)
    assert checks[1].suspicious is True
    assert checks[1].label == "mid"
    assert checks[1].window_lower_ms == 21900
    assert checks[1].window_upper_ms == 22100


def test_make_timing_checks_rejects_unknown_suspicious_index() -> None:
    with pytest.raises(ValueError):
        make_timing_checks([(1, 2, 3)], suspicious=[5])


def test_timing_check_validation() -> None:
    with pytest.raises(ValueError):
        TimingCheck(0, -1, 100, 0)
    with pytest.raises(ValueError):
        TimingCheck(0, 1, 100, -5)


def test_parse_timing_check_spec_ms_and_timestamp() -> None:
    assert parse_timing_check_spec("500:22000,100", 0) == TimingCheck(0, 500, 22000, 100)
    spec = parse_timing_check_spec("500:0:00:22,100", 1)
    assert spec == TimingCheck(1, 500, 22000, 100)
    flagged = parse_timing_check_spec("500:22000,100?", 2)
    assert flagged.suspicious is True
    default_tol = parse_timing_check_spec("500:22000?", 3)
    assert default_tol.tolerance_ms == 0
    assert default_tol.suspicious is True
    assert timestamp_field_to_ms("0:00:22.500") == 22500


def test_parse_timing_check_spec_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_timing_check_spec("no-colon", 0)
    with pytest.raises(ValueError):
        parse_timing_check_spec("not-a-time", 0)
    with pytest.raises(ValueError):
        parse_timing_check_spec("500:not-a-timestamp", 0)


def test_calibration_recovers_exact_fps_and_offset() -> None:
    # Ground truth: fps=1000, lead-in offset 500 ms, cues at frames 10000 and 20000.
    frames = [0, 10000, 20000]
    checks = make_timing_checks([(10000, 10500, 0), (20000, 20500, 0)])

    results = calibrate_microdvd(frames, [1000.0, 1001.0], checks)

    assert len(results) == 1
    result = results[0]
    assert result.fps == 1000.0
    assert result.offset_range == OffsetRange(500.0, 500.0)
    assert result.satisfied == (0, 1)
    assert result.excluded == ()


def test_calibration_returns_every_feasible_fps_not_just_first() -> None:
    # Loose windows leave both candidate frame-rates feasible with distinct ranges.
    frames = [0, 250, 500, 750]
    checks = make_timing_checks([
        (250, 12000, 500),
        (500, 22000, 500),
        (750, 32000, 500),
    ])

    results = calibrate_microdvd(frames, [23.976, 24.0, 25.0], checks)

    assert [r.fps for r in results] == [23.976, 24.0, 25.0]
    assert all(isinstance(r, CalibrationResult) for r in results)
    ranges = [r.offset_range for r in results]
    assert ranges[0] != ranges[2]


def test_calibration_empty_when_no_fps_fits() -> None:
    frames = [0, 10000, 20000]
    checks = make_timing_checks([(10000, 10500, 0), (20000, 20500, 0)])
    assert calibrate_microdvd(frames, [1001.0], checks) == []


def test_suspicious_check_can_be_excluded_and_is_named() -> None:
    # Third check contradicts the first two and is flagged suspicious.
    frames = [0, 250, 500, 750]
    checks = make_timing_checks(
        [(250, 12000, 100, None), (500, 22000, 100, None), (750, 40000, 100, "bad cue")],
        suspicious=[2],
    )

    results = calibrate_microdvd(frames, [25.0], checks)

    # The all-checks run is infeasible; the only result excludes the suspicious check.
    assert len(results) == 1
    result = results[0]
    assert result.satisfied == (0, 1)
    assert len(result.excluded) == 1
    assert result.excluded[0].index == 2
    assert result.excluded[0].label == "bad cue"
    assert result.excludes_suspicious_check is True


def test_each_suspicious_check_is_excluded_on_its_own() -> None:
    # A loose anchor plus two mutually contradictory tight checks: dropping either
    # suspicious check on its own restores feasibility, and they must be tried
    # as separate interpretations (never two checks dropped at once).
    frames = [0, 100, 200]
    checks = make_timing_checks(
        [(0, 500, 1000), (100, 600, 0), (200, 1200, 0)],
        suspicious=[1, 2],
    )

    results = calibrate_microdvd(frames, [1000.0], checks)
    excluded_indices = {r.excluded[0].index for r in results if r.excluded}
    assert excluded_indices == {1, 2}
    assert all(len(r.excluded) <= 1 for r in results)
    assert not any(not r.excluded for r in results)


def test_non_suspicious_check_is_never_excluded() -> None:
    frames = [0, 10000, 20000]
    checks = make_timing_checks([(10000, 10500, 0), (20000, 99999, 0)])
    results = calibrate_microdvd(frames, [1000.0], checks)
    assert results == []


def test_excluding_the_only_check_is_not_a_valid_interpretation() -> None:
    # The exclusion run must be skipped: an interpretation with no retained
    # constraints would have a meaningless unbounded offset range.
    frames = [0, 250]
    checks = make_timing_checks([(250, 12000, 100)], suspicious=[0])
    results = calibrate_microdvd(frames, [25.0], checks)
    assert len(results) == 1
    assert results[0].excluded == ()
    assert results[0].satisfied == (0,)


def test_calibration_accepts_file_path(tmp_path) -> None:
    path = tmp_path / "transcript.sub"
    path.write_text(FRAME_ONLY_SUB, encoding="utf-8")
    checks = make_timing_checks([(250, 12000, 500), (500, 22000, 500)])
    results = calibrate_microdvd(path, [25.0], checks)
    assert len(results) == 1
    assert results[0].fps == 25.0


def test_calibration_dedupes_repeated_fps() -> None:
    frames = [0, 10000, 20000]
    checks = make_timing_checks([(10000, 10500, 0), (20000, 20500, 0)])
    results = calibrate_microdvd(frames, [1000.0, 1000.0, 1000.0], checks)
    assert [r.fps for r in results] == [1000.0]


def test_calibration_validates_inputs() -> None:
    checks = make_timing_checks([(1, 10, 0)])
    with pytest.raises(ValueError):
        calibrate_microdvd([0, 1], [], checks)
    with pytest.raises(ValueError):
        calibrate_microdvd([0, 1], [0], checks)
    with pytest.raises(ValueError):
        calibrate_microdvd([0, 1], [25.0], [])


def test_format_report_lists_all_interpretations_and_exclusion() -> None:
    frames = [0, 250, 500, 750]
    checks = make_timing_checks(
        [(250, 12000, 1000, None), (500, 22000, 1000, None), (750, 40000, 100, "bad cue")],
        suspicious=[2],
    )
    results = calibrate_microdvd(frames, [24.0, 25.0], checks)
    report = format_calibration_report(results, checks)

    assert "fps=24" in report
    assert "fps=25" in report
    assert "excluded suspicious check: #2" in report
    assert "bad cue" in report
    assert "offset:" in report


def test_format_report_no_solution() -> None:
    checks = make_timing_checks([(1, 10, 0)])
    report = format_calibration_report([], checks)
    assert "No frame-rate satisfies" in report
