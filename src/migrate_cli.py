"""
Command line entry point for the grid migration analysis.

Input JSON schema::

    {
      "grid": {
        "lat_origin": "0", "lat_step": "0.000005",
        "lng_origin": "0", "lng_step": "0.000005"
      },
      "records": [
        {"id": "P001", "archive": "<p5 polyline string>", "original": null},
        {"id": "P002", "archive": "<p5 string>", "original": "<p6 string>"}
      ]
    }

All grid numbers are parsed as exact fractions from strings, so decimal
inputs never pick up binary floating point error.
"""

import argparse
import json
from fractions import Fraction
from typing import Any, Dict, List

from grid_migration import GridSpec, analyze_records, format_report


def _fraction(value: Any) -> Fraction:
    return Fraction(str(value))


def grid_from_config(config: Dict[str, Any]) -> GridSpec:
    return GridSpec(
        lat_origin=_fraction(config["lat_origin"]),
        lat_step=_fraction(config["lat_step"]),
        lng_origin=_fraction(config["lng_origin"]),
        lng_step=_fraction(config["lng_step"]),
    )


def records_from_config(config: Dict[str, Any]):
    records = []
    for row in config["records"]:
        records.append((row["id"], row["archive"], row.get("original")))
    return records


def run(input_path: str) -> str:
    with open(input_path, encoding="utf-8") as handle:
        config = json.load(handle)
    grid = grid_from_config(config["grid"])
    results = analyze_records(records_from_config(config), grid)
    return format_report(results)


def _demo_config() -> Dict[str, Any]:
    import polyline

    point = (40.641552, -8.653441)
    return {
        "grid": {
            "lat_origin": "0", "lat_step": "0.000005",
            "lng_origin": "0", "lng_step": "0.000005",
        },
        "records": [
            {
                "id": "demo-archive-only",
                "archive": polyline.encode([point], 5),
                "original": None,
            },
            {
                "id": "demo-with-original",
                "archive": polyline.encode([point], 5),
                "original": polyline.encode([point], 6),
            },
            {
                "id": "demo-conflict",
                "archive": polyline.encode([(40.64155, -8.653)], 5),
                "original": polyline.encode([(40.6416, -8.653)], 6),
            },
        ],
    }


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="path to the records JSON")
    parser.add_argument(
        "--demo", action="store_true", help="run the built-in synthetic demo"
    )
    args = parser.parse_args(argv)

    if args.demo:
        config = _demo_config()
        grid = grid_from_config(config["grid"])
        results = analyze_records(records_from_config(config), grid)
        print(format_report(results))
        return 0
    if not args.input:
        parser.error("provide an input JSON path or use --demo")
    print(run(args.input))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
