"""
Date-effective joins between DBF tables.

The common warehouse case is a transaction table keyed by (warehouse, item)
that needs the item description that was effective on the transaction date.
Master tables can contain several versions for the same key, including
versions for different warehouses, and may arrive in arbitrary order.

`temporal_join` keeps one result per transaction record and records on the
result both physical sources (table + record number).  Effective ranges are
half-open: ``[effective, expires)``.  Therefore if an old version expires on
2024-02-01 and a new one takes effect on 2024-02-01, a transaction dated
2024-02-01 matches only the new version.

Records marked as deleted are never considered valid versions, although the
raw master record used for diagnosis remains available as ``master``.
"""

import datetime
import collections

from . import dbf
from .bridge import baseinteger, basestring, unicode
from .exceptions import DbfError
from .utils import is_deleted, recno, source_table


DATE = ord('D')
DATETIME = ord('T')
TIMESTAMP = ord('@')


MATCHED = 'matched'
OVERLAP = 'overlap'
MASTER_DELETED = 'master-deleted'
MISSING = 'missing'
NO_VERSION = 'no-version'

STATUSES = (MATCHED, OVERLAP, MASTER_DELETED, MISSING, NO_VERSION)


class Provenance(tuple):
    """
    physical location of a source record

    The tuple is ``(table, record, table_name, record_number)`` and its
    attributes carry the same information.  ``record`` is the original DBF
    record object, not a copy, so field data and delete markers can still be
    inspected.
    """

    __slots__ = ()

    def __new__(cls, table, record):
        return tuple.__new__(cls, (
                table,
                record,
                table.filename,
                recno(record),
                ))

    def __getnewargs__(self):
        return self.table, self.record

    @property
    def table(self):
        return self[0]

    @property
    def record(self):
        return self[1]

    @property
    def table_name(self):
        return self[2]

    @property
    def record_number(self):
        return self[3]

    def __repr__(self):
        return 'Provenance(%r, record=%d)' % (self.table_name, self.record_number)


class TemporalJoinRecord(object):
    """
    one transaction joined to its effective master version

    ``transaction`` and ``master`` are the original DBF record objects (or
    None); ``transaction_source`` and ``master_source`` are Provenance tuples.
    Transaction fields, plus non-conflicting (or explicitly selected) master
    fields, are also exposed directly as attributes.
    """

    __slots__ = (
            'transaction', 'master', 'transaction_source', 'master_source',
            'master_sources', 'master_candidates',
            'status', 'join_date', 'effective', 'expires', '_field_values',
            )

    def __init__(self, transaction, master, join_date, effective=None,
                 expires=None, status=MATCHED, transaction_source=None,
                 master_source=None, master_sources=None,
                 master_candidates=None, field_values=None):
        object.__setattr__(self, 'transaction', transaction)
        object.__setattr__(self, 'master', master)
        object.__setattr__(self, 'join_date', join_date)
        object.__setattr__(self, 'effective', effective)
        object.__setattr__(self, 'expires', expires)
        object.__setattr__(self, 'status', status)
        object.__setattr__(self, 'transaction_source',
                transaction_source or Provenance(source_table(transaction), transaction))
        object.__setattr__(self, 'master_source',
                master_source if master is None else
                (master_source or Provenance(source_table(master), master)))
        resolved_master_source = object.__getattribute__(self, 'master_source')
        object.__setattr__(self, 'master_sources',
                tuple(master_sources) if master_sources is not None else
                (() if resolved_master_source is None else (resolved_master_source, ))
                )
        object.__setattr__(self, 'master_candidates',
                tuple(master_candidates) if master_candidates is not None else
                (() if master is None else (master, ))
                )
        object.__setattr__(self, '_field_values',
                field_values or collections.OrderedDict())

    def __contains__(self, name):
        return _field_key(name) in self._field_values

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        key = _field_key(name)
        values = object.__getattribute__(self, '_field_values')
        if key in values:
            return values[key]
        raise AttributeError(name)

    def __getitem__(self, name):
        if isinstance(name, baseinteger):
            return list(self._field_values.values())[name]
        return self._field_values[_field_key(name)]

    def __iter__(self):
        return iter(self._field_values.values())

    def __len__(self):
        return len(self._field_values)

    def __repr__(self):
        return '<TemporalJoinRecord %s transaction=%d master=%s>' % (
                self.status,
                self.transaction_source.record_number,
                'None' if self.master is None else self.master_source.record_number,
                )

    @property
    def records(self):
        "the (transaction, master) source record pair"
        return self.transaction, self.master

    @property
    def transaction_table(self):
        return self.transaction_source.table

    @property
    def transaction_record(self):
        return self.transaction_source.record

    @property
    def transaction_record_number(self):
        return self.transaction_source.record_number

    @property
    def master_table(self):
        if self.master_source is None:
            return None
        return self.master_source.table

    @property
    def master_record(self):
        return self.master

    @property
    def master_record_number(self):
        if self.master_source is None:
            return None
        return self.master_source.record_number

    @property
    def fields(self):
        return collections.OrderedDict(self._field_values)

    def as_dict(self):
        return dict(self._field_values)


