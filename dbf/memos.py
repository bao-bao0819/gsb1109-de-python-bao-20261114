"""
Extraction of dBase III / Clipper DBT memo payloads.

A DBT file is a sequence of fixed-size blocks.  Block zero holds the memo
header; block numbers start at one.  A memo field in a DBF record stores the
number of the block where that memo begins.  Memo content ends at the first
logical end marker (``\\x1a\\x1a``); bytes after that marker are block padding,
and a later memo starts in a later block.

The extractor works from the memo-field references in the DBF itself:

* each distinct (table file, block) memo is returned once, as the exact
  bytes stored in the DBT file -- no decoding and no re-encoding;
* every reference to that memo is listed with its table name, original
  record number, and field name;
* a reference that does not land on the beginning of a real memo is reported
  as missing, and its bytes are never appended to the following memo;
* block numbers are scoped to their own DBT file: the same number in two
  different tables is two different memos.
"""

from __future__ import print_function

from collections import OrderedDict

import os

from .bridge import *
from .constants import ASTERISK, CLOSED, END, ON_DISK, READ_ONLY, START
from .exceptions import DbfError

__all__ = (
        'MEMO_TERMINATOR', 'DBT_BLOCK_SIZE',
        'MemoReference', 'MemoPayload', 'MissingMemo', 'MemoExtraction',
        'extract_memos', 'extract_memo_payloads', 'memo_payloads',
        )

MEMO_TERMINATOR = b'\x1a\x1a'
DBT_BLOCK_SIZE = 512

_PAD_BYTES = frozenset((0x00, 0x1a, 0xff))
_FILL_BYTES = frozenset((0x00, 0xff))


