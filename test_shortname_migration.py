import itertools

import shortname_migration as sm
from shortname_migration import Station, Counter, plan_migration


def brute_force_best(stations, rules, width, codepage):
    """Exhaustive optimum used to cross-check the Hungarian assignment."""
    options = {
        s.station_id: sm.writable_aliases(s.name, rules, width, codepage)
        for s in stations
    }
    best = None
    for combo in itertools.product(*(options[s.station_id] for s in stations)):
        aliases = [o.alias for o in combo]
        if len(set(aliases)) != len(aliases):
            continue
        cost = sum(o.cost for o in combo)
        if best is None or cost < best[0]:
            best = (cost, dict(zip((s.station_id for s in stations), aliases)))
    return best


def test_encoded_len_uses_target_codepage():
    # GBK: every CJK char is exactly 2 bytes
    assert sm.encoded_len("人民路所", "cp936") == 8
    assert sm.encoded_len("人民路所", "utf-8") == 12
    # unencodable character -> None, not an exception
    assert sm.encoded_len("分行😀", "cp936") is None


def test_overwide_alias_rejected_not_truncated():
    rules = {"某超长营业网点名称": [("超长营业网点名", 1), ("超长", 2)]}
    opts = sm.writable_aliases("某超长营业网点名称", rules, 8, "cp936")
    assert [o.alias for o in opts] == ["超长"]  # 14-byte alias dropped whole
    # nothing is ever byte-sliced: every returned alias round-trips
    for o in opts:
        assert o.alias.encode("cp936").decode("cp936") == o.alias


def test_min_cost_assignment_matches_brute_force():
    stations = [
        Station("S1", "城东", "城东人民路储蓄所"),
        Station("S2", "城东", "城东人民广场分理处"),
        Station("S3", "城东", "城东东风路支行"),
    ]
    rules = {
        "城东人民路储蓄所":   [("人民路所", 1), ("人民路", 2), ("东风路", 5)],
        "城东人民广场分理处": [("人民广场", 1), ("人民路", 2)],
        "城东东风路支行":     [("东风路支", 1), ("东风路", 2), ("人民路", 6)],
    }
    plan = plan_migration(stations, [], rules, 8, "cp936")
    assert plan.ok
    expected_cost, _ = brute_force_best(stations, rules, 8, "cp936")
    assert plan.total_cost == expected_cost
    assert len(set(plan.mapping.values())) == len(stations)  # unique in branch


def test_uniqueness_is_per_branch_not_global():
    stations = [
        Station("S1", "城东", "甲营业所"),
        Station("S2", "城西", "乙营业所"),
    ]
    rules = {"甲营业所": [("营业所", 1)], "乙营业所": [("营业所", 1)]}
    plan = plan_migration(stations, [], rules, 8, "cp936")
    assert plan.ok  # same alias in different branches is allowed
    assert plan.mapping == {"S1": "营业所", "S2": "营业所"}


def test_counters_inherit_station_alias():
    stations = [Station("S1", "城东", "甲营业所")]
    counters = [Counter("C1", "S1"), Counter("C2", "S1")]
    rules = {"甲营业所": [("甲所", 1)]}
    plan = plan_migration(stations, counters, rules, 8, "cp936")
    assert plan.counter_mapping == {"C1": "甲所", "C2": "甲所"}


def test_infeasible_branch_reports_contenders():
    stations = [
        Station("S1", "城东", "甲营业所"),
        Station("S2", "城东", "乙营业所"),
        Station("S3", "城东", "丙营业所"),
    ]
    rules = {
        "甲营业所": [("营业所", 1)],
        "乙营业所": [("营业所", 1)],
        "丙营业所": [("营业所", 1)],  # 3 stations, 1 writable name
    }
    plan = plan_migration(stations, [], rules, 8, "cp936")
    assert not plan.ok
    conflict = plan.conflicts[0]
    assert conflict.branch == "城东"
    # the two losing stations are reported as contending for '营业所'
    assert len(conflict.contenders) == 2
    for wanted in conflict.contenders.values():
        assert [alias for alias, _ in wanted] == ["营业所"]
    # nothing was silently merged: no partial mapping leaked into the plan
    assert plan.mapping == {}


def test_station_with_no_writable_name_is_reported():
    stations = [Station("S1", "城东", "甲营业所")]
    rules = {"甲营业所": [("营业所超长名", 1)]}  # 12 bytes > 8
    plan = plan_migration(stations, [], rules, 8, "cp936")
    assert not plan.ok
    assert plan.conflicts[0].contenders == {"S1": []}


def test_orphan_counter_rejected():
    stations = [Station("S1", "城东", "甲营业所")]
    counters = [Counter("C9", "S404")]
    rules = {"甲营业所": [("甲所", 1)]}
    try:
        plan_migration(stations, counters, rules, 8, "cp936")
    except ValueError as exc:
        assert "C9" in str(exc)
    else:
        raise AssertionError("orphan counter must raise")
