"""
Migration support for moving legacy dBase/FoxPro/VFP records into a new
database that has explicit null support.

The regular :class:`~dbf.Record` collapses several distinct on-disk states
onto the same Python value.  For example a blank date and a nullable date
whose null bit is set both look like ``None``, an empty character field and
an empty memo both look like ``u''``, and a logical ``?`` looks like
``Unknown``/``None``.  Once those states are merged they cannot be separated
again, which loses information the moment the record leaves the .dbf file.

This module keeps the states apart:

* :class:`IntermediateRecord` stores one :class:`IntermediateField` per user
  field.  Each field carries its dbf type, whether the column is nullable in
  this table layout, the explicit null state (VFP ``_NullFlags`` bitmap), the
  decoded business value, and the raw on-disk bytes.
* :func:`intermediate_record` builds such a record from a :class:`~dbf.Record`
  or :class:`~dbf.RecordTemplate`, interpreting the null bitmap against the
  layout that actually owns the record -- never against a newer layout whose
  nullable columns may occupy different bitmap positions.
* :func:`restore_record` writes an intermediate record back into a table with
  a matching layout, reproducing the original business values and the
  distinction between an empty memo and a memo that points at a real
  zero-length block.
"""

import struct

from .bridge import basestring, bytes, to_bytes
from .constants import BINARY, END, FLAGS, LENGTH, NUL, START, TYPE, FieldType
from .data_types import Null
from .exceptions import DbfError, FieldMissingError
from .tables import Record, RecordTemplate

__all__ = [
        'IntermediateField', 'IntermediateRecord',
        'intermediate_record', 'intermediate_records', 'restore_record',
        ]

_NULLABLE = 0x02


class IntermediateField(object):
    """
    A single field captured for migration.

    Attributes:
        name:       upper-cased field name
        type:       :class:`~dbf.constants.FieldType` of the column
        nullable:   True when the owning layout marks the column nullable
        null_index: position within the layout's null bitmap, or None when
                    the column is not nullable in that layout
        is_null:    True when the explicit null bit was set (VFP Null); a
                    blank, uninitialized column is not considered null
        value:      decoded business value (``Null`` when ``is_null`` is set)
        raw:        raw bytes stored in the record for this field
        flags:      the field's layout flags (binary, nullable, system, ...)
        memo_block: memo pointer parsed from ``raw`` (0 means no block), or
                    None for non-memo fields
    """

    __slots__ = (
            'name', 'type', 'nullable', 'null_index', 'is_null', 'value',
            'raw', 'flags', 'memo_block',
            )

    def __init__(self, name, fielddef, is_null, value, raw, memo_block=None):
        self.name = name
        self.type = FieldType(fielddef[TYPE])
        self.nullable = bool(fielddef[FLAGS] & _NULLABLE)
        self.null_index = fielddef[NUL] if self.nullable else None
        self.is_null = is_null
        self.value = Null if is_null else value
        self.raw = to_bytes(raw)
        self.flags = fielddef[FLAGS]
        self.memo_block = memo_block

    @property
    def is_memo(self):
        """True for memo-backed columns (Memo, General, Picture)."""
        return self.memo_block is not None

    @property
    def has_memo_block(self):
        """True when a real memo block (including a zero-length one) is referenced."""
        return self.memo_block is not None and self.memo_block != 0

    def __repr__(self):
        state = 'null' if self.is_null else 'value'
        return 'IntermediateField(%r, %s, %s=%r)' % (
                self.name, self.type.symbol, state, self.value)

    def __eq__(self, other):
        if not isinstance(other, IntermediateField):
            return NotImplemented
        # memo block numbers and raw bytes are physical details that change
        # when a record is re-homed; the provenance (no block vs. a real
        # block, even a zero-length one) is part of the logical state
        return (
                self.name == other.name
                and self.type == other.type
                and self.nullable == other.nullable
                and self.null_index == other.null_index
                and self.is_null == other.is_null
                and (self.value is other.value or self.value == other.value)
                and self.has_memo_block == other.has_memo_block
                )

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result


