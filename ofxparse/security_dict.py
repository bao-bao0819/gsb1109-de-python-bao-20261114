"""
Versioned security dictionary.

OFX ``SECLIST`` messages only describe a security as it happened to be known
by one brokerage on one statement.  The same listed security can change
CUSIP/ticker between brokers, and a delisted company's ticker can be reused by
an unrelated new company.  This module turns one or more OFX security lists
plus a set of dated, manually curated mappings into a versioned dictionary that
resolves the identity of a holding on a specific trading day.

Identity rules
--------------

* The only identity key is the OFX ``UNIQUEID`` (normally a CUSIP).  Securities
  that merely share a ticker or a name are never merged.
* Manual mappings connect two identities with an effective date range; they are
  the *only* mechanism that can make two ids resolve to the same canonical
  security.
* Resolution is temporal: a mapping only applies to dates inside its closed
  ``[valid_from, valid_to]`` interval (``valid_to`` of ``None`` means open
  ended).
* When two individually legal mappings let the same identity reach two
  different securities on the same day, the full conflict chain is retained.
  A deterministic candidate is still returned for convenience but the result
  is flagged ambiguous, and dates without a fork keep resolving unambiguously.
"""

from __future__ import absolute_import

import csv
import datetime
import hashlib
import json


# Lower rank wins when a conflict forces a deterministic pick.  Manually
# curated mappings are trusted over identifiers inferred from OFX statements.
_SOURCE_RANK = {'manual': 0, 'ofx': 1}


