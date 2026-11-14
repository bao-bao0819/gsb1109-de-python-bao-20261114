# -*- coding: utf-8 -*-
"""
Sparse record corrections for DBF tables.

Correction sheets are sparse: they only carry the fields that are being
changed.  An empty cell is ambiguous, so each import declares what empty
means for every column:

* ``KEEP``  -- the cell was not supplied; leave the stored value alone
* ``CLEAR`` -- the cell means "empty this field"
* ``SET``   -- the cell itself is the value (so an explicit empty string is
               stored as an empty string)

The choice is resolved per cell, in this order:

1. an explicit :class:`TriState` cell value
2. a row-level override (``CorrectionRow(blank=...)`` or ``__blank__``)
3. a per-column policy (``column_blanks={...}``)
4. the batch default (``blank=...``)

Rows that share a composite key are merged.  If they write incompatible
values to the same field, only those fields are isolated as conflicts;
all compatible fields from the same rows still form one reviewable
:class:`CorrectionChange`.

Real zero values, logical false, and explicit memo clearing are all
ordinary corrections and are always sent to the target table.
"""

from __future__ import print_function

import codecs
import csv
import datetime
import sys
from collections import OrderedDict

try:
    from collections.abc import Mapping
except ImportError:
    from collections import Mapping

from aenum import Enum

from . import dbf
from .bridge import Decimal, basestring, bytes, unicode
from .constants import (
        BINARY, DECIMALS, FLAGS, NULLABLE, TYPE,
        CHAR, CURRENCY, DATE, DATETIME, DOUBLE, FLOAT, GENERAL, INTEGER,
        LOGICAL, MEMO, NUMERIC, PICTURE, TIMESTAMP,
        )
from .data_types import Null
from .exceptions import DbfError
from .utils import ensure_unicode, is_deleted, recno


class TriState(Enum):
    """
    Meaning of a sparse correction cell.

    ``KEEP`` leaves the target untouched, ``SET`` writes the cell value
    (including ``0``, ``False``, and an explicit empty string), and
    ``CLEAR`` empties the target field.
    """
    KEEP = 'keep'
    SET = 'set'
    CLEAR = 'clear'


BlankPolicy = TriState
CellState = TriState
KEEP = TriState.KEEP
SET = TriState.SET
CLEAR = TriState.CLEAR
BLANK_KEEP = KEEP
BLANK_SET = SET
BLANK_CLEAR = CLEAR


class CorrectionError(DbfError):
    """Base error for sparse correction imports."""

    def __init__(self, message, *args):
        DbfError.__init__(self, message, *args)


class CorrectionInputError(CorrectionError):
    """A correction row is malformed or unusable."""


class CorrectionConflictError(CorrectionError):
    """Raised by ``strict=True`` when a field receives conflicting values."""

    def __init__(self, conflicts):
        self.conflicts = list(conflicts)
        CorrectionError.__init__(
                self,
                'conflicting values for %d field%s'
                % (len(self.conflicts), ('', 's')[len(self.conflicts) != 1]),
                )


_POLICY_ALIASES = {
        'keep': KEEP,
        'leave': KEEP,
        'ignore': KEEP,
        'preserve': KEEP,
        'unset': KEEP,
        'absent': KEEP,
        'skip': KEEP,
        'not-set': KEEP,
        'not set': KEEP,
        u'未填': KEEP,
        u'保留': KEEP,
        'set': SET,
        'literal': SET,
        'value': SET,
        'explicit': SET,
        u'写入': SET,
        'clear': CLEAR,
        'blank': CLEAR,
        'empty': CLEAR,
        'null': CLEAR,
        'wipe': CLEAR,
        'erase': CLEAR,
        'delete': CLEAR,
        u'清空': CLEAR,
        u'清除': CLEAR,
        u'空格': CLEAR,
        }


