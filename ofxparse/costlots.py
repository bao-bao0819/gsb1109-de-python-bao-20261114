"""
Tax lot cost-range reconstruction from OFX investment transactions.

Legacy broker exports report buys and sells without recording which tax
lot each sold share came from.  Nothing in this module assumes an
accounting convention such as FIFO: instead, every way of assigning the
sold units to the buy lots that is consistent with the known data is
treated as a feasible allocation, and the bounds of that feasible set
are reported.

The feasible allocations form a transportation flow:

* each buy lot sends every one of its shares into a chain from which
  the shares are either assigned to a sale or kept as a leftover;
* each sale must receive exactly its sold quantity;
* a lot can only feed a sale that happened on or after its trade date;
* an end-of-day snapshot that states the per-lot remaining quantity
  pins how much of that lot may have reached the sales settled by that
  date.  The same lot share therefore can never be consumed by two
  sales.

Minimum/maximum cost for one sale and the share of every lot that is
forced into (or out of) every feasible allocation are obtained with a
lower-bound minimum-cost flow: the lower bounds are converted to node
balances and the cheapest feasible flow is built by successive
shortest-path augmentations (SPFA), so only the standard library is
required.
"""

from datetime import datetime, time
from decimal import Decimal

from .ofxparse import InvestmentTransaction

ZERO = Decimal(0)


def _as_datetime(value):
    """Normalize ``date`` and ``datetime`` values to ``datetime``."""
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time.min)

# OFX aggregate types that open (buy types) or close (sell types) a
# long tax lot.  ``reinvest`` is a dividend reinvestment, i.e. a buy.
BUY_TRANSACTION_TYPES = frozenset([
    'buydebt', 'buymf', 'buyopt', 'buyother', 'buystock', 'reinvest',
])
SELL_TRANSACTION_TYPES = frozenset([
    'selldebt', 'sellmf', 'sellopt', 'sellother', 'sellstock',
])


class InfeasibleAllocationError(ValueError):
    """Raised when no lot allocation matches the supplied data."""


class Lot(object):
    """A single buy lot (tax lot) of one security."""

    def __init__(self, lot_id, security, date, units, unit_cost,
                 transaction_id=None):
        self.id = lot_id
        self.security = security
        self.date = _as_datetime(date)
        self.units = Decimal(units)
        self.unit_cost = Decimal(unit_cost)
        self.transaction_id = transaction_id if transaction_id is not None \
            else lot_id

    def __repr__(self):
        return "<Lot id=%s security=%s units=%s unit_cost=%s>" % (
            self.id, self.security, self.units, self.unit_cost)


class Sale(object):
    """A sell transaction whose lot assignment is unknown."""

    def __init__(self, sale_id, security, date, units, transaction_id=None):
        self.id = sale_id
        self.security = security
        self.date = _as_datetime(date)
        self.units = Decimal(units)
        self.transaction_id = transaction_id if transaction_id is not None \
            else sale_id

    def __repr__(self):
        return "<Sale id=%s security=%s units=%s>" % (
            self.id, self.security, self.units)


class Snapshot(object):
    """End-of-day per-lot remaining share counts.

    ``remaining`` maps a lot id to the units of that lot still held at
    the end of ``date``.  Lots absent from the mapping are unconstrained
    by this snapshot.
    """

    def __init__(self, date, remaining):
        self.date = _as_datetime(date)
        self.remaining = dict(
            (lot_id, Decimal(units)) for lot_id, units in remaining.items()
        )

    def __repr__(self):
        return "<Snapshot date=%s lots=%d>" % (
            self.date, len(self.remaining))


class ForcedAllocation(object):
    """Lot shares that every feasible allocation must assign to a sale."""

    def __init__(self, lot_id, units, unit_cost):
        self.lot_id = lot_id
        self.units = Decimal(units)
        self.unit_cost = Decimal(unit_cost)
        self.cost = self.units * self.unit_cost

    def __repr__(self):
        return "<ForcedAllocation lot_id=%s units=%s>" % (
            self.lot_id, self.units)


class SaleCostRange(object):
    """Cost bounds and forced lot shares for one sell transaction."""

    def __init__(self, sale, min_cost, max_cost,
                 min_cost_allocation, max_cost_allocation,
                 forced_allocations):
        self.sale = sale
        self.min_cost = min_cost
        self.max_cost = max_cost
        self.min_unit_cost = (
            min_cost / sale.units if sale.units else ZERO)
        self.max_unit_cost = (
            max_cost / sale.units if sale.units else ZERO)
        self.cost_is_exact = min_cost == max_cost
        # lot_id -> units, the allocations that attain each bound
        self.min_cost_allocation = min_cost_allocation
        self.max_cost_allocation = max_cost_allocation
        self.forced_allocations = forced_allocations

    def __repr__(self):
        return "<SaleCostRange sale=%s min=%s max=%s forced=%d>" % (
            self.sale.id, self.min_cost, self.max_cost,
            len(self.forced_allocations))


