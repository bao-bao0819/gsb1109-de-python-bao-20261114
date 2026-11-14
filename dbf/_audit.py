"""
Uniqueness audit for one or more DBF key fields.

The audit compares the same physical records under a chain of increasingly
permissive read rules:

    1. original bytes, exactly as stored in the record
    2. bytes decoded with the table's code page, then trailing fill removed
    3. the decoded value after the business case-folding rule is applied

For every rule the audit partitions the selected records by their composite
key and reports groups with two or more members.  A record is *newly*
conflicting at a step when it was not already part of a conflicting group at
the previous step.  This matters for a group like::

    step 2:  A A B
    step 3:  A A B        # B is now folded into A

Only ``B`` is new at step 3; ``A A`` was already a collision and is kept in
the group for context, but is not reported as newly introduced.

If two separate collision groups later share one key, no row is individually
new, but the collision relationship is: such a step is reported as a merge
(``CollisionGroup.is_merge`` / ``previous_groups``) and still counts as an
introduced conflict.

The module can be used from Python::

    import dbf

    report = dbf.audit_key(
        'customer.dbf',
        ('CUST_CODE', 'BRANCH'),
        include_deleted=False,
        casefold='casefold',
    )
    print(report.format())
    print(report.first_collision_stage)

or from the command line::

    python -m dbf.audit customer.dbf --fields CUST_CODE BRANCH

Physical row numbers (``dbf.recno``) are always preserved.  ``AuditRecord.row``
is the one-based counterpart for display; no record is renumbered when
deleted records are excluded.
"""

from __future__ import print_function

import argparse
import binascii
import codecs
import json
import sys

from . import dbf
from .bridge import basestring, to_bytes, unicode
from .constants import BINARY, FLAGS, LENGTH, NULLABLE, NUL, START, TYPE
from .tables import Table

__all__ = (
        'AuditRecord', 'CollisionGroup', 'StageResult', 'AuditReport',
        'audit_key', 'audit_unique_key', 'check_unique_key',
        'find_key_collisions', 'main',
        )


# stage identifiers
RAW = 'raw_bytes'
CODEPAGE = 'codepage_trim'
CASEFOLD = 'casefold'

_STAGE_TITLES = {
        RAW: 'original bytes as stored',
        CODEPAGE: 'table code page, trailing fill removed',
        CASEFOLD: 'business case fold',
        }

_DEFAULT_FILL = u' \x00'
_NULL_KEY = object()
_CASEFOLD_NAMES = {
        'casefold': u'str.casefold',
        'upper': u'str.upper',
        'lower': u'str.lower',
        'none': u'identity',
        None: u'identity',
        False: u'identity',
        }


def _ensure_unicode(value):
    if isinstance(value, bytes):
        return value.decode(dbf.input_decoding or 'ascii')
    return value


def _field_names(fields):
    if isinstance(fields, basestring):
        for separator in (';', '+', ','):
            if separator in fields:
                fields = fields.split(separator)
                break
        else:
            fields = [fields]
    fields = [ _ensure_unicode(field).strip().upper() for field in fields ]
    if not fields or any(not field for field in fields):
        raise ValueError('at least one non-empty key field is required')
    if len(set(fields)) != len(fields):
        raise ValueError('duplicate key field in %r' % (fields, ))
    return fields


def _normalise_fill(fill):
    if fill is None:
        return u''
    if isinstance(fill, bytes):
        fill = fill.decode('latin-1')
    fill = _ensure_unicode(fill)
    # allow '--fill "\\x00"' to mean a NUL
    if '\\x' in fill:
        try:
            fill = codecs.decode(fill.encode('latin-1'), 'unicode_escape')
        except Exception:
            pass
    return fill


def _identity(value):
    return value


def _normalise_casefold(rule):
    if rule is None or rule is False:
        return 'none', _identity
    if callable(rule):
        return getattr(rule, '__name__', repr(rule)), rule
    rule = _ensure_unicode(rule).strip().lower()
    if rule in ('none', 'off', 'identity', 'raw'):
        return 'none', _identity
    if rule in ('casefold', 'fold', 'case-insensitive', 'case_insensitive'):
        return 'casefold', _text_casefold
    if rule in ('upper', 'uppercase', 'up'):
        return 'upper', lambda value: value.upper()
    if rule in ('lower', 'lowercase', 'down'):
        return 'lower', lambda value: value.lower()
    raise ValueError("casefold must be 'casefold', 'upper', 'lower', 'none', or a callable; not %r" % (rule, ))