class IntermediateRecord(object):
    """
    A layout-aware, field-typed snapshot of one record.

    The null bitmap is interpreted using the layout passed in at construction
    time.  Two layouts that share column names but disagree about which
    columns are nullable produce different field metadata, so a bitmap read
    from an older layout is never decoded with a newer layout's positions.
    """

    def __init__(self, layout, fields, deleted, null_bitmap=None):
        self._layout = layout
        self.fields = list(fields)
        self.deleted = deleted
        self.null_bitmap = None if null_bitmap is None else to_bytes(null_bitmap)
        self._by_name = dict((field.name, field) for field in self.fields)

    @property
    def layout(self):
        """The table layout (``_meta``) this record was interpreted with."""
        return self._layout

    @property
    def field_names(self):
        return [field.name for field in self.fields]

    def __getitem__(self, key):
        if isinstance(key, basestring):
            return self._by_name[key.upper()]
        return self.fields[key]

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        try:
            return self._by_name[name.upper()]
        except KeyError:
            raise AttributeError(name)

    def __len__(self):
        return len(self.fields)

    def __iter__(self):
        return iter(self.fields)

    def __eq__(self, other):
        if not isinstance(other, IntermediateRecord):
            return NotImplemented
        return (
                self.deleted == other.deleted
                and self.null_bitmap == other.null_bitmap
                and self.fields == other.fields
                )

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __repr__(self):
        return 'IntermediateRecord(%r, deleted=%r)' % (
                [field.name for field in self.fields], self.deleted)


def _null_state(record, layout, name, fielddef):
    """
    Returns (is_null, null_index) for a field using the supplied layout.

    Only consults the bitmap when the column is nullable *in this layout*;
    historical layouts where the same column was not nullable have no
    ``_NULLFLAGS`` block (or no null index for the column), and are treated as
    never-null regardless of the raw bitmap bytes.  The bitmap's physical
    offset always comes from the supplied layout, which is the version whose
    bytes are being interpreted -- never from a newer layout.
    """
    if not (fielddef[FLAGS] & _NULLABLE):
        return False, None
    if '_NULLFLAGS' not in layout:
        return False, None
    index = fielddef[NUL]
    null_def = layout['_NULLFLAGS']
    null_data = record._data[null_def[START]:null_def[END]]
    byte, bit = divmod(index, 8)
    return bool(null_data[byte] >> bit & 1), index


def _memo_block(fielddef, raw):
    """
    Parses a memo pointer from raw field bytes.

    dBase/Clipper/FoxPro2 memo-family fields hold an ASCII block number in ten
    bytes; VFP memo, general, and picture fields hold a signed little-endian
    binary pointer in four bytes.  A zero pointer means no memo block is
    referenced.
    """
    pointer = to_bytes(raw)
    if len(pointer) != 4:
        text = pointer.strip()
        return int(text) if text.isdigit() else 0
    return struct.unpack('<i', pointer)[0]


def intermediate_record(record, layout=None):
    """
    Captures a dbf record as a typed :class:`IntermediateRecord`.

    ``layout`` defaults to the layout that owns ``record``, which is the
    layout of the file version the record came from.  Passing another layout
    is only supported when it describes the same physical fields (same names,
    types, and lengths) but carries different nullability metadata, such as a
    reconstructed historical layout: the null bitmap and raw field bytes are
    then interpreted with that layout while decoded values still come from the
    record itself.
    """
    if not isinstance(record, (Record, RecordTemplate)):
        raise TypeError(
                "expected a Record or RecordTemplate, not %r" % type(record))
    if layout is None:
        layout = record._meta
    return _intermediate_with_layout(record, layout)


def _intermediate_with_layout(record, layout):
    from .utils import is_deleted

    own_layout = layout is record._meta
    if not own_layout:
        _assert_same_fields(record._meta, layout)
    fields = []
    null_bitmap = None
    if '_NULLFLAGS' in layout:
        null_def = layout['_NULLFLAGS']
        null_bitmap = record._data[null_def[START]:null_def[END]]
    memo_types = layout.memo_types
    for name in layout.user_fields:
        fielddef = layout[name]
        is_null, null_index = _null_state(record, layout, name, fielddef)
        raw = record._data[fielddef[START]:fielddef[END]]
        memo_block = None
        if fielddef[TYPE] in memo_types:
            memo_block = _memo_block(fielddef, raw)
        if is_null:
            value = Null
        elif own_layout:
            value = record._retrieve_field_value(name)
        else:
            value = _decode_with_layout(record, layout, name, fielddef, raw)
        field = IntermediateField(
                name, fielddef, is_null, value, raw, memo_block)
        field.null_index = null_index
        fields.append(field)
    deleted = False
    if isinstance(record, Record):
        deleted = is_deleted(record)
    return IntermediateRecord(layout, fields, deleted, null_bitmap)


