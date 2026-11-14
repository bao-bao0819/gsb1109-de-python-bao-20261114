"""Command line interface for cumulative rainfall surface banding."""

from __future__ import annotations

import argparse
import json
import sys

from geojson.rainfall import build_bands


def _load(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _dump(value, path):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if path == "-":
        sys.stdout.write(text + "\n")
    else:
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Build mutually exclusive rainfall bands from cumulative surfaces. "
            "Input is a GeoJSON FeatureCollection whose thresholds are stored "
            "in feature properties, or a JSON object keyed by threshold."
        )
    )
    parser.add_argument("input", help="input JSON/GeoJSON file, or - for stdin")
    parser.add_argument("--output", "-o", default="-",
                        help="output GeoJSON file, or - for stdout")
    parser.add_argument("--threshold", "-t", action="append", type=float,
                        help="configured threshold (repeatable)")
    parser.add_argument("--report", action="store_true",
                        help="write the full band/report JSON instead of a FeatureCollection")
    parser.add_argument("--no-violations", action="store_true",
                        help="omit the conflict union feature")
    parser.add_argument("--precision", type=int, default=10)
    arguments = parser.parse_args(argv)

    payload = _load(arguments.input)
    result = build_bands(
        payload,
        thresholds=arguments.threshold,
        precision=arguments.precision,
    )
    if arguments.report:
        _dump(result.to_dict(), arguments.output)
    else:
        _dump(
            result.to_feature_collection(
                include_violations=not arguments.no_violations
            ),
            arguments.output,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
