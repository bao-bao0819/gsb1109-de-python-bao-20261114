from __future__ import absolute_import

"""Correction-aware ledger for OFX statements.

OFX FITIDs are only unique inside one institution account, and a correction
may arrive before the statement that contains the original transaction.  This
module keeps every observed version, interprets FITIDs in an account-scoped
namespace, and exposes the transactions that are still valid.

The public API is exported from :mod:`ofxparse` and :mod:`ofxparse.ledger`.
"""

import collections
import datetime
import decimal
import os

import six

from .ofxparse import OfxParser


_PATH_TYPES = six.string_types + (bytes,)
if hasattr(os, 'PathLike'):
    _PATH_TYPES = _PATH_TYPES + (os.PathLike,)


TRANSACTION_ID_ATTRS = ('id', 'fitid', 'fit_id', 'transaction_id')
TRANSACTION_AMOUNT_ATTRS = ('amount', 'trnamt', 'total')
TRANSACTION_CORRECT_FITID_ATTRS = (
    'correct_fitid', 'corrected_fitid', 'correction_fitid',
    'correct_fit_id', 'corrected_transaction_id', 'correctfitid',
    'corrected_id', 'correction_id', 'replaces_fitid', 'replaces',
)
TRANSACTION_CORRECT_ACTION_ATTRS = (
    'correct_action', 'correction_action', 'correctaction',
    'corrected_action',
)
TRANSACTION_DATE_ATTRS = ('date', 'dtposted', 'posted_date', 'tradeDate')
TRANSACTION_TYPE_ATTRS = ('type', 'trntype')
TRANSACTION_PAYEE_ATTRS = ('payee', 'name')
TRANSACTION_MEMO_ATTRS = ('memo',)
TRANSACTION_CHECKNUM_ATTRS = ('checknum',)


DELETE = 'DELETE'
REPLACE = 'REPLACE'

_ACTION_ALIASES = {
    'delete': DELETE,
    'deleted': DELETE,
    'del': DELETE,
    'remove': DELETE,
    'removed': DELETE,
    'void': DELETE,
    'voided': DELETE,
    'cancel': DELETE,
    'cancelled': DELETE,
    'canceled': DELETE,
    'replace': REPLACE,
    'replaced': REPLACE,
    'replacement': REPLACE,
    'update': REPLACE,
    'updated': REPLACE,
    'correct': REPLACE,
    'corrected': REPLACE,
}


def _value(obj, names, default=None):
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _is_ofx_content(source):
    if isinstance(source, bytes):
        return b'<OFX' in source[:4096].upper()
    return '<OFX' in source[:4096].upper()


def _text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace').strip()
    return str(value).strip()


def _transaction_id(transaction):
    return _text(_value(transaction, TRANSACTION_ID_ATTRS, ''))


def _transaction_amount(transaction):
    value = _value(transaction, TRANSACTION_AMOUNT_ATTRS)
    if value is None:
        return decimal.Decimal('0')
    if isinstance(value, decimal.Decimal):
        return value
    text = _text(value).replace(' ', '').replace('+', '')
    if not text:
        return decimal.Decimal('0')
    # Accept the same 1,234.56 / 1.234,56 variants as OfxParser.toDecimal.
    if ',' in text and '.' in text:
        if text.rfind(',') > text.rfind('.'):
            text = text.replace('.', '').replace(',', '.')
        else:
            text = text.replace(',', '')
    elif ',' in text:
        text = text.replace(',', '.')
    try:
        return decimal.Decimal(text)
    except (decimal.InvalidOperation, ValueError, TypeError):
        return decimal.Decimal('0')


def _transaction_date(transaction):
    value = _value(transaction, TRANSACTION_DATE_ATTRS)
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime.combine(value, datetime.time())
    if value is not None:
        text = _text(value)
        if text:
            digits = text[:14] if len(text) >= 14 else text[:8]
            if digits.isdigit():
                try:
                    return OfxParser.parseOfxDateTime(text)
                except ValueError:
                    pass
            iso = text.replace('Z', '+00:00')
            try:
                return datetime.datetime.fromisoformat(iso)
            except (AttributeError, ValueError):
                pass
    return None


def _transaction_payee(transaction):
    return _text(_value(transaction, TRANSACTION_PAYEE_ATTRS, ''))


def _transaction_memo(transaction):
    return _text(_value(transaction, TRANSACTION_MEMO_ATTRS, ''))


def _transaction_checknum(transaction):
    return _text(_value(transaction, TRANSACTION_CHECKNUM_ATTRS, ''))


