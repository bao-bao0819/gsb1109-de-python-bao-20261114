"""
Tests for olefile
"""

from __future__ import print_function
import unittest

import os, sys
import io
import struct
from shutil import copy2

# Insert the olefile package relative to this test dir in the sys.path
# to make sure we test the right one, in case another olefile version
# is also installed in the main python path.
_thismodule_dir = os.path.normpath(os.path.abspath(os.path.dirname(__file__)))
# print('_thismodule_dir = %r' % _thismodule_dir)
_parent_dir = os.path.normpath(os.path.join(_thismodule_dir, '..'))
# print('_parent_dir = %r' % _thirdparty_dir)
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import olefile


class Test_isOleFile(unittest.TestCase):
    'Tests for the isOleFile function'

    def setUp(self):
        self.non_ole_file = u"tests/images/flower.jpg"
        self.ole_file = u"tests/images/test-ole-file.doc"

    def test_isOleFile_str_path_false(self):
        'Test isOleFile with a unicode filename of a non-OLE file'
        is_ole = olefile.isOleFile(filename=self.non_ole_file)
        self.assertFalse(is_ole)

    def test_isOleFile_str_path_true(self):
        'Test isOleFile with a unicode filename of an OLE file'
        is_ole = olefile.isOleFile(filename=self.ole_file)
        self.assertTrue(is_ole)

    def test_isOleFile_bytes_path_false(self):
        'Test isOleFile with a bytes filename of a non-OLE file'
        is_ole = olefile.isOleFile(filename=self.non_ole_file.encode('latin_1'))
        self.assertFalse(is_ole)

    def test_isOleFile_bytes_path_true(self):
        'Test isOleFile with a bytes filename of an OLE file'
        is_ole = olefile.isOleFile(filename=self.ole_file.encode('latin_1'))
        self.assertTrue(is_ole)

    def test_isOleFile_bytes_string_false(self):
        'Test isOleFile with contents of a non-OLE file in a bytes string as filename parameter'
        with open(self.non_ole_file, 'rb') as f:
            data = f.read()
        assert (len(data) >= olefile.MINIMAL_OLEFILE_SIZE)
        is_ole = olefile.isOleFile(filename=data)
        self.assertFalse(is_ole)

    def test_isOleFile_small_bytes_string_false(self):
        'Test isOleFile with contents of a small non-OLE file (<1536b) in a bytes string as filename parameter'
        data = b" " * 100
        # In this case, we expect a FileNotFoundError exception, because isOleFile treats filename as a path
        # due to issue #142
        if sys.version_info[0] < 3:
            # On Python 2.x, this case raises an IOError exception:
            expected_exception = IOError
        else:
            # On Python 3.x, this case raises a FileNotFoundError exception:
            expected_exception = FileNotFoundError
        with self.assertRaises(expected_exception):
            is_ole = olefile.isOleFile(filename=data)

    def test_isOleFile_bytes_string_true(self):
        'Test isOleFile with contents of an OLE file in a bytes string as filename parameter'
        with open(self.ole_file, 'rb') as f:
            data = f.read()
        assert(len(data) >= olefile.MINIMAL_OLEFILE_SIZE)
        is_ole = olefile.isOleFile(filename=data)
        self.assertTrue(is_ole)

    def test_isOleFile_bytes_data_false(self):
        'Test isOleFile with contents of a non-OLE file in a bytes string as data parameter'
        with open(self.non_ole_file, 'rb') as f:
            data = f.read()
        is_ole = olefile.isOleFile(data=data)
        self.assertFalse(is_ole)

    def test_isOleFile_bytes_data_true(self):
        'Test isOleFile with contents of an OLE file in a bytes string as data parameter'
        with open(self.ole_file, 'rb') as f:
            data = f.read()
        is_ole = olefile.isOleFile(data=data)
        self.assertTrue(is_ole)

    def test_isOleFile_small_bytes_data_false(self):
        'Test isOleFile with contents of a small non-OLE file (<1536b) in a bytes string as data parameter'
        data = b" " * 100
        is_ole = olefile.isOleFile(data=data)
        self.assertFalse(is_ole)

    def test_isOleFile_small_bytes_ole_magic_data_false(self):
        'Test isOleFile with contents of a small non-OLE file (<1536b) starting with OLE magic in a bytes string as data parameter'
        with open(self.ole_file, 'rb') as f:
            data = f.read(100)
        is_ole = olefile.isOleFile(data=data)
        self.assertFalse(is_ole)

    def test_isOleFile_file_false(self):
        'Test isOleFile with a non-OLE file object as filename parameter'
        with open(self.non_ole_file, 'rb') as f:
            is_ole = olefile.isOleFile(filename=f)
        self.assertFalse(is_ole)

    def test_isOleFile_file_true(self):
        'Test isOleFile with an OLE file object as filename parameter'
        with open(self.ole_file, 'rb') as f:
            is_ole = olefile.isOleFile(filename=f)
        self.assertTrue(is_ole)


