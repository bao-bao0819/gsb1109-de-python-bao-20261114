#!/usr/bin/env python3
"""离线网点共享分发包的最小字节选择。

背景：几个离线网点只同步 DBF 部分记录，FPT 备注占传输大头。
给定每个网点明确持有的旧载荷摘要、各自所需记录，以及一份已测出
字节数的差量包目录，选出一套总字节最少的共享分发包。

规则：
- 每个网点必须能从自己已有的载荷、或下发的完整件出发，沿差量包
  逐步还原到目标载荷；不允许靠互相依赖的补丁凭空起步（无源环）。
- 目标摘要按原表名、字段定义和记录内容重新计算并核对。
- 多个网点用到同一个下发包时，该包只计一次传输预算。

输入（JSON，文件路径作为命令行参数，缺省读标准输入）：

{
  "table": "filiale",
  "fields": [{"name": "knr", "type": "C", "length": 6}, ...],
  "records": [{"key": "r1", "values": {"knr": "100001", ...}}, ...],
  "payloads": [{"digest": "<sha256>", "records": ["r1", "r2"]}, ...],   # 可选，用于核对
  "branches": [
    {"name": "nord", "held": ["<sha256>", ...], "needs": ["r1", "r2"],
     "target": "<sha256>"}                                              # target 可选，给出则核对
  ],
  "packages": [
    {"id": "f-pc",  "kind": "full",  "to": "<sha256>", "bytes": 4200},
    {"id": "d-pb-pc", "kind": "delta", "from": "<sha256>", "to": "<sha256>", "bytes": 900}
  ]
}

输出（JSON 写到标准输出）：
{
  "total_bytes": 3350,
  "selected_packages": ["d-p0-pb", "d-pb-pc", "d-pb-pd"],
  "targets":  {"nord": "<sha256>", ...},
  "plans":    {"nord": ["d-pb-pc"], ...}        # 每个网点的还原顺序
}
"""

import argparse
import hashlib
import heapq
import json
import sys
from collections import namedtuple

Package = namedtuple("Package", ["id", "src", "dst", "size"])
Branch = namedtuple("Branch", ["name", "held", "target"])

_HEX = set("0123456789abcdef")


class ProblemError(Exception):
    """输入数据不符合要求。"""


class InfeasibleError(ProblemError):
    """现有目录下无法让所有网点还原到目标。"""


def canonical_json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def payload_digest(table, fields, records):
    """按原表名、字段定义（含类型/长度/小数位）和记录内容计算载荷摘要。"""
    field_spec = [
        [f["name"], f["type"], f.get("length", 0), f.get("decimal", 0)]
        for f in fields
    ]
    rows = [
        [key, [values.get(f["name"]) for f in fields]]
        for key, values in records
    ]
    rows.sort(key=lambda row: row[0])
    doc = {"table": table, "fields": field_spec, "records": rows}
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()


def _check_digest(value, where):
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in _HEX for c in value)):
        raise ProblemError("%s: 非法摘要 %r" % (where, value))


def prepare(problem):
    """解析并核对输入，返回 (packages, branches)。"""
    table = problem["table"]
    fields = problem["fields"]
    records = {}
    for rec in problem["records"]:
        if rec["key"] in records:
            raise ProblemError("记录键重复: %r" % rec["key"])
        records[rec["key"]] = rec["values"]

    def digest_of(keys, where):
        pairs = []
        for key in keys:
            if key not in records:
                raise ProblemError("%s: 未知记录键 %r" % (where, key))
            pairs.append((key, records[key]))
        return payload_digest(table, fields, pairs)

    # 按原表和字段核对目录中出现的载荷摘要
    for entry in problem.get("payloads", []):
        actual = digest_of(entry["records"], "payloads 条目")
        if actual != entry["digest"]:
            raise ProblemError(
                "载荷摘要核对失败: 声明 %s，按原表和字段算出 %s"
                % (entry["digest"], actual))

    branches = []
    for b in problem["branches"]:
        target = digest_of(b["needs"], "网点 %s" % b["name"])
        declared = b.get("target")
        if declared is not None and declared != target:
            raise ProblemError(
                "网点 %s 目标摘要核对失败: 声明 %s，按原表和字段算出 %s"
                % (b["name"], declared, target))
        held = set(b.get("held", []))
        for digest in held:
            _check_digest(digest, "网点 %s 持有载荷" % b["name"])
        branches.append(Branch(b["name"], held, target))

    packages = {}
    for p in problem["packages"]:
        kind = p["kind"]
        src = p.get("from")
        dst = p["to"]
        if kind == "full":
            src = None
        elif kind != "delta":
            raise ProblemError("包 %s: 未知类型 %r" % (p["id"], kind))
        if src is not None:
            _check_digest(src, "包 %s 的 from" % p["id"])
        _check_digest(dst, "包 %s 的 to" % p["id"])
        if src == dst:
            raise ProblemError("包 %s: 源与目标相同" % p["id"])
        size = p["bytes"]
        if not isinstance(size, int) or size <= 0:
            raise ProblemError("包 %s: 字节数必须为正整数" % p["id"])
        key = (src, dst)
        old = packages.get(key)
        candidate = Package(p["id"], src, dst, size)
        if old is None or candidate.size < old.size:
            packages[key] = candidate  # 同起讫的重复包只留最便宜的
    return list(packages.values()), branches