def _transaction_type(transaction):
    return _text(_value(transaction, TRANSACTION_TYPE_ATTRS, '')).lower()


def _correct_fitid(transaction):
    return _text(_value(transaction, TRANSACTION_CORRECT_FITID_ATTRS, ''))


def _correct_action(transaction):
    value = _text(_value(transaction, TRANSACTION_CORRECT_ACTION_ATTRS, ''))
    if not value:
        return ''
    return _ACTION_ALIASES.get(value.lower(), value.upper())


def _core_fingerprint(transaction):
    # A FITID identifies a slot in the account.  Repeated statements may
    # quote a different posting date or description for the same slot, but a
    # different amount changes the balance and is a genuine ambiguity.
    return (_transaction_amount(transaction),)


def _sort_key(transaction, transaction_id):
    date = _transaction_date(transaction)
    if date is None:
        date = datetime.datetime.min
    return (date, transaction_id, _transaction_type(transaction),
            _transaction_amount(transaction), _transaction_payee(transaction))


def _ledger_sort_key(account_key, transaction):
    return (account_key.identity, _sort_key(
        transaction, _transaction_id(transaction)))


class TransactionList(list):
    """List that also tolerates being called like a method."""

    def __call__(self):
        return self

    @property
    def ids(self):
        return [_transaction_id(transaction) for transaction in self]

    @property
    def fitids(self):
        return self.ids


class CorrectionChainList(list):
    """List of chains that also tolerates method-style access."""

    def __call__(self):
        return self

    @property
    def ids(self):
        return [chain.id for chain in self]


