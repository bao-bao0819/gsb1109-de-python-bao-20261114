"""Command-line entry point for MicroDVD frame-rate/offset calibration."""

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .microdvd import (
    TimingCheck,
    calibrate_microdvd,
    format_calibration_report,
    match_check_frames,
    parse_timing_check_spec,
    read_microdvd_frames,
)


def _read_spec_lines(path: Path) -> list[str]:
    specs: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            specs.append(line)
    return specs


class CalibrationCLI:
    """CLI front-end for :func:`pysubs2.calibration.calibrate_microdvd`."""

    def __init__(self) -> None:
        parser = self.parser = argparse.ArgumentParser(
            prog="pysubs2 calibrate",
            description=(
                "Find frame-rates and lead-in offset ranges that satisfy all "
                "broadcast-time windows for a frame-only MicroDVD file. "
                "Suspicious checks (spec ending with '?') are also tried with "
                "that single check excluded; every feasible interpretation is "
                "reported."
            ),
        )
        parser.add_argument(
            "file",
            metavar="FILE",
            type=Path,
            help="Frame-only MicroDVD subtitle file (*.sub).",
        )
        parser.add_argument(
            "--fps",
            metavar="FPS",
            action="append",
            type=float,
            default=[],
            help="Candidate frame-rate from the registration table. Repeat to list several.",
        )
        parser.add_argument(
            "--check",
            dest="checks",
            metavar="SPEC",
            action="append",
            default=[],
            help=(
                "Timing check FRAME:EXPECTED[,TOLERANCE][?], where EXPECTED is "
                "milliseconds or H:MM:SS[.mmm] (use '.' for fractional seconds), "
                "TOLERANCE is a millisecond half-width (default 0) and a "
                "trailing '?' flags the check as suspicious. Repeat for every check."
            ),
        )
        parser.add_argument(
            "--checks-file",
            metavar="PATH",
            type=Path,
            default=None,
            help="Read timing check specs (one per line, '#' comments allowed) from this file.",
        )
        parser.add_argument(
            "--input-enc",
            metavar="ENCODING",
            default="utf-8",
            help="Character encoding of the MicroDVD file (default: utf-8).",
        )

    def __call__(self, argv: Sequence[str] | None = None) -> int:
        try:
            return self.main(argv if argv is not None else sys.argv[1:])
        except KeyboardInterrupt:
            print("\nAborted by user.", file=sys.stderr)
            return 1

    def main(self, argv: Sequence[str]) -> int:
        args = self.parser.parse_args(argv)

        if not args.fps:
            self.parser.error("at least one candidate --fps is required")

        specs = list(args.checks)
        if args.checks_file is not None:
            try:
                specs.extend(_read_spec_lines(args.checks_file))
            except OSError as exc:
                print(f"error: cannot read checks file {args.checks_file}: {exc}", file=sys.stderr)
                return 1
        if not specs:
            self.parser.error("at least one --check (or --checks-file) entry is required")

        try:
            checks: list[TimingCheck] = [
                parse_timing_check_spec(spec, index) for index, spec in enumerate(specs)
            ]
            event_frames = read_microdvd_frames(args.file, encoding=args.input_enc)
            results = calibrate_microdvd(
                event_frames,
                candidate_fps=args.fps,
                timing_checks=checks,
            )
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        matched_frames = match_check_frames(event_frames, checks)
        print(format_calibration_report(results, checks, matched_frames))
        return 0 if results else 1


def __main__() -> None:
    cli = CalibrationCLI()
    sys.exit(cli())


if __name__ == "__main__":
    __main__()
