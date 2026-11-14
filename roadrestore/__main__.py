"""命令行入口：``python -m roadrestore 输入.geojson``。

输入为 GeoJSON FeatureCollection，每个 Feature 需带道路号与起止里程属性。
默认输出包含 routes/gaps/manual_review 三段的 JSON；
``--format geojson`` 改为输出还原后的 LineString FeatureCollection。
"""

from __future__ import annotations

import argparse
import json
import sys

from roadrestore.restore import (
    result_as_dict,
    result_as_geojson,
    restore_from_features,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m roadrestore",
        description="按道路号与里程还原被切碎、方向混杂的道路中心线。")
    parser.add_argument(
        "input",
        help="输入 GeoJSON FeatureCollection 路径，- 表示标准输入。")
    parser.add_argument(
        "-o", "--output",
        help="输出 JSON 路径，缺省写到标准输出。")
    parser.add_argument(
        "-t", "--tolerance", type=float, default=0.0,
        help="端点平面距离容差，与坐标同单位，默认 0。")
    parser.add_argument(
        "--format", choices=("report", "geojson"), default="report",
        help="report 输出三类结果字典，geojson 输出线路要素集合。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.input == "-":
        collection = json.load(sys.stdin)
    else:
        with open(args.input, encoding="utf-8") as handle:
            collection = json.load(handle)

    features = collection.get("features")
    if not isinstance(features, list):
        raise SystemExit("输入必须是 GeoJSON FeatureCollection")

    result = restore_from_features(features,
                                   endpoint_tolerance=args.tolerance)
    payload = (result_as_geojson(result) if args.format == "geojson"
               else result_as_dict(result))
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
