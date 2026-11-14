"""Pair security transfers that appear in more than one account.

A security transfer is normally reported twice: once as an outgoing transfer in
the delivering account and once as an incoming transfer in the receiving
account.  The two records usually have different FITIDs, so they cannot be
joined by identifier.  This module matches them on:

* the security identifier,
* the direction (``OUT`` and ``IN``),
* the transferred units, and
* the settlement (or trade) date, within an allowed window.

A single outgoing record may be split across several incoming records.  The
allocation is solved as a maximum flow, so a record's units are never used more
than once.  When several allocations have the same maximum total, only the
relationships that occur in every one of those allocations are reported as
confirmed.  The remaining units are returned per record, together with the
range that is still ambiguous.
"""

from __future__ import absolute_import

import datetime
import decimal
import re
from collections import OrderedDict

import six


IN = 'IN'
OUT = 'OUT'

DEFAULT_SETTLEMENT_WINDOW = datetime.timedelta(days=3)

_ZERO = decimal.Decimal(0)


def _as_decimal(value):
    if value is None:
        return None
    if isinstance(value, decimal.Decimal):
        result = value
    else:
        text = six.text_type(value).strip()
        if not text:
            return None
        result = decimal.Decimal(text)
    if not result.is_finite():
        raise ValueError("quantity must be finite")
    return result


def _normalize_security(value):
    if value is None:
        return None
    uniqueid = getattr(value, 'uniqueid', None)
    if uniqueid is not None:
        value = uniqueid
    text = six.text_type(value).strip()
    return text.upper() if text else None


def _normalize_window(value):
    if value is None:
        return None
    if isinstance(value, datetime.timedelta):
        window = value
    elif isinstance(value, bool):
        raise TypeError("settlement_window must not be a boolean")
    elif isinstance(value, (int, float)):
        window = datetime.timedelta(days=value)
    elif isinstance(value, six.string_types):
        text = value.strip()
        iso = re.match(
            r'^P(?:(?P<days>\d+(?:\.\d+)?)D)?'
            r'(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?'
            r'(?:(?P<minutes>\d+(?:\.\d+)?)M)?'
            r'(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$',
            text,
            re.IGNORECASE,
        )
        simple = re.match(
            r'^(?P<amount>-?\d+(?:\.\d+)?)\s*'
            r'(?P<unit>d|day|days|h|hour|hours|m|min|minute|minutes)?$',
            text,
            re.IGNORECASE,
        )
        if iso and any(iso.groupdict().values()):
            days = float(iso.group('days') or 0)
            hours = float(iso.group('hours') or 0)
            minutes = float(iso.group('minutes') or 0)
            seconds = float(iso.group('seconds') or 0)
            window = datetime.timedelta(
                days=days, hours=hours, minutes=minutes, seconds=seconds)
        elif simple:
            amount = float(simple.group('amount'))
            unit = (simple.group('unit') or 'd').lower()
            if unit.startswith('h'):
                window = datetime.timedelta(hours=amount)
            elif unit.startswith('m'):
                window = datetime.timedelta(minutes=amount)
            else:
                window = datetime.timedelta(days=amount)
        else:
            raise ValueError("invalid settlement window: %r" % value)
    else:
        raise TypeError("settlement_window must be a timedelta, number, "
                        "or string")
    if window < datetime.timedelta(0):
        raise ValueError("settlement_window must not be negative")
    return window


class TransferRecord(object):
    """One investment transfer record, normalized for matching."""

    def __init__(self, transaction, account_id=None, broker_id=None,
                 direction=None, security=None, quantity=None,
                 trade_date=None, settle_date=None, security_key=None):
        self.transaction = transaction
        self.account_id = account_id
        self.broker_id = broker_id
        self.direction = direction
        self.security = security
        if security_key is not None:
            self.security_key = security_key
        else:
            self.security_key = _normalize_security(security)
        self.quantity = quantity
        self.trade_date = trade_date
        self.settle_date = settle_date

    @property
    def fitid(self):
        return getattr(self.transaction, 'id', None)

    @property
    def memo(self):
        return getattr(self.transaction, 'memo', '') or ''

    @property
    def account_key(self):
        if self.account_id is None:
            return None
        return (self.broker_id, self.account_id)

    @property
    def date(self):
        return self.settle_date or self.trade_date

    @property
    def signed_units(self):
        if self.quantity is None:
            return None
        return self.quantity if self.direction == IN else -self.quantity

    @property
    def units(self):
        return self.signed_units

    @property
    def key(self):
        return '|'.join([
            self.account_id or '',
            self.broker_id or '',
            six.text_type(self.fitid or ''),
            self.direction or '',
            self.security_key or '',
        ])

    def _identity(self):
        return {
            'account_id': self.account_id,
            'broker_id': self.broker_id,
            'fitid': self.fitid,
            'direction': self.direction,
            'security': self.security,
            'units': self.quantity,
            'trade_date': self.trade_date,
            'settle_date': self.settle_date,
            'memo': self.memo,
        }

    def __repr__(self):
        return ("<TransferRecord direction=%s security=%s units=%s "
                "account=%s fitid=%s>" % (
                    self.direction, self.security, self.quantity,
                    self.account_id, self.fitid))


