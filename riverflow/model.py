"""Flow-network model for daily river discharge reconciliation.

The river is a directed graph.  Every reach (arc) carries a non-negative
daily flow; every station (node) is conservative except for the explicitly
marked inflow and outflow boundaries, whose external exchange is itself
bounded by a reading or a reading interval.

Stations may also carry a *through-flow* reading ("only the total is known
at this fork").  To bound that quantity each station is split into an
``in`` node and an ``out`` node joined by a station arc; every incoming
reach lands on ``in`` and every outgoing reach leaves from ``out``.

For one weakly connected component the constraints are a linear program:

    conservation at every split node,
    lower <= flow of every reach <= upper,
    lower <= through-flow of every station arc <= upper,
    lower <= external exchange of every boundary station <= upper.

Feasibility and per-arc minima/maxima are solved with pure max-flow
(Hoffman's circulation construction), so the two branches of a fork each
get their *own* independently reachable maximum.  Nothing in here assumes
two branches can be maximal at the same time.
"""

from dataclasses import dataclass, field

from .maxflow import Dinic, EPS

INF = float("inf")
SUPER_SOURCE = "@@super_source@@"
SUPER_SINK = "@@super_sink@@"
OUTSIDE = "@@outside@@"
_IN = "@in"
_OUT = "@out"


def in_node(identifier):
    return identifier + _IN


def out_node(identifier):
    return identifier + _OUT


@dataclass
class Reach:
    """A directed river segment between two stable station ids."""

    identifier: str
    source: str
    target: str
    lower: float = 0.0
    upper: float = INF


@dataclass
class Station:
    """A gauging station.

    ``ext_lower`` / ``ext_upper`` bound the net external injection
    (positive for inflows, negative for outflows).  Interior stations
    carry zero-width ``(0, 0)`` ext bounds so conservation is enforced.

    ``pass_lower`` / ``pass_upper`` bound the total flow passing through
    the station (the station arc).  Unconstrained by default.
    """

    identifier: str
    ext_lower: float = 0.0
    ext_upper: float = 0.0
    kind: str = "interior"
    pass_lower: float = 0.0
    pass_upper: float = INF


@dataclass
class Violation:
    """Locatable explanation of why a component cannot be reconciled."""

    component: list
    cut: dict
    core: dict
    message: str


@dataclass
class ComponentResult:
    station_ids: list
    reach_ids: list
    feasible: bool
    reach_bounds: dict = field(default_factory=dict)
    station_bounds: dict = field(default_factory=dict)
    feasible_flows: dict = field(default_factory=dict)
    violation: object = None


def _clean(value):
    """Round away floating point dust while keeping ``inf`` intact."""
    if value == INF or value == -INF:
        return value
    rounded = round(value, 9)
    if abs(rounded) < EPS:
        return 0.0
    return rounded


def _relaxed_station(station):
    return Station(station.identifier, -INF, INF, station.kind, 0.0, INF)