class AccountCollection(collections.OrderedDict):
    """Account ledgers keyed by :class:`AccountKey`.

    String lookups by account number are accepted when they are unambiguous,
    which keeps simple callers from having to build an ``AccountKey``.
    """

    def _resolve(self, key):
        if collections.OrderedDict.__contains__(self, key):
            return key
        if isinstance(key, AccountKey):
            raise KeyError(key)
        account_id = _text(getattr(key, 'account_id', key))
        matches = [candidate for candidate in self
                   if candidate.account_id == account_id]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(key)
        raise KeyError('account id is ambiguous; use AccountKey')

    def __getitem__(self, key):
        return collections.OrderedDict.__getitem__(self, self._resolve(key))

    def __contains__(self, key):
        try:
            self._resolve(key)
        except KeyError:
            return False
        return True

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class AccountKey(collections.namedtuple(
        '_AccountKey',
        'organization fid account_id routing_number branch_id '
        'account_type brokerid')):
    """Identity of the account in which ::FITID values are interpreted.

    OFX only promises that a FITID is unique inside one institution account.
    The same value may therefore occur at another institution or in another
    account without describing the same transaction.
    """

    __slots__ = ()

    def __new__(cls, organization='', fid='', account_id='',
                routing_number='', branch_id='', account_type='',
                brokerid='', **aliases):
        institution = aliases.pop('institution', None)
        if institution is not None:
            if isinstance(institution, dict):
                organization = organization or institution.get('organization',
                                                              '')
                fid = fid or institution.get('fid', '')
            elif isinstance(institution, (tuple, list)):
                if len(institution) > 0:
                    organization = organization or institution[0]
                if len(institution) > 1:
                    fid = fid or institution[1]
            else:
                organization = organization or getattr(
                    institution, 'organization', '')
                fid = fid or getattr(institution, 'fid', '')
        organization = (aliases.pop('org', None) or
                        aliases.pop('fi_org', None) or organization or '')
        fid = (aliases.pop('fi_fid', None) or aliases.pop('fid_id', None) or
               fid or '')
        account_id = (account_id or aliases.pop('acctid', None) or
                      aliases.pop('account', None) or
                      aliases.pop('number', None) or '')
        routing_number = (routing_number or aliases.pop('bankid', None) or
                          aliases.pop('bank_id', None) or '')
        branch_id = branch_id or aliases.pop('branchid', None) or ''
        account_type = (account_type or aliases.pop('accttype', None) or '')
        brokerid = brokerid or aliases.pop('broker_id', None) or ''
        if aliases:
            unknown = sorted(aliases)
            raise TypeError('unexpected account key argument(s): %s' %
                            ', '.join(unknown))
        return super(AccountKey, cls).__new__(
            cls,
            _text(organization),
            _text(fid),
            _text(account_id),
            _text(routing_number),
            _text(branch_id),
            _text(account_type),
            _text(brokerid),
        )

    @property
    def institution(self):
        return (self.organization, self.fid)

    @property
    def institution_id(self):
        return self.institution

    @property
    def routing_fallback(self):
        if self.account_id:
            return ''
        if self.routing_number and self.branch_id:
            return '%s/%s' % (self.routing_number, self.branch_id)
        return self.routing_number

    @property
    def identity(self):
        if self.organization or self.fid:
            institution = (self.organization, self.fid)
        else:
            institution = ('', '', self.routing_number)
        account = self.account_id or self.brokerid or self.routing_fallback
        return institution + (account,)

    @property
    def org(self):
        return self.organization

    @property
    def acctid(self):
        return self.account_id

    @property
    def bankid(self):
        return self.routing_number

    def _asdict(self):
        return collections.OrderedDict(zip(self._fields, self))

    def __eq__(self, other):
        if isinstance(other, AccountKey):
            return self.identity == other.identity
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self):
        return hash(self.identity)

    @classmethod
    def from_account(cls, account=None, **overrides):
        if account is None:
            key = cls()
        elif isinstance(account, cls):
            key = account
        elif isinstance(account, (tuple, list)):
            key = cls.from_tuple(*account)
        elif isinstance(account, six.string_types):
            key = cls(account_id=account)
        elif isinstance(account, dict):
            institution = account.get('institution')
            organization = account.get('organization') or ''
            fid = account.get('fid') or ''
            if institution is not None:
                if not organization:
                    organization = getattr(institution, 'organization', '')
                if not fid:
                    fid = getattr(institution, 'fid', '')
            key = cls(
                organization=organization,
                fid=fid,
                account_id=account.get('account_id',
                                       account.get('number', '')),
                routing_number=account.get('routing_number', ''),
                branch_id=account.get('branch_id', ''),
                account_type=account.get('account_type', ''),
                brokerid=account.get('brokerid', ''),
            )
        else:
            institution = getattr(account, 'institution', None)
            organization = getattr(account, 'organization', '')
            fid = getattr(account, 'fid', '')
            if institution is not None:
                if not organization:
                    organization = getattr(institution, 'organization', '')
                if not fid:
                    fid = getattr(institution, 'fid', '')
            number = getattr(account, 'account_id', '')
            if not number:
                number = getattr(account, 'number', '')
            key = cls(
                organization=organization,
                fid=fid,
                account_id=number,
                routing_number=getattr(account, 'routing_number', ''),
                branch_id=getattr(account, 'branch_id', ''),
                account_type=getattr(account, 'account_type', ''),
                brokerid=getattr(account, 'brokerid', ''),
            )
        if overrides:
            values = key._asdict()
            values.update((name, value) for name, value in overrides.items()
                          if value is not None)
            key = cls(**values)
        return key

    @classmethod
    def from_tuple(cls, *values):
        if len(values) == 1 and isinstance(values[0], (tuple, list)):
            values = tuple(values[0])
        names = cls._fields
        return cls(**dict(zip(names, values)))

    @property
    def routing_fallback(self):
        if self.account_id:
            return ''
        if self.routing_number and self.branch_id:
            return '%s/%s' % (self.routing_number, self.branch_id)
        return self.routing_number

    def short(self):
        parts = [part for part in (self.organization, self.fid)
                 if part]
        if self.account_id:
            parts.append(self.account_id)
        elif self.routing_fallback:
            parts.append(self.routing_fallback)
        return '/'.join(parts)

    def __str__(self):
        return self.short()