class TestOleFileIO(unittest.TestCase):
    'Tests for the OleFileIO class'

    def setUp(self):
        self.non_ole_file = u"tests/images/flower.jpg"
        self.ole_file = u"tests/images/test-ole-file.doc"

    def test_context_manager(self):
        with olefile.OleFileIO(self.ole_file) as ole:
            exists = ole.exists('worddocument')
            self.assertTrue(exists)

    def test_exists_worddocument(self):
        ole = olefile.OleFileIO(self.ole_file)
        exists = ole.exists('worddocument')
        self.assertTrue(exists)
        ole.close()

    def test_exists_no_vba_macros(self):
        ole = olefile.OleFileIO(self.ole_file)
        exists = ole.exists('macros/vba')
        self.assertFalse(exists)
        ole.close()

    def test_get_type(self):
        ole = olefile.OleFileIO(self.ole_file)
        doc_type = ole.get_type('worddocument')
        self.assertEqual(doc_type, olefile.STGTY_STREAM)
        ole.close()

    def test_get_size(self):
        ole = olefile.OleFileIO(self.ole_file)
        size = ole.get_size('worddocument')
        self.assertGreater(size, 0)
        ole.close()

    def test_get_rootentry_name(self):
        ole = olefile.OleFileIO(self.ole_file)
        root = ole.get_rootentry_name()
        self.assertEqual(root, "Root Entry")
        ole.close()

    def test_meta(self):
        ole = olefile.OleFileIO(self.ole_file)
        meta = ole.get_metadata()
        self.assertEqual(meta.author, b"Laurence Ipsum")
        self.assertEqual(meta.num_pages, 1)
        ole.close()

    def test_minifat_writing(self):
        ole_file_copy = "tests/images/test-ole-file-copy.doc"
        minifat_stream_name = "\x01compobj"
        if os.path.isfile(ole_file_copy):
            os.remove(ole_file_copy)
        copy2(self.ole_file, ole_file_copy)

        ole = olefile.OleFileIO(ole_file_copy, write_mode = True)
        stream = ole.openstream(minifat_stream_name)
        self.assertTrue(stream.size < ole.minisectorcutoff)
        str_read = stream.read()
        self.assertTrue(len(str_read) == stream.size)
        self.assertTrue(str_read != b'\x00' * stream.size)
        stream.close()
        ole.write_stream(minifat_stream_name, b'\x00' * stream.size)
        ole.close()

        ole = olefile.OleFileIO(ole_file_copy)
        stream = ole.openstream(minifat_stream_name)
        self.assertTrue(stream.size < ole.minisectorcutoff)
        str_read_replaced = stream.read()
        self.assertTrue(len(str_read_replaced) == stream.size)
        self.assertTrue(str_read_replaced != str_read)
        self.assertTrue(str_read_replaced == b'\x00' * len(str_read))
        stream.close()
        ole.close()
        os.remove(ole_file_copy)

    def test_write_sect_too_large(self):
        'Test write_sect with data larger than a sector'
        # read OLE file in memory to avoid writing to disk
        with open(self.ole_file, 'rb') as f:
            file_in_memory = io.BytesIO(f.read())
        with olefile.OleFileIO(file_in_memory, write_mode=True) as ole:
            size = ole.sector_size + 1
            # Attempt to overwrite last sector (ole.nb_sect - 1) with data too large
            # it should raise ValueError:
            with self.assertRaises(ValueError):
                ole.write_sect(ole.nb_sect - 1, b"x" * size)