class _ComponentNetwork:
    """Hoffman super-source/super-sink circulation for one component."""

    def __init__(self, stations, reaches):
        self.stations = list(stations)
        self.reaches = list(reaches)
        node_ids = []
        for station in self.stations:
            node_ids += [in_node(station.identifier), out_node(station.identifier)]
        node_ids += [OUTSIDE, SUPER_SOURCE, SUPER_SINK]
        self.index = {node: i for i, node in enumerate(node_ids)}
        self.dinic = Dinic(len(node_ids))
        self.demand = {node: 0.0 for node in node_ids}
        # id -> (forward arc, capacity above lower, lower)
        self.reach_arcs = {}
        self.station_arcs = {}
        # station id -> (injection arcs, abstraction arcs)
        self.external_arcs = {}
        self._build()

    def _add_lower_upper(self, source, target, lower, upper):
        arc = self.dinic.add_edge(self.index[source], self.index[target],
                                  upper - lower)
        self.demand[source] -= lower
        self.demand[target] += lower
        return arc, upper - lower

    def _build(self):
        # Station arcs carry the through-flow reading.
        for station in self.stations:
            arc, capacity = self._add_lower_upper(
                in_node(station.identifier), out_node(station.identifier),
                station.pass_lower, station.pass_upper)
            self.station_arcs[station.identifier] = (
                arc, capacity, station.pass_lower)

        # Reaches connect the out-node upstream to the in-node downstream.
        for reach in self.reaches:
            arc, capacity = self._add_lower_upper(
                out_node(reach.source), in_node(reach.target),
                reach.lower, reach.upper)
            self.reach_arcs[reach.identifier] = (arc, capacity, reach.lower)

        # External exchange as two non-negative arcs so that infinite
        # signed bounds never enter the lower-shift construction.
        for station in self.stations:
            lower, upper = station.ext_lower, station.ext_upper
            injections, abstractions = [], []
            if upper > EPS:
                arc_lower = max(lower, 0.0)
                arc, capacity = self._add_lower_upper(
                    OUTSIDE, in_node(station.identifier), arc_lower, upper)
                injections.append((arc, capacity, arc_lower))
            if lower < -EPS:
                arc_lower = max(-upper, 0.0)
                arc_upper = -lower
                arc, capacity = self._add_lower_upper(
                    out_node(station.identifier), OUTSIDE,
                    arc_lower, arc_upper)
                abstractions.append((arc, capacity, arc_lower))
            self.external_arcs[station.identifier] = (
                injections, abstractions)

        total_supply = 0.0
        for node, balance in self.demand.items():
            if balance > EPS:
                self.dinic.add_edge(self.index[SUPER_SOURCE],
                                    self.index[node], balance)
                total_supply += balance
            elif balance < -EPS:
                self.dinic.add_edge(self.index[node],
                                    self.index[SUPER_SINK], -balance)
        self.total_supply = total_supply

    def solve(self):
        """Return (feasible, reach flows, ext flows, through flows)."""
        pushed = self.dinic.max_flow(self.index[SUPER_SOURCE],
                                     self.index[SUPER_SINK])
        feasible = pushed >= self.total_supply - 1e-7
        reach_flows, external_flows, pass_flows = {}, {}, {}
        if feasible:
            for identifier, (arc, capacity, lower) in self.reach_arcs.items():
                reach_flows[identifier] = lower + self.dinic.flow_on(arc)
            for identifier, (arc, capacity, lower) in self.station_arcs.items():
                pass_flows[identifier] = lower + self.dinic.flow_on(arc)
            for identifier, (injections, abstractions) in \
                    self.external_arcs.items():
                injection = sum(
                    lower + self.dinic.flow_on(arc)
                    for arc, capacity, lower in injections)
                abstraction = sum(
                    lower + self.dinic.flow_on(arc)
                    for arc, capacity, lower in abstractions)
                external_flows[identifier] = injection - abstraction
        return feasible, reach_flows, external_flows, pass_flows

    def residual_view(self, reach_flows, external_flows, pass_flows):
        """Build one residual Dinic over the split nodes plus OUTSIDE.

        Returns ``(view, pos, registry)``.  The registry records, for every
        logical arc, its two endpoint nodes and the forward residual arc
        object so individual arcs can be temporarily detached while probing
        whether a rerouting cycle exists.
        """
        nodes = []
        for station in self.stations:
            nodes += [in_node(station.identifier), out_node(station.identifier)]
        nodes.append(OUTSIDE)
        view = Dinic(len(nodes))
        pos = {node: i for i, node in enumerate(nodes)}
        registry = {"reach": {}, "station": {}, "external": {}}

        for reach in self.reaches:
            flow = reach_flows[reach.identifier]
            source, target = out_node(reach.source), in_node(reach.target)
            forward = view.add_edge(
                pos[source], pos[target], reach.upper - flow)
            view._graph[forward.to][forward.rev].cap = flow - reach.lower
            registry["reach"][reach.identifier] = (
                source, target, flow, reach.lower, reach.upper, forward)

        for station in self.stations:
            flow = pass_flows[station.identifier]
            source, target = (in_node(station.identifier),
                              out_node(station.identifier))
            forward = view.add_edge(
                pos[source], pos[target], station.pass_upper - flow)
            view._graph[forward.to][forward.rev].cap = (
                flow - station.pass_lower)
            registry["station"][station.identifier] = (
                source, target, flow, station.pass_lower,
                station.pass_upper, forward)

            # Signed exchange as one conceptual arc OUTSIDE -> in node.
            ext_flow = external_flows[station.identifier]
            ext_source, ext_target = OUTSIDE, in_node(station.identifier)
            ext_forward = view.add_edge(
                pos[ext_source], pos[ext_target],
                station.ext_upper - ext_flow)
            view._graph[ext_forward.to][ext_forward.rev].cap = (
                ext_flow - station.ext_lower)
            registry["external"][station.identifier] = (
                ext_source, ext_target, ext_flow, station.ext_lower,
                station.ext_upper, ext_forward)
        return view, pos, registry


def _detach(view, forward):
    """Remove a logical arc's two residual edges, remembering capacities."""
    reverse = view._graph[forward.to][forward.rev]
    saved = (forward.cap, reverse.cap)
    forward.cap = 0.0
    reverse.cap = 0.0
    return saved


def _restore(view, forward, saved):
    reverse = view._graph[forward.to][forward.rev]
    forward.cap, reverse.cap = saved