def _decode_with_layout(record, layout, name, fielddef, raw):
    """Decodes raw field bytes using the supplied (historical) layout."""
    retrieve = layout.fieldtypes[fielddef[TYPE]]['Retrieve']
    memo = layout.memo
    if memo is None:
        memo = record._meta.memo
    return retrieve(raw, fielddef, memo, layout.decoder)


def _assert_same_fields(source_layout, other_layout):
    """Rejects cross-layout interpretation of physically different records."""
    if source_layout.user_fields != other_layout.user_fields:
        raise DbfError(
                "cannot interpret a record with a layout that has different "
                "fields; open the historical table and use its own layout")
    for name in other_layout.user_fields:
        left, right = source_layout[name], other_layout[name]
        if (left[TYPE] != right[TYPE] or left[LENGTH] != right[LENGTH]
                or left[START] != right[START] or left[END] != right[END]):
            raise DbfError(
                    "field %r has a different type, length, or offset in the "
                    "supplied layout" % name)


def intermediate_records(records, layout=None):
    """Lazily captures an iterable of records (or a whole table)."""
    for record in records:
        yield intermediate_record(record, layout)


def _check_compatible(intermediate, target_meta):
    """
    Verifies the intermediate record can be restored into ``target_meta``.

    Field names and dbf types must match.  A field captured as an explicit
    null requires the target column to be nullable; non-nullable historical
    columns may be restored into columns that became nullable later because
    they are written as ordinary values, which clear the target's null bit.

    Bit positions are never copied from the source bitmap -- each value is
    written by field name through the target layout, so a column whose null
    index shifted between versions cannot land in the wrong bit.
    """
    target_names = target_meta.user_fields
    for field in intermediate.fields:
        if field.name not in target_names:
            raise FieldMissingError(
                    "field %r does not exist in target table" % field.name)
        target_def = target_meta[field.name]
        if FieldType(target_def[TYPE]) != field.type:
            raise DbfError(
                    "field %r is type %s in the record but %s in the target "
                    "table" % (field.name, field.type.symbol,
                               FieldType(target_def[TYPE]).symbol))
        if field.is_null and not (target_def[FLAGS] & _NULLABLE):
            raise DbfError(
                    "field %r holds an explicit null but the target column "
                    "is not nullable" % field.name)


def restore_record(table, intermediate):
    """
    Appends ``intermediate`` to ``table`` and returns the new record.

    The table must be open read-write and contain the captured fields with
    the same dbf types.  Explicit nulls are re-flagged in the new table's own
    bitmap, blank values stay blank rather than becoming null, and memo
    fields reproduce the original distinction between no memo block (empty or
    None memo) and a real zero-length block.
    """
    from .constants import READ_WRITE
    from .utils import delete as mark_deleted

    target_meta = table._meta
    if target_meta.status != READ_WRITE:
        raise DbfError(
                "%s is not open in read/write mode" % target_meta.filename)
    _check_compatible(intermediate, target_meta)

    field_values = {}
    for field in intermediate.fields:
        if field.is_null:
            field_values[field.name] = Null
        elif field.is_memo:
            if field.has_memo_block:
                field_values[field.name] = _restore_memo_value(field)
            elif target_meta[field.name][FLAGS] & _NULLABLE:
                # blank target records default nullable columns to Null;
                # writing None clears the null bit and stores a zero pointer
                field_values[field.name] = None
            # non-nullable tables already blank a memo to a zero pointer
        else:
            field_values[field.name] = field.value

    table.append()
    new_record = table[-1]
    new_record._start_flux()
    try:
        for name, value in field_values.items():
            new_record[name] = value
    except Exception:
        new_record._rollback_flux()
        raise
    new_record._commit_flux()
    if intermediate.deleted:
        mark_deleted(new_record)
    return new_record


def _restore_memo_value(field):
    """
    Returns the Python value to write for a memo that referenced a real block.

    Populated memos are restored with their decoded content; a memo that
    pointed at a real zero-length block is restored with an empty value so a
    fresh zero-length block is allocated, keeping it distinct from a memo
    that never had a block.
    """
    if field.value:
        return field.value
    if field.flags & BINARY:
        return bytes()
    return u''