def _field_key(name):
    if not isinstance(name, basestring):
        raise TypeError('%r is not a field name' % (name, ))
    return unicode(name).upper()


def _record_value(record, field):
    value = record[field]
    if isinstance(value, basestring):
        return unicode(value).rstrip()
    return value


def _source_key(record, fields):
    return tuple(_record_value(record, field) for field in fields)


def _inner_date(value):
    if isinstance(value, datetime.datetime):
        return value, 'datetime'
    if isinstance(value, datetime.date):
        return value, 'date'
    # dbf.Date / dbf.DateTime are not subclasses of datetime.date
    try:
        inner = value._date
    except AttributeError:
        inner = None
    if inner is not None:
        return inner, 'date'
    try:
        inner = value._datetime
    except AttributeError:
        inner = None
    if inner is not None:
        return inner, 'datetime'
    return None, None


def _normalize_date(value, what, allow_missing=False):
    if value is None or value is dbf.Null or value == '':
        if allow_missing:
            return None
        raise DbfError('%s has no date' % what)
    inner, kind = _inner_date(value)
    if inner is None:
        if allow_missing:
            return None
        raise DbfError('%s has no date' % what)
    if kind == 'datetime':
        if inner.time() != datetime.time(0, 0):
            raise DbfError('%s must not contain a time of day: %r' % (what, value))
        return inner.date()
    if kind == 'date':
        return inner
    if allow_missing:
        return None
    raise DbfError('%s must be a date, not %r' % (what, type(value)))


def _date_of(record, field, what, allow_missing=False):
    field = _field_key(field)
    if field not in source_table(record).field_names:
        raise DbfError('%r is not a field in %s' % (field, what))
    return _normalize_date(
            record[field], '%s.%s' % (what, field), allow_missing)


def _validate_fields(table, fields, what):
    names = table.field_names
    if isinstance(fields, basestring):
        fields = [fields]
    fields = [_field_key(field) for field in fields]
    for field in fields:
        if field not in names:
            raise DbfError('%r is not a field in %s' % (field, what))
    return tuple(fields)


def _validate_date_field(table, field, role):
    fields = _validate_fields(table, field, table.filename)
    if len(fields) != 1:
        raise DbfError('%s date must be a single field, not %r' % (role, fields))
    field = fields[0]
    info = table.field_info(field)
    if info.field_type not in (DATE, DATETIME, TIMESTAMP):
        raise DbfError('%s field %r is not a date field' % (role, field))
    return field


def _validate_table(table, role):
    if not isinstance(table, dbf.Table):
        raise TypeError('%s must be a dbf.Table, not %r' % (role, type(table)))
    if table.status == dbf.CLOSED:
        raise DbfError('%s table %s is closed' % (role, table.filename))