def _arc_extrema(view, pos, record):
    """Independently reachable min/max of one logical arc.

    The examined arc is detached so it cannot serve as its own rerouting
    path.  A cheap reachability probe first proves when an extremum is
    already pinned; a graph copy is only paid when flow can actually move.
    """
    source, target, flow, lower, upper, forward = record
    source_idx, target_idx = pos[source], pos[target]

    saved = _detach(view, forward)

    # Increase: a return path target -> source must survive the removal.
    increase = 0.0
    if source_idx in view.reachable(target_idx):
        probe = view.copy()
        increase = probe.max_flow(target_idx, source_idx)
    upper_value = _clean(min(flow + increase, upper))

    # Decrease: a rerouting path source -> target must survive the removal.
    decrease = 0.0
    if target_idx in view.reachable(source_idx):
        probe = view.copy()
        decrease = probe.max_flow(source_idx, target_idx)
    lower_value = _clean(max(flow - decrease, lower))

    _restore(view, forward, saved)
    return lower_value, upper_value


def _cut_certificate(network):
    """Translate the failed max-flow cut into a readable balance certificate.

    In Hoffman's construction the violated node set lies on the super-sink
    side of the residual cut, i.e. the stations not reachable from the
    super source.
    """
    reachable = network.dinic.reachable(network.index[SUPER_SOURCE])
    aux = {SUPER_SOURCE, SUPER_SINK, OUTSIDE}
    inside = set()
    for node, idx in network.index.items():
        if idx in reachable or node in aux:
            continue
        if node.endswith(_IN):
            inside.add(node[:-len(_IN)])
        elif node.endswith(_OUT):
            inside.add(node[:-len(_OUT)])
    if not inside:
        inside = {s.identifier for s in network.stations}

    leaving, entering = [], []
    outflow_min = 0.0
    inflow_max = 0.0
    for reach in network.reaches:
        if reach.source in inside and reach.target not in inside:
            leaving.append(reach.identifier)
            outflow_min += reach.lower
        elif reach.target in inside and reach.source not in inside:
            entering.append(reach.identifier)
            inflow_max += reach.upper

    inflow_stations, outflow_stations = [], []
    for station in network.stations:
        if station.identifier not in inside:
            continue
        if station.kind == "inflow":
            inflow_stations.append(station.identifier)
            inflow_max += station.ext_upper
        elif station.kind == "outflow":
            outflow_stations.append(station.identifier)
            outflow_min += -station.ext_upper

    gap = _clean(outflow_min - inflow_max)
    cut = {
        "stations": sorted(inside),
        "leaving_reaches": leaving,
        "entering_reaches": entering,
        "inflow_stations": inflow_stations,
        "outflow_stations": outflow_stations,
        "outflow_minimum": _clean(outflow_min),
        "inflow_maximum": _clean(inflow_max),
        "gap": gap,
    }
    message = (
        "minimum required outflow {out} exceeds maximum available inflow "
        "{in_} by {gap} for station set {stations}".format(
            out=_clean(outflow_min), in_=_clean(inflow_max),
            gap=gap, stations=sorted(inside)))
    return cut, message


def _infeasible_core(stations, reaches):
    """Irreducible constraint set responsible for the infeasibility.

    Each constraint is tentatively relaxed; if the component stays
    infeasible without it, it is not part of the core and stays relaxed.
    The result is irreducible (every listed constraint is needed) though
    not necessarily the unique minimum-cardinality set.
    """
    active_reaches = {r.identifier for r in reaches}
    active_stations = {s.identifier for s in stations}

    for reach in list(reaches):
        active_reaches.discard(reach.identifier)
        trial_reaches = [
            r if r.identifier in active_reaches
            else Reach(r.identifier, r.source, r.target, 0.0, INF)
            for r in reaches
        ]
        network = _ComponentNetwork(stations, trial_reaches)
        feasible, _, _, _ = network.solve()
        if feasible:
            active_reaches.add(reach.identifier)

    for station in list(stations):
        active_stations.discard(station.identifier)
        trial_stations = [
            s if s.identifier in active_stations else _relaxed_station(s)
            for s in stations
        ]
        trial_reaches = [
            r if r.identifier in active_reaches
            else Reach(r.identifier, r.source, r.target, 0.0, INF)
            for r in reaches
        ]
        network = _ComponentNetwork(trial_stations, trial_reaches)
        feasible, _, _, _ = network.solve()
        if feasible:
            active_stations.add(station.identifier)

    return {
        "reach_ids": sorted(active_reaches),
        "station_ids": sorted(active_stations),
    }