class TransferPair(object):
    """A relationship between an outgoing and an incoming record."""

    def __init__(self, outgoing, incoming, quantity, min_quantity=None,
                 max_quantity=None, date_basis=None):
        self.outgoing = outgoing
        self.incoming = incoming
        self.quantity = quantity
        self.min_quantity = quantity if min_quantity is None else min_quantity
        self.max_quantity = quantity if max_quantity is None else max_quantity
        self.date_basis = date_basis

    @property
    def out_record(self):
        return self.outgoing

    @property
    def in_record(self):
        return self.incoming

    @property
    def out_transaction(self):
        return self.outgoing.transaction

    @property
    def in_transaction(self):
        return self.incoming.transaction

    @property
    def confirmed(self):
        return self.min_quantity > _ZERO

    @property
    def quantity_exact(self):
        return self.min_quantity == self.max_quantity

    @property
    def units(self):
        return self.quantity

    @property
    def key(self):
        return '%s -> %s' % (self.outgoing.key, self.incoming.key)

    def _identity(self):
        return {
            'outgoing': self.outgoing._identity(),
            'incoming': self.incoming._identity(),
            'quantity': self.quantity,
            'min_quantity': self.min_quantity,
            'max_quantity': self.max_quantity,
            'quantity_exact': self.quantity_exact,
            'date_basis': self.date_basis,
        }

    def __repr__(self):
        return ("<TransferPair %s %s -> %s %s units=%s range=%s..%s>" % (
            self.outgoing.security, self.outgoing.account_id,
            self.incoming.account_id, self.incoming.direction,
            self.quantity, self.min_quantity, self.max_quantity))


class RecordMatch(object):
    """The matched and remaining quantity of one transfer record."""

    def __init__(self, record, matched_min, matched_max, reason=None,
                 candidate_count=0):
        self.record = record
        self.matched_min = matched_min
        self.matched_max = matched_max
        self.reason = reason
        self.candidate_count = candidate_count
        self.same_account_candidates = 0
        self.window_candidates = 0

    @property
    def transaction(self):
        return self.record.transaction

    @property
    def direction(self):
        return self.record.direction

    @property
    def security(self):
        return self.record.security

    @property
    def quantity(self):
        return self.record.quantity

    @property
    def remaining_min(self):
        return self.record.quantity - self.matched_max

    @property
    def remaining_max(self):
        return self.record.quantity - self.matched_min

    @property
    def remaining_quantity(self):
        return self.remaining_max

    @property
    def definitely_unmatched_quantity(self):
        return self.remaining_min

    @property
    def confirmed_quantity(self):
        return self.matched_min

    @property
    def ambiguous_quantity(self):
        return self.matched_max - self.matched_min

    @property
    def definite_remaining_quantity(self):
        return self.remaining_min

    @property
    def possible_remaining_quantity(self):
        return self.remaining_max

    @property
    def unmatched_quantity(self):
        return self.remaining_max

    @property
    def unresolved_quantity(self):
        return self.matched_max - self.matched_min

    @property
    def is_resolved(self):
        return self.remaining_min == _ZERO and self.remaining_max == _ZERO

    def _identity(self):
        return {
            'account_id': self.record.account_id,
            'broker_id': self.record.broker_id,
            'fitid': self.record.fitid,
            'direction': self.record.direction,
            'security': self.record.security,
            'quantity': self.record.quantity,
            'matched_min': self.matched_min,
            'matched_max': self.matched_max,
            'remaining_quantity': self.remaining_quantity,
            'definitely_unmatched_quantity': self.remaining_min,
            'remaining_min': self.remaining_min,
            'remaining_max': self.remaining_max,
            'unresolved_quantity': self.unresolved_quantity,
            'reason': self.reason,
        }

    def __repr__(self):
        return ("<RecordMatch direction=%s security=%s remaining=%s "
                "range=%s..%s>" % (
                    self.direction, self.security, self.remaining_quantity,
                    self.remaining_min, self.remaining_max))


