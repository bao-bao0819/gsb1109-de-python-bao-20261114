"""
Persistent, table-scoped storage for extracted DBT memo payloads.

The extraction in :mod:`dbf.memos` produces distinct payloads keyed by
(table file, DBT block).  :class:`MemoStore` writes each payload to its own
file next to a JSON manifest, so the DBT can later be discarded and memos
looked up by table name, original row number, and field name.

Every payload is written once even when several rows point at it; the manifest
lists every referencing row.  References whose block was unusable are
recorded as missing entries and have no payload file.  The table file is part
of the lookup key, so the same block number in another table never resolves
to this table's memo.
"""

from __future__ import print_function

from collections import OrderedDict
import hashlib
import io
import json
import os
import re

from .bridge import *
from .exceptions import DbfError
from .memos import extract_memos

__all__ = (
        'MemoStore', 'StoredMemo', 'save_memos', 'load_memos', 'find_memo',
        )

MANIFEST_NAME = 'manifest.json'
PAYLOAD_DIR = 'payloads'
MANIFEST_VERSION = 1


def _safe_name(name):
    name = os.path.splitext(os.path.basename(name))[0] or 'table'
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', name)
    return name[:48] or 'table'


def _table_key(entry):
    """
    A stable per-table directory name; two tables with the same short name
    from different paths still get separate directories.
    """
    digest = hashlib.sha1(entry['table_id'].encode('utf-8')).hexdigest()[:10]
    return '%s-%s' % (_safe_name(entry['table_name']), digest)