class _Record(dict):
    """
    dictionary with attribute access, so both ``entry['payload']`` and
    ``entry.payload`` work
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        try:
            del self[name]
        except KeyError:
            raise AttributeError(name)


class MemoReference(_Record):
    """
    One memo-field reference: table, original record number, and field.
    """

    def __init__(self, table, table_name, table_id, filename, recno, field, block, deleted=False):
        _Record.__init__(
                self,
                table=table,
                table_name=table_name,
                name=table_name,
                table_id=table_id,
                filename=filename,
                recno=recno,
                record=recno,
                row=recno,
                row_number=recno + 1,
                field=field,
                block=block,
                deleted=bool(deleted),
                )


class MemoPayload(_Record):
    """
    One distinct memo payload and every reference that points at it.
    """

    def __init__(self, table, table_name, table_id, filename, block, payload, references):
        _Record.__init__(
                self,
                table=table,
                table_name=table_name,
                name=table_name,
                table_id=table_id,
                filename=filename,
                block=block,
                payload=payload,
                payload_bytes=payload,
                data=payload,
                content=payload,
                size=len(payload),
                references=references,
                refs=references,
                reference_count=len(references),
                missing=False,
                reason=None,
                )


class MissingMemo(_Record):
    """
    One or more references whose block cannot supply a valid memo.
    """

    def __init__(self, table, table_name, table_id, filename, block, references, reason, raw_block=None):
        _Record.__init__(
                self,
                table=table,
                table_name=table_name,
                name=table_name,
                table_id=table_id,
                filename=filename,
                block=block,
                raw_block=raw_block,
                payload=None,
                payload_bytes=None,
                data=None,
                content=None,
                size=0,
                references=references,
                refs=references,
                reference_count=len(references),
                missing=True,
                reason=reason,
                )


class MemoExtraction(list):
    """
    Result of :func:`extract_memos`.

    Iteration and normal list access give one :class:`MemoPayload` per
    distinct table/block memo.  References to unusable blocks live in
    ``missing`` and are never mixed into a payload.  ``references`` flattens
    every reference from both groups.

    Look entries up with ``find(table, record, field)`` or
    ``by_block(table, block)``; ``table`` may be a :class:`dbf.Table`, a full
    DBF filename, or the table name.
    """

    def __init__(self, entries=(), missing=(), tables=None):
        list.__init__(self, entries)
        self.missing = list(missing)
        self.tables = list(tables or ())
        self._by_block = {}
        self._by_table_reference = {}
        self._by_name_reference = {}
        self._index()

    def _index(self):
        for entry in list(self) + self.missing:
            if entry['block'] is not None:
                self._by_block[(entry['table_id'], entry['block'])] = entry
            for ref in entry['references']:
                table_key = (ref['table_id'], ref['recno'], ref['field'])
                name_key = (ref['table_name'].upper(), ref['recno'], ref['field'])
                self._by_table_reference[table_key] = entry
                self._by_name_reference.setdefault(name_key, entry)

    @property
    def payloads(self):
        return list(self)

    @property
    def memos(self):
        return list(self)

    @property
    def entries(self):
        return list(self)

    @property
    def all(self):
        return list(self) + self.missing

    @property
    def references(self):
        result = []
        for entry in list(self) + self.missing:
            result.extend(entry['references'])
        return result

    @property
    def missing_references(self):
        result = []
        for entry in self.missing:
            result.extend(entry['references'])
        return result

    def by_table(self, table=None):
        """
        payloads (or missing entries) for one table, or a name -> entries map
        """
        if table is None:
            grouped = OrderedDict()
            for entry in list(self) + self.missing:
                grouped.setdefault(entry['table_name'], []).append(entry)
            return grouped
        table_id = self._resolve_table(table)
        return [entry for entry in list(self) + self.missing if entry['table_id'] == table_id]

    def as_dict(self):
        return {
                'payloads': [dict(entry) for entry in self],
                'missing': [dict(entry) for entry in self.missing],
                }

    def manifest(self, include_payload=False):
        """
        JSON-friendly description of the extraction.

        Raw payload bytes are omitted by default so the result can be
        serialized directly; pass ``include_payload=True`` to embed them.
        The persisted-file variant lives in :class:`dbf.memo_store.MemoStore`.
        """
        def payload_entry(entry):
            result = {
                'table_name': entry['table_name'],
                'table_id': entry['table_id'],
                'block': entry['block'],
                'size': entry['size'],
                'references': [
                    {
                        'table_name': ref['table_name'],
                        'table_id': ref['table_id'],
                        'record': ref['recno'],
                        'row_number': ref['row_number'],
                        'field': ref['field'],
                        'deleted': ref['deleted'],
                        }
                    for ref in entry['references']
                    ],
                }
            if include_payload:
                result['payload'] = entry['payload']
            return result
        return {
                'tables': self._table_manifest(),
                'payloads': [payload_entry(entry) for entry in self],
                'missing': [
                    {
                        'table_name': entry['table_name'],
                        'table_id': entry['table_id'],
                        'block': entry['block'],
                        'reason': entry['reason'],
                        'references': [
                            {
                                'table_name': ref['table_name'],
                                'table_id': ref['table_id'],
                                'record': ref['recno'],
                                'row_number': ref['row_number'],
                                'field': ref['field'],
                                'deleted': ref['deleted'],
                                }
                            for ref in entry['references']
                            ],
                        }
                    for entry in self.missing
                    ],
                }

    def _table_manifest(self):
        seen = OrderedDict()
        for entry in list(self) + self.missing:
            seen.setdefault(entry['table_id'], {
                    'table_name': entry['table_name'],
                    'table_id': entry['table_id'],
                    'filename': entry['filename'],
                    })
        return list(seen.values())

    def _resolve_table(self, table):
        from .tables import Table
        if isinstance(table, Table):
            return _canonical_name(table.filename)
        if not isinstance(table, basestring):
            raise TypeError("%r is not a table or table name" % (table,))
        if table == ':memory:':
            return table
        if os.path.isabs(table) or os.sep in table or (os.altsep and os.altsep in table):
            return _canonical_name(table)
        for name, table_id in self._name_map.items():
            if name.lower() == table.lower():
                return table_id
        return _canonical_name(table)

    @property
    def _name_map(self):
        names = {}
        for entry in self.all:
            names.setdefault(entry['table_name'], entry['table_id'])
            names.setdefault(os.path.basename(entry['table_id']), entry['table_id'])
            names.setdefault(os.path.splitext(os.path.basename(entry['table_id']))[0], entry['table_id'])
        return names

    def find(self, table, recno, field):
        """
        Return the payload or missing entry for a table/record/field reference.
        ``recno`` is the original 0-based physical record number; ``row_number``
        on the returned entry is the 1-based dBase row number.
        """
        field = field.upper()
        if isinstance(table, basestring) and not (
                os.path.isabs(table) or os.sep in table or (os.altsep and os.altsep in table)
                ) and table != ':memory:':
            entry = self._by_name_reference.get((table.upper(), recno, field))
            if entry is not None:
                return entry
        table_id = self._resolve_table(table)
        return self._by_table_reference.get((table_id, recno, field))

    def by_block(self, table, block):
        """
        Return the payload or missing entry for a table/block pair.
        """
        return self._by_block.get((self._resolve_table(table), block))

    def __getitem__(self, item):
        if isinstance(item, basestring):
            name = item.lower()
            if name in ('payloads', 'payload', 'memos', 'available'):
                return self.payloads
            elif name == 'missing':
                return self.missing
            elif name in ('all', 'entries'):
                return self.all
            elif name in ('references', 'refs'):
                return self.references
            elif name == 'manifest':
                return self.manifest()
            raise KeyError(item)
        elif isinstance(item, tuple):
            if len(item) == 2:
                return self.by_block(*item)
            elif len(item) == 3:
                return self.find(*item)
            raise TypeError("memo extraction index must be (table, block) or (table, recno, field)")
        return list.__getitem__(self, item)


def _canonical_name(filename):
    if not filename or filename == ':memory:':
        return filename
    return os.path.abspath(filename)


def _table_identity(table, table_name):
    filename = _canonical_name(table.filename)
    if table_name:
        display = table_name
    elif filename and filename != ':memory:':
        display = os.path.splitext(os.path.basename(filename))[0]
    else:
        display = filename
    return filename, display


def _header_next_block(data):
    """
    Return the DBT header's next-free block number (little-endian).
    """
    header = bytearray(data[:4])
    if len(header) < 4:
        return None
    return int(
            header[0]
            | (header[1] << 8)
            | (header[2] << 16)
            | (header[3] << 24)
            )


def _is_free_block(block_bytes):
    """
    True when a block holds only block padding (and no logical end marker).
    """
    if MEMO_TERMINATOR in block_bytes:
        return False
    if not block_bytes:
        return True
    return all(byte in _FILL_BYTES for byte in bytearray(block_bytes))


def _next_memo_block(block, terminator_end, block_size):
    """
    First block that can begin a new memo after one whose end marker stopped
    at ``terminator_end``.

    Allocation uses whole blocks: the memo occupies every block touched by its
    content or its end marker, so a marker straddling a block boundary keeps
    that block occupied and the next memo begins after it.
    """
    last_marker_byte = terminator_end - 1
    return max(last_marker_byte // block_size + 1, block + 1)


_FILL_BYTES = frozenset((0x00, 0xff))


def _run_of_fill_before(data, boundary, block_size):
    """
    True when the bytes immediately before ``boundary`` end in a long fill
    run (and ``boundary`` is a block boundary).
    """
    previous = data[max(0, boundary - block_size):boundary]
    run = 0
    for byte in bytearray(reversed(previous)):
        if byte in _FILL_BYTES:
            run += 1
        else:
            break
    return run >= 32


def _embedded_reference_start(data, block, end_block, block_size, referenced):
    """
    Return the first referenced block in ``(block, end_block)`` that starts a
    new memo after a long fill run, or None.

    A memo's bytes are contiguous.  A long run of 0x00/0xff immediately before
    a *referenced* block boundary, followed by real content in that block,
    means the candidate block is damaged and the scan merely ran into the
    following memo.  Resuming at the referenced block keeps the damaged
    reference missing instead of handing it the next memo's payload.

    Only referenced blocks can trigger this split, so ordinary memo text
    (including binary payloads) is never cut at an arbitrary boundary.
    """
    candidates = set(referenced)
    boundary = block * block_size + block_size
    limit = end_block * block_size
    while boundary < limit:
        candidate = boundary // block_size
        if candidate in candidates:
            following = bytearray(data[boundary:boundary + 1])
            if following and following[0] not in _PAD_BYTES:
                if _run_of_fill_before(data, boundary, block_size):
                    return candidate
        boundary += block_size
    return None


def _scan_memos(data, block_size, parsed_end, referenced):
    """
    Walk the DBT memo area in file order.

    Returns ``starts``, a map of every block that physically begins a memo to
    its payload (bytes before the logical end marker).

    Memos are allocated sequentially: a memo starts at block one or in the
    first block available after the previous memo's end marker; the remainder
    of the final block is padding, and free blocks are skipped.  Because the
    DBT has no per-memo length field, this structure is what distinguishes a
    real memo start from a pointer into the middle of a memo.  A block that
    begins with fill bytes is not a memo start, and a candidate that would
    swallow a fill-separated following memo is rejected, so a damaged block
    never absorbs the memo after it.
    """
    starts = {}
    block = 1
    while block < parsed_end:
        first = data[block * block_size:(block + 1) * block_size]
        if not first:
            break
        if first[:2] == MEMO_TERMINATOR:
            # an allocated zero-length memo
            starts[block] = b''
            block += 1
            continue
        if first[:1] in (b'\x00', b'\xff') or _is_free_block(first):
            # free / padding block, not a memo start
            block += 1
            continue
        chunk = data[block * block_size:min(parsed_end * block_size, len(data))]
        terminator = chunk.find(MEMO_TERMINATOR)
        if terminator < 0:
            # no logical end marker remains in the used area, so nothing after
            # this point can be delimited reliably
            break
        used_end = block * block_size + terminator + len(MEMO_TERMINATOR)
        end_block = _next_memo_block(block, used_end, block_size)
        resume = _embedded_reference_start(
                data, block, end_block, block_size, referenced)
        if resume is not None:
            block = resume
            continue
        starts[block] = chunk[:terminator]
        block = end_block
    return starts


def _missing_reason(block, total_blocks, parsed_end, starts):
    if block < 1 or block >= total_blocks or block >= parsed_end:
        return 'out_of_range'
    return 'bad_block'


def _extract_table(table, table_name=None):
    from .tables import Db3Table, ClpTable
    filename, display = _table_identity(table, table_name)
    meta = table._meta
    if meta.location != ON_DISK:
        return MemoExtraction(tables=[table])
    if not isinstance(table, (Db3Table, ClpTable)):
        raise DbfError(
                "%s uses a %s memo file, not a DBT memo file"
                % (table.filename, table._version)
                )
    memofields = [f for f in (meta.memofields or ()) if f in meta.fields]
    if not memofields:
        return MemoExtraction(tables=[table])

    groups = OrderedDict()
    bad_groups = OrderedDict()
    for record in table:
        recno = record._recnum
        deleted = record._data[0] == ASTERISK
        for field in memofields:
            fielddef = meta[field]
            token = to_bytes(record._data[fielddef[START]:fielddef[END]]).strip()
            if not token:
                continue
            try:
                block = int(token)
            except (TypeError, ValueError):
                key = (filename, token)
                group = bad_groups.get(key)
                if group is None:
                    group = bad_groups[key] = {
                            'block': None,
                            'raw_block': token,
                            'references': [],
                            }
                group['references'].append(MemoReference(
                        display, display, filename, filename, recno, field, None, deleted
                        ))
                continue
            if block == 0:
                # zero means "no memo in this field", not a broken reference
                continue
            key = (filename, block)
            group = groups.get(key)
            if group is None:
                group = groups[key] = {
                        'block': block,
                        'references': [],
                        }
            group['references'].append(MemoReference(
                    display, display, filename, filename, recno, field, block, deleted
                    ))

    if not groups and not bad_groups:
        return MemoExtraction(tables=[table])

    try:
        with open(meta.memoname, 'rb') as mfd:
            data = mfd.read()
    except (IOError, OSError):
        missing = []
        for group in groups.values():
            missing.append(MissingMemo(
                    display, display, filename, filename, group['block'],
                    group['references'], 'memo_file_missing',
                    ))
        for group in bad_groups.values():
            missing.append(MissingMemo(
                    display, display, filename, filename, None,
                    group['references'], 'bad_reference',
                    raw_block=group['raw_block'],
                    ))
        return MemoExtraction(missing=missing, tables=[table])

    block_size = DBT_BLOCK_SIZE
    total_blocks = (len(data) + block_size - 1) // block_size
    header_next = _header_next_block(data)
    highest_ref = max([group['block'] for group in groups.values()] or [0])
    if header_next is None or not 2 <= header_next <= total_blocks + 1 or header_next <= highest_ref:
        # a header that does not reach past every live reference is stale and
        # must not truncate the used area
        header_next = None
    parsed_end = total_blocks if header_next is None else min(header_next, total_blocks)
    referenced = sorted(set(key[1] for key in groups if key[1] > 0))
    starts = _scan_memos(data, block_size, parsed_end, referenced)

    payloads = []
    missing = []
    for key in sorted(groups):
        block = key[1]
        group = groups[key]
        target = (display, display, filename, filename)
        if block in starts:
            payloads.append(MemoPayload(
                    *target,
                    block=block,
                    payload=starts[block],
                    references=group['references'],
                    ))
        else:
            missing.append(MissingMemo(
                    *target,
                    block=block,
                    references=group['references'],
                    reason=_missing_reason(block, total_blocks, parsed_end, starts),
                    ))

    for group in bad_groups.values():
        missing.append(MissingMemo(
                display, display, filename, filename, None,
                group['references'], 'bad_reference',
                raw_block=group['raw_block'],
                ))
    payloads.sort(key=lambda entry: (entry['table_id'], entry['block']))
    missing.sort(key=lambda entry: (
            entry['table_id'],
            entry['block'] if entry['block'] is not None else -1,
            ))
    return MemoExtraction(payloads, missing, tables=[table])


def extract_memos(source, table_name=None):
    """
    Extract the memo payloads referenced by the memo fields in ``source``.

    ``source`` may be a :class:`dbf.Table`, a DBF filename, or a sequence of
    either.  The result is a :class:`MemoExtraction`:

    * iterating it yields one :class:`MemoPayload` per distinct
      (table file, block number) memo;
    * each payload carries the raw DBT bytes in ``payload`` and every
      reference in ``references`` (table name, original record number,
      field name, and whether the row is deleted);
    * references to blocks that are not valid memo starts are in ``.missing``
      (``missing=True``) and are never merged into another memo.

    A block number alone is not a global memo identity: the same number in
    another table's DBT is a different memo.

    Payloads are returned as bytes so the original DBT encoding is preserved;
    callers decide how (or whether) to decode them.
    """
    from .tables import Table
    if isinstance(source, (list, tuple, set, frozenset)):
        entries = []
        missing = []
        tables = []
        for item in source:
            result = extract_memos(item)
            entries.extend(result)
            missing.extend(result.missing)
            tables.extend(result.tables)
        return MemoExtraction(entries, missing, tables=tables)
    if isinstance(source, Table):
        table = source
        owned = False
        if table.status == CLOSED:
            table.open(READ_ONLY)
            owned = True
        try:
            return _extract_table(table, table_name)
        finally:
            if owned:
                table.close()
    if isinstance(source, basestring):
        # ignore_memos only skips *decoding* memos; the raw field pointers and
        # the memo file name remain available, so a missing/corrupt DBT can be
        # reported as missing references instead of aborting the open
        table = Table(source, ignore_memos=True)
        table.open(READ_ONLY)
        try:
            return _extract_table(table, table_name)
        finally:
            table.close()
    raise TypeError("%r is not a table, filename, nor sequence of either" % (source,))


extract_memo_payloads = extract_memos
memo_payloads = extract_memos