def _normalize_master_fields(master_fields, transactions, master):
    if master_fields is None:
        field_map = collections.OrderedDict()
        for field in master.field_names:
            if field not in transactions.field_names:
                field_map[field] = field
        return field_map
    if isinstance(master_fields, dict):
        items = master_fields.items()
    else:
        items = master_fields
    field_map = collections.OrderedDict()
    for item in items:
        if isinstance(item, basestring):
            source, target = _field_key(item), _field_key(item)
        else:
            source, target = item
            source, target = _field_key(source), _field_key(target)
        if source not in master.field_names:
            raise DbfError('%r is not a field in %s' % (source, master.filename))
        if target in transactions.field_names:
            raise DbfError(
                    'master field %r collides with transaction field %r; '
                    'provide a result name' % (source, target))
        field_map[source] = target
    return field_map


class _VersionIndex(object):
    """
    lazy index of master records grouped by join key
    """

    def __init__(self, table, transaction_key_fields, master_key_fields,
                 effective_field, expires_field, ignore_bad_rows=False):
        self.table = table
        self.transaction_key_fields = transaction_key_fields
        self.master_key_fields = master_key_fields
        self.effective_field = effective_field
        self.expires_field = expires_field
        self.ignore_bad_rows = ignore_bad_rows
        self._by_key = None
        self._parsed = {}

    def _build(self):
        by_key = {}
        for record in self.table:
            key = _source_key(record, self.master_key_fields)
            by_key.setdefault(key, []).append(record)
        self._by_key = by_key
        return by_key

    def records_for(self, transaction):
        by_key = self._by_key
        if by_key is None:
            by_key = self._build()
        return by_key.get(_source_key(transaction, self.transaction_key_fields), ())

    def versions_for(self, transaction):
        key = _source_key(transaction, self.transaction_key_fields)
        parsed = self._parsed.get(key)
        if parsed is not None:
            return parsed
        versions = []
        deleted = []
        for record in self.records_for(transaction):
            where = '%s record %d' % (self.table.filename, recno(record))
            try:
                effective = _date_of(
                        record, self.effective_field, self.table.filename)
                expires = _date_of(
                        record, self.expires_field, self.table.filename,
                        allow_missing=True)
            except DbfError:
                if self.ignore_bad_rows:
                    continue
                raise
            if effective is None:
                if self.ignore_bad_rows:
                    continue
                raise DbfError('%s has no %s date' % (where, self.effective_field))
            if expires is not None and expires <= effective:
                if self.ignore_bad_rows:
                    continue
                raise DbfError('%s has an empty or reversed effective range' % where)
            version = (effective, expires, record)
            if is_deleted(record):
                deleted.append(version)
            else:
                versions.append(version)
        parsed = (versions, deleted)
        self._parsed[key] = parsed
        return parsed


def _covers(version, moment):
    effective, expires, record = version
    return effective <= moment and (expires is None or moment < expires)


def _candidates(versions, moment):
    return [version for version in versions if _covers(version, moment)]


def _transaction_fields(transaction):
    return collections.OrderedDict(
            (field, _record_value(transaction, field))
            for field in source_table(transaction).field_names
            )


def _result_fields(transaction, master, master_field_map):
    values = _transaction_fields(transaction)
    for source, target in master_field_map.items():
        values[target] = None if master is None else _record_value(master, source)
    return values


def _resolve(transaction, versions, deleted, moment, master_field_map):
    candidates = _candidates(versions, moment)
    if len(candidates) == 1:
        effective, expires, master = candidates[0]
        return TemporalJoinRecord(
                transaction, master, moment, effective, expires, MATCHED,
                field_values=_result_fields(
                    transaction, master, master_field_map),
                )
    elif len(candidates) > 1:
        candidates = sorted(
                candidates,
                key=lambda version: (version[0], recno(version[2])),
                )
        effective = candidates[0][0]
        return TemporalJoinRecord(
                transaction, None, moment, effective, None, OVERLAP,
                master_candidates=[version[2] for version in candidates],
                master_sources=[
                    Provenance(source_table(version[2]), version[2])
                    for version in candidates
                    ],
                field_values=_result_fields(
                    transaction, None, master_field_map),
                )
    covering_deleted = [version for version in deleted if _covers(version, moment)]
    if covering_deleted:
        effective, expires, master = min(
                covering_deleted, key=lambda version: (version[0], recno(version[2])))
        return TemporalJoinRecord(
                transaction, master, moment, effective, expires, MASTER_DELETED,
                field_values=_result_fields(
                    transaction, None, master_field_map),
                )
    status = MISSING if not versions and not deleted else NO_VERSION
    return TemporalJoinRecord(
            transaction, None, moment, status=status,
            field_values=_result_fields(
                transaction, None, master_field_map),
            )