class _Entry(object):
    __slots__ = (
        'namespace', 'id', 'transaction', 'targets', 'actions_by_target',
        'self_actions', 'content_fingerprints', 'occurrences', 'data_conflict',
    )

    def __init__(self, namespace, transaction_id, transaction):
        self.namespace = namespace
        self.id = transaction_id
        self.transaction = transaction
        self.targets = set()
        self.actions_by_target = {}
        self.self_actions = set()
        self.content_fingerprints = set()
        self.occurrences = 0
        self.data_conflict = False

    def absorb(self, transaction):
        self.occurrences += 1
        target = _correct_fitid(transaction)
        action = _correct_action(transaction)
        deletion = action == DELETE or (not action and target == self.id)
        if target and target == self.id:
            # A self-referencing correction removes this transaction; it must
            # not become a cycle in the correction graph.
            self.self_actions.add(action or DELETE)
            if len(self.self_actions) > 1:
                self.data_conflict = True
        elif target:
            self.targets.add(target)
            if len(self.targets) > 1:
                self.data_conflict = True
            if action:
                actions = self.actions_by_target.setdefault(target, set())
                actions.add(action)
                if len(actions) > 1:
                    self.data_conflict = True
            else:
                # A correction that names a prior FITID but omits the action
                # is a replacement, which is the only non-destructive action.
                self.actions_by_target.setdefault(target, set()).add(REPLACE)
        elif action:
            self.self_actions.add(action)
            if len(self.self_actions) > 1:
                self.data_conflict = True

        if not deletion:
            fingerprint = _core_fingerprint(transaction)
            self.content_fingerprints.add(fingerprint)
            if len(self.content_fingerprints) > 1:
                self.data_conflict = True

    @property
    def actions(self):
        result = set(self.self_actions)
        for actions in self.actions_by_target.values():
            result.update(actions)
        return result

    @property
    def correct_fitid(self):
        if not self.targets:
            return ''
        return sorted(self.targets)[0]

    @property
    def correct_action(self):
        actions = self.actions
        if not actions:
            return ''
        if DELETE in self.self_actions:
            return DELETE
        return sorted(actions)[0]

    @property
    def is_delete(self):
        return DELETE in self.self_actions or (
            DELETE in self.actions and REPLACE not in self.actions)

    @property
    def is_correction(self):
        return bool(self.targets) or bool(self.self_actions)

    @property
    def amount(self):
        return _transaction_amount(self.transaction)

    @property
    def sort_key(self):
        return _sort_key(self.transaction, self.id)


class CorrectionChain(object):
    """One connected correction history inside a single account.

    ``entries``/``transactions`` are the full history.  ``valid_ids`` and
    ``valid_transactions`` contain only the transaction that survived all
    corrections.  A chain whose history is contradictory is ``pending`` and
    contributes no transactions or balance to the active ledger.
    """

    def __init__(self, namespace, node_ids, entries, valid_ids, pending,
                 reasons, outgoing):
        self.namespace = namespace
        self.node_ids = tuple(sorted(node_ids))
        self._entries = entries
        self.valid_ids = tuple(sorted(valid_ids))
        self.pending = bool(pending)
        self.reasons = tuple(sorted(reasons))
        self._outgoing = outgoing

    @property
    def id(self):
        roots = self.root_ids
        return roots[0] if roots else (self.node_ids[0]
                                       if self.node_ids else '')

    @property
    def fitids(self):
        return self.transaction_ids

    @property
    def transaction_ids(self):
        return tuple(sorted(entry.id for entry in self.entries))

    @property
    def entries(self):
        return tuple(self._entries[node] for node in self.node_ids
                     if node in self._entries)

    @property
    def transactions(self):
        return tuple(entry.transaction
                     for entry in sorted(self.entries, key=lambda item: item.sort_key))

    @property
    def history(self):
        return self.transactions

    @property
    def all_transactions(self):
        return self.transactions

    @property
    def valid_transactions(self):
        return TransactionList(self._entries[node].transaction
                               for node in self.valid_ids
                               if node in self._entries)

    @property
    def current_transactions(self):
        return self.valid_transactions

    @property
    def pending_transactions(self):
        if not self.pending:
            return TransactionList()
        return TransactionList(entry.transaction
                               for entry in sorted(
                                   self.entries, key=lambda item: item.sort_key))

    @property
    def unreconciled_transactions(self):
        return self.pending_transactions

    @property
    def balance_contribution(self):
        if self.pending:
            return decimal.Decimal('0')
        return sum((self._entries[node].amount for node in self.valid_ids
                    if node in self._entries), decimal.Decimal('0'))

    @property
    def balance(self):
        return self.balance_contribution

    @property
    def root_ids(self):
        result = []
        corrected_sources = {source for source, target, action in self._outgoing}
        for node in self.node_ids:
            if node not in self._entries:
                continue
            if node not in corrected_sources:
                result.append(node)
        return tuple(sorted(result))

    @property
    def leaf_ids(self):
        corrected = {target for source, target, action in self._outgoing
                     if source in self._entries}
        return tuple(sorted(node for node in self.node_ids
                            if node not in corrected
                            and node in self._entries))

    @property
    def ordered_ids(self):
        """IDs from the oldest known transaction to its latest correction."""
        order = []
        visited = set()

        def visit(node):
            if node in visited:
                return
            visited.add(node)
            if node in self._entries:
                order.append(node)
            for source, target, action in self._outgoing:
                if target == node:
                    visit(source)

        for root in self.root_ids:
            visit(root)
        for node in self.node_ids:
            visit(node)
        return tuple(node for node in order if node in self._entries)

    @property
    def ordered_transactions(self):
        return tuple(self._entries[node].transaction
                     for node in self.ordered_ids)

    @property
    def transition_ids(self):
        return self.ordered_ids

    @property
    def chain_ids(self):
        return self.ordered_ids

    @property
    def valid_transaction(self):
        transactions = self.valid_transactions
        return transactions[0] if transactions else None

    @property
    def current_transaction(self):
        return self.valid_transaction

    @property
    def valid_transaction_id(self):
        return self.valid_ids[0] if self.valid_ids else None

    @property
    def current_fitid(self):
        return self.valid_transaction_id

    @property
    def is_pending(self):
        return self.pending

    @property
    def is_deleted(self):
        return self.status == 'deleted'

    @property
    def deleted(self):
        return self.is_deleted

    @property
    def correction_ids(self):
        return tuple(sorted(source for source, target, action
                            in self._outgoing))

    @property
    def status(self):
        if self.pending:
            return 'pending'
        if self.valid_ids:
            return 'active'
        if any(self._entries[node].is_delete for node in self.node_ids
               if node in self._entries):
            return 'deleted'
        return 'empty'

    def __len__(self):
        return len(self.entries)

    def __iter__(self):
        return iter(self.transactions)

    def __repr__(self):
        return '<CorrectionChain %s %s>' % (self.namespace, self.status)


