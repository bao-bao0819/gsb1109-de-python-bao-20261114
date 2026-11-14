"""Plan a lowest-cost short-name mapping for migrating legacy DBF files.

Context
-------
Legacy station/counter DBF files store Chinese station names that are wider
than the target field.  Cleaning rules give every full name a set of
acceptable short names, each with a preference cost.  Constraints:

* within one branch (网点) every short name must be unique;
* the counter table references stations by foreign key, so counters must
  reuse the short name chosen for their station;
* a candidate is only *writable* if its encoding in the target code page
  fits the field byte width -- names are never truncated (truncating could
  split a multi-byte character), and two stations are never silently merged
  by giving them the same short name.

The module computes, per branch, the minimum-total-cost assignment of
stations to writable short names (Hungarian algorithm).  Branches that
cannot be assigned are reported together with the contended writable names.
"""

from dataclasses import dataclass, field

INF = float("inf")
_BIG = 10 ** 12  # "forbidden edge" cost for the Hungarian matrix


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------

@dataclass
class Station:
    station_id: str
    branch: str
    name: str  # full (legacy) name


@dataclass
class Counter:
    counter_id: str
    station_id: str  # foreign key -> Station.station_id


@dataclass
class AliasOption:
    alias: str
    cost: float
    nbytes: int  # actual encoded byte length in the target code page


@dataclass
class BranchConflict:
    branch: str
    # stations that could not get a name, with the writable names they
    # compete for and the station each name was given to instead
    contenders: dict = field(default_factory=dict)  # station_id -> [(alias, holder_id)]
    options: dict = field(default_factory=dict)     # station_id -> [AliasOption]


@dataclass
class MigrationPlan:
    mapping: dict = field(default_factory=dict)      # station_id -> alias
    total_cost: float = 0.0
    conflicts: list = field(default_factory=list)    # [BranchConflict]
    counter_mapping: dict = field(default_factory=dict)  # counter_id -> alias

    @property
    def ok(self):
        return not self.conflicts


# ---------------------------------------------------------------------------
# code page / byte-width checks
# ---------------------------------------------------------------------------

def encoded_len(text, codepage):
    """Byte length of *text* in *codepage*, or None if not encodable."""
    try:
        return len(text.encode(codepage))
    except UnicodeEncodeError:
        return None


def writable_aliases(name, rules, width, codepage):
    """Alias options for *name* that actually fit the target field.

    A candidate is writable only when it is encodable in the target code
    page and its full encoded form fits in *width* bytes.  Over-wide
    candidates are rejected outright -- never truncated -- so a multi-byte
    character can never be split.
    """
    options = []
    for alias, cost in rules.get(name, []):
        nbytes = encoded_len(alias, codepage)
        if nbytes is not None and nbytes <= width:
            options.append(AliasOption(alias, cost, nbytes))
    return options


# ---------------------------------------------------------------------------
# Hungarian algorithm (rectangular, minimisation, pure Python)
# ---------------------------------------------------------------------------

def hungarian(cost):
    """Minimum-cost assignment for an n x m matrix (n <= m).

    Returns (assignment, total) where assignment[i] is the column chosen
    for row i, or (None, INF) when no finite-cost perfect matching exists.
    Forbidden edges should be marked with a cost >= _BIG.
    """
    n = len(cost)
    m = len(cost[0])
    assert n <= m, "more stations than candidate names"
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)   # p[j] = row matched to column j
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    assignment = [None] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    total = -v[0]
    if total >= _BIG or any(a is None for a in assignment):
        return None, INF
    return assignment, total


# ---------------------------------------------------------------------------
# per-branch assignment
# ---------------------------------------------------------------------------

def assign_branch(branch, stations, rules, width, codepage):
    """Return (mapping, cost, conflict) for one branch.

    mapping: station_id -> alias (None when the branch is infeasible)
    conflict: BranchConflict or None
    """
    options = {s.station_id: writable_aliases(s.name, rules, width, codepage)
               for s in stations}

    # candidate name pool: union of all writable aliases in this branch
    pool = sorted({opt.alias for opts in options.values() for opt in opts})

    if len(stations) > len(pool):
        holders = _greedy_holders(stations, options)
        return None, INF, _conflict(branch, stations, options, holders)

    col = {alias: j for j, alias in enumerate(pool)}
    cost = [[_BIG] * len(pool) for _ in stations]
    for i, s in enumerate(stations):
        for opt in options[s.station_id]:
            cost[i][col[opt.alias]] = opt.cost

    assignment, total = hungarian(cost)
    if assignment is None:
        # best-effort greedy holders so the conflict report can name them
        holders = _greedy_holders(stations, options)
        return None, INF, _conflict(branch, stations, options, holders)

    mapping = {}
    for i, s in enumerate(stations):
        mapping[s.station_id] = pool[assignment[i]]
    return mapping, total, None


def _greedy_holders(stations, options):
    """Greedy (not necessarily optimal) name holders for conflict reports."""
    holders = {}
    taken = set()
    for s in sorted(stations, key=lambda s: len(options[s.station_id])):
        for opt in sorted(options[s.station_id], key=lambda o: o.cost):
            if opt.alias not in taken:
                holders[opt.alias] = s.station_id
                taken.add(opt.alias)
                break
    return holders