def solve_component(stations, reaches):
    """Reconcile one weakly connected component."""
    network = _ComponentNetwork(stations, reaches)
    feasible, reach_flows, external_flows, pass_flows = network.solve()
    station_ids = [s.identifier for s in stations]
    reach_ids = [r.identifier for r in reaches]

    if not feasible:
        cut, message = _cut_certificate(network)
        core = _infeasible_core(stations, reaches)
        return ComponentResult(
            station_ids=station_ids, reach_ids=reach_ids, feasible=False,
            violation=Violation(station_ids, cut, core, message))

    view, pos, registry = network.residual_view(
        reach_flows, external_flows, pass_flows)
    reach_bounds, station_bounds = {}, {}
    for reach in reaches:
        reach_bounds[reach.identifier] = _arc_extrema(
            view, pos, registry["reach"][reach.identifier])
    for station in stations:
        if station.kind == "interior":
            station_bounds[station.identifier] = _arc_extrema(
                view, pos, registry["station"][station.identifier])
        else:
            station_bounds[station.identifier] = _arc_extrema(
                view, pos, registry["external"][station.identifier])

    return ComponentResult(
        station_ids=station_ids, reach_ids=reach_ids, feasible=True,
        reach_bounds=reach_bounds, station_bounds=station_bounds,
        feasible_flows={
            "reaches": reach_flows,
            "stations": external_flows,
            "through": pass_flows,
        })


def weak_components(stations, reaches):
    """Split the network into weakly connected components."""
    adjacency = {s.identifier: set() for s in stations}
    for reach in reaches:
        adjacency.setdefault(reach.source, set()).add(reach.target)
        adjacency.setdefault(reach.target, set()).add(reach.source)

    unseen = set(adjacency)
    components = []
    while unseen:
        seed = next(iter(unseen))
        stack = [seed]
        group = set()
        while stack:
            node = stack.pop()
            if node in group:
                continue
            group.add(node)
            stack.extend(adjacency[node])
        unseen -= group
        components.append(group)

    station_by_id = {s.identifier: s for s in stations}
    results = []
    for group in components:
        group_stations = [
            station_by_id[node] for node in group if node in station_by_id]
        group_reaches = [r for r in reaches
                         if r.source in group and r.target in group]
        results.append((group_stations, group_reaches))
    return results


def reconcile(stations, reaches):
    """Solve every weak component independently.

    Returns ``(results, errors)`` where ``errors`` lists structural data
    problems with the offending element id.  Infeasible components carry a
    :class:`Violation`; all other components still receive full bounds.
    """
    errors = validate(stations, reaches)
    if errors:
        return [], errors

    results = []
    for group_stations, group_reaches in weak_components(stations, reaches):
        results.append(solve_component(group_stations, group_reaches))
    return results, []


def validate(stations, reaches):
    """Return structural problems (unknown endpoints, bad bounds, ...)."""
    errors = []
    seen_stations = set()
    for station in stations:
        if not station.identifier:
            errors.append({
                "element": station.identifier, "kind": "station",
                "message": "station is missing an id"})
        if station.identifier in seen_stations:
            errors.append({
                "element": station.identifier, "kind": "station",
                "message": "duplicate station id"})
        seen_stations.add(station.identifier)
        if station.ext_lower > station.ext_upper + EPS:
            errors.append({
                "element": station.identifier, "kind": "station",
                "message": "flow lower bound exceeds upper bound"})
        if station.pass_lower > station.pass_upper + EPS:
            errors.append({
                "element": station.identifier, "kind": "station",
                "message": "through-flow lower bound exceeds upper bound"})
        if station.pass_lower < -EPS:
            errors.append({
                "element": station.identifier, "kind": "station",
                "message": "through-flow lower bound is negative"})
        if station.kind not in ("interior", "inflow", "outflow"):
            errors.append({
                "element": station.identifier, "kind": "station",
                "message": "unknown station kind {!r}".format(station.kind)})

    seen_reaches = set()
    for reach in reaches:
        if not reach.identifier:
            errors.append({
                "element": reach.identifier, "kind": "reach",
                "message": "reach is missing an id"})
        if reach.identifier in seen_reaches:
            errors.append({
                "element": reach.identifier, "kind": "reach",
                "message": "duplicate reach id"})
        seen_reaches.add(reach.identifier)
        for endpoint in (reach.source, reach.target):
            if endpoint not in seen_stations:
                errors.append({
                    "element": reach.identifier, "kind": "reach",
                    "message": "endpoint station {!r} does not exist".format(
                        endpoint)})
        if reach.lower > reach.upper + EPS:
            errors.append({
                "element": reach.identifier, "kind": "reach",
                "message": "flow lower bound exceeds upper bound"})
        if reach.lower < -EPS:
            errors.append({
                "element": reach.identifier, "kind": "reach",
                "message": "flow lower bound is negative"})
    return errors