class TransferMatchResult(object):
    """The confirmed pairs and the remaining quantities of a matching run."""

    def __init__(self, pairs, record_matches, ambiguities, issues,
                 settlement_window, matched_quantity=_ZERO):
        self.pairs = pairs
        self.record_matches = record_matches
        self.ambiguities = ambiguities
        self.issues = issues
        self.settlement_window = settlement_window
        self.matched_quantity = matched_quantity

    @property
    def matches(self):
        return self.pairs

    @property
    def confirmed_pairs(self):
        return self.pairs

    @property
    def records(self):
        return [item.record for item in self.record_matches]

    @property
    def unmatched(self):
        return [item for item in self.record_matches
                if item.remaining_quantity > _ZERO]

    @property
    def unmatched_outgoing(self):
        return [item for item in self.unmatched
                if item.direction == OUT]

    @property
    def unmatched_incoming(self):
        return [item for item in self.unmatched
                if item.direction == IN]

    @property
    def unmatched_quantity(self):
        return OrderedDict(
            (item.record, item.remaining_quantity)
            for item in self.unmatched)

    @property
    def total_unmatched(self):
        return self.total_unmatched_outgoing + self.total_unmatched_incoming

    @property
    def total_unmatched_outgoing(self):
        outgoing = sum((record.quantity for record in self.records
                        if record.direction == OUT), _ZERO)
        return outgoing - self.matched_quantity

    @property
    def total_unmatched_incoming(self):
        incoming = sum((record.quantity for record in self.records
                        if record.direction == IN), _ZERO)
        return incoming - self.matched_quantity

    @property
    def total_matched(self):
        return self.matched_quantity

    @property
    def total_remaining_min(self):
        return sum((item.remaining_min for item in self.record_matches),
                   _ZERO)

    @property
    def total_remaining_max(self):
        return sum((item.remaining_max for item in self.record_matches),
                   _ZERO)

    @property
    def unresolved(self):
        return [item for item in self.record_matches
                if item.unresolved_quantity > _ZERO]

    @property
    def ambiguous_pairs(self):
        return [pair for pair in self.ambiguities
                if not pair.quantity_exact]

    @property
    def fully_confirmed_pairs(self):
        return [pair for pair in self.pairs if pair.quantity_exact]

    def pairs_for(self, record):
        if isinstance(record, RecordMatch):
            record = record.record
        related = []
        for pair in self.pairs:
            if pair.outgoing is record or pair.incoming is record:
                related.append(pair)
        return related

    def match_for(self, record):
        if isinstance(record, RecordMatch):
            return record
        for item in self.record_matches:
            if item.record is record:
                return item
        return None

    def remaining_for(self, record):
        item = self.match_for(record)
        return None if item is None else item.remaining_quantity

    def as_dict(self):
        def record_dict(record):
            result = record._identity()
            result['units'] = str(result['units'])
            for name in ('trade_date', 'settle_date'):
                if result[name] is not None:
                    result[name] = result[name].isoformat()
            return result

        def pair_dict(pair):
            result = pair._identity()
            for name in ('quantity', 'min_quantity', 'max_quantity'):
                result[name] = str(result[name])
            result['outgoing'] = record_dict(pair.outgoing)
            result['incoming'] = record_dict(pair.incoming)
            return result

        def match_dict(item):
            result = item._identity()
            for name in ('quantity', 'matched_min', 'matched_max',
                         'remaining_quantity', 'definitely_unmatched_quantity',
                         'remaining_min', 'remaining_max',
                         'unresolved_quantity'):
                result[name] = str(result[name])
            return result

        if self.settlement_window is None:
            window = None
        else:
            window = self.settlement_window.total_seconds()

        return {
            'settlement_window_seconds': window,
            'pairs': [pair_dict(pair) for pair in self.pairs],
            'unmatched': [match_dict(item) for item in self.unmatched],
            'ambiguities': [pair_dict(pair) for pair in self.ambiguities],
            'issues': list(self.issues),
        }

    def render(self):
        """Return a compact, human-readable report of the reconciliation."""
        lines = []
        window = self.settlement_window
        if window is None:
            lines.append('Settlement window: disabled')
        else:
            lines.append('Settlement window: %s' % (window,))
        if self.pairs:
            lines.append('Confirmed transfer relationships:')
            for pair in self.pairs:
                if pair.quantity_exact:
                    amount = '%s' % pair.quantity
                else:
                    amount = '%s..%s (confirmed %s)' % (
                        pair.min_quantity, pair.max_quantity, pair.quantity)
                lines.append('  %s %s %s -> %s %s %s: %s units' % (
                    pair.outgoing.security,
                    pair.outgoing.account_id,
                    pair.outgoing.fitid,
                    pair.incoming.account_id,
                    pair.incoming.fitid,
                    pair.outgoing.direction + '/' + pair.incoming.direction,
                    amount,
                ))
        else:
            lines.append('Confirmed transfer relationships: none')
        if self.ambiguities:
            lines.append('Unconfirmed relationships (range across valid '
                         'allocations):')
            for pair in self.ambiguities:
                lines.append('  %s %s %s -> %s %s: %s..%s units' % (
                    pair.outgoing.security,
                    pair.outgoing.account_id,
                    pair.outgoing.fitid,
                    pair.incoming.account_id,
                    pair.incoming.fitid,
                    pair.min_quantity,
                    pair.max_quantity,
                ))
        if self.unmatched:
            lines.append('Unmatched remaining quantities:')
            for item in self.unmatched:
                record = item.record
                if item.remaining_min == item.remaining_max:
                    amount = '%s' % item.remaining_quantity
                else:
                    amount = '%s..%s' % (
                        item.remaining_min, item.remaining_max)
                lines.append('  %s %s %s %s remaining %s (%s)' % (
                    record.security,
                    record.account_id,
                    record.fitid,
                    record.direction,
                    amount,
                    item.reason or 'unmatched',
                ))
        else:
            lines.append('Unmatched remaining quantities: none')
        lines.append('Total matched: %s; unmatched OUT: %s; unmatched IN: %s'
                     % (self.total_matched, self.total_unmatched_outgoing,
                        self.total_unmatched_incoming))
        return '\n'.join(lines)

    def __iter__(self):
        return iter(self.pairs)

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, name):
        if isinstance(name, six.integer_types):
            return self.pairs[name]
        return getattr(self, name)

    def __repr__(self):
        return ("<TransferMatchResult pairs=%d unmatched=%d "
                "ambiguities=%d>" % (
                    len(self.pairs), len(self.unmatched),
                    len(self.ambiguities)))