class AccountLedger(object):
    """Correction-aware ledger for one institution account."""

    def __init__(self, key):
        self.key = AccountKey.from_account(key)
        self._entries = {}

    @property
    def namespace(self):
        return self.key

    @property
    def account_id(self):
        return self.key.account_id

    def add_transaction(self, transaction):
        transaction_id = _transaction_id(transaction)
        if not transaction_id:
            return None
        entry = self._entries.get(transaction_id)
        if entry is None:
            entry = _Entry(self.key, transaction_id, transaction)
            entry.absorb(transaction)
            self._entries[transaction_id] = entry
        else:
            entry.absorb(transaction)
            if not _correct_fitid(entry.transaction) and _correct_fitid(transaction):
                # Keep correction metadata visible on the representative
                # object even when the plain statement arrived first.
                entry.transaction = transaction
        return entry

    def add_transactions(self, transactions):
        for transaction in transactions:
            self.add_transaction(transaction)
        return self

    def _build(self):
        entries = self._entries
        node_ids = set(entries)
        outgoing = []
        for entry in entries.values():
            for target in entry.targets:
                actions = entry.actions_by_target.get(target) or {REPLACE}
                for action in actions:
                    outgoing.append((entry.id, target, action))
                    node_ids.add(target)

        adjacency = dict((node, set()) for node in node_ids)
        outgoing_by_source = collections.defaultdict(list)
        for source, target, action in outgoing:
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
            outgoing_by_source[source].append((target, action))

        conflicts = {}
        for node in node_ids:
            corrections = [(source, action)
                           for source, target, action in outgoing
                           if target == node]
            replacements = {source for source, action in corrections
                            if action == REPLACE}
            deletions = {source for source, action in corrections
                         if action == DELETE}
            reasons = []
            if replacements and deletions:
                reasons.append('conflicting_correction_actions')
            if len(replacements) > 1:
                reasons.append('multiple_replacements')
            if len(deletions) > 1:
                # Multiple independent DELETE corrections are idempotent,
                # not mutually exclusive.
                pass
            if reasons:
                conflicts.setdefault(node, set()).update(reasons)
            for source in replacements | deletions:
                entry = entries.get(source)
                if entry is not None and entry.data_conflict:
                    conflicts.setdefault(node, set()).add('inconsistent_duplicate')
            entry = entries.get(node)
            if entry is not None and entry.data_conflict:
                conflicts.setdefault(node, set()).add('inconsistent_duplicate')

        components = []
        seen = set()
        for node in sorted(node_ids):
            if node in seen:
                continue
            component = set()
            stack = [node]
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(adjacency.get(current, ()))
            seen.update(component)
            components.append(component)

        chains = []
        for component in components:
            reasons = set()
            for node in component:
                reasons.update(conflicts.get(node, ()))
            for entry in entries.values():
                if entry.id in component and entry.data_conflict:
                    reasons.add('inconsistent_duplicate')

            if _has_cycle(component, outgoing_by_source, entries):
                reasons.add('correction_cycle')

            pending = bool(reasons)
            valid_ids = []
            if not pending:
                corrected = {target for source, target, action in outgoing}
                for node in component:
                    entry = entries.get(node)
                    if entry is None:
                        continue
                    if entry.is_delete:
                        continue
                    if node in corrected:
                        continue
                    valid_ids.append(node)
            chains.append(CorrectionChain(
                self.key, component, entries, valid_ids, pending, reasons,
                outgoing))
        chains.sort(key=lambda chain: (chain.status != 'pending',
                                       chain.id))
        return CorrectionChainList(chains)

    @property
    def chains(self):
        return self._build()

    @property
    def correction_chains(self):
        return self.chains

    @property
    def pending_chains(self):
        return CorrectionChainList(chain for chain in self.chains
                                   if chain.pending)

    @property
    def unreconciled_chains(self):
        return self.pending_chains

    @property
    def pending_reconciliation(self):
        return self.pending_chains

    @property
    def valid_transactions(self):
        transactions = []
        for chain in self.chains:
            transactions.extend(chain.valid_transactions)
        return TransactionList(sorted(transactions,
            key=lambda transaction: _sort_key(
                transaction, _transaction_id(transaction))))

    @property
    def current_transactions(self):
        return self.valid_transactions

    @property
    def active_transactions(self):
        return self.valid_transactions

    @property
    def transactions(self):
        return self.valid_transactions

    @property
    def pending_transactions(self):
        transactions = []
        for chain in self.pending_chains:
            transactions.extend(chain.pending_transactions)
        return TransactionList(sorted(transactions,
            key=lambda transaction: _sort_key(
                transaction, _transaction_id(transaction))))

    @property
    def unreconciled_transactions(self):
        return self.pending_transactions

    @property
    def all_transactions(self):
        return [entry.transaction for entry in sorted(
            self._entries.values(), key=lambda entry: entry.sort_key)]

    @property
    def balance_contribution(self):
        return sum((chain.balance_contribution for chain in self.chains),
                   decimal.Decimal('0'))

    @property
    def balance(self):
        return self.balance_contribution

    @property
    def total(self):
        return self.balance_contribution

    def get_transaction(self, transaction_id):
        entry = self._entries.get(_text(transaction_id))
        return entry.transaction if entry is not None else None

    def has_transaction(self, transaction_id):
        return _text(transaction_id) in self._entries

    def __len__(self):
        return len(self._entries)

    def __iter__(self):
        return iter(self.transactions)

    def __getitem__(self, transaction_id):
        entry = self._entries[_text(transaction_id)]
        return entry.transaction

    def __repr__(self):
        return '<AccountLedger %s valid=%d pending=%d>' % (
            self.key, len(self.valid_transactions),
            len(self.pending_transactions))


