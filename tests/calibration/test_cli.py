from textwrap import dedent

import pytest

from pysubs2.calibration.cli import CalibrationCLI

FRAME_ONLY_SUB = dedent("""\
    {1}{1}25.0
    {0}{50}First cue.
    {250}{300}Second cue.
    {500}{550}Third cue.
    {750}{800}Fourth cue.
""")


def _write_sub(tmp_path):
    path = tmp_path / "transcript.sub"
    path.write_text(FRAME_ONLY_SUB, encoding="utf-8")
    return path


def test_cli_reports_feasible_interpretations(tmp_path, capsys) -> None:
    path = _write_sub(tmp_path)
    rv = CalibrationCLI()([
        str(path),
        "--fps", "24",
        "--fps", "25",
        "--check", "250:12000,1000",
        "--check", "500:22000,1000",
        "--check", "750:32000,1000",
    ])
    out = capsys.readouterr().out
    assert rv == 0
    assert "fps=24" in out
    assert "fps=25" in out
    assert "Feasible interpretations (2)" in out


def test_cli_returns_nonzero_without_solution(tmp_path, capsys) -> None:
    path = _write_sub(tmp_path)
    rv = CalibrationCLI()([
        str(path),
        "--fps", "1001",
        "--check", "250:12000,0",
        "--check", "500:22000,0",
    ])
    out = capsys.readouterr().out
    assert rv == 1
    assert "No frame-rate satisfies" in out


def test_cli_names_excluded_suspicious_check(tmp_path, capsys) -> None:
    path = _write_sub(tmp_path)
    rv = CalibrationCLI()([
        str(path),
        "--fps", "25",
        "--check", "250:12000,100",
        "--check", "500:22000,100",
        "--check", "750:40000,100?",
    ])
    out = capsys.readouterr().out
    assert rv == 0
    assert "excluded suspicious check: #2" in out
    assert "[suspicious]" in out


def test_cli_accepts_timestamp_and_checks_file(tmp_path, capsys) -> None:
    path = _write_sub(tmp_path)
    checks_file = tmp_path / "checks.txt"
    checks_file.write_text(
        "# registration table\n250:0:00:12,1000\n500:0:00:22,1000\n",
        encoding="utf-8",
    )
    rv = CalibrationCLI()([
        str(path),
        "--fps", "25",
        "--checks-file", str(checks_file),
    ])
    out = capsys.readouterr().out
    assert rv == 0
    assert "fps=25" in out


def test_cli_requires_fps_and_checks(tmp_path) -> None:
    path = _write_sub(tmp_path)
    with pytest.raises(SystemExit):
        CalibrationCLI()([str(path), "--check", "250:12000"])
    with pytest.raises(SystemExit):
        CalibrationCLI()([str(path), "--fps", "25"])


def test_cli_missing_file_is_clean_error(tmp_path, capsys) -> None:
    rv = CalibrationCLI()([
        str(tmp_path / "missing.sub"),
        "--fps", "25",
        "--check", "250:12000,100",
    ])
    err = capsys.readouterr().err
    assert rv == 1
    assert "error:" in err
    assert "Traceback" not in err