class _FlowNetwork(object):
    """A Dinic maximum-flow network over non-negative integer capacities.

    The transfer quantities are scaled to integers before entering the
    network, which keeps the arithmetic exact and the residual copies cheap.
    """

    def __init__(self, node_count):
        self.node_count = node_count
        self.adjacency = [[] for _ in range(node_count)]
        self.targets = []
        self.capacities = []

    def add_edge(self, source, target, capacity):
        edge = len(self.targets)
        self.targets.append(target)
        self.capacities.append(capacity)
        self.targets.append(source)
        self.capacities.append(0)
        self.adjacency[source].append(edge)
        self.adjacency[target].append(edge + 1)
        return edge

    def copy(self):
        copied = _FlowNetwork(self.node_count)
        copied.adjacency = self.adjacency
        copied.targets = self.targets
        copied.capacities = list(self.capacities)
        return copied

    def max_flow(self, source, sink, limit=None):
        total = 0
        if limit is not None and limit <= 0:
            return total
        node_count = self.node_count
        adjacency = self.adjacency
        targets = self.targets
        capacities = self.capacities

        while True:
            level = [-1] * node_count
            level[source] = 0
            queue = [source]
            head = 0
            while head < len(queue):
                node = queue[head]
                head += 1
                next_level = level[node] + 1
                for edge in adjacency[node]:
                    target = targets[edge]
                    if capacities[edge] and level[target] < 0:
                        level[target] = next_level
                        queue.append(target)
            if level[sink] < 0:
                return total

            position = [0] * node_count
            path = []
            node = source
            while True:
                if node == sink:
                    amount = min(capacities[edge] for edge in path)
                    for edge in path:
                        capacities[edge] -= amount
                        capacities[edge ^ 1] += amount
                    total += amount
                    if limit is not None and total >= limit:
                        return limit
                    for index, edge in enumerate(path):
                        if capacities[edge] == 0:
                            del path[index:]
                            break
                    node = targets[path[-1]] if path else source
                    continue

                advanced = False
                edges = adjacency[node]
                while position[node] < len(edges):
                    edge = edges[position[node]]
                    if capacities[edge] and \
                            level[targets[edge]] == level[node] + 1:
                        path.append(edge)
                        node = targets[edge]
                        advanced = True
                        break
                    position[node] += 1
                if advanced:
                    continue
                if not path:
                    break
                path.pop()
                parent = targets[path[-1]] if path else source
                position[parent] += 1
                node = parent

    def edge_range(self, edge, capacity):
        """Return ``(min, max)`` flow through ``edge`` across all max flows."""
        flow = capacity - self.capacities[edge]
        source = self.targets[edge ^ 1]
        target = self.targets[edge]

        increase = 0
        if flow < capacity:
            residual = self.copy()
            residual.capacities[edge] = 0
            residual.capacities[edge ^ 1] = 0
            increase = min(
                capacity - flow,
                residual.max_flow(target, source, capacity - flow))

        decrease = 0
        if flow:
            residual = self.copy()
            residual.capacities[edge] = 0
            residual.capacities[edge ^ 1] = 0
            decrease = min(flow, residual.max_flow(source, target, flow))

        return flow - decrease, flow + increase