def _has_cycle(component, outgoing_by_source, entries):
    visiting = set()
    visited = set()

    def visit(node):
        if node in visiting:
            return True
        if node in visited or node not in entries:
            return False
        visiting.add(node)
        for target, action in outgoing_by_source.get(node, ()):
            if target in component and visit(target):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in component if node in entries)


class CorrectionLedger(object):
    """Accumulate OFX statements and expose the currently valid transactions.

    Transactions are never discarded when a statement is added.  Corrections
    can therefore arrive before or after the statement that contains the
    original transaction, and duplicate statements are idempotent.
    """

    def __init__(self, sources=None, account=None, **overrides):
        self._accounts = AccountCollection()
        if sources is not None:
            self.add(sources, account=account, **overrides)

    def copy(self):
        ledger = CorrectionLedger()
        for key, account_ledger in self._accounts.items():
            target = AccountLedger(key)
            for entry in account_ledger._entries.values():
                for _ in range(max(1, entry.occurrences)):
                    target.add_transaction(entry.transaction)
            ledger._accounts[key] = target
        return ledger

    @property
    def accounts(self):
        return self._accounts

    @property
    def account_ledgers(self):
        return self._accounts

    @property
    def by_account(self):
        return self._accounts

    def for_account(self, account):
        key = AccountKey.from_account(account)
        ledger = self._accounts.get(key)
        if ledger is None:
            ledger = AccountLedger(key)
            self._accounts[key] = ledger
        return ledger

    def get_account(self, account):
        return self.for_account(account)

    ledger_for = get_account

    def add_transaction(self, transaction, account=None, **overrides):
        key = AccountKey.from_account(account, **overrides)
        ledger = self._accounts.get(key)
        if ledger is None:
            ledger = AccountLedger(key)
            self._accounts[key] = ledger
        ledger.add_transaction(transaction)
        return self

    def add_transactions(self, transactions, account=None, **overrides):
        key = AccountKey.from_account(account, **overrides)
        ledger = self._accounts.get(key)
        if ledger is None:
            ledger = AccountLedger(key)
            self._accounts[key] = ledger
        ledger.add_transactions(transactions)
        return self

    def add_statement(self, statement, account=None, **overrides):
        transactions = getattr(statement, 'transactions', statement)
        return self.add_transactions(transactions, account=account, **overrides)

    def add_ofx(self, ofx):
        accounts = getattr(ofx, 'accounts', None)
        if accounts is None:
            account = getattr(ofx, 'account', None)
            accounts = [account] if account is not None else []
        signon = getattr(ofx, 'signon', None)
        for account in accounts:
            overrides = {}
            institution = getattr(account, 'institution', None)
            organization = getattr(institution, 'organization', '')
            fid = getattr(institution, 'fid', '')
            if not organization:
                organization = getattr(account, 'organization', '')
            if not fid:
                fid = getattr(account, 'fid', '')
            if not organization and signon is not None:
                organization = getattr(signon, 'fi_org', '') or ''
            if not fid and signon is not None:
                fid = getattr(signon, 'fi_fid', '') or ''
            if organization:
                overrides['organization'] = organization
            if fid:
                overrides['fid'] = fid
            self.add_account(account, **overrides)
        return self

    def add_account(self, account, **overrides):
        statement = getattr(account, 'statement', None)
        if statement is None:
            return self
        return self.add_statement(statement, account=account, **overrides)

    def add_file(self, source):
        if hasattr(source, 'read'):
            return self.add_ofx(OfxParser.parse(source))
        if isinstance(source, bytes) and _is_ofx_content(source):
            return self.add_ofx(OfxParser.parse(six.BytesIO(source)))
        if isinstance(source, six.string_types) and _is_ofx_content(source):
            return self.add_ofx(OfxParser.parse(six.StringIO(source)))
        with open(source, 'rb') as file_handle:
            return self.add_ofx(OfxParser.parse(file_handle))

    def add_path(self, path):
        return self.add_file(path)

    load_file = add_file
    parse_file = add_file

    def ingest(self, source, account=None, **overrides):
        return self.add(source, account=account, **overrides)

    def add(self, source, account=None, **overrides):
        if isinstance(source, _PATH_TYPES):
            return self.add_file(source)
        if hasattr(source, 'read'):
            return self.add_file(source)
        if hasattr(source, 'accounts') or hasattr(source, 'account'):
            return self.add_ofx(source)
        if hasattr(source, 'statement') and hasattr(source, 'account_id'):
            return self.add_account(source)
        if hasattr(source, 'transactions') and not hasattr(source, 'id'):
            return self.add_statement(source, account=account, **overrides)
        if isinstance(source, dict) and any(name in source for name in TRANSACTION_ID_ATTRS):
            return self.add_transaction(source, account=account, **overrides)
        if hasattr(source, 'id') or hasattr(source, 'fitid'):
            return self.add_transaction(source, account=account, **overrides)
        try:
            iterator = iter(source)
        except TypeError:
            return self.add_transaction(source, account=account, **overrides)
        return self.add_transactions(iterator, account=account, **overrides)

    def add_files(self, sources):
        for source in sources:
            self.add_file(source)
        return self

    def feed(self, source, account=None, **overrides):
        return self.add(source, account=account, **overrides)

    def update(self, source, account=None, **overrides):
        return self.add(source, account=account, **overrides)

    def ingest_ofx(self, ofx):
        return self.add_ofx(ofx)

    def ingest_file(self, source):
        return self.add_file(source)

    def ingest_files(self, sources):
        return self.add_files(sources)

    def ingest_statement(self, statement, account=None, **overrides):
        return self.add_statement(statement, account=account, **overrides)

    def ingest_transactions(self, transactions, account=None, **overrides):
        return self.add_transactions(transactions, account=account,
                                     **overrides)

    def ingest_account(self, account, **overrides):
        return self.add_account(account, **overrides)

    def clear(self):
        self._accounts.clear()

    @property
    def valid_transactions(self):
        pairs = []
        for key, ledger in self._accounts.items():
            pairs.extend((key, transaction)
                         for transaction in ledger.valid_transactions)
        return TransactionList(transaction for key, transaction in sorted(
            pairs, key=lambda pair: _ledger_sort_key(pair[0], pair[1])))

    @property
    def current_transactions(self):
        return self.valid_transactions

    @property
    def active_transactions(self):
        return self.valid_transactions

    @property
    def transactions(self):
        return self.valid_transactions

    @property
    def pending_transactions(self):
        pairs = []
        for key, ledger in self._accounts.items():
            pairs.extend((key, transaction)
                         for transaction in ledger.pending_transactions)
        return TransactionList(transaction for key, transaction in sorted(
            pairs, key=lambda pair: _ledger_sort_key(pair[0], pair[1])))

    @property
    def unreconciled_transactions(self):
        return self.pending_transactions

    @property
    def all_transactions(self):
        pairs = []
        for key, ledger in self._accounts.items():
            pairs.extend((key, transaction)
                         for transaction in ledger.all_transactions)
        return TransactionList(transaction for key, transaction in sorted(
            pairs, key=lambda pair: _ledger_sort_key(pair[0], pair[1])))

    @property
    def chains(self):
        chains = []
        for ledger in self._accounts.values():
            chains.extend(ledger.chains)
        return CorrectionChainList(chains)

    @property
    def correction_chains(self):
        return self.chains

    @property
    def pending_chains(self):
        chains = []
        for ledger in self._accounts.values():
            chains.extend(ledger.pending_chains)
        return CorrectionChainList(chains)

    @property
    def unreconciled_chains(self):
        return self.pending_chains

    @property
    def pending_reconciliation(self):
        return self.pending_chains

    @property
    def balance_contribution(self):
        return sum((ledger.balance_contribution
                    for ledger in self._accounts.values()),
                   decimal.Decimal('0'))

    @property
    def balance(self):
        return self.balance_contribution

    @property
    def total(self):
        return self.balance_contribution

    def snapshot(self):
        return {
            'transactions': self.valid_transactions,
            'current_transactions': self.valid_transactions,
            'pending': self.pending_transactions,
            'pending_transactions': self.pending_transactions,
            'pending_chains': self.pending_chains,
            'balance': self.balance_contribution,
            'accounts': dict(self._accounts),
        }

    def __len__(self):
        return len(self.valid_transactions)

    def __iter__(self):
        return iter(self.valid_transactions)

    def __contains__(self, transaction):
        if isinstance(transaction, six.string_types):
            return any(_transaction_id(item) == transaction
                       for item in self.valid_transactions)
        for item in self.valid_transactions:
            if item is transaction:
                return True
        return False

    def __getitem__(self, account):
        if isinstance(account, AccountKey):
            return self._accounts[account]
        if isinstance(account, (tuple, list)):
            return self._accounts[AccountKey.from_tuple(*account)]
        account_id = _text(getattr(account, 'account_id', account))
        matches = [ledger for key, ledger in self._accounts.items()
                   if key.account_id == account_id]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(account)
        raise KeyError('account id is ambiguous; use AccountKey')

    def __repr__(self):
        return '<CorrectionLedger accounts=%d valid=%d pending=%d>' % (
            len(self._accounts), len(self.valid_transactions),
            len(self.pending_transactions))


