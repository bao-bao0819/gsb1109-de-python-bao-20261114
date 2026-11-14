"""Dinic's maximum-flow algorithm on capacities that may be fractional.

The river reconciliation only needs a small, dependency-free max-flow
engine.  Capacities are ordinary floats (``inf`` is allowed) and every
comparison against zero goes through :data:`EPS` so that floating point
noise cannot leave phantom residual capacity behind.
"""

from collections import deque
import sys

# River networks can be long chains and the blocking-flow DFS is recursive;
# lift the interpreter limit so realistic station counts do not overflow.
sys.setrecursionlimit(max(100000, sys.getrecursionlimit()))

EPS = 1e-9


class _Arc:
    __slots__ = ("to", "rev", "cap")

    def __init__(self, to, rev, cap):
        self.to = to
        self.rev = rev
        self.cap = cap


class Dinic:
    """Directed graph supporting one max-flow computation per instance."""

    def __init__(self, node_count):
        self.node_count = node_count
        self._graph = [[] for _ in range(node_count)]
        self._level = [-1] * node_count

    def add_edge(self, source, target, capacity):
        """Add a capacitated arc and return the forward arc object."""
        forward = _Arc(target, len(self._graph[target]), float(capacity))
        backward = _Arc(source, len(self._graph[source]), 0.0)
        self._graph[source].append(forward)
        self._graph[target].append(backward)
        return forward

    def _bfs(self, source, sink):
        self._level = [-1] * self.node_count
        self._level[source] = 0
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for arc in self._graph[node]:
                if arc.cap > EPS and self._level[arc.to] < 0:
                    self._level[arc.to] = self._level[node] + 1
                    queue.append(arc.to)
        return self._level[sink] >= 0

    def _dfs(self, node, sink, pushed):
        if node == sink:
            return pushed
        arcs = self._graph[node]
        for index in range(self._it[node], len(arcs)):
            self._it[node] = index
            arc = arcs[index]
            if arc.cap > EPS and self._level[arc.to] == self._level[node] + 1:
                amount = self._dfs(arc.to, sink, min(pushed, arc.cap))
                if amount > EPS:
                    arc.cap -= amount
                    self._graph[arc.to][arc.rev].cap += amount
                    return amount
        return 0.0

    def flow_on(self, arc):
        """Return how much flow has been pushed through a forward arc.

        The reverse arc starts empty, so its residual capacity equals the
        total flow carried by ``arc`` — this stays exact even when the
        forward capacity is infinite.
        """
        return self._graph[arc.to][arc.rev].cap

    def copy(self):
        """Deep-copy the current residual capacities."""
        clone = Dinic(self.node_count)
        clone._level = self._level[:]
        for node, arcs in enumerate(self._graph):
            for arc in arcs:
                clone._graph[node].append(_Arc(arc.to, arc.rev, arc.cap))
        return clone

    def max_flow(self, source, sink, limit=None):
        """Push the maximum flow from ``source`` to ``sink``."""
        if limit is None:
            limit = float("inf")
        total = 0.0
        while total < limit - EPS and self._bfs(source, sink):
            self._it = [0] * self.node_count
            while total < limit - EPS:
                pushed = self._dfs(source, sink, limit - total)
                if pushed <= EPS:
                    break
                total += pushed
        return total

    def reachable(self, source):
        """Return the nodes reachable from ``source`` through positive arcs."""
        seen = {source}
        queue = deque([source])
        while queue:
            node = queue.popleft()
            for arc in self._graph[node]:
                if arc.cap > EPS and arc.to not in seen:
                    seen.add(arc.to)
                    queue.append(arc.to)
        return seen