def _text_casefold(value):
    casefold = getattr(value, 'casefold', None)
    if casefold is None:
        return value.lower()
    return casefold()


def _fold(value, rule):
    if value is None:
        # Null is distinct from an empty string; keep it out of the text rule
        return _NULL_KEY
    if isinstance(value, bytes):
        # binary key components are not text and are left untouched
        return value
    result = rule(value)
    if isinstance(result, list):
        result = tuple(result)
    return result


def _json_value(value):
    if value is _NULL_KEY:
        return None
    if isinstance(value, bytes):
        return {'hex': binascii.hexlify(value).decode('ascii'), 'repr': repr(value)}
    if isinstance(value, unicode):
        return value
    return value


def _display_value(value):
    if value is _NULL_KEY:
        return '<null>'
    return repr(value)


def _display_key(key):
    return ' | '.join(_display_value(part) for part in key)


class AuditRecord(object):
    """
    One physical DBF record as seen by each read rule.
    """

    __slots__ = ('recno', 'deleted', 'raw', 'decoded', 'folded')

    def __init__(self, recno, deleted, raw, decoded, folded):
        self.recno = recno
        self.deleted = deleted
        self.raw = tuple(raw)
        self.decoded = tuple(decoded)
        self.folded = tuple(folded)

    @property
    def row(self):
        "one-based physical row number"
        return self.recno + 1

    def values_for(self, stage):
        if stage == RAW:
            return self.raw
        elif stage == CODEPAGE:
            return self.decoded
        elif stage == CASEFOLD:
            return self.folded
        raise ValueError('unknown stage %r' % (stage, ))

    def as_dict(self, stage):
        return {
                'recno': self.recno,
                'row': self.row,
                'deleted': self.deleted,
                'values': [_json_value(value) for value in self.values_for(stage)],
                }

    def __repr__(self):
        return 'AuditRecord(recno=%d, row=%d, deleted=%r)' % (self.recno, self.row, self.deleted)


class CollisionGroup(object):
    """
    All records sharing one composite key at one stage.

    ``new_recnos`` contains only the records that were not already in a
    collision at the previous stage.  ``records`` still contains the whole
    group so the original row numbers remain available.

    A group can also be a *merge*: two or more collision groups from the
    previous stage now share one key.  Then no individual row is new, but the
    cross-group relationship is; ``previous_groups`` records how many earlier
    groups flowed into this one.
    """

    __slots__ = ('stage', 'key', 'records', 'new_recnos', 'previous_groups')

    def __init__(self, stage, key, records, new_recnos, previous_groups=0):
        self.stage = stage
        self.key = tuple(key)
        self.records = tuple(sorted(records, key=lambda record: record.recno))
        self.new_recnos = tuple(sorted(new_recnos))
        self.previous_groups = previous_groups

    @property
    def recnos(self):
        return tuple(record.recno for record in self.records)

    @property
    def rows(self):
        return tuple(record.row for record in self.records)

    @property
    def new_rows(self):
        return tuple(recno + 1 for recno in self.new_recnos)

    @property
    def is_merge(self):
        "two or more previously separate collision groups now share this key"
        return self.previous_groups > 1

    @property
    def is_new(self):
        return bool(self.new_recnos)

    @property
    def deleted_recnos(self):
        return tuple(record.recno for record in self.records if record.deleted)

    @property
    def key_display(self):
        return _display_key(self.key)

    def new_records(self):
        wanted = set(self.new_recnos)
        return tuple(record for record in self.records if record.recno in wanted)

    def as_dict(self):
        return {
                'key': [_json_value(value) for value in self.key],
                'key_display': self.key_display,
                'records': [record.as_dict(self.stage) for record in self.records],
                'recnos': list(self.recnos),
                'rows': list(self.rows),
                'new_recnos': list(self.new_recnos),
                'new_rows': list(self.new_rows),
                'deleted_recnos': list(self.deleted_recnos),
                'previous_groups': self.previous_groups,
                'is_merge': self.is_merge,
                }

    def __len__(self):
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    def __repr__(self):
        return 'CollisionGroup(stage=%r, recnos=%r, new_recnos=%r, previous_groups=%d)' % (
                self.stage, self.recnos, self.new_recnos, self.previous_groups)