def _policy(value, default=None):
    if value is None:
        return default
    if isinstance(value, TriState):
        return value
    if value is True:
        return CLEAR
    if value is False:
        return KEEP
    text = unicode(value).strip().lower()
    try:
        return _POLICY_ALIASES[text]
    except KeyError:
        raise CorrectionInputError('unknown blank policy: %r' % (value, ))


def _policy_map(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CorrectionInputError('column blank policies must be a mapping')
    result = {}
    for column, policy in value.items():
        if column is None or column == '*':
            continue
        result[ensure_unicode(column).upper()] = _policy(policy)
    return result


def _mapping_default(value):
    if not isinstance(value, Mapping):
        return None
    if '*' in value:
        return _policy(value['*'])
    if None in value:
        return _policy(value[None])
    return None


class CorrectionRow(object):
    """
    A sparse row plus row-level blank-policy overrides.

    ``blank`` is either one policy for the whole row or a mapping of
    column names to policies.  ``column_blanks`` is an additional mapping.
    Columns absent from ``values`` are always left untouched.
    """

    def __init__(self, values=None, blank=None, column_blanks=None):
        if values is None:
            values = {}
        if not isinstance(values, Mapping):
            values = dict(values)
        self.values = dict(values)
        self.blank = blank
        self.column_blanks = dict(column_blanks or {})

    def __getitem__(self, column):
        return self.values[column]

    def get(self, column, default=None):
        return self.values.get(column, default)

    def __contains__(self, column):
        return column in self.values

    def keys(self):
        return self.values.keys()

    def items(self):
        return self.values.items()

    def __repr__(self):
        return 'CorrectionRow(%r, blank=%r)' % (self.values, self.blank)


class CorrectionConflict(object):
    """Incompatible values supplied for one field of one composite key."""

    def __init__(self, key, field, candidates):
        self.key = key
        self.field = field
        self.candidates = candidates
        self.values = [candidate[1] for candidate in candidates]
        self.sources = [candidate[0] for candidate in candidates]

    def __repr__(self):
        return 'CorrectionConflict(key=%r, field=%r, values=%r)' % (
                self.key, self.field, self.values,
                )


class CorrectionChange(object):
    """The merged, reviewable correction for one composite key."""

    def __init__(self, key, recno, record, values, before, conflicts, sources):
        self.key = key
        self.recno = recno
        self.record = record
        self.values = values
        self.before = before
        self.after = dict(before)
        self.conflicts = conflicts
        self.sources = sources
        self.applied = False
        self.error = None

    @property
    def fields(self):
        return list(self.values)

    @property
    def changed_fields(self):
        return [field for field in self.values
                if not _same_value(self.before.get(field), self.after.get(field))]

    @property
    def isolated(self):
        return self.conflicts

    @property
    def ok(self):
        return self.error is None and not self.conflicts

    def as_dict(self):
        return {
                'key': self.key,
                'recno': self.recno,
                'values': dict(self.values),
                'before': dict(self.before),
                'after': dict(self.after),
                'conflicts': dict(
                    (field, conflict.values)
                    for field, conflict in self.conflicts.items()),
                'sources': list(self.sources),
                'applied': self.applied,
                'error': self.error,
                }

    def __repr__(self):
        return 'CorrectionChange(key=%r, fields=%r, applied=%r)' % (
                self.key, self.fields, self.applied,
                )


class CorrectionReport(object):
    """Result of applying (or planning) a sparse correction batch."""

    def __init__(self):
        self.changes = []
        self.conflicts = []
        self.unmatched = []
        self.ambiguous = []
        self.errors = []
        self.dry_run = False

    @property
    def applied(self):
        return [change for change in self.changes if change.applied]

    @property
    def applied_count(self):
        return len(self.applied)

    @property
    def skipped(self):
        return [change for change in self.changes if not change.applied]

    @property
    def skipped_count(self):
        return len(self.skipped)

    @property
    def conflict_fields(self):
        return [(conflict.key, conflict.field) for conflict in self.conflicts]

    @property
    def ok(self):
        return (not self.conflicts and not self.errors and not self.ambiguous
                and not self.unmatched)

    def require_clean(self):
        if self.conflicts:
            raise CorrectionConflictError(self.conflicts)
        if self.errors:
            raise CorrectionError(self.errors[0])
        if self.unmatched:
            raise CorrectionInputError(
                    'no record found for key%s %s'
                    % (('', 's')[len(self.unmatched) != 1],
                       ', '.join(repr(key) for key in self.unmatched))
                    )
        if self.ambiguous:
            raise CorrectionInputError(
                    'key%s %s matched more than one record'
                    % (('', 's')[len(self.ambiguous) != 1],
                       ', '.join(repr(key) for key in self.ambiguous))
                    )
        return self

    def as_dict(self):
        return {
                'changes': [change.as_dict() for change in self.changes],
                'conflicts': [
                    {'key': conflict.key,
                     'field': conflict.field,
                     'values': conflict.values,
                     'sources': conflict.sources}
                    for conflict in self.conflicts
                    ],
                'unmatched': list(self.unmatched),
                'ambiguous': list(self.ambiguous),
                'errors': list(self.errors),
                'dry_run': self.dry_run,
                }

    def __iter__(self):
        return iter(self.changes)

    def __len__(self):
        return len(self.changes)

    def __bool__(self):
        return bool(self.changes)

    __nonzero__ = __bool__

    def __repr__(self):
        return ('CorrectionReport(changes=%d, applied=%d, conflicts=%d, '
                'unmatched=%d, ambiguous=%d, errors=%d)'
                % (len(self.changes), self.applied_count, len(self.conflicts),
                   len(self.unmatched), len(self.ambiguous), len(self.errors)))


Correction = CorrectionChange
CorrectionPlan = CorrectionReport


_META_KEYS = frozenset((
        '__blank__', '_blank_', '__blank_policy__', '__row_blank__',
        '__clear__', '__keep__',
        ))


def _is_mapping(value):
    return isinstance(value, Mapping)


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, basestring):
        return not value.strip()
    if isinstance(value, bytes):
        return not value.strip()
    return False