class FileHandleCloseTest(unittest.TestCase):
    """Test file handles are closed correctly."""

    def setUp(self):
        self.non_ole_file = "tests/images/flower.jpg"
        self.ole_file = "tests/images/test-ole-file.doc"

    def leaking_test_function(self):
        """Function that leaks an open OleFileIO."""
        ole = olefile.OleFileIO(self.ole_file)

    @unittest.skip('Cannot predict when __del__ is run, so cannot test that '
                   'it issues a warning')
    # requires python version 3.2 or higher
    def test_warning(self):
        """Test that warning is issued when ole file leaks open fp."""
        with self.assertWarns(olefile.OleFileIONotClosed):
            self.leaking_test_function()

    @unittest.skip('Cannot test attribute fp of OleFileIO instance that '
                   'failed to construct')
    def test_init_fail(self):
        """Test that file handle is closed if open() from __init__ fails."""
        ole = None
        try:
            ole = olefile.OleFileIO(self.non_ole_file)
            self.fail('Should have raised an exception')
        except Exception as exc:
            self.assertEqual(str(exc), 'not an OLE2 structured storage file')
            self.assertTrue(ole.fp.closed)   # ole is still None

    def test_context_manager(self):
        """Test that file handle is closed by context manager."""
        file_handle = None
        with olefile.OleFileIO(self.ole_file) as ole:
            file_handle = ole.fp
            self.assertFalse(file_handle.closed)
        self.assertTrue(file_handle.closed)

    def test_manual(self):
        """Test that simple manual close always closes self-created handle."""
        ole = olefile.OleFileIO(self.ole_file)
        self.assertFalse(ole.fp.closed)
        _ = ole.listdir()
        self.assertFalse(ole.fp.closed)
        ole.close()
        self.assertTrue(ole.fp.closed)

    # TODO: check if this test is still relevant
    # def test_fp_stays_open(self):
    #     """Test that fp is not automatically closed if provided by caller."""
    #     with open(self.ole_file, 'rb') as file_handle:
    #         self.assertFalse(file_handle.closed)
    #         with olefile.OleFileIO(file_handle) as ole:
    #             self.assertFalse(file_handle.closed)
    #             self.assertEqual(file_handle, ole.fp)
    #         # check that handle is still open, although ole is now closed
    #         self.assertFalse(file_handle.closed)
    #
    #         # do something with it
    #         file_handle.seek(0)
    #         self.assertTrue(olefile.isOleFile(file_handle))
    #
    #     # now should be closed
    #     self.assertTrue(file_handle.closed)




# --- helpers to build small OLE containers for extraction tests ---------------

def _direntry(name, entry_type, start, size, left=olefile.NOSTREAM,
              right=olefile.NOSTREAM, child=olefile.NOSTREAM):
    encoded = name.encode('utf-16le')
    name_raw = (encoded + b'\x00\x00').ljust(64, b'\x00')
    return struct.pack(
        '<64sHBBIII16sIQQIII', name_raw, len(encoded) + 2, entry_type, 1,
        left, right, child, b'\x00' * 16, 0, 0, 0, start,
        size & 0xFFFFFFFF, size >> 32)


