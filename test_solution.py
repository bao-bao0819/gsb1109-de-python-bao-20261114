import copy
import json
import os

import pytest

import solution

SAMPLE = os.path.join(os.path.dirname(__file__), "sample_input.json")


def load_sample():
    with open(SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


def package_bytes(problem):
    return {p["id"]: p["bytes"] for p in problem["packages"]}


def test_sample_optimum():
    problem = load_sample()
    result = solution.solve_problem(problem)
    assert result["total_bytes"] == 3350
    assert result["selected_packages"] == ["d-p0-pb", "d-pb-pc", "d-pb-pd"]


def test_shared_package_counted_once():
    problem = load_sample()
    result = solution.solve_problem(problem)
    sizes = package_bytes(problem)
    # sued 和 ost 都用到了 d-p0-pb，但预算只计一次
    assert "d-p0-pb" in result["plans"]["sued"]
    assert "d-p0-pb" in result["plans"]["ost"]
    assert result["total_bytes"] == sum(
        sizes[pid] for pid in result["selected_packages"])


def test_cycle_cannot_bootstrap():
    problem = load_sample()
    result = solution.solve_problem(problem)
    # 环状补丁再便宜也不能凭空起步
    assert not any("px" in pid or "py" in pid
                   for pid in result["selected_packages"])
    # 只剩环状补丁时问题不可行
    broken = copy.deepcopy(problem)
    broken["packages"] = [p for p in problem["packages"]
                          if p["id"] in ("d-px-py", "d-py-px", "d-py-pd")]
    with pytest.raises(solution.InfeasibleError):
        solution.solve_problem(broken)


def test_full_package_grounds_chain():
    problem = load_sample()
    # 拿掉所有增量包，只剩完整件也能落地
    only_full = copy.deepcopy(problem)
    only_full["packages"] = [p for p in problem["packages"]
                             if p["kind"] == "full"]
    result = solution.solve_problem(only_full)
    sizes = package_bytes(problem)
    assert result["total_bytes"] == sizes["f-pc"] + sizes["f-pd"]
    assert result["plans"]["nord"] == ["f-pc"]
    assert result["plans"]["sued"] == ["f-pd"]


def test_target_digest_mismatch_rejected():
    problem = load_sample()
    problem["branches"][0]["target"] = "0" * 64
    with pytest.raises(solution.ProblemError):
        solution.solve_problem(problem)


def test_payload_digest_mismatch_rejected():
    problem = load_sample()
    problem["payloads"][0]["digest"] = "f" * 64
    with pytest.raises(solution.ProblemError):
        solution.solve_problem(problem)


def test_digest_depends_on_table_and_fields():
    problem = load_sample()
    base = solution.payload_digest(
        problem["table"], problem["fields"],
        [(r["key"], r["values"]) for r in problem["records"][:1]])
    other_fields = copy.deepcopy(problem["fields"])
    other_fields[0]["length"] += 1
    changed = solution.payload_digest(
        problem["table"], other_fields,
        [(r["key"], r["values"]) for r in problem["records"][:1]])
    renamed = solution.payload_digest(
        "andere", problem["fields"],
        [(r["key"], r["values"]) for r in problem["records"][:1]])
    assert base != changed
    assert base != renamed


def test_branch_already_holds_target():
    problem = load_sample()
    result = solution.solve_problem(problem)
    nord_target = result["targets"]["nord"]
    problem["branches"][0]["held"].append(nord_target)
    result = solution.solve_problem(problem)
    assert result["plans"]["nord"] == []


def test_plan_chain_reaches_target():
    problem = load_sample()
    result = solution.solve_problem(problem)
    packages = {p["id"]: p for p in problem["packages"]}
    for branch in problem["branches"]:
        reach = set(branch["held"])
        for pid in result["plans"][branch["name"]]:
            pkg = packages[pid]
            if pkg["kind"] == "delta":
                assert pkg["from"] in reach  # 每步都有立足点
            reach.add(pkg["to"])
        assert result["targets"][branch["name"]] in reach