class StageResult(object):
    """
    Result of applying one read rule to the selected records.
    """

    __slots__ = ('index', 'stage', 'title', 'rule', 'groups', 'collision_recnos')

    def __init__(self, index, stage, title, rule, groups):
        self.index = index
        self.stage = stage
        self.title = title
        self.rule = rule
        self.groups = tuple(groups)
        self.collision_recnos = tuple(sorted(
                record.recno
                for group in self.groups
                for record in group.records
                ))

    @property
    def has_collisions(self):
        return bool(self.groups)

    @property
    def group_count(self):
        return len(self.groups)

    @property
    def new_recnos(self):
        return tuple(sorted(
                recno
                for group in self.groups
                for recno in group.new_recnos
                ))

    @property
    def new_rows(self):
        return tuple(recno + 1 for recno in self.new_recnos)

    @property
    def introduced(self):
        "True when this rule makes at least one previously unique record collide"
        return bool(self.new_recnos)

    @property
    def merged_groups(self):
        "groups formed by merging two or more earlier collision groups"
        return tuple(group for group in self.groups if group.is_merge)

    @property
    def introduces_conflict(self):
        "True when this rule adds a colliding row or merges existing groups"
        return self.introduced or bool(self.merged_groups)

    @property
    def new_groups(self):
        return tuple(group for group in self.groups if group.is_new)

    def collisions(self, only_new=False):
        if only_new:
            return self.new_groups
        return self.groups

    def as_dict(self):
        return {
                'index': self.index,
                'stage': self.stage,
                'title': self.title,
                'rule': self.rule,
                'has_collisions': self.has_collisions,
                'introduced': self.introduced,
                'introduces_conflict': self.introduces_conflict,
                'group_count': self.group_count,
                'collision_recnos': list(self.collision_recnos),
                'new_recnos': list(self.new_recnos),
                'new_rows': list(self.new_rows),
                'merged_group_count': len(self.merged_groups),
                'groups': [group.as_dict() for group in self.groups],
                }

    def format(self, only_new=False):
        lines = []
        lines.append('stage %d: %s' % (self.index, self.title))
        lines.append('  rule: %s' % self.rule)
        groups = self.collisions(only_new=only_new)
        hidden = len(self.groups) - len(groups)
        if not groups:
            lines.append('  no collision groups')
            if hidden:
                lines.append('  (%d pre-existing group(s) hidden by --only-new)' % hidden)
            return '\n'.join(lines)
        lines.append('  %d collision group(s), %d newly conflicting record(s), %d merged group(s)' % (
                self.group_count, len(self.new_recnos), len(self.merged_groups)))
        if hidden:
            lines.append('  (%d pre-existing group(s) hidden by --only-new)' % hidden)
        for group in groups:
            lines.append('  [%s]' % group.key_display)
            lines.append('    recnos: %s' % ', '.join('#%d' % recno for recno in group.recnos))
            lines.append('    rows:   %s' % ', '.join('row %d' % row for row in group.rows))
            if group.new_recnos:
                lines.append('    new:    %s' % ', '.join(
                        '#%d (row %d)' % (recno, recno + 1)
                        for recno in group.new_recnos))
            elif group.is_merge:
                lines.append('    new:    (none; merges %d previously separate groups)' % (
                        group.previous_groups, ))
            else:
                lines.append('    new:    (none; already conflicting before this step)')
        return '\n'.join(lines)

    def __repr__(self):
        return 'StageResult(index=%d, stage=%r, groups=%d, new=%r)' % (
                self.index, self.stage, self.group_count, self.new_recnos)