def temporal_join(transactions, master, transaction_key, master_key,
                  transaction_date, effective, expires, master_fields=None,
                  validate='report'):
    """
    Join each transaction to the one master version effective on its date.

    ``transaction_key`` / ``master_key`` are field names or sequences of
    field names; ``transaction_date`` is the transaction's business date;
    ``effective`` / ``expires`` define the half-open master range
    ``[effective, expires)``.  A blank expires date means "open ended".

    The result has exactly one TemporalJoinRecord per transaction.  Status is
    one of MATCHED, OVERLAP, MASTER_DELETED, MISSING, or NO_VERSION.  Every
    result exposes ``transaction``, ``master``, ``transaction_source`` and
    ``master_source``.  Transaction fields are available directly, and all
    non-conflicting master fields are added automatically.  A transaction
    field always wins over a same-named master field; use ``master_fields``
    to select a different result name when the master value is needed.

    ``master_fields`` can select/rename master fields.  Use a sequence of
    names, or ``{'master_field': 'result_name'}`` pairs.  An explicitly named
    result that collides with a transaction field is an error.

    ``validate`` is ``'report'`` (default), which reports overlapping ranges
    as OVERLAP; ``'error'`` raises on an overlap; ``'ignore'`` additionally
    treats malformed master ranges and missing transaction dates as
    NO_VERSION rather than raising.
    """

    valid_modes = ('report', 'error', 'ignore')
    if validate not in valid_modes:
        raise ValueError('validate must be one of %r' % (valid_modes, ))
    _validate_table(transactions, 'transaction')
    _validate_table(master, 'master')

    transaction_key = _validate_fields(
            transactions, transaction_key, transactions.filename)
    master_key = _validate_fields(master, master_key, master.filename)
    if len(transaction_key) != len(master_key):
        raise DbfError('transaction and master keys must have the same number of fields')
    transaction_date = _validate_date_field(
            transactions, transaction_date, 'transaction')
    effective = _validate_date_field(master, effective, 'master')
    expires = _validate_date_field(master, expires, 'master')

    master_field_map = _normalize_master_fields(
            master_fields, transactions, master)

    version_index = _VersionIndex(
            master, transaction_key, master_key, effective, expires,
            ignore_bad_rows=(validate == 'ignore'))
    results = []

    for transaction in transactions:
        moment = _date_of(
                transaction, transaction_date, transactions.filename,
                allow_missing=(validate == 'ignore'))
        if moment is None:
            results.append(TemporalJoinRecord(
                    transaction, None, None, status=NO_VERSION,
                    field_values=_result_fields(
                        transaction, None, master_field_map),
                    ))
            continue
        versions, deleted = version_index.versions_for(transaction)
        result = _resolve(
                transaction, versions, deleted, moment, master_field_map)
        if result.status == OVERLAP and validate == 'error':
            key = _source_key(transaction, transaction_key)
            overlaps = sorted(
                    _candidates(versions, moment),
                    key=lambda version: (version[0], recno(version[2]),
                            version[2]._recnum),
                    )
            rows = ', '.join(
                    '%s record %d' % (master.filename, recno(version[2]))
                    for version in overlaps
                    )
            raise DbfError(
                    'overlapping effective master versions for key %r on %s: %s' % (
                        key, moment, rows))
        results.append(result)
    return results


def describe_results(results):
    """
    return a dictionary of status counts for diagnostics
    """
    counts = dict((status, 0) for status in STATUSES)
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def join_by_date(*args, **kwargs):
    """
    alias for temporal_join
    """
    return temporal_join(*args, **kwargs)


def effective_join(*args, **kwargs):
    """
    alias for temporal_join
    """
    return temporal_join(*args, **kwargs)


def versioned_join(*args, **kwargs):
    """
    alias for temporal_join
    """
    return temporal_join(*args, **kwargs)