def build_ole(entries):
    """Build a minimal CFB v3 container.

    ``entries`` is a list of ``(name, data, share_key)`` tuples. Entries with
    the same non-None share_key reference the same physical stream, which is
    used to test duplicate references. Returns the container bytes.
    """
    sector_size = 512
    mini_size = 64
    cutoff = 4096
    end = olefile.ENDOFCHAIN

    # Allocate mini streams (payload < cutoff) and regular streams.
    allocation = {}
    mini_stream = bytearray()
    minifat = []
    unique = []
    for name, data, share_key in entries:
        key = share_key if share_key is not None else name
        if key in allocation:
            continue
        if len(data) < cutoff:
            start_mini = len(minifat)
            nb = (len(data) + mini_size - 1) // mini_size
            for i in range(nb):
                minifat.append(start_mini + i + 1 if i + 1 < nb else end)
            allocation[key] = ('mini', start_mini, len(data))
            mini_stream += data
            mini_stream += b'\x00' * (nb * mini_size - len(data))
        else:
            allocation[key] = ('fat', None, len(data))
        unique.append((key, data))

    regular = [(key, data) for key, data in unique
               if allocation[key][0] == 'fat']
    regular_payloads = {}
    nb_dir = (1 + len(entries)) * 128
    dir_sectors = (nb_dir + sector_size - 1) // sector_size
    minifat_sectors = (len(minifat) * 4 + sector_size - 1) // sector_size
    mini_sectors = (len(mini_stream) + sector_size - 1) // sector_size
    regular_sectors = sum((len(data) + sector_size - 1) // sector_size
                          for _, data in regular)

    nb_data_sectors = dir_sectors + minifat_sectors + mini_sectors + regular_sectors
    nb_fat = 1
    while True:
        nb_sectors = nb_fat + nb_data_sectors
        needed = (nb_sectors + 127) // 128
        if needed <= nb_fat:
            break
        nb_fat = needed

    fat = [olefile.FREESECT] * (nb_fat * 128)
    for i in range(nb_fat):
        fat[i] = olefile.FATSECT
    position = nb_fat

    def chain(start, count):
        for i in range(count):
            fat[start + i] = start + i + 1 if i + 1 < count else end

    dir_start = position
    chain(dir_start, dir_sectors)
    position += dir_sectors
    minifat_start = position if minifat_sectors else end
    if minifat_sectors:
        chain(minifat_start, minifat_sectors)
        position += minifat_sectors
    mini_start = position if mini_sectors else end
    if mini_sectors:
        chain(mini_start, mini_sectors)
        position += mini_sectors
    for key, data in regular:
        count = (len(data) + sector_size - 1) // sector_size
        start = position
        chain(start, count)
        allocation[key] = ('fat', start, len(data))
        regular_payloads[start] = data
        position += count
    nb_sectors = position
    fat = fat[:nb_sectors]

    # Directory entries: root with a right-linked chain of children.
    direntries = [_direntry('Root Entry', olefile.STGTY_ROOT, mini_start,
                            len(mini_stream), child=1 if entries else end)]
    for index, (name, data, share_key) in enumerate(entries):
        key = share_key if share_key is not None else name
        kind, start, size = allocation[key]
        right = index + 2 if index + 1 < len(entries) else end
        direntries.append(_direntry(name, olefile.STGTY_STREAM, start, size,
                                    right=right))
    directory = b''.join(direntries).ljust(dir_sectors * sector_size, b'\x00')

    header = struct.pack(
        '<8s16sHHHHHHLLLLLLLLLL', olefile.MAGIC, b'\x00' * 16, 62, 3,
        0xFFFE, 9, 6, 0, 0, 0, nb_fat, dir_start, 0, cutoff,
        minifat_start, minifat_sectors, end, 0)
    difat = [i for i in range(nb_fat)] + [olefile.FREESECT] * (109 - nb_fat)
    header += struct.pack('<109L', *difat)
    assert len(header) == 512

    fat_bytes = struct.pack('<%dL' % len(fat), *fat)
    fat_bytes = fat_bytes.ljust(nb_fat * sector_size, b'\xff')
    minifat_bytes = struct.pack('<%dL' % len(minifat), *minifat) if minifat else b''
    minifat_bytes = minifat_bytes.ljust(minifat_sectors * sector_size, b'\xff')

    body = bytearray(fat_bytes)
    body += directory
    body += minifat_bytes
    body += bytes(mini_stream).ljust(mini_sectors * sector_size, b'\x00')
    # Append regular payloads at the offsets allocated above.
    for start in sorted(regular_payloads):
        data = regular_payloads[start]
        expected = (start - nb_fat) * sector_size
        body += b'\x00' * (expected - len(body))
        body += data.ljust(((len(data) + 511) // 512) * 512, b'\x00')
    assert len(body) == nb_sectors * sector_size
    return header + bytes(body)


class TestExtractAttachments(unittest.TestCase):
    'Tests for recursive attachment extraction'

    def setUp(self):
        self.doc = u"tests/images/test-ole-file.doc"

    def _entry_names(self, result):
        return [item.path for item in result.streams]

    def test_flat_extraction_is_path_ordered(self):
        outer = build_ole([('b.txt', b'b' * 10, None),
                           ('a.txt', b'a' * 10, None)])
        result = olefile.extract_attachments(outer, max_depth=0)
        self.assertEqual(self._entry_names(result),
                         [['a.txt'], ['b.txt']])
        self.assertEqual(result.unpacked_bytes, 20)
        self.assertEqual(result.pending, [])

    def test_nested_extraction_and_depth_limit(self):
        inner = build_ole([('inside.txt', b'hello', None)])
        outer = build_ole([('att.bin', inner, None),
                           ('tail.txt', b'tail', None)])
        result = olefile.extract_attachments(outer, max_depth=1)
        self.assertEqual(self._entry_names(result), [
            ['att.bin'], ['att.bin', 'inside.txt'], ['tail.txt']])
        self.assertEqual(result.unpacked_bytes, len(inner) + 4 + 5)

        limited = olefile.extract_attachments(outer, max_depth=0)
        self.assertEqual(self._entry_names(limited), [['att.bin'], ['tail.txt']])
        self.assertEqual([item.path for item in limited.pending],
                         [['att.bin', 'inside.txt']])
        self.assertEqual(limited.pending[0].reason, 'depth')

    def test_corrupt_stream_does_not_charge_unread_bytes(self):
        # Declare a 4096-byte stream but only append 512 physical bytes,
        # then add a following stream so the run continues.
        outer = build_ole([('att.bin', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
                             + b'\x00' * 4088, None),
                           ('tail.txt', b'x' * 20, None)])
        # Patch att.bin's directory size to 4096 while only 512 bytes are
        # present in its sector chain. build_ole already allocated its
        # sectors for 4096 bytes, so physically truncate the container by
        # the missing sectors instead.
        truncated = bytes(outer[:-512])
        result = olefile.extract_attachments(
            truncated, max_depth=0,
            max_unpacked_bytes=len(outer) - 1024)
        by_path = result.streams_by_path
        self.assertIn(('att.bin',), by_path)
        attachment = by_path[('att.bin',)]
        # The declared size must never be charged; only real bytes count.
        self.assertLess(attachment.size, 4096)
        # 3584 bytes for the damaged 4096-byte stream plus 20 for tail.txt;
        # the 512 unread bytes must not be charged.
        self.assertEqual(result.unpacked_bytes,
                         attachment.size + len('x' * 20))
        self.assertTrue(result.errors)

    def test_duplicate_reference_read_once_but_both_paths_listed(self):
        payload = b'x' * 100
        outer = build_ole([('first.bin', payload, 'shared'),
                           ('second.bin', payload, 'shared')])
        result = olefile.extract_attachments(outer, max_depth=0)
        self.assertEqual(self._entry_names(result),
                         [['first.bin'], ['second.bin']])
        self.assertEqual(result.unpacked_bytes, len(payload))
        self.assertIs(result.streams[0].data, result.streams[1].data)
        unique = result.unique_streams
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].sources, [['first.bin'], ['second.bin']])

    def test_shared_nested_ole_read_and_opened_once(self):
        inner = build_ole([('inside.txt', b'hello', None)])
        outer = build_ole([('first.bin', inner, 'shared'),
                           ('second.bin', inner, 'shared')])
        result = olefile.extract_attachments(outer, max_depth=1)
        self.assertEqual(self._entry_names(result), [
            ['first.bin'], ['first.bin', 'inside.txt'],
            ['second.bin'], ['second.bin', 'inside.txt']])
        # The embedded file is parsed and read once, even though it is
        # referenced from two directory entries.
        self.assertEqual(result.unpacked_bytes, len(inner) + 5)
        unique = result.unique_streams
        self.assertEqual(len(unique), 2)
        self.assertEqual(unique[0].sources,
                         [['first.bin'], ['second.bin']])
        self.assertEqual(unique[1].sources,
                         [['first.bin', 'inside.txt'],
                          ['second.bin', 'inside.txt']])

    def test_budget_boundary_defers_next_stream(self):
        outer = build_ole([('a.bin', b'a' * 10, None),
                           ('b.bin', b'b' * 10, None)])
        result = olefile.extract_attachments(outer, max_unpacked_bytes=10,
                                             max_depth=0)
        self.assertEqual(self._entry_names(result), [['a.bin']])
        self.assertEqual(result.unpacked_bytes, 10)
        self.assertEqual([item.path for item in result.pending], [['b.bin']])
        self.assertEqual(result.pending[0].reason, 'budget')

    def test_deferred_does_not_charge_budget(self):
        outer = build_ole([('a.bin', b'a' * 10, None),
                           ('b.bin', b'b' * 10, None),
                           ('c.bin', b'c' * 10, None)])
        result = olefile.extract_attachments(outer, max_unpacked_bytes=25,
                                             max_depth=0)
        self.assertEqual(self._entry_names(result),
                         [['a.bin'], ['b.bin']])
        self.assertEqual(result.unpacked_bytes, 20)
        self.assertEqual([item.path for item in result.pending],
                         [['c.bin']])

    def test_corrupt_inner_only_charges_bytes_actually_read(self):
        inner = build_ole([('inside.txt', b'hello', None)])
        # Build a top-level container with a nested attachment followed by a
        # normal stream, then cut the last sector off the container. The
        # directory still declares the full size of the attachment, but only
        # the bytes physically present may consume the budget.
        outer = build_ole([('att.bin', inner, None),
                           ('tail.txt', b'x' * 30, None)])
        truncated = bytes(outer[:-1024])
        budget = len(inner) - 512
        result = olefile.extract_attachments(truncated, max_depth=1,
                                             max_unpacked_bytes=budget)
        by_path = result.streams_by_path
        # The directory says 2560 bytes, but only 2048 are physically
        # present. The budget is charged 2048, not the declared size.
        self.assertEqual(by_path[('att.bin',)].size, 2048)
        self.assertEqual(result.unpacked_bytes, 2048)
        self.assertLess(result.unpacked_bytes, len(inner))
        self.assertFalse(result.pending)
        self.assertTrue(result.errors)

    def test_budget_ends_exactly_at_nested_stream_end(self):
        inner = build_ole([('inside.txt', b'hello', None)])
        outer = build_ole([('att.bin', inner, None),
                           ('zzz.txt', b'z' * 7, None)])
        result = olefile.extract_attachments(
            outer, max_depth=1, max_unpacked_bytes=len(inner) + 5)
        self.assertEqual(self._entry_names(result),
                         [['att.bin'], ['att.bin', 'inside.txt']])
        self.assertEqual(result.unpacked_bytes, len(inner) + 5)
        self.assertEqual([item.path for item in result.pending],
                         [['zzz.txt']])
        self.assertEqual(result.pending[0].reason, 'budget')

    def test_corrupt_nested_ole_is_error_not_crash(self):
        inner = build_ole([('inside.txt', b'hello', None)])
        broken = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 2000
        outer = build_ole([('broken.bin', broken, None),
                           ('ok.txt', b'ok', None)])
        result = olefile.extract_attachments(outer, max_depth=2)
        self.assertEqual(self._entry_names(result),
                         [['broken.bin'], ['ok.txt']])
        self.assertTrue(result.errors)


if __name__ == '__main__':
    unittest.main()