class AuditReport(object):
    """
    Complete three-stage uniqueness report for one DBF and one key.
    """

    __slots__ = ('filename', 'fields', 'include_deleted', 'codepage', 'fill',
                 'casefold_name', 'record_count', 'deleted_count', 'stages')

    def __init__(self, filename, fields, include_deleted, codepage, fill,
                 casefold_name, record_count, deleted_count, stages):
        self.filename = filename
        self.fields = tuple(fields)
        self.include_deleted = include_deleted
        self.codepage = codepage
        self.fill = fill
        self.casefold_name = casefold_name
        self.record_count = record_count
        self.deleted_count = deleted_count
        self.stages = tuple(stages)

    @property
    def first_collision_stage(self):
        "first stage that introduces a collision, or None"
        for stage in self.stages:
            if stage.introduces_conflict:
                return stage
        return None

    @property
    def first_collision_index(self):
        stage = self.first_collision_stage
        return None if stage is None else stage.index

    @property
    def first_collision(self):
        stage = self.first_collision_stage
        if stage is None:
            return None
        return {
                'index': stage.index,
                'stage': stage.stage,
                'title': stage.title,
                'rule': stage.rule,
                'new_recnos': list(stage.new_recnos),
                'new_rows': list(stage.new_rows),
                'merged_groups': [
                        list(group.recnos) for group in stage.merged_groups
                        ],
                }

    @property
    def has_collisions(self):
        return any(stage.has_collisions for stage in self.stages)

    @property
    def clean_stages(self):
        return tuple(stage for stage in self.stages if not stage.has_collisions)

    def stage(self, name_or_index):
        if isinstance(name_or_index, int):
            for stage in self.stages:
                if stage.index == name_or_index:
                    return stage
            raise IndexError(name_or_index)
        for stage in self.stages:
            if stage.stage == name_or_index:
                return stage
        raise KeyError(name_or_index)

    def as_dict(self):
        return {
                'filename': self.filename,
                'fields': list(self.fields),
                'include_deleted': self.include_deleted,
                'codepage': self.codepage,
                'fill': self.fill,
                'casefold': self.casefold_name,
                'record_count': self.record_count,
                'deleted_count': self.deleted_count,
                'has_collisions': self.has_collisions,
                'first_collision': self.first_collision,
                'stages': [stage.as_dict() for stage in self.stages],
                }

    def format(self, only_new=False):
        lines = []
        lines.append('uniqueness audit: %s' % self.filename)
        lines.append('key: %s' % ' + '.join(self.fields))
        lines.append('records: %d selected, %d deleted %s' % (
                self.record_count,
                self.deleted_count,
                'included' if self.include_deleted else 'excluded',
                ))
        lines.append('code page: %s; trailing fill: %r; case fold: %s' % (
                self.codepage, self.fill, self.casefold_name))
        for stage in self.stages:
            lines.append('')
            lines.append(stage.format(only_new=only_new))
        lines.append('')
        first = self.first_collision_stage
        if first is None:
            lines.append('result: no collision under any of the three rules')
        else:
            lines.append('result: first collision introduced at stage %d (%s)' % (
                    first.index, first.title))
            if first.new_recnos:
                lines.append('        newly conflicting recnos: %s' % (
                        ', '.join('#%d' % recno for recno in first.new_recnos), ))
            else:
                lines.append('        no new rows; previously separate groups merged: %s' % (
                        '; '.join(
                            '%s' % ', '.join('#%d' % recno for recno in group.recnos)
                            for group in first.merged_groups
                            if not group.new_recnos
                            ),
                        ))
        return '\n'.join(lines)

    def __repr__(self):
        return 'AuditReport(filename=%r, fields=%r, first=%r)' % (
                self.filename, self.fields,
                None if self.first_collision_stage is None else self.first_collision_stage.index)


def _open_table(source):
    """
    Return ``(table, opened_here)`` for a filename or Table instance.
    """
    if isinstance(source, Table):
        table = source
        opened_here = False
        if table.status == dbf.CLOSED:
            table.open(dbf.READ_ONLY)
            opened_here = True
        return table, opened_here
    table = Table(source)
    table.open(dbf.READ_ONLY)
    return table, True


