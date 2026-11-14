"""
Tests for DBT memo payload extraction.
"""

from __future__ import print_function

import os
import shutil
import tempfile
import unittest

import hashlib
import json

import dbf
from dbf.constants import END, START
from dbf.memos import DBT_BLOCK_SIZE, MEMO_TERMINATOR

tempdir = tempfile.mkdtemp()


def make_table(name, rows, fields='name C(10); note M'):
    filename = os.path.join(tempdir, name)
    table = dbf.Table(filename, fields, dbf_type='db3', overwrite=True)
    table.open(dbf.READ_WRITE)
    for row in rows:
        table.append(row)
    table.close()
    return filename


def dbt_name(filename):
    # dbf supports a table whose name has no extension, so the memo file is
    # the same base name plus '.dbt'
    return os.path.splitext(filename)[0] + '.dbt'


def read_dbt(filename):
    with open(dbt_name(filename), 'rb') as fh:
        return fh.read()


def write_dbt(filename, data):
    with open(dbt_name(filename), 'wb') as fh:
        fh.write(data)


def memo_blocks(data):
    blocks = {}
    block = 1
    total = (len(data) + DBT_BLOCK_SIZE - 1) // DBT_BLOCK_SIZE
    while block < total:
        chunk = data[block * DBT_BLOCK_SIZE:]
        pos = chunk.find(MEMO_TERMINATOR)
        if pos < 0:
            break
        blocks[block] = chunk[:pos]
        used = block * DBT_BLOCK_SIZE + pos + len(MEMO_TERMINATOR)
        block = max((used + DBT_BLOCK_SIZE - 1) // DBT_BLOCK_SIZE, block + 1)
    return blocks


def _open(table):
    if isinstance(table, str):
        table = dbf.Table(table)
        table.open(dbf.READ_ONLY)
        return table, True
    if table.status == dbf.CLOSED:
        table.open(dbf.READ_ONLY)
        return table, True
    return table, False


def pointer_of(table, recno, field='NOTE'):
    table, owned = _open(table)
    try:
        fielddef = table._meta[field]
        token = bytes(table[recno]._data[fielddef[START]:fielddef[END]]).strip()
        return int(token) if token else 0
    finally:
        if owned:
            table.close()


def save_pointer(table, recno, block, field='NOTE'):
    from array import array
    table, owned = _open(table)
    if table.status != dbf.READ_WRITE:
        table.close()
        table.open(dbf.READ_WRITE)
        owned = True
    try:
        fielddef = table._meta[field]
        start, end = fielddef[START], fielddef[END]
        record = table[recno]
        record._data[start:end] = array(
                'B', ('%*s' % (end - start, block)).encode('ascii'))
        record._dirty = True
        record._update_disk()
    finally:
        if owned:
            table.close()


class Base(unittest.TestCase):

    def setUp(self):
        self.files = []

    def tearDown(self):
        for name in self.files:
            for ext in ('.dbf', '.dbt'):
                try:
                    os.remove(os.path.splitext(name)[0] + ext)
                except OSError:
                    pass
            try:
                os.remove(name)
            except OSError:
                pass

    def make(self, name, rows, fields='name C(10); note M'):
        filename = make_table(name, rows, fields=fields)
        self.files.append(filename)
        return filename


class TestExtraction(Base):

    def test_simple_payload_and_reference(self):
        filename = self.make('simple', [('a', 'first'), ('b', 'second')])
        result = dbf.extract_memos(filename)
        self.assertEqual(2, len(result))
        self.assertEqual([], result.missing)
        first = result.find(filename, 0, 'note')
        self.assertIsNotNone(first)
        self.assertEqual(b'first', first.payload)
        self.assertFalse(first.missing)
        self.assertEqual(0, first.references[0].recno)
        self.assertEqual(1, first.references[0].row_number)
        self.assertEqual('NOTE', first.references[0].field)
        self.assertEqual(
                os.path.splitext(os.path.basename(filename))[0],
                first.table_name,
                )

    def test_table_method(self):
        filename = self.make('method', [('a', 'one')])
        table = dbf.Table(filename)
        result = table.extract_memos()
        self.assertEqual(b'one', result[0].payload)

    def test_references_are_deduplicated_per_payload(self):
        filename = self.make('dupes', [('a', 'shared'), ('b', 'different'), ('c', 'other')])
        shared_block = pointer_of(dbf.Table(filename), 0)
        save_pointer(dbf.Table(filename), 1, shared_block)
        result = dbf.extract_memos(filename)
        shared = [entry for entry in result if entry.payload == b'shared']
        self.assertEqual(1, len(shared))
        self.assertEqual(2, shared[0].reference_count)
        self.assertEqual([0, 1], [ref.recno for ref in shared[0].references])
        self.assertEqual([1, 2], [ref.row_number for ref in shared[0].references])
        # the now-orphaned memo is not referenced, so it is not delivered
        self.assertNotIn(b'different', [entry.payload for entry in result])

    def test_same_block_number_in_different_tables_is_not_shared(self):
        one = self.make('table_one', [('a', 'alpha')])
        two = self.make('table_two', [('a', 'beta')])
        self.assertEqual(set(memo_blocks(read_dbt(one))), set(memo_blocks(read_dbt(two))))
        result = dbf.extract_memos([one, two])
        self.assertEqual(2, len(result))
        alpha = result.find(one, 0, 'note')
        beta = result.find(two, 0, 'note')
        self.assertIsNot(alpha, beta)
        self.assertEqual(b'alpha', alpha.payload)
        self.assertEqual(b'beta', beta.payload)
        self.assertEqual(b'alpha', result.by_block(one, 1).payload)
        self.assertEqual(b'beta', result.by_block(two, 1).payload)

    def test_payload_stops_at_logical_terminator_and_skips_padding(self):
        filename = self.make('bounds', [('a', 'payload')])
        block = pointer_of(dbf.Table(filename), 0)
        data = bytearray(read_dbt(filename))
        payload = b'payload'
        start = block * DBT_BLOCK_SIZE
        data[start:start + len(payload)] = payload
        data[start + len(payload):start + len(payload) + 2] = MEMO_TERMINATOR
        data[start + len(payload) + 2:start + DBT_BLOCK_SIZE] = b'\x00' * (
                DBT_BLOCK_SIZE - len(payload) - 2)
        write_dbt(filename, bytes(data))
        result = dbf.extract_memos(filename)
        self.assertEqual(b'payload', result[0].payload)
        self.assertNotIn(b'\x00', result[0].payload)

    def test_binary_payload_preserves_original_bytes(self):
        filename = self.make('binary', [('a', 'placeholder')])
        block = pointer_of(dbf.Table(filename), 0)
        raw = b'\x80\x81\xfe\xff' + b'caf\xe9'
        data = bytearray(read_dbt(filename))
        start = block * DBT_BLOCK_SIZE
        data[start:start + len(raw)] = raw
        data[start + len(raw):start + len(raw) + 2] = MEMO_TERMINATOR
        write_dbt(filename, bytes(data))
        result = dbf.extract_memos(filename)
        self.assertEqual(raw, result[0].payload)
        self.assertIsInstance(result[0].payload, bytes)

    def test_multiblock_payload_terminates_before_next_memo(self):
        long_text = 'x' * 1200
        filename = self.make('multiblock', [('a', long_text), ('b', 'tail')])
        result = dbf.extract_memos(filename)
        self.assertEqual(2, len(result))
        values = dict((entry.payload, entry) for entry in result)
        self.assertIn(long_text.encode('ascii'), values)
        self.assertIn(b'tail', values)
        long_entry = values[long_text.encode('ascii')]
        self.assertEqual(0, long_entry.references[0].recno)
        tail_entry = values[b'tail']
        self.assertEqual(1, tail_entry.references[0].recno)

    def test_bad_reference_out_of_range_is_missing_and_does_not_absorb_next_memo(self):
        filename = self.make('badblock', [('a', 'first'), ('b', 'second')])
        unused_block = (len(read_dbt(filename)) // DBT_BLOCK_SIZE) + 1
        save_pointer(dbf.Table(filename), 0, unused_block)
        result = dbf.extract_memos(filename)
        self.assertEqual([b'second'], [entry.payload for entry in result])
        self.assertEqual(1, len(result.missing))
        missing = result.missing[0]
        self.assertTrue(missing.missing)
        self.assertEqual('out_of_range', missing.reason)
        self.assertEqual(0, missing.references[0].recno)
        self.assertIsNone(missing.payload)
        # the bad row is not attached to the next memo
        self.assertEqual(
                [1],
                [ref.recno for ref in result.find(filename, 1, 'note').references],
                )

    def test_reference_into_middle_of_memo_is_missing(self):
        long_text = 'y' * 1200
        filename = self.make('middle', [('a', long_text), ('b', 'after')])
        table = dbf.Table(filename)
        first_block = pointer_of(table, 0)
        save_pointer(table, 1, first_block + 1)
        result = dbf.extract_memos(filename)
        self.assertEqual(1, len(result))
        self.assertEqual(long_text.encode('ascii'), result[0].payload)
        self.assertEqual(1, len(result.missing))
        self.assertEqual('bad_block', result.missing[0].reason)
        self.assertEqual(1, result.missing[0].references[0].recno)

    def test_damaged_block_is_missing_not_merged_with_next_memo(self):
        filename = self.make('damaged', [('a', 'first'), ('b', 'second')])
        table = dbf.Table(filename)
        first_block = pointer_of(table, 0)
        data = bytearray(read_dbt(filename))
        start = first_block * DBT_BLOCK_SIZE
        # the memo at the first block loses its terminator and is filled with
        # padding; the next memo sits intact in the following block
        data[start:start + DBT_BLOCK_SIZE] = b'first' + b'\x00' * (DBT_BLOCK_SIZE - 5)
        write_dbt(filename, bytes(data))
        result = dbf.extract_memos(filename)
        # the damaged block is missing; the second memo keeps its own payload
        self.assertEqual([b'second'], [entry.payload for entry in result])
        missing = [entry for entry in result.missing if entry.block == first_block]
        self.assertEqual(1, len(missing))
        self.assertEqual('bad_block', missing[0].reason)
        self.assertEqual(0, missing[0].references[0].recno)

    def test_empty_memo_is_delivered_as_empty_payload(self):
        filename = self.make('zero', [('a', '')])
        result = dbf.extract_memos(filename)
        # an empty memo is still a memo: immediate terminator, empty payload
        self.assertEqual(1, len(result))
        self.assertEqual(b'', result[0].payload)
        self.assertEqual([], result.missing)

    def test_block_with_only_padding_is_missing(self):
        filename = self.make('padding', [('a', 'real')])
        block = pointer_of(dbf.Table(filename), 0)
        data = bytearray(read_dbt(filename))
        start = block * DBT_BLOCK_SIZE
        data[start:start + DBT_BLOCK_SIZE] = b'\x00' * DBT_BLOCK_SIZE
        write_dbt(filename, bytes(data))
        result = dbf.extract_memos(filename)
        self.assertEqual([], list(result))
        self.assertEqual(1, len(result.missing))
        self.assertEqual('bad_block', result.missing[0].reason)

    def test_vfp_is_rejected(self):
        filename = os.path.join(tempdir, 'vfp_memo')
        table = dbf.Table(filename, 'name C(10); note M', dbf_type='vfp', overwrite=True)
        table.open(dbf.READ_WRITE)
        table.append(('a', 'text'))
        table.close()
        self.files.append(filename)
        self.assertRaises(dbf.DbfError, dbf.extract_memos, filename)

    def test_deleted_row_is_still_a_reference(self):
        filename = self.make('deleted', [('a', 'kept'), ('b', 'gone')])
        table = dbf.Table(filename)
        table.open(dbf.READ_WRITE)
        dbf.delete(table[1])
        table.close()
        result = dbf.extract_memos(filename)
        self.assertEqual(2, len(result))
        gone = [entry for entry in result if entry.payload == b'gone'][0]
        self.assertTrue(gone.references[0].deleted)

    def test_manifest_lists_all_references(self):
        filename = self.make('manifest', [('a', 'same'), ('b', 'other')])
        table = dbf.Table(filename)
        block = pointer_of(table, 0)
        save_pointer(table, 1, block)
        manifest = dbf.extract_memos(filename).manifest()
        self.assertEqual(1, len(manifest['payloads']))
        self.assertEqual(2, len(manifest['payloads'][0]['references']))
        self.assertEqual([0, 1], [ref['record'] for ref in manifest['payloads'][0]['references']])
        self.assertEqual(os.path.abspath(filename), manifest['tables'][0]['filename'])

    def test_missing_dbt_reports_every_reference_as_missing(self):
        filename = self.make('nodbt', [('a', 'x'), ('b', 'y')])
        os.remove(dbt_name(filename))
        result = dbf.extract_memos(filename)
        self.assertEqual([], list(result))
        # each referenced block is reported separately
        self.assertEqual(
                set([1, 2]),
                set(entry.block for entry in result.missing),
                )
        self.assertTrue(all(entry.reason == 'memo_file_missing' for entry in result.missing))

    def test_reference_to_unused_block_is_missing(self):
        filename = self.make('unused', [('a', 'real')])
        save_pointer(dbf.Table(filename), 0, 7)
        result = dbf.extract_memos(filename)
        self.assertEqual([], list(result))
        self.assertEqual(1, len(result.missing))
        self.assertEqual('out_of_range', result.missing[0].reason)
        self.assertEqual(7, result.missing[0].block)


class TestMemoStore(Base):

    def setUp(self):
        Base.setUp(self)
        self.directory = os.path.join(tempdir, 'store-%s' % self._testMethodName)
        shutil.rmtree(self.directory, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)
        Base.tearDown(self)

    def test_round_trip_lookup_by_name_row_field(self):
        filename = self.make('stored', [('a', 'alpha'), ('b', 'beta')])
        store = dbf.save_memos(filename, self.directory)
        self.assertEqual(2, len(store))
        self.assertEqual(b'alpha', store.payload('stored', 1, 'note'))
        self.assertEqual(b'beta', store.payload('stored', 2, 'NOTE'))
        self.assertIsNone(store.find('stored', 1, 'name'))
        # a fresh load from disk resolves the same references
        loaded = dbf.load_memos(self.directory)
        self.assertEqual(b'alpha', loaded.payload('stored', 1, 'note'))
        self.assertEqual(b'beta', loaded.payload('stored', 2, 'note'))
        self.assertEqual(b'alpha', dbf.find_memo(
                self.directory, 'stored', 1, 'note').payload)

    def test_duplicate_references_write_one_payload_file(self):
        filename = self.make('shared', [('a', 'same'), ('b', 'other')])
        table = dbf.Table(filename)
        save_pointer(table, 1, pointer_of(table, 0))
        store = dbf.save_memos(filename, self.directory)
        self.assertEqual(1, len(store))
        entry = store.payloads[0]
        self.assertEqual(2, entry.reference_count)
        payload_dir = os.path.dirname(os.path.join(self.directory, entry.path))
        self.assertEqual(1, len(os.listdir(payload_dir)))
        self.assertEqual(b'same', store.payload('shared', 1, 'note'))
        self.assertEqual(b'same', store.payload('shared', 2, 'note'))

    def test_same_block_number_in_other_table_does_not_collide(self):
        one = self.make('one', [('a', 'alpha')])
        two = self.make('two', [('a', 'beta')])
        store = dbf.save_memos([one, two], self.directory)
        self.assertEqual(2, len(store))
        self.assertEqual(b'alpha', store.payload('one', 1, 'note'))
        self.assertEqual(b'beta', store.payload('two', 1, 'note'))
        self.assertEqual(1, store.find('one', 1, 'note').block)
        self.assertEqual(1, store.find('two', 1, 'note').block)
        self.assertIsNot(store.find('one', 1, 'note'), store.find('two', 1, 'note'))

    def test_missing_reference_persisted_as_missing(self):
        filename = self.make('stored_missing', [('a', 'first'), ('b', 'second')])
        save_pointer(dbf.Table(filename), 0, 99)
        store = dbf.save_memos(filename, self.directory)
        self.assertEqual(1, len(store))
        self.assertEqual([b'second'], [entry.payload for entry in store])
        entry = store.find('stored_missing', 1, 'note')
        self.assertTrue(entry['missing'])
        self.assertIsNone(entry['path'])
        self.assertIsNone(store.payload('stored_missing', 1, 'note'))
        loaded = dbf.load_memos(self.directory)
        self.assertTrue(loaded.find('stored_missing', 1, 'note')['missing'])
        self.assertEqual(1, len(loaded.missing_for('stored_missing')))
        self.assertEqual(b'second', loaded.payload('stored_missing', 2, 'note'))

    def test_manifest_records_payload_checksum_and_path(self):
        filename = self.make('checksum', [('a', 'payload')])
        dbf.save_memos(filename, self.directory)
        manifest = json.load(open(os.path.join(self.directory, 'manifest.json')))
        entry = manifest['payloads'][0]
        self.assertEqual(hashlib.sha256(b'payload').hexdigest(), entry['sha256'])
        self.assertTrue(os.path.exists(os.path.join(self.directory, entry['path'])))

    def test_existing_store_requires_overwrite(self):
        filename = self.make('overwrite', [('a', 'payload')])
        dbf.save_memos(filename, self.directory)
        self.assertRaises(dbf.DbfError, dbf.save_memos, filename, self.directory)
        dbf.save_memos(filename, self.directory, overwrite=True)


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        shutil.rmtree(tempdir, True)