def _decimal_scale(values):
    scale = 0
    for value in values:
        exponent = value.as_tuple().exponent
        if exponent < 0:
            scale = max(scale, -exponent)
    return scale


def _to_integer(value, scale):
    return int(value.scaleb(scale))


def _to_decimal(value, scale):
    result = decimal.Decimal(value).scaleb(-scale)
    if result == result.to_integral_value():
        return result.to_integral_value()
    return result.normalize()


class _Candidate(object):
    def __init__(self, outgoing, incoming, quantity, date_basis):
        self.outgoing = outgoing
        self.incoming = incoming
        self.quantity = quantity
        self.date_basis = date_basis
        self.edge_index = None
        self.min_quantity = _ZERO
        self.max_quantity = _ZERO


def _get_attribute(item, names):
    for name in names:
        if hasattr(item, name):
            value = getattr(item, name)
            if value is not None:
                return value
    return None


def _looks_like_transaction(item):
    if not hasattr(item, 'type'):
        return False
    return (hasattr(item, 'units') or hasattr(item, 'settleDate') or
            hasattr(item, 'tradeDate') or hasattr(item, 'tferaction'))


def _account_details(account):
    account_id = _get_attribute(account, ('account_id', 'number'))
    broker_id = _get_attribute(account, ('brokerid', 'broker_id'))
    if broker_id is None:
        institution = getattr(account, 'institution', None)
        if institution is not None:
            broker_id = _get_attribute(
                institution, ('fid', 'organization'))
    return account_id, broker_id


def _iter_source(source):
    if source is None:
        return

    if isinstance(source, dict):
        for account_id, transactions in source.items():
            for transaction in transactions:
                yield transaction, (account_id, None)
        return

    if _looks_like_transaction(source):
        yield source, (None, None)
        return

    if hasattr(source, 'statement'):
        statement = source.statement
        account_id, broker_id = _account_details(source)
        for transaction in getattr(statement, 'transactions', []):
            yield transaction, (account_id, broker_id)
        return

    if hasattr(source, 'transactions') and not hasattr(source, 'accounts'):
        for transaction in source.transactions:
            yield transaction, (None, None)
        return

    if hasattr(source, 'accounts'):
        for account in source.accounts:
            for transaction, details in _iter_source(account):
                yield transaction, details
        return

    try:
        iterator = iter(source)
    except TypeError:
        raise TypeError("match_transfers() cannot read %r" % (source,))

    for item in iterator:
        if (isinstance(item, tuple) and len(item) == 2 and
                _looks_like_transaction(item[1])):
            account, transaction = item
            if hasattr(account, 'statement'):
                account_id, broker_id = _account_details(account)
            elif isinstance(account, dict):
                account_id = account.get('account_id')
                broker_id = account.get('brokerid') or account.get('broker_id')
            else:
                account_id = (six.text_type(account)
                              if account is not None else None)
                broker_id = getattr(transaction, 'broker_id', None)
            yield transaction, (account_id, broker_id)
        else:
            for transaction, details in _iter_source(item):
                yield transaction, details


def _security_aliases(source, extra):
    aliases = {}

    def add_list(security_list):
        direct = {}
        secondary = {}
        for security in security_list:
            uniqueid = _normalize_security(
                getattr(security, 'uniqueid', None))
            if not uniqueid:
                continue
            direct[uniqueid] = uniqueid
            for value in (getattr(security, 'ticker', None),
                          getattr(security, 'name', None)):
                key = _normalize_security(value)
                if key and key not in secondary:
                    secondary[key] = uniqueid
        for key, value in secondary.items():
            if key not in direct:
                aliases[key] = value

    security_list = getattr(source, 'security_list', None)
    if security_list:
        add_list(security_list)
    if isinstance(source, (list, tuple)):
        for item in source:
            security_list = getattr(item, 'security_list', None)
            if security_list:
                add_list(security_list)
    if extra:
        for key, value in extra.items():
            aliases[_normalize_security(key)] = _normalize_security(value)
    return aliases