def _raw_records(table, fields, include_deleted, encoding, fill, decode_errors, fold_rule):
    layout = table._meta
    definitions = []
    for field in fields:
        if field not in table.field_names:
            raise ValueError('field %r is not in %s' % (field, table.filename))
        fielddef = layout[field]
        field_type = fielddef[TYPE]
        if field_type in layout.memo_types:
            raise ValueError('memo field %r cannot be used as a uniqueness key' % (field, ))
        definitions.append((
                field,
                fielddef[START],
                fielddef[LENGTH],
                field_type in layout.character_types,
                bool(fielddef[FLAGS] & BINARY),
                fielddef[FLAGS] & NULLABLE and '_NULLFLAGS' in layout,
                fielddef[NUL],
                ))
    fill_bytes = fill.encode('latin-1')
    null_flags = layout.get('_NULLFLAGS')

    def is_null(data, null_index):
        if null_index is None:
            return False
        byte, bit = divmod(null_index, 8)
        return bool(data[null_flags[START] + byte] >> bit & 1)

    def build(recno, deleted, data):
        raw_values = []
        decoded_values = []
        folded_values = []
        for field, start, length, text, binary, nullable, null_index in definitions:
            raw = to_bytes(data[start:start + length])
            raw_values.append(raw)
            if is_null(data, null_index if nullable else None):
                decoded = None
            elif binary:
                decoded = raw.rstrip(fill_bytes)
            elif text:
                decoded = raw.decode(encoding, decode_errors).rstrip(fill)
            else:
                # packed/binary numbers have no code-page representation
                decoded = raw
            decoded_values.append(decoded)
            folded_values.append(_fold(decoded, fold_rule))
        return AuditRecord(recno, deleted, raw_values, decoded_values, folded_values)

    records = []
    deleted_count = 0
    total = len(table)
    header = layout.header
    on_disk = (
            layout.location == dbf.ON_DISK
            and layout.dfd is not None
            and layout.status != dbf.CLOSED
            )
    if on_disk:
        # read the raw records in one sequential pass; this keeps the audit
        # usable on tables with millions of rows without materialising Records
        dfd = layout.dfd
        dfd.seek(header.start)
        for recno in range(total):
            data = dfd.read(header.record_length)
            if len(data) != header.record_length:
                raise ValueError(
                        'short read for record %d in %s' % (recno, table.filename))
            deleted = data[0:1] == b'*'
            if deleted:
                deleted_count += 1
                if not include_deleted:
                    continue
            records.append(build(recno, deleted, data))
    else:
        for recno in range(total):
            record = table[recno]
            deleted = bool(dbf.is_deleted(record))
            if deleted:
                deleted_count += 1
                if not include_deleted:
                    continue
            records.append(build(recno, deleted, record._data))
    return records, deleted_count


def _partition(records, stage):
    groups = {}
    for record in records:
        key = record.values_for(stage)
        groups.setdefault(key, []).append(record)
    return groups


def _collision_groups(stage, groups, previous_collision_groups):
    """
    ``previous_collision_groups`` maps recno -> previous group id.  A new group
    reports as ``new_recnos`` only rows with no previous group.  When rows from
    two or more previous groups land in the same new group, ``previous_groups``
    records that merge so the step is not reported as a no-op.
    """
    result = []
    for key, records in groups.items():
        if len(records) < 2:
            continue
        recnos = set(record.recno for record in records)
        new_recnos = set(recno for recno in recnos if recno not in previous_collision_groups)
        prior = set(
                previous_collision_groups[recno]
                for recno in recnos
                if recno in previous_collision_groups
                )
        result.append(CollisionGroup(
                stage, key, records, new_recnos,
                previous_groups=len(prior),
                ))
    result.sort(key=lambda group: min(record.recno for record in group.records))
    return result