class StoredMemo(dict):
    """
    A memo loaded from (or about to be written to) a :class:`MemoStore`.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value

    @property
    def reference_count(self):
        return len(self['references'])

    @property
    def refs(self):
        return self['references']


class MemoStore(object):
    """
    Detached memo storage.

    ``MemoStore.from_source(source, directory, table_name=None)`` extracts
    memos and writes them out.  ``MemoStore(directory)`` loads an existing
    store.  Lookups use :meth:`find` (1-based original row number) or
    :meth:`find_record` (0-based physical record number).
    """

    def __init__(self, directory, extraction=None):
        self.directory = os.path.abspath(directory)
        self._entries = []
        self._missing = []
        self._referenced = {}
        self._loaded_payloads = {}
        if extraction is not None:
            self._from_extraction(extraction)
        elif os.path.exists(os.path.join(self.directory, MANIFEST_NAME)):
            self._load_manifest()

    @classmethod
    def from_source(cls, source, directory, table_name=None, overwrite=False):
        """
        Extract memos from ``source`` and persist them under ``directory``.
        """
        return cls.write(extract_memos(source, table_name=table_name),
                directory, overwrite=overwrite)

    @classmethod
    def write(cls, extraction, directory, overwrite=False):
        """
        Persist an existing :class:`MemoExtraction`.
        """
        store = cls(directory, extraction=extraction)
        store.save(overwrite=overwrite)
        return store

    def _from_extraction(self, extraction):
        for entry in extraction:
            self._entries.append(StoredMemo(
                    table_name=entry['table_name'],
                    table_id=entry['table_id'],
                    filename=entry['filename'],
                    block=entry['block'],
                    size=entry['size'],
                    payload=entry['payload'],
                    references=[self._stored_reference(ref) for ref in entry['references']],
                    missing=False,
                    reason=None,
                    path=None,
                    ))
        for entry in extraction.missing:
            stored = StoredMemo(
                    table_name=entry['table_name'],
                    table_id=entry['table_id'],
                    filename=entry['filename'],
                    block=entry['block'],
                    size=0,
                    payload=None,
                    references=[self._stored_reference(ref) for ref in entry['references']],
                    missing=True,
                    reason=entry['reason'],
                    raw_block=entry.get('raw_block'),
                    path=None,
                    )
            self._missing.append(stored)
        self._reindex()

    def _reindex(self):
        self._referenced = {}
        for entry in self._entries + self._missing:
            for ref in entry['references']:
                record_key = (entry['table_id'], ref['recno'], ref['field'])
                row_key = (entry['table_id'], ref['row_number'], ref['field'])
                self._referenced[('record',) + record_key] = entry
                self._referenced[('row',) + row_key] = entry
                # short table names resolve too, unless another table shares
                # the name (in which case the ambiguous key is not added)
                for alias in (ref['table_name'].upper(),
                        os.path.splitext(os.path.basename(entry['table_id']))[0].upper()):
                    alias_key = ('row', alias, ref['row_number'], ref['field'])
                    if alias_key in self._referenced and self._referenced[alias_key] is not entry:
                        self._referenced[alias_key] = None
                    elif alias_key not in self._referenced:
                        self._referenced[alias_key] = entry

    def save(self, overwrite=False):
        """
        Write payload files and the manifest.
        """
        if not os.path.isdir(self.directory):
            os.makedirs(self.directory)
        manifest_path = os.path.join(self.directory, MANIFEST_NAME)
        if os.path.exists(manifest_path) and not overwrite:
            raise DbfError("%r already contains a memo store; use overwrite=True" % self.directory)
        tables = OrderedDict()
        payloads = []
        missing = []
        for entry in self._entries:
            table_dir = _table_key(entry)
            directory = os.path.join(PAYLOAD_DIR, table_dir)
            absolute = os.path.join(self.directory, directory)
            if not os.path.isdir(absolute):
                os.makedirs(absolute)
            name = 'block-%d.bin' % entry['block']
            path = os.path.join(directory, name)
            absolute = os.path.join(self.directory, path)
            with open(absolute, 'wb') as fh:
                fh.write(entry['payload'])
            entry['path'] = path
            entry['sha256'] = hashlib.sha256(entry['payload']).hexdigest()
            tables.setdefault(entry['table_id'], {
                'table_name': entry['table_name'],
                'table_id': entry['table_id'],
                'dbf_filename': entry['filename'],
                })
            payloads.append({
                'table_name': entry['table_name'],
                'table_id': entry['table_id'],
                'block': entry['block'],
                'size': entry['size'],
                'sha256': entry['sha256'],
                'path': entry['path'],
                'references': self._reference_dicts(entry['references']),
                })
        for entry in self._missing:
            tables.setdefault(entry['table_id'], {
                'table_name': entry['table_name'],
                'table_id': entry['table_id'],
                'dbf_filename': entry['filename'],
                })
            missing.append({
                'table_name': entry['table_name'],
                'table_id': entry['table_id'],
                'block': entry['block'],
                'reason': entry['reason'],
                'raw_block': entry.get('raw_block'),
                'references': self._reference_dicts(entry['references']),
                })
        manifest = {
            'version': MANIFEST_VERSION,
            'tables': list(tables.values()),
            'payloads': payloads,
            'missing': missing,
            }
        with io.open(manifest_path, 'w', encoding='utf-8') as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write('\n')
        self._manifest = manifest
        return manifest_path

    def _reference_dicts(self, references):
        result = []
        for ref in references:
            result.append({
                'table_name': ref['table_name'],
                'table_id': ref['table_id'],
                'record': ref['recno'],
                'row_number': ref['row_number'],
                'field': ref['field'],
                'deleted': ref['deleted'],
                })
        return result

    def _load_manifest(self):
        manifest_path = os.path.join(self.directory, MANIFEST_NAME)
        with io.open(manifest_path, 'r', encoding='utf-8') as fh:
            manifest = json.load(fh)
        if manifest.get('version') != MANIFEST_VERSION:
            raise DbfError("%r is not a supported memo store" % self.directory)
        self._manifest = manifest
        self._entries = []
        self._missing = []
        for item in manifest.get('payloads', ()):
            self._entries.append(StoredMemo(
                    table_name=item['table_name'],
                    table_id=item['table_id'],
                    filename=item['table_id'],
                    block=item['block'],
                    size=item['size'],
                    payload=None,
                    path=item['path'],
                    sha256=item.get('sha256'),
                    references=[self._stored_reference(ref) for ref in item['references']],
                    missing=False,
                    reason=None,
                    ))
        for item in manifest.get('missing', ()):
            self._missing.append(StoredMemo(
                    table_name=item['table_name'],
                    table_id=item['table_id'],
                    filename=item['table_id'],
                    block=item['block'],
                    size=0,
                    payload=None,
                    path=None,
                    references=[self._stored_reference(ref) for ref in item['references']],
                    missing=True,
                    reason=item['reason'],
                    raw_block=item.get('raw_block'),
                    ))
        self._reindex()

    def _stored_reference(self, item):
        return StoredMemo(
                table_name=item['table_name'],
                table_id=item['table_id'],
                recno=item['record'],
                record=item['record'],
                row=item['record'],
                row_number=item['row_number'],
                field=item['field'],
                deleted=item.get('deleted', False),
                )

    def _load_payload(self, entry):
        if entry['payload'] is not None:
            return entry['payload']
        if entry.get('path') is None:
            return None
        key = (entry['table_id'], entry['block'])
        if key in self._loaded_payloads:
            return self._loaded_payloads[key]
        path = os.path.join(self.directory, entry['path'])
        with open(path, 'rb') as fh:
            payload = fh.read()
        self._loaded_payloads[key] = payload
        entry['payload'] = payload
        return payload

    @property
    def payloads(self):
        return list(self._entries)

    @property
    def missing(self):
        return list(self._missing)

    @property
    def entries(self):
        return list(self._entries)

    @property
    def tables(self):
        if not hasattr(self, '_manifest'):
            return []
        return [StoredMemo(**item) for item in self._manifest.get('tables', ())]

    def _resolve_table_id(self, table):
        from .tables import Table
        if isinstance(table, Table):
            return os.path.abspath(table.filename)
        if not isinstance(table, basestring):
            raise TypeError("%r is not a table or table name" % (table,))
        if table == ':memory:':
            return table
        if os.path.isabs(table) or os.sep in table or (os.altsep and os.altsep in table):
            return os.path.abspath(table)
        for entry in self._entries + self._missing:
            if entry['table_name'].lower() == table.lower():
                return entry['table_id']
        for entry in self._entries + self._missing:
            base = os.path.splitext(os.path.basename(entry['table_id']))[0]
            if base.lower() == table.lower():
                return entry['table_id']
        return os.path.abspath(table)

    def find(self, table, row, field):
        """
        Look up by table name, original 1-based row number, and field name.
        Returns a stored payload or a missing entry; ``None`` when the field
        had no memo at that row.
        """
        return self._lookup('row', table, row, field)

    def find_record(self, table, record, field):
        """
        Look up by table name, 0-based physical record number, and field.
        """
        return self._lookup('record', table, record, field)

    def find_ref(self, table, row, field):
        """
        Look up by table name, 1-based original row number, and field.
        ``row`` counts rows in the DBF file in physical order, including
        deleted rows, and matches ``dbf.recno(record) + 1``.
        """
        return self.find(table, row, field)

    def _lookup(self, base, table, number, field):
        field = field.upper()
        if isinstance(table, basestring) and not (
                os.path.isabs(table) or os.sep in table or (os.altsep and os.altsep in table)
                ):
            entry = self._referenced.get((base, table.upper(), number, field))
            if entry is not None:
                return entry
            elif (base, table.upper(), number, field) in self._referenced:
                # ambiguous short name shared by more than one stored table
                raise DbfError(
                        "table name %r is ambiguous in this memo store; "
                        "use the full filename" % (table,))
        table_id = self._resolve_table_id(table)
        return self._referenced.get((base, table_id, number, field))

    def payload(self, table, row, field):
        """
        Return the raw payload bytes for a row/field reference, or ``None``.
        Missing references also return ``None``; use :meth:`find` to tell
        them apart from "no memo".
        """
        entry = self.find(table, row, field)
        if entry is None or entry['missing']:
            return None
        return self._load_payload(entry)

    def references(self, table=None):
        """
        Every stored reference, optionally limited to one table.
        """
        entries = self._entries + self._missing if table is None else \
                [entry for entry in self._entries + self._missing
                 if entry['table_id'] == self._resolve_table_id(table)]
        result = []
        for entry in entries:
            for ref in entry['references']:
                result.append(ref)
        return result

    def missing_for(self, table=None):
        if table is None:
            return list(self._missing)
        table_id = self._resolve_table_id(table)
        return [entry for entry in self._missing if entry['table_id'] == table_id]

    def manifest(self):
        if not hasattr(self, '_manifest'):
            raise DbfError("memo store %r has not been saved yet" % self.directory)
        return self._manifest

    def __len__(self):
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __getitem__(self, item):
        if isinstance(item, tuple):
            if len(item) == 3:
                return self.find(*item)
            raise TypeError("memo store index must be (table, row, field)")
        return self._entries[item]


def save_memos(source, directory, table_name=None, overwrite=False):
    """
    Extract memos from ``source`` and save them under ``directory``.
    """
    return MemoStore.from_source(source, directory, table_name=table_name,
            overwrite=overwrite)


def load_memos(directory):
    """
    Open a previously saved memo store.
    """
    return MemoStore(directory)


def find_memo(directory, table, row, field):
    """
    Look up a saved memo by table name, original row number, and field.
    Returns the stored entry (with its payload loaded) or ``None``.
    """
    store = MemoStore(directory)
    entry = store.find(table, row, field)
    if entry is not None and not entry['missing']:
        store._load_payload(entry)
    return entry