def _record_from_transaction(transaction, account_id, broker_id, aliases,
                             issues):
    transaction_type = six.text_type(
        getattr(transaction, 'type', '') or '').lower()
    action = getattr(transaction, 'tferaction', None)
    action = six.text_type(action).strip().upper() if action else None

    if transaction_type != 'transfer' and action not in (IN, OUT):
        return None

    units = _get_attribute(transaction, ('units', 'quantity'))
    if units is None:
        issues.append(
            "Transfer %r has no units and was ignored" %
            (getattr(transaction, 'id', None),))
        return None
    try:
        units = _as_decimal(units)
    except (ValueError, decimal.InvalidOperation):
        issues.append(
            "Transfer %r has invalid units %r" %
            (getattr(transaction, 'id', None), units))
        return None
    if units == _ZERO:
        issues.append(
            "Transfer %r has zero units and was ignored" %
            (getattr(transaction, 'id', None),))
        return None

    security = _get_attribute(transaction, ('security', 'uniqueid'))
    security_key = _normalize_security(security)
    if not security_key:
        issues.append(
            "Transfer %r has no security and was ignored" %
            (getattr(transaction, 'id', None),))
        return None
    canonical_key = aliases.get(security_key, security_key)

    if action in (IN, OUT):
        direction = action
        if (units > _ZERO and direction == OUT) or \
                (units < _ZERO and direction == IN):
            issues.append(
                "Transfer %r has TFERACTION %s but units %s; "
                "the action was used" %
                (getattr(transaction, 'id', None), action, units))
    elif units > _ZERO:
        direction = IN
        issues.append(
            "Transfer %r has no TFERACTION; IN was inferred from units" %
            (getattr(transaction, 'id', None),))
    else:
        direction = OUT
        issues.append(
            "Transfer %r has no TFERACTION; OUT was inferred from units" %
            (getattr(transaction, 'id', None),))

    trade_date = _get_attribute(transaction, ('tradeDate', 'trade_date'))
    settle_date = _get_attribute(transaction, ('settleDate', 'settle_date'))

    return TransferRecord(
        transaction=transaction,
        account_id=account_id,
        broker_id=broker_id,
        direction=direction,
        security=security,
        quantity=abs(units),
        trade_date=trade_date,
        settle_date=settle_date,
        security_key=canonical_key,
    )


def _collect_records(source, aliases, issues):
    records = []
    seen_objects = set()
    seen_identities = {}
    for transaction, (account_id, broker_id) in _iter_source(source):
        record = _record_from_transaction(
            transaction, account_id, broker_id, aliases, issues)
        if record is None:
            continue
        identity = id(record.transaction)
        if identity in seen_objects:
            continue
        seen_objects.add(identity)
        if record.account_id is None:
            record.account_id = _get_attribute(
                transaction, ('account_id', 'acctid'))
        if record.broker_id is None:
            record.broker_id = _get_attribute(
                transaction, ('broker_id', 'brokerid'))
        if record.fitid:
            key = (record.account_key, record.fitid)
            previous = seen_identities.get(key)
            if previous is not None and previous._identity() == \
                    record._identity():
                issues.append(
                    "Transfer %r was reported more than once and only the "
                    "first copy was used" % (record.fitid,))
                continue
            seen_identities[key] = record
        records.append(record)
    return records


def _pair_dates(outgoing, incoming):
    if outgoing.settle_date is not None and incoming.settle_date is not None:
        return outgoing.settle_date, incoming.settle_date, 'settle'
    if outgoing.trade_date is not None and incoming.trade_date is not None:
        return outgoing.trade_date, incoming.trade_date, 'trade'
    if outgoing.date is not None and incoming.date is not None:
        return outgoing.date, incoming.date, 'mixed'
    return None


def _naive(value):
    if value is not None and value.tzinfo is not None:
        offset = value.utcoffset()
        if offset is not None:
            value = value - offset
        value = value.replace(tzinfo=None)
    return value


def _date_difference(left, right):
    return abs(_naive(left) - _naive(right))


def _record_sort_key(record):
    return (
        record.security_key or '',
        record.account_id or '',
        record.broker_id or '',
        record.date.isoformat() if record.date is not None else '',
        record.direction,
        six.text_type(record.fitid or ''),
    )