class SecurityCostReport(object):
    """All sale cost ranges for one security."""

    def __init__(self, security, lots, sales, ranges, snapshots):
        self.security = security
        self.lots = lots
        self.sales = sales
        self.ranges = ranges
        self.snapshots = snapshots


# ---------------------------------------------------------------------------
# Lower-bound minimum-cost flow
# ---------------------------------------------------------------------------

class _Network(object):
    """Flow network describing the feasible lot allocations.

    For every lot a chain runs alongside the chronologically ordered
    sales: an edge from chain node ``j-1`` to sale ``j`` allocates lot
    shares to that sale, while the carry edge to chain node ``j`` keeps
    the shares for later.  Pinning a carry edge (or the source edge for
    snapshots predating every sale) at the snapshot remainder enforces
    the end-of-day constraints.
    """

    def __init__(self, lots, sales, snapshots):
        self.lots = lots
        self.sales = sales
        self.snapshots = snapshots
        self.lot_by_id = dict((lot.id, lot) for lot in lots)

        self.source = 0
        self.sink = 1
        next_node = 2
        self.sale_node = {}
        for sale in sales:
            self.sale_node[sale.id] = next_node
            next_node += 1
        # chain nodes: (lot_id, k) where k sales have been passed
        self.chain_node = {}
        for lot in lots:
            for k in range(len(sales) + 1):
                self.chain_node[(lot.id, k)] = next_node
                next_node += 1
        self.node_count = next_node

        # edge tuples: (from, to, lower, upper, kind, key)
        self.edges = []
        self.alloc_edge = {}
        self.carry_edge = {}
        self.source_edge = {}
        self._build()

    def _add(self, fr, to, lower, upper, kind, key=None):
        index = len(self.edges)
        self.edges.append((fr, to, lower, upper, kind, key))
        return index

    def _build(self):
        sales = self.sales
        for lot in self.lots:
            head = self.chain_node[(lot.id, 0)]
            # Every lot share must enter the chain and then either be
            # allocated to a sale or carried to the sink as a leftover,
            # so end-of-day remainders cannot be bypassed by leaving
            # shares at the source.
            self.source_edge[lot.id] = self._add(
                self.source, head, lot.units, lot.units, 'source', lot.id)
            for j, sale in enumerate(sales, start=1):
                node = self.chain_node[(lot.id, j - 1)]
                if lot.date <= sale.date:
                    self.alloc_edge[(lot.id, sale.id)] = self._add(
                        node, self.sale_node[sale.id],
                        ZERO, lot.units, 'alloc', (lot.id, sale.id))
                self.carry_edge[(lot.id, j)] = self._add(
                    node, self.chain_node[(lot.id, j)],
                    ZERO, lot.units, 'carry', (lot.id, j))
            self._add(
                self.chain_node[(lot.id, len(sales))], self.sink,
                ZERO, lot.units, 'leftover', lot.id)
        for sale in sales:
            self._add(
                self.sale_node[sale.id], self.sink,
                sale.units, sale.units, 'sale_sink', sale.id)
        self._apply_snapshots()

    def _apply_snapshots(self):
        for snapshot in self.snapshots:
            cutoff = sum(
                1 for sale in self.sales if sale.date <= snapshot.date)
            for lot_id, remaining in snapshot.remaining.items():
                if lot_id not in self.lot_by_id:
                    raise ValueError(
                        "Snapshot at %s references unknown lot %r"
                        % (snapshot.date, lot_id))
                if remaining < 0:
                    raise ValueError(
                        "Snapshot at %s gives negative remainder for lot %r"
                        % (snapshot.date, lot_id))
                lot = self.lot_by_id[lot_id]
                if snapshot.date < lot.date:
                    # The lot did not exist yet; the reading is irrelevant.
                    continue
                if cutoff == 0:
                    # No modeled sale predates the snapshot, so the whole
                    # lot must still be held.  A smaller reading implies a
                    # disposition absent from the transaction data.
                    if remaining != lot.units:
                        raise InfeasibleAllocationError(
                            "Snapshot at %s says lot %r holds %s units "
                            "before any sale, but the lot contains %s"
                            % (snapshot.date, lot_id, remaining,
                               lot.units))
                    continue
                edge_index = self.carry_edge[(lot_id, cutoff)]
                self._pin(edge_index, remaining)

    def _pin(self, edge_index, value):
        fr, to, lower, upper, kind, key = self.edges[edge_index]
        # The reading is an exact end-of-day quantity: the flow carried
        # past the cutoff must equal the recorded remainder.  Readings
        # larger than the lot itself make every allocation infeasible.
        self.edges[edge_index] = (fr, to, value, value, kind, key)

    def solve(self, weights=None):
        """Return flows per edge index for the cheapest feasible flow.

        ``weights`` maps an edge index to its per-unit cost; unweighted
        edges cost zero.  ``None`` is returned when no feasible flow
        exists.
        """
        return _min_cost_flow(self.node_count, self.edges, weights or {})