def _conflict(branch, stations, options, holders):
    contenders = {}
    for s in stations:
        wanted = options[s.station_id]
        if not wanted:
            contenders[s.station_id] = []  # no writable name at all
        elif all(opt.alias in holders and holders[opt.alias] != s.station_id
                 for opt in wanted):
            contenders[s.station_id] = [
                (opt.alias, holders.get(opt.alias)) for opt in wanted
            ]
    return BranchConflict(
        branch=branch,
        contenders=contenders,
        options=options,
    )


# ---------------------------------------------------------------------------
# whole-batch planning
# ---------------------------------------------------------------------------

def plan_migration(stations, counters, rules, width, codepage="cp936"):
    """Compute the lowest-cost writable mapping for the whole batch.

    stations/counters: iterables of Station/Counter
    rules: {full_name: [(alias, preference_cost), ...]}
    width: target field width in *bytes* (checked in *codepage*)
    """
    branches = {}
    for s in stations:
        branches.setdefault(s.branch, []).append(s)

    plan = MigrationPlan()
    for branch in sorted(branches):
        mapping, cost, conflict = assign_branch(
            branch, branches[branch], rules, width, codepage)
        if conflict is not None:
            plan.conflicts.append(conflict)
        else:
            plan.mapping.update(mapping)
            plan.total_cost += cost

    # FK consistency: counters inherit their station's short name.
    # A counter whose station has no mapping is surfaced, never merged.
    plan.counter_mapping = {
        c.counter_id: plan.mapping.get(c.station_id) for c in counters
    }
    orphan = [c.counter_id for c in counters
              if c.station_id not in {s.station_id for s in stations}]
    if orphan:
        raise ValueError(f"counters reference unknown stations: {orphan}")
    return plan


def render_report(plan, stations, codepage, width):
    """Human-readable migration report (Chinese)."""
    by_id = {s.station_id: s for s in stations}
    lines = []
    for branch in sorted({s.branch for s in stations}):
        lines.append(f"网点 {branch}:")
        for s in sorted(by_id.values(), key=lambda s: s.station_id):
            if s.branch != branch:
                continue
            alias = plan.mapping.get(s.station_id)
            if alias is None:
                lines.append(f"  {s.station_id} {s.name} -> <未分配>")
            else:
                nbytes = len(alias.encode(codepage))
                lines.append(
                    f"  {s.station_id} {s.name} -> {alias}"
                    f"  ({nbytes}/{width} 字节, {codepage})")
    lines.append(f"总成本: {plan.total_cost}")
    for conflict in plan.conflicts:
        lines.append(f"!! 网点 {conflict.branch} 无法一一分配:")
        for sid, wanted in conflict.contenders.items():
            s = by_id[sid]
            if not wanted:
                if conflict.options.get(sid):
                    lines.append(f"    {sid} {s.name}: 候选名称均被占用")
                else:
                    lines.append(
                        f"    {sid} {s.name}: 没有可写入的简称"
                        f"（全部超宽或无法按 {codepage} 编码）")
            else:
                for alias, holder in wanted:
                    holder_txt = holder or "无"
                    lines.append(
                        f"    {sid} {s.name}: 争用 {alias!r}"
                        f"（已分给 {holder_txt}）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# demo: build sample data, plan, and write a real DBF to prove it fits
# ---------------------------------------------------------------------------

def _demo():
    import dbf

    codepage = "cp936"
    width = 8  # bytes in the target NAME field

    stations = [
        Station("S01", "城东", "城东人民路储蓄所"),
        Station("S02", "城东", "城东人民广场分理处"),
        Station("S03", "城东", "城东东风路支行"),
        Station("S04", "城西", "城西解放路储蓄所"),
        Station("S05", "城西", "城西解放西路分理处"),
    ]
    counters = [
        Counter("C01", "S01"), Counter("C02", "S01"),
        Counter("C03", "S02"), Counter("C04", "S03"),
        Counter("C05", "S04"), Counter("C06", "S05"),
    ]
    rules = {
        "城东人民路储蓄所":   [("人民路所", 1), ("人民路", 2), ("城东人路", 4)],
        "城东人民广场分理处": [("人民广场", 1), ("人民路", 2), ("广场分理", 3)],
        "城东东风路支行":     [("东风路支", 1), ("东风路", 2)],
        "城西解放路储蓄所":   [("解放路所", 1), ("解放路", 2)],
        "城西解放西路分理处": [("解放西路", 1), ("解放路", 2), ("解放西", 3)],
    }

    plan = plan_migration(stations, counters, rules, width, codepage)
    print(render_report(plan, stations, codepage, width))

    if plan.ok:
        table = dbf.Table(
            "demo_stations.dbf",
            "STATION_ID C(8); NAME C(8); BRANCH C(12)",
            codepage=codepage,
        )
        table.open(dbf.READ_WRITE)
        for s in stations:
            alias = plan.mapping[s.station_id]
            assert len(alias.encode(codepage)) <= width  # never overflows
            table.append((s.station_id, alias, s.branch))
        table.close()
        print("已写出 demo_stations.dbf（全部名称按字节宽度校验通过）")


if __name__ == "__main__":
    _demo()
