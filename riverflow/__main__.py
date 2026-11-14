"""Command line entry point: ``python -m riverflow input.geojson out.geojson``.

The input may be one mixed FeatureCollection or a file with two
FeatureCollections (a JSON array or an object holding ``stations`` and
``reaches``).  Reconciled features are written back with ``flow_min`` /
``flow_max`` properties, and a JSON audit report goes to ``--report``.
"""

import argparse
import json
import sys

from .io import (
    dump_feature_collection,
    reconcile_layers,
    split_features,
)


def _features_of(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("type") == "FeatureCollection":
        return payload.get("features", [])
    if payload.get("type") == "Feature":
        return [payload]
    raise ValueError("unsupported GeoJSON layout in {}".format(path))


def _load(path):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        stations, reaches = [], []
        for item in payload:
            part_s, part_r, unknown = split_features(
                item.get("features", [item]))
            if unknown:
                raise ValueError("unrecognised feature geometry")
            stations.extend(part_s)
            reaches.extend(part_r)
        return stations, reaches
    if payload.get("type") in ("FeatureCollection", "Feature"):
        features = payload.get("features", [payload])
        stations, reaches, unknown = split_features(features)
        if unknown:
            raise ValueError("unrecognised feature geometry")
        return stations, reaches
    if "stations" in payload or "reaches" in payload:
        stations = payload.get("stations", {}).get("features", [])
        reaches = payload.get("reaches", {}).get("features", [])
        return stations, reaches
    raise ValueError("unsupported GeoJSON layout in {}".format(path))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reconcile daily river flows over station/reach layers.")
    parser.add_argument(
        "input",
        help="input GeoJSON (mixed layer, or the station layer with --reaches)")
    parser.add_argument(
        "output", nargs="?",
        help="output GeoJSON with flow_min/flow_max written back")
    parser.add_argument(
        "--reaches",
        help="separate reach-layer GeoJSON when the two layers are split")
    parser.add_argument("--report", help="write the JSON audit report here")
    parser.add_argument(
        "--strict", action="store_true",
        help="exit non-zero when any component is infeasible or invalid")
    args = parser.parse_args(argv)

    if args.reaches:
        s_points, s_lines, unknown_s = split_features(
            _features_of(args.input))
        r_points, r_lines, unknown_r = split_features(
            _features_of(args.reaches))
        if unknown_s or unknown_r:
            raise ValueError("unrecognised feature geometry")
        station_features = s_points + r_points
        reach_features = s_lines + r_lines
    else:
        station_features, reach_features = _load(args.input)

    report = reconcile_layers(station_features, reach_features)

    if args.output:
        dump_feature_collection(station_features + reach_features, args.output)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

    bad = (report["infeasible_component_count"] > 0
           or bool(report["structural_errors"]))
    if args.strict and bad:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