def _min_cost_flow(node_count, edges, weights):
    """Lower-bound minimum-cost flow, successive shortest paths.

    Quantities are :class:`Decimal`.  Lower bounds become node balances
    served by a balancing source/sink, and the cheapest feasible flow is
    built by repeatedly augmenting along the shortest residual path.
    Shortest paths use SPFA (Bellman-Ford with a relaxation queue),
    which tolerates the negative-cost reverse arcs without a heap - the
    graphs here are small and sparse, so the queue beats heap-based
    Dijkstra under :class:`Decimal` comparisons.  ``None`` is returned
    when the lower bounds cannot be satisfied.
    """
    balance = [ZERO] * node_count
    arcs = [[] for _ in range(node_count)]
    forward = []

    def add_arc(fr, to, capacity, cost):
        forward_index = len(arcs[fr])
        reverse_index = len(arcs[to])
        arcs[fr].append([to, reverse_index, capacity, cost])
        arcs[to].append([fr, forward_index, ZERO, -cost])
        return len(arcs[fr]) - 1

    for edge_index, (fr, to, lower, upper, kind, key) in enumerate(edges):
        cost = weights.get(edge_index, ZERO)
        arc_index = add_arc(fr, to, upper - lower, cost)
        forward.append((fr, arc_index, upper - lower))
        balance[fr] -= lower
        balance[to] += lower

    super_source = node_count
    super_sink = node_count + 1
    arcs.append([])
    arcs.append([])
    total_nodes = node_count + 2
    required = ZERO
    for node, node_balance in enumerate(balance):
        if node_balance > 0:
            add_arc(super_source, node, node_balance, ZERO)
            required += node_balance
        elif node_balance < 0:
            add_arc(node, super_sink, -node_balance, ZERO)
    # Closing the sink back to the source turns the fixed-value flow
    # into a circulation so the balancing flow can return to the
    # source's deficit.
    flow_value_bound = sum((edge[3] for edge in edges), ZERO)
    add_arc(1, 0, flow_value_bound, ZERO)

    def shortest_path(origin, destination):
        # SPFA with a list-backed FIFO queue.
        distances = [None] * total_nodes
        predecessor = [None] * total_nodes
        distances[origin] = ZERO
        queue = [origin]
        in_queue = [False] * total_nodes
        in_queue[origin] = True
        head = 0
        while head < len(queue):
            node = queue[head]
            head += 1
            in_queue[node] = False
            base = distances[node]
            for arc_index, arc in enumerate(arcs[node]):
                to_node, reverse, capacity, cost = arc
                if capacity <= 0:
                    continue
                candidate = base + cost
                current = distances[to_node]
                if current is None or candidate < current:
                    distances[to_node] = candidate
                    predecessor[to_node] = (node, arc_index)
                    if not in_queue[to_node]:
                        queue.append(to_node)
                        in_queue[to_node] = True
        if distances[destination] is None:
            return None
        return predecessor

    def augment(predecessor, origin, destination):
        node = destination
        bottleneck = None
        while node != origin:
            prev_node, arc_index = predecessor[node]
            capacity = arcs[prev_node][arc_index][2]
            if bottleneck is None or capacity < bottleneck:
                bottleneck = capacity
            node = prev_node
        node = destination
        while node != origin:
            prev_node, arc_index = predecessor[node]
            arc = arcs[prev_node][arc_index]
            to_node, reverse_index, capacity, cost = arc
            arc[2] = capacity - bottleneck
            arcs[to_node][reverse_index][2] += bottleneck
            node = prev_node
        return bottleneck

    pushed = ZERO
    while pushed < required:
        predecessor = shortest_path(super_source, super_sink)
        if predecessor is None:
            return None
        pushed += augment(predecessor, super_source, super_sink)

    flows = []
    for edge_index, (fr, to, lower, upper, kind, key) in enumerate(edges):
        _, arc_index, initial_capacity = forward[edge_index]
        residual = arcs[fr][arc_index][2]
        flows.append(lower + (initial_capacity - residual))
    return flows