def apply_corrections(sources, account=None, **overrides):
    """Return a :class:`CorrectionLedger` populated from one or more sources."""
    ledger = CorrectionLedger()
    if isinstance(sources, _PATH_TYPES) or hasattr(sources, 'read'):
        return ledger.add(sources)
    if hasattr(sources, 'accounts') or hasattr(sources, 'transactions'):
        return ledger.add(sources, account=account, **overrides)
    try:
        iterator = iter(sources)
    except TypeError:
        return ledger.add(sources, account=account, **overrides)
    for source in iterator:
        ledger.add(source, account=account, **overrides)
    return ledger


import_ledger = apply_corrections
load_ledger = apply_corrections
collect_ledger = apply_corrections
build_ledger = apply_corrections
merge_ofx = apply_corrections
merge_files = apply_corrections
merge_statements = apply_corrections
reconcile_ofx = apply_corrections
reconcile_corrections = apply_corrections
load_ofx = apply_corrections
import_ofx = apply_corrections
TransactionLedger = CorrectionLedger
OfxCorrectionLedger = CorrectionLedger
CorrectionTracker = CorrectionLedger
TransactionCorrectionLedger = CorrectionLedger
CorrectionResolver = CorrectionLedger
CorrectionLog = CorrectionLedger
TransactionReconciler = CorrectionLedger