def _build_matches(records, settlement_window, require_distinct_accounts):
    issues = []
    pair_candidates = []
    groups = OrderedDict()
    same_account_counts = {}
    window_counts = {}
    matched = {}
    matched_scales = {}

    for record in sorted(records, key=_record_sort_key):
        groups.setdefault(record.security_key, {
            OUT: [],
            IN: [],
        })[record.direction].append(record)

    record_usage = {}
    for security_key, group in groups.items():
        outgoing = group[OUT]
        incoming = group[IN]
        if not outgoing or not incoming:
            for record in outgoing + incoming:
                record_usage[record] = (_ZERO, _ZERO)
            continue

        candidates = []
        for out_record in outgoing:
            for in_record in incoming:
                if (require_distinct_accounts and
                        out_record.account_key is not None and
                        in_record.account_key is not None and
                        out_record.account_key == in_record.account_key):
                    same_account_counts[out_record] = (
                        same_account_counts.get(out_record, 0) + 1)
                    same_account_counts[in_record] = (
                        same_account_counts.get(in_record, 0) + 1)
                    continue
                dates = _pair_dates(out_record, in_record)
                if dates is None:
                    if settlement_window is not None:
                        window_counts[out_record] = (
                            window_counts.get(out_record, 0) + 1)
                        window_counts[in_record] = (
                            window_counts.get(in_record, 0) + 1)
                        continue
                    date_basis = 'missing'
                else:
                    out_date, in_date, date_basis = dates
                    if settlement_window is not None and \
                            _date_difference(out_date, in_date) > \
                            settlement_window:
                        window_counts[out_record] = (
                            window_counts.get(out_record, 0) + 1)
                        window_counts[in_record] = (
                            window_counts.get(in_record, 0) + 1)
                        continue
                quantity = min(out_record.quantity, in_record.quantity)
                if quantity <= _ZERO:
                    continue
                candidates.append(_Candidate(
                    out_record, in_record, quantity, date_basis))

        scale = _decimal_scale(
            [record.quantity for record in outgoing + incoming] +
            [candidate.quantity for candidate in candidates])
        matched_scales[security_key] = scale

        node_count = 2 + len(outgoing) + len(incoming)
        source_node = 0
        out_offset = 1
        in_offset = out_offset + len(outgoing)
        sink_node = node_count - 1
        network = _FlowNetwork(node_count)

        out_edges = {}
        for index, record in enumerate(outgoing):
            node = out_offset + index
            edge_index = network.add_edge(
                source_node, node, _to_integer(record.quantity, scale))
            out_edges[record] = edge_index

        in_edges = {}
        for index, record in enumerate(incoming):
            node = in_offset + index
            edge_index = network.add_edge(
                node, sink_node, _to_integer(record.quantity, scale))
            in_edges[record] = edge_index

        for candidate in candidates:
            source = out_offset + outgoing.index(candidate.outgoing)
            target = in_offset + incoming.index(candidate.incoming)
            candidate.edge_index = network.add_edge(
                source, target, _to_integer(candidate.quantity, scale))

        matched[security_key] = network.max_flow(source_node, sink_node)

        for record in outgoing:
            record_usage[record] = tuple(
                _to_decimal(value, scale) for value in network.edge_range(
                    out_edges[record], _to_integer(record.quantity, scale)))
        for record in incoming:
            record_usage[record] = tuple(
                _to_decimal(value, scale) for value in network.edge_range(
                    in_edges[record], _to_integer(record.quantity, scale)))

        for candidate in candidates:
            minimum, maximum = network.edge_range(
                candidate.edge_index, _to_integer(candidate.quantity, scale))
            candidate.min_quantity = _to_decimal(minimum, scale)
            candidate.max_quantity = _to_decimal(maximum, scale)
            pair_candidates.append(candidate)

    pairs = []
    ambiguities = []
    for candidate in pair_candidates:
        pair = TransferPair(
            candidate.outgoing,
            candidate.incoming,
            candidate.min_quantity,
            min_quantity=candidate.min_quantity,
            max_quantity=candidate.max_quantity,
            date_basis=candidate.date_basis,
        )
        if candidate.min_quantity > _ZERO:
            pairs.append(pair)
        if candidate.min_quantity != candidate.max_quantity:
            ambiguities.append(pair)

    pairs.sort(key=lambda pair: (
        pair.outgoing.security_key or '',
        pair.outgoing.date.isoformat() if pair.outgoing.date else '',
        pair.incoming.date.isoformat() if pair.incoming.date else '',
        pair.outgoing.key,
        pair.incoming.key,
    ))
    ambiguities.sort(key=lambda pair: (
        pair.outgoing.key, pair.incoming.key))

    candidate_counts = {}
    for candidate in pair_candidates:
        candidate_counts[candidate.outgoing] = candidate_counts.get(
            candidate.outgoing, 0) + 1
        candidate_counts[candidate.incoming] = candidate_counts.get(
            candidate.incoming, 0) + 1

    record_matches = []
    for record in sorted(records, key=_record_sort_key):
        matched_min, matched_max = record_usage.get(
            record, (_ZERO, _ZERO))
        record_match = RecordMatch(
            record, matched_min, matched_max,
            candidate_count=candidate_counts.get(record, 0))
        record_match.same_account_candidates = same_account_counts.get(
            record, 0)
        record_match.window_candidates = window_counts.get(record, 0)
        if record_match.remaining_quantity > _ZERO:
            if record_match.candidate_count == 0:
                if record.date is None and settlement_window is not None:
                    record_match.reason = 'missing_date'
                elif same_account_counts.get(record, 0):
                    record_match.reason = 'same_account_only'
                elif window_counts.get(record, 0):
                    record_match.reason = 'outside_settlement_window'
                else:
                    record_match.reason = 'no_counterpart'
            elif record_match.matched_max == _ZERO:
                record_match.reason = 'no_eligible_counterpart'
            elif record_match.matched_min == _ZERO:
                record_match.reason = 'ambiguous'
            elif record_match.unresolved_quantity > _ZERO:
                record_match.reason = 'partially_ambiguous'
            else:
                record_match.reason = 'partial'
        record_matches.append(record_match)

    total_matched = _ZERO
    for security_key, amount in matched.items():
        total_matched += _to_decimal(amount, matched_scales[security_key])
    return pairs, record_matches, ambiguities, issues, total_matched