# ---------------------------------------------------------------------------
# Public analysis API
# ---------------------------------------------------------------------------

def _sale_allocation_from_flows(network, flows, sale):
    allocation = {}
    for lot in network.lots:
        edge_index = network.alloc_edge.get((lot.id, sale.id))
        if edge_index is None:
            continue
        flow = flows[edge_index]
        if flow:
            allocation[lot.id] = flow
    return allocation


def _cost_of(allocation, lot_by_id):
    return sum(
        (units * lot_by_id[lot_id].unit_cost
         for lot_id, units in allocation.items()),
        ZERO)


def _forced_allocations(network, sale):
    """Lot shares forced into ``sale`` across every feasible allocation."""
    forced = []
    for lot in network.lots:
        edge_index = network.alloc_edge.get((lot.id, sale.id))
        if edge_index is None:
            continue
        # Minimizing this single edge over all feasible flows gives the
        # smallest amount of the lot the sale can ever receive.
        flows = network.solve({edge_index: Decimal(1)})
        if flows is None:
            raise InfeasibleAllocationError(
                "Allocation became infeasible while probing lot %r for "
                "sale %r" % (lot.id, sale.id))
        minimum = flows[edge_index]
        if minimum > 0:
            forced.append(ForcedAllocation(
                lot.id, minimum, lot.unit_cost))
    return forced


def _analyze_security(security, lots, sales, snapshots):
    lots = sorted(lots, key=lambda lot: (lot.date, lot.id))
    sales = sorted(sales, key=lambda sale: (sale.date, sale.id))

    network = _Network(lots, sales, snapshots)
    feasibility_flows = network.solve()
    if feasibility_flows is None:
        # Distinguish a shortage of shares from a contradictory snapshot.
        if not snapshots or _Network(lots, sales, []).solve() is None:
            raise InfeasibleAllocationError(
                "Security %s: the buy lots cannot cover the recorded "
                "sales and lot availability dates" % security)
        raise InfeasibleAllocationError(
            "Security %s: the end-of-day lot remainders contradict the "
            "buys and sales; no allocation satisfies every snapshot"
            % security)

    lot_by_id = dict((lot.id, lot) for lot in lots)
    ranges = []
    for sale in sales:
        min_weights = {}
        max_weights = {}
        for lot in lots:
            edge_index = network.alloc_edge.get((lot.id, sale.id))
            if edge_index is None:
                continue
            min_weights[edge_index] = lot.unit_cost
            max_weights[edge_index] = -lot.unit_cost

        min_flows = network.solve(min_weights)
        max_flows = network.solve(max_weights)
        if min_flows is None or max_flows is None:
            raise InfeasibleAllocationError(
                "Security %s: lost feasibility while bounding sale %r"
                % (security, sale.id))

        min_allocation = _sale_allocation_from_flows(
            network, min_flows, sale)
        max_allocation = _sale_allocation_from_flows(
            network, max_flows, sale)
        forced = _forced_allocations(network, sale)
        ranges.append(SaleCostRange(
            sale,
            _cost_of(min_allocation, lot_by_id),
            _cost_of(max_allocation, lot_by_id),
            min_allocation,
            max_allocation,
            forced,
        ))
    return SecurityCostReport(security, lots, sales, ranges, snapshots)