def _same_value(left, right):
    if left is right:
        return True
    if left is Null or right is Null:
        return False
    try:
        return left == right
    except Exception:
        return False


def _coerce_key_value(table, field, value):
    if _is_blank(value) or value is None:
        raise CorrectionInputError(
                "key field %r has an empty value" % (field, )
                )
    value = _coerce_value(table, field, value)
    if isinstance(value, basestring):
        return value.strip()
    if isinstance(value, bytes):
        return value.strip()
    return value


def _empty_value(table, field):
    fielddef = table._meta[field]
    field_type = fielddef[TYPE]
    flags = fielddef[FLAGS]
    binary = bool(flags & BINARY)
    if field_type in (CHAR, MEMO, GENERAL, PICTURE):
        return b'' if binary else ''
    return None


def _null_value(table, field):
    if table._meta[field][FLAGS] & NULLABLE:
        return Null
    return _empty_value(table, field)


def _coerce_value(table, field, value):
    fielddef = table._meta[field]
    field_type = fielddef[TYPE]
    flags = fielddef[FLAGS]
    binary = bool(flags & BINARY)
    if value is Null:
        return _null_value(table, field)
    if value is None:
        return _empty_value(table, field)
    if field_type in (CHAR, MEMO, GENERAL, PICTURE):
        if binary:
            if isinstance(value, bytes):
                return value
            raise CorrectionInputError(
                    "field %r requires a bytes value, not %r"
                    % (field, type(value))
                    )
        if not isinstance(value, basestring):
            value = unicode(value)
        return value
    if field_type in (NUMERIC, FLOAT, DOUBLE):
        text = unicode(value).strip()
        if not text:
            return None
        number = float(text)
        if field_type in (NUMERIC, FLOAT) and fielddef[DECIMALS] == 0:
            return int(number)
        return number
    if field_type == INTEGER:
        return int(value)
    if field_type == CURRENCY:
        return Decimal(unicode(value).strip())
    if field_type == LOGICAL:
        if isinstance(value, bool):
            return value
        if isinstance(value, dbf.Logical):
            return value
        text = unicode(value).strip().lower()
        if text in ('1', 't', 'true', 'y', 'yes', 'on', u'是'):
            return True
        if text in ('0', 'f', 'false', 'n', 'no', 'off', u'否'):
            return False
        if text in ('?', 'unknown', 'null', 'none'):
            return None
        raise CorrectionInputError(
                "unable to interpret %r as a logical value for %r"
                % (value, field)
                )
    if field_type == DATE:
        if isinstance(value, dbf.Date):
            return value
        if isinstance(value, datetime.datetime):
            return dbf.Date(value.date())
        if isinstance(value, datetime.date):
            return dbf.Date(value)
        text = unicode(value).strip()
        if not text:
            return None
        if len(text) == 8 and text.isdigit():
            return dbf.Date.fromymd(text)
        for format in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'):
            try:
                return dbf.Date.strptime(text, format)
            except ValueError:
                pass
        raise CorrectionInputError(
                "unable to interpret %r as a date for %r" % (value, field)
                )
    if field_type == DATETIME or field_type == TIMESTAMP:
        if isinstance(value, dbf.DateTime):
            return value
        if isinstance(value, datetime.datetime):
            return dbf.DateTime(value)
        text = unicode(value).strip()
        if not text:
            return None
        if len(text) == 8 and text.isdigit():
            return dbf.DateTime(dbf.Date.fromymd(text))
        for format in (None, '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                       '%Y-%m-%dT%H:%M:%S'):
            try:
                if format is None:
                    return dbf.DateTime.strptime(text)
                return dbf.DateTime.strptime(text, format)
            except ValueError:
                pass
        raise CorrectionInputError(
                "unable to interpret %r as a datetime for %r" % (value, field)
                )
    return value


def _token(table, field, action, value):
    if action is KEEP:
        return None
    if action is CLEAR:
        return ('clear', )
    field_type = table._meta[field][TYPE]
    value = _coerce_value(table, field, value)
    if value is Null:
        return ('null', )
    if field_type == LOGICAL:
        if value is dbf.Unknown or value is None:
            return ('clear', )
        if isinstance(value, dbf.Logical):
            value = bool(value)
        return ('bool', bool(value))
    if field_type == CURRENCY:
        cents = int(round(Decimal(unicode(value)) * 10000))
        return ('currency', cents)
    if field_type == CHAR and not isinstance(value, bytes):
        value = value.strip()
    if field_type in (CHAR, MEMO, GENERAL, PICTURE) and _is_blank(value):
        return ('clear', )
    if isinstance(value, bool):
        return ('bool', value)
    if isinstance(value, (int, float)) and field_type in (
            NUMERIC, FLOAT, DOUBLE, INTEGER):
        return ('number', repr(float(value)))
    if isinstance(value, float):
        return ('float', repr(value))
    if isinstance(value, dbf.DateTime):
        return ('datetime', value.isoformat())
    if isinstance(value, dbf.Date):
        return ('date', value.isoformat())
    if isinstance(value, datetime.datetime):
        return ('datetime', value.isoformat())
    if isinstance(value, datetime.date):
        return ('date', value.isoformat())
    if isinstance(value, bytes):
        return ('bytes', value)
    if isinstance(value, Decimal):
        return ('decimal', str(value))
    return ('set', value)


class _CaseInsensitiveMapping(Mapping):
    """Read-only view over normalized row values for callable key functions."""

    def __init__(self, values):
        self._values = values

    def __getitem__(self, key):
        return self._values[ensure_unicode(key).upper()]

    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError(key)
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class _ParsedRow(object):
    def __init__(self, index, values, blank, column_blanks):
        self.index = index
        self.values = values
        self.blank = blank
        self.column_blanks = column_blanks

    def policy_for(self, column):
        if column in self.column_blanks:
            return self.column_blanks[column]
        if self.blank is None:
            return None
        if _is_mapping(self.blank):
            policies = _policy_map(self.blank)
            if column in policies:
                return policies[column]
            return _mapping_default(self.blank)
        return _policy(self.blank)


def _consume_meta(mapping, values, index):
    row_blank = None
    explicit = {}
    for key, value in mapping.items():
        key = ensure_unicode(key)
        if key not in _META_KEYS:
            values[key.upper()] = value
            continue
        if key in ('__blank__', '_blank_', '__blank_policy__', '__row_blank__'):
            if row_blank is not None:
                raise CorrectionInputError(
                        'row %d supplies more than one blank policy' % (index + 1)
                        )
            row_blank = value
        else:
            names = value
            if isinstance(names, basestring):
                names = [name.strip() for name in names.split(',')]
            policy = CLEAR if key == '__clear__' else KEEP
            for name in names:
                explicit[ensure_unicode(name).upper()] = policy
    values.update(explicit)
    return row_blank


def _make_row(row, columns, index=0):
    row_blank = None
    row_columns = {}
    if isinstance(row, CorrectionRow):
        values = OrderedDict(
                (ensure_unicode(k).upper(), v) for k, v in row.values.items())
        row_blank = row.blank
        row_columns = _policy_map(row.column_blanks)
    elif isinstance(row, Mapping):
        values = OrderedDict()
        row_blank = _consume_meta(row, values, index)
        row_columns = {}
    elif isinstance(row, tuple) and len(row) == 2 and _is_mapping(row[0]):
        values = OrderedDict()
        row_blank = _consume_meta(row[0], values, index)
        row_columns = {}
        if row[1] is not None:
            if row_blank is not None:
                raise CorrectionInputError(
                        'row %d supplies more than one blank policy' % (index + 1)
                        )
            row_blank = row[1]
    elif columns is not None:
        values = OrderedDict(
                (ensure_unicode(k).upper(), v) for k, v in zip(columns, row))
    else:
        raise CorrectionInputError(
                'correction rows must be mappings, CorrectionRow instances, '
                'or tuples with explicit column names'
                )
    if _is_mapping(row_blank):
        row_columns.update(_policy_map(row_blank))
        default = _mapping_default(row_blank)
        row_blank = default
    elif row_blank is not None:
        row_blank = _policy(row_blank)
    return _ParsedRow(index, values, row_blank, row_columns)


def _csv_kwds(dialect, delimiter):
    kwds = {'dialect': dialect}
    if delimiter is not None:
        kwds['delimiter'] = delimiter
    return kwds


def _read_csv(rows, encoding=None, has_header=True, dialect='excel',
              delimiter=None):
    if encoding is None:
        encoding = 'utf-8-sig'
    kwds = _csv_kwds(dialect, delimiter)
    with codecs.open(rows, 'r', encoding=encoding) as fd:
        if has_header:
            reader = csv.DictReader(fd, **kwds)
            return [dict((key, value) for key, value in row.items()
                         if key is not None)
                    for row in reader]
        return [row for row in csv.reader(fd, **kwds)]


def _load_rows(rows, columns=None, encoding=None, has_header=True,
               dialect='excel', delimiter=None):
    kwds = _csv_kwds(dialect, delimiter)
    if isinstance(rows, basestring):
        rows = _read_csv(rows, encoding=encoding, has_header=has_header,
                         dialect=dialect, delimiter=delimiter)
    elif hasattr(rows, 'read'):
        if has_header:
            rows = list(csv.DictReader(rows, **kwds))
        else:
            rows = list(csv.reader(rows, **kwds))
    if isinstance(rows, Mapping):
        if all(_is_mapping(value) for value in rows.values()):
            rows = [value for value in rows.values()]
        else:
            rows = [rows]
    return list(rows)


def _normalise_key_fields(key_fields, keys):
    if key_fields is None:
        key_fields = keys
    if key_fields is None:
        raise CorrectionInputError('key_fields is required')
    if callable(key_fields):
        return key_fields
    if isinstance(key_fields, basestring):
        key_fields = [field for field in key_fields.replace(',', ' ').split()
                      if field]
    else:
        key_fields = list(key_fields)
    if not key_fields:
        raise CorrectionInputError('at least one key field is required')
    return [ensure_unicode(field).upper() for field in key_fields]


def _key_for_parsed_row(row, key_fields, table):
    if callable(key_fields):
        key = key_fields(_CaseInsensitiveMapping(row.values))
    else:
        missing = [field for field in key_fields if field not in row.values]
        if missing:
            raise CorrectionInputError(
                    'correction row %d is missing key field%s %s'
                    % (row.index + 1, ('', 's')[len(missing) != 1],
                       ', '.join(missing))
                    )
        key = tuple(_coerce_key_value(table, field, row.values[field])
                    for field in key_fields)
    if not isinstance(key, tuple):
        key = (key, )
    return _normalise_key(key)


def _key_for_record(record, key_fields, table):
    if callable(key_fields):
        key = key_fields(record)
    else:
        key = tuple(_coerce_key_value(table, field, record[field])
                    for field in key_fields)
    if not isinstance(key, tuple):
        key = (key, )
    return _normalise_key(key)


def _normalise_key(key):
    if isinstance(key, tuple):
        return tuple(_normalise_key(item) for item in key)
    if isinstance(key, basestring):
        return key.strip()
    if isinstance(key, bytes):
        return key.strip()
    return key


def _resolve_cell(table, field, raw, row, default_policy, column_policies):
    if isinstance(raw, TriState):
        if raw is KEEP:
            return KEEP, None
        if raw is CLEAR:
            return CLEAR, None
        return SET, _empty_value(table, field)
    if raw is None or _is_blank(raw):
        policy = row.policy_for(field)
        if policy is None:
            policy = column_policies.get(field, default_policy)
        if policy is KEEP:
            return KEEP, None
        if policy is SET and _is_text_field(table, field):
            if raw is None:
                return CLEAR, None
            return SET, raw
        return CLEAR, None
    return SET, raw


def _is_text_field(table, field):
    return table._meta[field][TYPE] in (CHAR, MEMO, GENERAL, PICTURE)


def _validate_fields(table, rows, key_fields, ignore_unknown_fields):
    table_fields = set(table.field_names)
    if not callable(key_fields):
        missing = [field for field in key_fields if field not in table_fields]
        if missing:
            raise CorrectionInputError(
                    'key field%s %s not in %s'
                    % (('', 's')[len(missing) != 1], ', '.join(missing),
                       table.filename)
                    )
    key_names = set(key_fields) if not callable(key_fields) else set()
    for row in rows:
        for field in row.values:
            if field in key_names:
                continue
            if field not in table_fields and not ignore_unknown_fields:
                raise CorrectionInputError(
                        '%r is not a field in %s' % (field, table.filename)
                        )


def _merge_rows(table, key, rows, key_fields, default_policy, column_policies):
    values = OrderedDict()
    candidates_by_field = OrderedDict()
    sources = []
    key_names = set(key_fields) if not callable(key_fields) else set()
    for row in rows:
        sources.append(row.index)
        for field in table.field_names:
            if field in key_names or field not in row.values:
                continue
            action, value = _resolve_cell(
                    table, field, row.values[field], row,
                    default_policy, column_policies,
                    )
            if action is KEEP:
                continue
            try:
                token = _token(table, field, action, value)
            except (ValueError, TypeError) as exc:
                raise CorrectionInputError(
                        'correction row %d, field %r: %s'
                        % (row.index + 1, field, exc)
                        )
            if token is None:
                continue
            candidates = candidates_by_field.setdefault(field, [])
            for candidate in candidates:
                if candidate[2] == token:
                    break
            else:
                candidates.append((row.index, value, token))
    conflicts = OrderedDict()
    for field, candidates in candidates_by_field.items():
        if len(candidates) == 1:
            values[field] = _coerce_value(table, field, candidates[0][1])
        else:
            conflicts[field] = CorrectionConflict(
                    key, field,
                    [(candidate[0], candidate[1]) for candidate in candidates],
                    )
    return values, conflicts, sources


def _target_index(table, key_fields, include_deleted):
    index = OrderedDict()
    for record in table:
        if not include_deleted and is_deleted(record):
            continue
        try:
            key = _key_for_record(record, key_fields, table)
        except CorrectionInputError:
            continue
        index.setdefault(key, []).append(record)
    return index


def _apply_change(change):
    before = {}
    for field in change.values:
        before[field] = change.record[field]
    change.before = before
    try:
        dbf.write(change.record, **dict(change.values))
    except Exception:
        change.error = sys.exc_info()[1]
        change.after = dict(before)
        return False
    change.after = dict(
            (field, change.record[field]) for field in change.values)
    change.applied = True
    return True


def _preview_change(change):
    before = {}
    after = {}
    for field, value in change.values.items():
        try:
            before[field] = change.record[field]
            after[field] = _coerce_value(change.record._meta.table(), field, value)
        except Exception:
            change.error = sys.exc_info()[1]
            return False
    change.before = before
    change.after = after
    return True


def _blank_configuration(blank, column_blanks, default_blank):
    default_policy = _policy(default_blank, None)
    blank_map = None
    if _is_mapping(blank):
        blank_map = blank
        if default_policy is None:
            default_policy = _mapping_default(blank) or KEEP
    elif blank is not None and default_policy is None:
        default_policy = _policy(blank)
    if default_policy is None:
        default_policy = KEEP

    policies = {}
    for source in (blank_map, column_blanks):
        if source is None:
            continue
        if not _is_mapping(source):
            raise CorrectionInputError('column_blanks must be a mapping')
        for column, policy in source.items():
            if column is None or column == '*':
                continue
            policies[ensure_unicode(column).upper()] = _policy(policy)
    if column_blanks is not None:
        default = _mapping_default(column_blanks)
        if default is not None:
            default_policy = default
    return default_policy, policies


def apply_corrections(table, rows=None, key_fields=None, blank=None,
                      column_blanks=None, default_blank=None, dry_run=False,
                      strict=False, include_deleted=False,
                      ignore_unknown_fields=False, encoding=None,
                      field_names=None, columns=None, has_header=True,
                      keys=None, key=None, blank_policy=None,
                      blank_columns=None, empty_means=None,
                      filename=None, dialect='excel', delimiter=None,
                      **kwargs):
    """
    Apply sparse correction rows to the records of ``table``.

    ``rows`` may be an iterable of mappings, :class:`CorrectionRow`
    objects, tuples together with ``columns``/``field_names``, a CSV file
    name, or an open file-like object.  ``key_fields`` names the composite
    key used to locate target records.

    ``blank`` is the batch default for cells that are present but empty,
    and may also be a mapping of column names to policies.
    ``column_blanks`` sets per-column defaults.  Use ``KEEP`` to mean "not
    supplied" and ``CLEAR`` to mean "empty the field".  ``SET`` stores the
    blank itself.

    A row can override the batch convention with
    ``CorrectionRow(blank=...)``, a ``__blank__`` entry, or ``__clear__`` /
    ``__keep__`` lists of columns.

    Real zero and false values are ordinary values.  Memo fields can be
    cleared with ``CLEAR`` (or an explicit empty value).  By default,
    conflicting values are isolated per field while compatible fields are
    still applied; pass ``strict=True`` to raise
    :class:`CorrectionConflictError` instead.
    """
    if table is None:
        if filename is None:
            raise CorrectionInputError('a table or filename is required')
        table = filename
    if blank is None:
        blank = blank_policy
    if blank is None:
        blank = empty_means
    if column_blanks is None:
        column_blanks = blank_columns
    if rows is None:
        raise CorrectionInputError('rows are required')
    if key_fields is None:
        key_fields = key
    if key_fields is None:
        key_fields = keys
    key_fields = _normalise_key_fields(key_fields, keys)
    default_policy, column_policies = _blank_configuration(
            blank, column_blanks, default_blank)
    if columns is None:
        columns = field_names
    if columns is not None:
        columns = [ensure_unicode(column).upper() for column in columns]

    opened = False
    previous_status = None
    if isinstance(table, basestring):
        table = dbf.Table(table)
    if table.status == dbf.CLOSED:
        table.open(dbf.READ_WRITE)
        opened = True
    elif table.status != dbf.READ_WRITE:
        previous_status = table.status
        table.open(dbf.READ_WRITE)
    try:
        rows = _load_rows(rows, columns=columns, encoding=encoding,
                          has_header=has_header, dialect=dialect,
                          delimiter=delimiter)
        parsed = [_make_row(item, columns, index)
                  for index, item in enumerate(rows)]
        if not callable(key_fields):
            _validate_fields(table, parsed, key_fields, ignore_unknown_fields)
        groups = OrderedDict()
        for row in parsed:
            key = _key_for_parsed_row(row, key_fields, table)
            groups.setdefault(key, []).append(row)
        index = _target_index(table, key_fields, include_deleted)

        report = CorrectionReport()
        report.dry_run = dry_run
        pending = []
        for key, group_rows in groups.items():
            targets = index.get(key, [])
            if not targets:
                report.unmatched.append(key)
                continue
            if len(targets) != 1:
                report.ambiguous.append(key)
                continue
            values, conflicts, sources = _merge_rows(
                    table, key, group_rows, key_fields, default_policy,
                    column_policies)
            record = targets[0]
            change = CorrectionChange(
                    key, recno(record), record, values,
                    dict((field, record[field]) for field in values),
                    conflicts, sources)
            report.changes.append(change)
            for conflict in conflicts.values():
                report.conflicts.append(conflict)
            if values:
                pending.append(change)

        if strict and (report.unmatched or report.ambiguous or report.conflicts):
            if report.conflicts:
                raise CorrectionConflictError(report.conflicts)
            if report.unmatched:
                raise CorrectionInputError(
                        'no record found for key%s %s'
                        % (('', 's')[len(report.unmatched) != 1],
                           ', '.join(repr(key) for key in report.unmatched))
                        )
            raise CorrectionInputError(
                    'key%s %s matched more than one record'
                    % (('', 's')[len(report.ambiguous) != 1],
                       ', '.join(repr(key) for key in report.ambiguous))
                    )

        if not dry_run:
            for change in pending:
                if not _apply_change(change):
                    report.errors.append(change.error)
        else:
            for change in pending:
                if not _preview_change(change):
                    report.errors.append(change.error)
    finally:
        if opened:
            table.close()
        elif previous_status is not None:
            table.open(previous_status)
    return report


def import_corrections(table, rows=None, key_fields=None, **kwargs):
    """Alias for :func:`apply_corrections`."""
    return apply_corrections(table, rows, key_fields, **kwargs)


def correct_records(table, rows=None, key_fields=None, **kwargs):
    """Alias for :func:`apply_corrections`."""
    return apply_corrections(table, rows, key_fields, **kwargs)


def apply_record_corrections(table, rows=None, key_fields=None, **kwargs):
    """Alias for :func:`apply_corrections`."""
    return apply_corrections(table, rows, key_fields, **kwargs)


def plan_corrections(table, rows=None, key_fields=None, **kwargs):
    """Plan a sparse correction batch without writing to the table."""
    kwargs['dry_run'] = True
    return apply_corrections(table, rows, key_fields, **kwargs)