def match_transfers(source=None, settlement_window=DEFAULT_SETTLEMENT_WINDOW,
                    security_aliases=None,
                    require_distinct_accounts=True, **options):
    """Match security transfer records across accounts.

    ``source`` may be an :class:`~ofxparse.ofxparse.Ofx` result, an account, a
    statement, an iterable of those, or an iterable of
    :class:`~ofxparse.ofxparse.InvestmentTransaction` objects.  A transaction
    may also be supplied as ``(account, transaction)`` or ``(account_id,
    transaction)``.

    ``settlement_window`` is the largest allowed difference between the two
    records' settlement dates.  It accepts a ``timedelta``, a number of days,
    or strings such as ``"3d"`` or ``"P3D"``.  ``None`` disables the date
    check.  When both records have a settlement date that is used; otherwise
    their trade dates are used.

    Records are compared on security, direction, and quantity; the date
    window is checked on top of that.  A record is never allocated twice,
    even if its FITID happens to repeat elsewhere.  A record's units are a
    capacity, not a single pairing decision, so one outgoing record can be
    split across several incoming records and vice versa.  Only
    relationships present in every maximum allocation are put in
    ``result.pairs``.  A pair's ``quantity`` is the amount guaranteed to
    belong to that relationship; ``min_quantity`` and ``max_quantity`` show
    the range across all equally good allocations.  ``result.total_matched``
    and ``result.total_unmatched_outgoing`` /
    ``result.total_unmatched_incoming`` are derived from the maximum flow,
    so they do not depend on which particular allocation was chosen.
    """
    if security_aliases is None:
        security_aliases = {}
    window_options = {}
    for name in ('window', 'settlement_window_days', 'max_settlement_days'):
        if name in options:
            window_options[name] = options.pop(name)
    if 'window' in window_options:
        settlement_window = window_options['window']
    elif 'settlement_window_days' in window_options:
        settlement_window = window_options['settlement_window_days']
    elif 'max_settlement_days' in window_options:
        settlement_window = window_options['max_settlement_days']
    for name in ('aliases', 'alias'):
        if name in options:
            security_aliases = dict(security_aliases)
            security_aliases.update(options.pop(name) or {})
    if 'allow_same_account' in options:
        require_distinct_accounts = not options.pop('allow_same_account')
    if options:
        raise TypeError(
            "unexpected arguments: %s" % ', '.join(sorted(options)))
    window = _normalize_window(settlement_window)
    aliases = _security_aliases(source, security_aliases)
    issues = []
    records = _collect_records(source, aliases, issues)
    pairs, record_matches, ambiguities, match_issues, matched = \
        _build_matches(records, window, require_distinct_accounts)
    issues.extend(match_issues)
    return TransferMatchResult(
        pairs, record_matches, ambiguities, issues, window,
        matched_quantity=matched)


class TransferMatcher(object):
    """Reusable matcher with a fixed settlement window."""

    def __init__(self, settlement_window=DEFAULT_SETTLEMENT_WINDOW,
                 security_aliases=None, require_distinct_accounts=True):
        self.settlement_window = settlement_window
        self.security_aliases = security_aliases or {}
        self.require_distinct_accounts = require_distinct_accounts

    def match(self, source):
        return match_transfers(
            source,
            settlement_window=self.settlement_window,
            security_aliases=self.security_aliases,
            require_distinct_accounts=self.require_distinct_accounts,
        )

    __call__ = match


def reconcile_transfers(source, **kwargs):
    return match_transfers(source, **kwargs)


def pair_transfers(source, **kwargs):
    return match_transfers(source, **kwargs)