def _closure(held, full_dsts, deltas):
    """从已有载荷和完整件出发可达的载荷集合（无源环不会凭空可达）。"""
    reach = set(held)
    reach.update(full_dsts)
    changed = True
    while changed:
        changed = False
        for src, dst in deltas:
            if src in reach and dst not in reach:
                reach.add(dst)
                changed = True
    return reach


def _feasible(selected, branches):
    fulls = [p.dst for p in selected if p.src is None]
    deltas = [(p.src, p.dst) for p in selected if p.src is not None]
    return all(b.target in _closure(b.held, fulls, deltas) for b in branches)


def _greedy_seed(packages, branches):
    """每个网点各自求一条最省路径，取并集作为可行上界。"""
    fulls = [p for p in packages if p.src is None]
    deltas = [p for p in packages if p.src is not None]
    chosen = set()
    for b in branches:
        if b.target in b.held:
            continue
        dist = {d: 0 for d in b.held}
        prev = {}
        pq = [(0, d) for d in b.held]
        heapq.heapify(pq)
        while pq:
            cost, state = heapq.heappop(pq)
            if cost > dist.get(state, float("inf")):
                continue
            if state == b.target:
                break
            for p in fulls:  # 完整件任何时刻都可用
                new = cost + p.size
                if p.dst != state and new < dist.get(p.dst, float("inf")):
                    dist[p.dst] = new
                    prev[p.dst] = (state, p)
                    heapq.heappush(pq, (new, p.dst))
            for p in deltas:
                if p.src == state:
                    new = cost + p.size
                    if new < dist.get(p.dst, float("inf")):
                        dist[p.dst] = new
                        prev[p.dst] = (state, p)
                        heapq.heappush(pq, (new, p.dst))
        if b.target not in dist:
            return None
        state = b.target
        while state in prev:
            state, p = prev[state]
            chosen.add(packages.index(p))
    return [packages[i] for i in chosen]


def minimize(packages, branches):
    """分支定界求总字节最少的包集合，返回 (cost, packages)。"""
    if not _feasible(packages, branches):
        raise InfeasibleError("差量包目录无法覆盖所有网点目标")

    seed = _greedy_seed(packages, branches)
    best_sel = seed if seed is not None else list(packages)
    best_cost = sum(p.size for p in best_sel)

    order = sorted(range(len(packages)), key=lambda i: -packages[i].size)
    current = []

    def recurse(k, cost):
        nonlocal best_sel, best_cost
        if cost >= best_cost:
            return
        remaining = current + [packages[order[j]] for j in range(k, len(order))]
        if not _feasible(remaining, branches):
            return
        if k == len(order):
            best_sel = list(current)
            best_cost = cost
            return
        pkg = packages[order[k]]
        recurse(k + 1, cost)  # 先试不选（更省字节）
        current.append(pkg)
        recurse(k + 1, cost + pkg.size)
        current.pop()

    recurse(0, 0)
    return best_cost, best_sel


def _build_plan(selected, branch):
    """给出该网点从已有载荷/完整件出发的还原顺序（包 id 列表）。"""
    fulls = [p for p in selected if p.src is None]
    deltas = [p for p in selected if p.src is not None]
    path = {d: [] for d in branch.held}
    for p in fulls:
        if p.dst not in path:
            path[p.dst] = [p.id]
    changed = True
    while changed:
        changed = False
        for p in deltas:
            if p.src in path and p.dst not in path:
                path[p.dst] = path[p.src] + [p.id]
                changed = True
    return path.get(branch.target)


def solve_problem(problem):
    packages, branches = prepare(problem)
    cost, chosen = minimize(packages, branches)
    plans = {}
    for b in branches:
        plan = _build_plan(chosen, b)
        if plan is None:  # 理论上不会发生，minimize 已保证可行
            raise InfeasibleError("网点 %s 无法还原" % b.name)
        plans[b.name] = plan
    return {
        "total_bytes": cost,
        "selected_packages": sorted(p.id for p in chosen),
        "targets": {b.name: b.target for b in branches},
        "plans": plans,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", nargs="?", default="-",
                        help="输入 JSON 文件，缺省读标准输入")
    args = parser.parse_args(argv)
    try:
        with (sys.stdin if args.input == "-"
              else open(args.input, encoding="utf-8")) as fh:
            problem = json.load(fh)
        result = solve_problem(problem)
    except ProblemError as exc:
        print("错误: %s" % exc, file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
