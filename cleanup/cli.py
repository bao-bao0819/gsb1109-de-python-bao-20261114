"""
Command line entry point.

    python -m cleanup.cli --data ./data                 # dry run (default)
    python -m cleanup.cli --data ./data --apply         # physical cleanup
    python -m cleanup.cli --data ./data --report out.txt
    python -m cleanup.cli --fixture ./data              # (re)build sample tables

A dry run opens every table READ_ONLY, hashes every guarded file before
and after, and refuses to continue if any byte changed.
"""

from __future__ import annotations

import argparse
import sys

from .apply import apply_cleanup
from .fixture import build_fixture
from .model import analyze, hash_files, schema_files
from .report import render_report
from .schema import SCHEMA


def run_dry_run(data_dir):
    paths = schema_files(data_dir, SCHEMA)
    before = hash_files(paths)
    result = analyze(data_dir, SCHEMA)
    after = hash_files(paths)
    if before != after:
        raise RuntimeError(
            "dry run modified a file; aborting: %r"
            % [p for p in paths if before[p] != after.get(p)]
        )
    report = render_report(result, hashes_before=before, hashes_after=after)
    return result, report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Referential-integrity aware cleanup of deletion-marked DBF rows."
    )
    parser.add_argument("--data", default="./data", help="directory with the .dbf files")
    parser.add_argument("--apply", action="store_true", help="physically pack removable rows")
    parser.add_argument("--fixture", metavar="DIR", help="(re)build the sample tables in DIR and exit")
    parser.add_argument("--report", metavar="FILE", help="also write the report to FILE")
    args = parser.parse_args(argv)

    if args.fixture:
        build_fixture(args.fixture)
        print("fixture written to %s" % args.fixture)
        return 0

    result, report = run_dry_run(args.data)
    print(report)

    if args.apply:
        counts = apply_cleanup(args.data, SCHEMA, result)
        print("")
        print("Applied physical cleanup:")
        for table_name in sorted(counts):
            print("  %s: %d row(s) removed" % (table_name, counts[table_name]))
        # prove the post-apply state matches a fresh analysis
        post = analyze(args.data, SCHEMA)
        if post.removable:
            raise RuntimeError(
                "post-apply re-analysis still finds removable rows: %r"
                % sorted(post.removable, key=lambda r: (r.table, r.recno))
            )
        print("  verification: no removable rows remain; markers on retained rows preserved.")

    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report)
            handle.write("\n")
        print("report written to %s" % args.report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