def analyze(lots, sales, snapshots=None):
    """Compute sale cost ranges from generic lots, sales and snapshots.

    Returns a dict keyed by security id.  Lots and sales are grouped by
    security internally; each security is solved independently.
    """
    snapshots = snapshots or []
    lots_by_security = {}
    sales_by_security = {}
    for lot in lots:
        if lot.units <= 0:
            raise ValueError("Lot %r has no positive unit count" % lot.id)
        lots_by_security.setdefault(lot.security, []).append(lot)
    for sale in sales:
        if sale.units <= 0:
            raise ValueError("Sale %r has no positive unit count" % sale.id)
        sales_by_security.setdefault(sale.security, []).append(sale)

    # A snapshot normally spans every security, so its lot ids are
    # validated against all lots before each security is filtered out.
    all_lot_ids = set(lot.id for lot in lots)
    for snapshot in snapshots:
        unknown = set(snapshot.remaining) - all_lot_ids
        if unknown:
            raise ValueError(
                "Snapshot at %s references unknown lots: %s"
                % (snapshot.date, ', '.join(sorted(unknown))))

    reports = {}
    for security in sales_by_security:
        security_lots = lots_by_security.get(security, [])
        known_lot_ids = set(lot.id for lot in security_lots)
        security_snapshots = []
        for snapshot in snapshots:
            remaining = dict(
                (lot_id, units)
                for lot_id, units in snapshot.remaining.items()
                if lot_id in known_lot_ids)
            if remaining:
                security_snapshots.append(Snapshot(snapshot.date, remaining))
        reports[security] = _analyze_security(
            security, security_lots, sales_by_security[security],
            security_snapshots)
    return reports


def _transaction_unit_cost(transaction, basis):
    units = abs(transaction.units)
    if basis == 'cash' and transaction.total:
        return abs(transaction.total) / units
    return transaction.unit_price


def lots_and_sales_from_statement(statement, basis='cash'):
    """Turn an OFX investment statement into :class:`Lot` and Sale lists."""
    if basis not in ('cash', 'price'):
        raise ValueError("basis must be 'cash' (total outlay per share, "
                         "including commission) or 'price' (unit price)")
    lots = []
    sales = []
    seen_lot_ids = set()
    for index, transaction in enumerate(statement.transactions):
        if not isinstance(transaction, InvestmentTransaction):
            continue
        if transaction.tradeDate is None:
            raise ValueError(
                "Transaction %r has no trade date; cannot order it against "
                "the tax lots" % transaction.id)
        units = abs(transaction.units)
        if units == 0:
            continue
        security = transaction.security
        if transaction.type in BUY_TRANSACTION_TYPES:
            lot_id = transaction.id or 'lot-%d' % index
            if lot_id in seen_lot_ids:
                lot_id = '%s#%d' % (lot_id, index)
            seen_lot_ids.add(lot_id)
            lots.append(Lot(
                lot_id, security, transaction.tradeDate, units,
                _transaction_unit_cost(transaction, basis),
                transaction_id=transaction.id,
            ))
        elif transaction.type in SELL_TRANSACTION_TYPES:
            sale_id = transaction.id or 'sale-%d' % index
            sales.append(Sale(
                sale_id, security, transaction.tradeDate, units,
                transaction_id=transaction.id,
            ))
    return lots, sales


def analyze_statement(statement, snapshots=None, basis='cash'):
    """Analyze the trades of a parsed OFX investment statement.

    ``snapshots`` is a list of :class:`Snapshot` objects using the buy
    transactions' FITIDs as lot ids.  ``basis='cash'`` values each lot
    at the actual cash outlay per share (``total / units``, which
    includes commission and fees); ``basis='price'`` uses the reported
    unit price instead.
    """
    lots, sales = lots_and_sales_from_statement(statement, basis=basis)
    return analyze(lots, sales, snapshots=snapshots)


def format_report(reports):
    """Render the reports returned by :func:`analyze` as plain text.

    Every sale is listed with its lowest and highest possible cost and,
    separately, the lot shares that every feasible allocation must use.
    When no lot share is forced the text says so explicitly instead of
    presenting a FIFO (or any other) guess as fact.
    """
    lines = []
    for security in sorted(reports):
        report = reports[security]
        lines.append("Security %s" % security)
        lines.append("  buy lots:")
        for lot in report.lots:
            lines.append(
                "    %s  %s units @ %s on %s"
                % (lot.id, lot.units, lot.unit_cost,
                   lot.date.date().isoformat()))
        for result in report.ranges:
            sale = result.sale
            lines.append(
                "  sale %s on %s, %s units:"
                % (sale.id, sale.date.date().isoformat(), sale.units))
            exact = " (exact)" if result.cost_is_exact else ""
            lines.append(
                "    cost range: %s .. %s%s  (%s .. %s per unit)"
                % (result.min_cost, result.max_cost, exact,
                   result.min_unit_cost, result.max_unit_cost))
            if result.forced_allocations:
                lines.append("    forced lot shares:")
                for forced in result.forced_allocations:
                    lines.append(
                        "      %s: %s units @ %s = %s"
                        % (forced.lot_id, forced.units,
                           forced.unit_cost, forced.cost))
            else:
                lines.append("    forced lot shares: none (the lot "
                             "assignment is not uniquely determined)")
    return "\n".join(lines)