def audit_key(table_or_filename, fields, include_deleted=False,
              casefold='casefold', encoding=None, fill=_DEFAULT_FILL,
              decode_errors=None):
    """
    Audit one composite key against raw bytes, code-page decoding, and a
    business case-folding rule.

    Parameters
    ----------
    table_or_filename:
        A DBF filename or an already opened ``dbf.Table``.
    fields:
        Iterable (or ``;``/``+``/``,`` separated string) of field names.
    include_deleted:
        Include records whose delete flag is set.  Physical recnos never change.
    casefold:
        ``'casefold'`` (default), ``'upper'``, ``'lower'``, ``'none'``, or a
        callable applied to every decoded key component.
    encoding:
        Override the table's code page.  By default the code page from the DBF
        header is used.
    fill:
        Characters treated as trailing field fill.  Defaults to spaces and NUL.
    decode_errors:
        Codec error policy.  Defaults to the table's ``unicode_errors``
        (normally ``'strict'``).
    """
    table, opened_here = _open_table(table_or_filename)
    try:
        fields = _field_names(fields)
        codepage = encoding or table._meta.codepage or dbf.default_codepage
        codepage = codecs.lookup(codepage).name
        if decode_errors is None:
            decode_errors = table._meta.unicode_errors or 'strict'
        fill = _normalise_fill(fill)
        casefold_name, fold_rule = _normalise_casefold(casefold)

        records, deleted_count = _raw_records(
                table, fields, include_deleted, codepage, fill,
                decode_errors, fold_rule,
                )
        selected_count = len(records)

        stages = []
        previous_collisions = {}
        for index, (stage, title) in enumerate((
                (RAW, _STAGE_TITLES[RAW]),
                (CODEPAGE, _STAGE_TITLES[CODEPAGE]),
                (CASEFOLD, _STAGE_TITLES[CASEFOLD]),
                ), 1):
            groups = _partition(records, stage)
            collisions = _collision_groups(stage, groups, previous_collisions)
            if stage == RAW:
                rule = 'raw field bytes, no decoding or trimming'
            elif stage == CODEPAGE:
                rule = 'decode as %s, then rstrip(%r)' % (codepage, fill)
            else:
                rule = 'decode as %s, rstrip(%r), then %s' % (
                        codepage, fill, casefold_name)
            stage_result = StageResult(index, stage, title, rule, collisions)
            stages.append(stage_result)
            previous_collisions = dict(
                    (recno, group_index)
                    for group_index, group in enumerate(stage_result.groups)
                    for recno in group.recnos
                    )

        return AuditReport(
                filename=table.filename,
                fields=fields,
                include_deleted=include_deleted,
                codepage=codepage,
                fill=fill,
                casefold_name=casefold_name,
                record_count=selected_count,
                deleted_count=deleted_count,
                stages=stages,
                )
    finally:
        if opened_here:
            table.close()


# aliases that read naturally at call sites
audit_unique_key = audit_key
check_unique_key = audit_key
find_key_collisions = audit_key


def _build_parser():
    parser = argparse.ArgumentParser(
            prog='python -m dbf.audit',
            description='Audit a DBF composite key for duplicate values.',
            )
    parser.add_argument('dbf_file', help='DBF file to audit')
    parser.add_argument('--fields', nargs='+', required=True,
            help='key field names (case-insensitive)')
    parser.add_argument('--include-deleted', action='store_true',
            help='include records whose delete flag is set')
    parser.add_argument('--casefold', default='casefold',
            help="business case rule: casefold (default), upper, lower, none")
    parser.add_argument('--encoding', default=None,
            help='override the code page stored in the DBF header')
    parser.add_argument('--fill', default=_DEFAULT_FILL,
            help="trailing fill characters (default: space and NUL)")
    parser.add_argument('--decode-errors', default=None,
            help="codec error policy (default: table's unicode_errors)")
    parser.add_argument('--only-new', action='store_true',
            help='show only groups that introduce new conflicts')
    parser.add_argument('--json', action='store_true',
            help='emit JSON instead of text')
    parser.add_argument('--fail-on-collision', action='store_true',
            help='exit 1 when any collision is found')
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    report = audit_key(
            args.dbf_file,
            args.fields,
            include_deleted=args.include_deleted,
            casefold=args.casefold,
            encoding=args.encoding,
            fill=args.fill,
            decode_errors=args.decode_errors,
            )
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(report.format(only_new=args.only_new))
    if args.fail_on_collision and report.has_collisions:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