def as_date(value):
    """Coerce ``date``/``datetime``/ISO strings to a ``datetime.date``."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.datetime.strptime(value.strip()[:10], '%Y-%m-%d').date()


def default_us_holidays(year):
    """US equity market holidays for one year, expressed as fixed rules.

    Fixed-date holidays that fall on a weekend are shifted to the Friday
    (Saturday) or Monday (Sunday) on which the market actually closes.
    """
    def nth_weekday(month, weekday, ordinal):
        first = datetime.date(year, month, 1)
        shift = (weekday - first.weekday()) % 7
        return first + datetime.timedelta(days=shift + 7 * (ordinal - 1))

    def last_weekday(month, weekday):
        if month == 12:
            anchor = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            anchor = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
        return anchor - datetime.timedelta(days=(anchor.weekday() - weekday) % 7)

    def observed(day):
        # Fixed-date holidays are observed on Friday when they fall on a
        # Saturday and on Monday when they fall on a Sunday.
        if day.weekday() == 5:
            return day - datetime.timedelta(days=1)
        if day.weekday() == 6:
            return day + datetime.timedelta(days=1)
        return day

    holidays = [
        observed(datetime.date(year, 1, 1)),            # New Year
        nth_weekday(1, 0, 3),                           # MLK day
        nth_weekday(2, 0, 3),                           # Presidents' day
        last_weekday(5, 0),                             # Memorial day
        observed(datetime.date(year, 6, 19)),           # Juneteenth
        observed(datetime.date(year, 7, 4)),            # Independence day
        nth_weekday(9, 0, 1),                           # Labor day
        nth_weekday(11, 3, 4),                          # Thanksgiving
        observed(datetime.date(year, 12, 25)),          # Christmas
    ]
    return set(holidays)


class TradingCalendar(object):
    """Trading-day calendar used to snap holding dates to a trading day."""

    def __init__(self, holidays=None):
        self._extra_holidays = set(as_date(d) for d in (holidays or []))
        self._holiday_years = {}

    def _holidays(self, year):
        if year not in self._holiday_years:
            self._holiday_years[year] = (
                default_us_holidays(year) | self._extra_holidays)
        return self._holiday_years[year]

    def is_trading_day(self, day):
        day = as_date(day)
        return day.weekday() < 5 and day not in self._holidays(day.year)

    def nearest_trading_day(self, day, preference='previous'):
        """Snap a non-trading day to the previous (default) or next trading day."""
        day = as_date(day)
        step = -1 if preference == 'previous' else 1
        while not self.is_trading_day(day):
            day += datetime.timedelta(days=step)
        return day


class SecuritySnapshot(object):
    """One OFX observation of a security (names/tickers can change over time)."""

    def __init__(self, name, ticker, memo, observed_on):
        self.name = name
        self.ticker = ticker
        self.memo = memo
        self.observed_on = as_date(observed_on)

    def as_dict(self):
        return {
            'name': self.name,
            'ticker': self.ticker,
            'memo': self.memo,
            'observed_on': self.observed_on.isoformat()
            if self.observed_on else None,
        }


class SecurityEntry(object):
    """A security identity, keyed solely by its OFX unique id (e.g. CUSIP)."""

    def __init__(self, uniqueid):
        self.uniqueid = uniqueid
        self.snapshots = []

    def add_snapshot(self, name, ticker, memo=None, observed_on=None):
        snapshot = SecuritySnapshot(name, ticker, memo, observed_on)
        self.snapshots = [
            s for s in self.snapshots
            if not (s.observed_on == snapshot.observed_on
                    and s.ticker == snapshot.ticker
                    and s.name == snapshot.name)
        ]
        self.snapshots.append(snapshot)
        self.snapshots.sort(key=lambda s: s.observed_on or datetime.date.min)
        return snapshot

    def snapshot_on(self, day):
        """Most recent snapshot observed on or before ``day``."""
        day = as_date(day)
        eligible = [s for s in self.snapshots
                    if s.observed_on is None or s.observed_on <= day]
        if eligible:
            return eligible[-1]
        return self.snapshots[0] if self.snapshots else None

    def as_dict(self):
        return {
            'uniqueid': self.uniqueid,
            'snapshots': [s.as_dict() for s in self.snapshots],
        }


class SecurityMapping(object):
    """A dated edge stating that ``source_id`` maps to ``target_id``."""

    def __init__(self, source_id, target_id, valid_from, valid_to=None,
                 source='manual', note=None, sequence=0):
        if source_id == target_id:
            raise ValueError(
                'Refusing self-referential mapping for %r' % source_id)
        self.source_id = source_id
        self.target_id = target_id
        self.valid_from = as_date(valid_from)
        self.valid_to = as_date(valid_to)
        self.source = source
        self.note = note
        self.sequence = sequence
        if self.valid_from is None:
            raise ValueError('A mapping requires a valid_from date')
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError('valid_to precedes valid_from on mapping %s->%s'
                             % (source_id, target_id))

    def active_on(self, day):
        day = as_date(day)
        if day < self.valid_from:
            return False
        return self.valid_to is None or day <= self.valid_to

    def source_rank(self):
        return _SOURCE_RANK.get(self.source, len(_SOURCE_RANK))

    def as_dict(self):
        return {
            'source_id': self.source_id,
            'target_id': self.target_id,
            'valid_from': self.valid_from.isoformat(),
            'valid_to': self.valid_to.isoformat() if self.valid_to else None,
            'source': self.source,
            'note': self.note,
            'sequence': self.sequence,
        }


class MappingConflict(object):
    """A same-day fork: one identity reaches more than one terminal security."""

    def __init__(self, identity, as_of, paths, cycles, preferred):
        self.identity = identity
        self.as_of = as_date(as_of)
        # Each path is a tuple of (node ids, mapping edges taken)
        self.paths = paths
        # Cycles are node paths that loop back onto an already visited node.
        self.cycles = cycles
        self.preferred = preferred
        self.candidates = sorted({node_path[-1] for node_path, _ in paths})

    def chains(self):
        """Human-readable conflict chains, edge by edge."""
        rendered = []
        for node_path, edges in self.paths:
            chain = [node_path[0]]
            for node, edge in zip(node_path[1:], edges):
                chain.append(' --%s@%s--> %s' % (
                    edge.source, edge.valid_from.isoformat(), node))
            rendered.append(''.join(chain))
        for node_path in self.cycles:
            rendered.append('cycle: ' + ' -> '.join(node_path))
        return rendered

    def signature(self):
        """Stable fingerprint of the conflict shape, independent of date."""
        chain_text = self.chains()
        return (self.identity,
                tuple(sorted(self.candidates)),
                tuple(sorted(chain_text)))

    def as_dict(self):
        return {
            'identity': self.identity,
            'as_of': self.as_of.isoformat(),
            'candidates': self.candidates,
            'preferred': self.preferred,
            'chains': self.chains(),
            'paths': [
                {
                    'nodes': node_path,
                    'edges': [edge.as_dict() for edge in edges],
                }
                for node_path, edges in self.paths
            ],
            'cycles': self.cycles,
        }


class ResolutionResult(object):
    """Outcome of resolving one holding identity on one trading day."""

    def __init__(self, identity, canonical_id, as_of, snapshot,
                 ambiguous, conflict, chosen_path):
        self.identity = identity
        self.canonical_id = canonical_id
        self.as_of = as_date(as_of)
        self.name = snapshot.name if snapshot else None
        self.ticker = snapshot.ticker if snapshot else None
        self.memo = snapshot.memo if snapshot else None
        self.ambiguous = ambiguous
        self.conflict = conflict
        self.chosen_path = chosen_path

    @property
    def resolved(self):
        return self.canonical_id is not None

    def as_dict(self):
        return {
            'identity': self.identity,
            'canonical_id': self.canonical_id,
            'as_of': self.as_of.isoformat() if self.as_of else None,
            'name': self.name,
            'ticker': self.ticker,
            'memo': self.memo,
            'ambiguous': self.ambiguous,
            'conflict': self.conflict.as_dict() if self.conflict else None,
            'chosen_path': self.chosen_path,
        }

    def __repr__(self):
        flag = ' AMBIGUOUS' if self.ambiguous else ''
        return '<ResolutionResult %s -> %s @ %s%s>' % (
            self.identity, self.canonical_id, self.as_of, flag)


class SecurityDictionary(object):
    """Versioned, temporal dictionary of security identities."""

    def __init__(self, calendar=None):
        self.calendar = calendar or TradingCalendar()
        self._entries = {}
        self._outgoing = {}
        self._mappings = []
        self._revisions = []
        self._previous_hash = None
        self._sequence = 0

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def add_security(self, uniqueid, name=None, ticker=None, memo=None,
                     observed_on=None):
        if not uniqueid:
            raise ValueError('Security uniqueid is required')
        entry = self._entries.get(uniqueid)
        if entry is None:
            entry = SecurityEntry(uniqueid)
            self._entries[uniqueid] = entry
            self._outgoing.setdefault(uniqueid, [])
        entry.add_snapshot(name, ticker, memo, observed_on)
        return entry

    def load_ofx_securities(self, securities, observed_on=None):
        """Ingest an OFX ``security_list`` (parsed ``Security`` objects)."""
        if hasattr(securities, 'security_list'):
            securities = securities.security_list
        added = []
        for security in securities or []:
            added.append(self.add_security(
                security.uniqueid,
                getattr(security, 'name', None),
                getattr(security, 'ticker', None),
                getattr(security, 'memo', None),
                observed_on))
        self.commit('ofx security list (%d entries)' % len(added))
        return added

    def add_mapping(self, source_id, target_id, valid_from, valid_to=None,
                    source='manual', note=None):
        # Identical duplicate edges do not create a fork or consume a sequence.
        for existing in self._mappings:
            if (existing.source_id == source_id
                    and existing.target_id == target_id
                    and existing.valid_from == as_date(valid_from)
                    and existing.valid_to == as_date(valid_to)
                    and existing.source == source):
                return existing
        self._sequence += 1
        mapping = SecurityMapping(
            source_id, target_id, valid_from, valid_to,
            source=source, note=note, sequence=self._sequence)
        self._mappings.append(mapping)
        self._entries.setdefault(source_id, SecurityEntry(source_id))
        self._entries.setdefault(target_id, SecurityEntry(target_id))
        self._outgoing.setdefault(source_id, []).append(mapping)
        self._outgoing.setdefault(target_id, [])
        self._outgoing[source_id].sort(
            key=lambda edge: (edge.valid_from, edge.sequence))
        return mapping

    def load_mappings_csv(self, source_file, source='manual'):
        """Load dated mappings from a CSV path or file-like object.

        Expected columns: ``source_id,target_id,effective_date`` with optional
        ``end_date``, ``source`` and ``note`` columns.
        """
        if hasattr(source_file, 'read'):
            handle = source_file
            close_after = False
        else:
            handle = open(source_file, 'r')
            close_after = True
        try:
            reader = csv.DictReader(handle)
            added = []
            for row in reader:
                row = {
                    (key or '').strip().lower(): (value or '').strip()
                    for key, value in row.items()
                }
                valid_from = row.get('effective_date') or row.get('valid_from')
                valid_to = row.get('end_date') or row.get('valid_to') or None
                added.append(self.add_mapping(
                    row['source_id'],
                    row['target_id'],
                    valid_from,
                    valid_to=valid_to,
                    source=row.get('source') or source,
                    note=row.get('note') or None))
        finally:
            if close_after:
                handle.close()
        self.commit('csv mappings (%d rows)' % len(added))
        return added

    # ------------------------------------------------------------------ #
    # Lookups that never merge on ticker or name alone
    # ------------------------------------------------------------------ #
    def get(self, uniqueid):
        return self._entries.get(uniqueid)

    def find_by_ticker(self, ticker, as_of=None):
        """Return every identity that ever used ``ticker`` (no auto-merge)."""
        matches = []
        for entry in self._entries.values():
            for snapshot in entry.snapshots:
                if snapshot.ticker is None:
                    continue
                if snapshot.ticker.upper() != ticker.upper():
                    continue
                if as_of is not None and snapshot.observed_on is not None \
                        and snapshot.observed_on > as_date(as_of):
                    continue
                matches.append((entry.uniqueid, snapshot))
                break
        return matches

    def find_by_name(self, name):
        """Return every identity carrying ``name``; identical names stay apart."""
        return [
            entry.uniqueid for entry in self._entries.values()
            if any(snapshot.name == name for snapshot in entry.snapshots)
        ]

    # ------------------------------------------------------------------ #
    # Temporal resolution
    # ------------------------------------------------------------------ #
    def _active_edges(self, uniqueid, day):
        return [edge for edge in self._outgoing.get(uniqueid, [])
                if edge.active_on(day)]

    def _walk(self, uniqueid, day):
        """Depth-first walk of edges active on ``day``.

        Returns terminal paths, each a ``(node_path, edge_path)`` pair, and
        detects cycles along the way.
        """
        paths = []
        cycles = []

        def visit(node, node_path, edge_path):
            active = [edge for edge in self._active_edges(node, day)
                      if edge.target_id not in node_path]
            cyclic = [edge for edge in self._active_edges(node, day)
                      if edge.target_id in node_path]
            for edge in cyclic:
                cycles.append(node_path + [edge.target_id])
            if not active:
                # A node whose only edges loop back is not a terminal; the
                # cycle recorded above carries that branch's chain.
                if not cyclic:
                    paths.append((node_path, edge_path))
                return
            for edge in active:
                visit(edge.target_id,
                      node_path + [edge.target_id],
                      edge_path + [edge])

        visit(uniqueid, [uniqueid], [])
        return paths, cycles

    def resolve(self, uniqueid, as_of):
        """Resolve ``uniqueid`` to a canonical identity for trading day ``as_of``."""
        if uniqueid not in self._entries:
            raise KeyError('Unknown security identity %r' % uniqueid)
        day = self.calendar.nearest_trading_day(as_date(as_of))
        paths, cycles = self._walk(uniqueid, day)

        terminals = sorted({node_path[-1] for node_path, _ in paths})
        conflict = None
        ambiguous = len(terminals) > 1 or bool(cycles)
        if ambiguous:
            conflict = MappingConflict(
                uniqueid, day, paths, cycles,
                preferred=self._preferred_terminal(paths, cycles))

        canonical_id = conflict.preferred if conflict else terminals[0]
        chosen_path = self._chosen_path(uniqueid, canonical_id, paths, cycles)
        entry = self._entries.get(canonical_id)
        snapshot = entry.snapshot_on(day) if entry else None
        return ResolutionResult(
            uniqueid, canonical_id, day, snapshot,
            ambiguous, conflict, chosen_path)

    @staticmethod
    def _path_score(edges):
        if not edges:
            return (0, 0)
        return (min(edge.source_rank() for edge in edges),
                min(edge.sequence for edge in edges))

    def _preferred_terminal(self, paths, cycles):
        if not paths:
            # Every outgoing branch cycles; anchor to the smallest cycling id
            # so the choice is deterministic without dropping the chain.
            return min({node for path in cycles for node in path})
        ranked = sorted(
            paths,
            key=lambda item: (self._path_score(item[1]), item[0][-1]))
        return ranked[0][0][-1]

    def _chosen_path(self, uniqueid, canonical_id, paths, cycles):
        for node_path, _ in paths:
            if node_path[-1] == canonical_id:
                return node_path
        if cycles:
            return cycles[0]
        return [uniqueid, canonical_id]

    def resolve_position(self, position, as_of=None):
        """Resolve a parsed OFX ``Position``/``InvestmentTransaction``."""
        uniqueid = position.security
        day = as_of
        if day is None:
            day = getattr(position, 'date', None) \
                or getattr(position, 'tradeDate', None)
        if day is None:
            raise ValueError(
                'No as_of date supplied and position carries no date')
        return self.resolve(uniqueid, day)

    def conflicts_on(self, day):
        """All identity forks visible on ``day``."""
        day = self.calendar.nearest_trading_day(as_date(day))
        conflicts = []
        for uniqueid in self._entries:
            result = self.resolve(uniqueid, day)
            if result.ambiguous:
                conflicts.append(result.conflict)
        return conflicts

    def detect_conflicts(self):
        """Scan every mapping boundary date for same-day identity forks.

        The same fork shape can be visible from several boundary dates; one
        representative conflict (earliest date) is returned per shape.
        """
        boundaries = set()
        for mapping in self._mappings:
            boundaries.add(mapping.valid_from)
            if mapping.valid_to is not None:
                boundaries.add(
                    mapping.valid_to + datetime.timedelta(days=1))
        detected = {}
        for boundary in sorted(boundaries):
            day = self.calendar.nearest_trading_day(boundary)
            for conflict in self.conflicts_on(day):
                signature = conflict.signature()
                if signature not in detected \
                        or conflict.as_of < detected[signature].as_of:
                    detected[signature] = conflict
        return sorted(detected.values(),
                      key=lambda conflict: (conflict.identity,
                                            conflict.as_of))

    # ------------------------------------------------------------------ #
    # Versioning
    # ------------------------------------------------------------------ #
    def _state_digest(self):
        payload = {
            'entries': [entry.as_dict() for _, entry in
                        sorted(self._entries.items())],
            'mappings': [mapping.as_dict() for mapping in self._mappings],
            'previous_hash': self._previous_hash,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()

    def commit(self, source):
        digest = self._state_digest()
        try:
            committed_at = datetime.datetime.now(
                datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        except AttributeError:  # pragma: no cover - Python 2 fallback
            committed_at = datetime.datetime.utcnow().isoformat() + 'Z'
        revision = {
            'sequence': len(self._revisions) + 1,
            'source': source,
            'committed_at': committed_at,
            'entries': len(self._entries),
            'mappings': len(self._mappings),
            'hash': digest,
            'previous_hash': self._previous_hash,
        }
        self._revisions.append(revision)
        self._previous_hash = digest
        return revision

    @property
    def version(self):
        return self._previous_hash

    @property
    def revisions(self):
        return list(self._revisions)

    def to_dict(self):
        return {
            'version': self.version,
            'revisions': self.revisions,
            'entries': [entry.as_dict() for _, entry in
                        sorted(self._entries.items())],
            'mappings': [mapping.as_dict() for mapping in self._mappings],
            'conflicts': [conflict.as_dict()
                          for conflict in self.detect_conflicts()],
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)
