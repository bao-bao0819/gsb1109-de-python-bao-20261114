"""
Tests for olefile
"""

from __future__ import print_function
import unittest

import os, sys
import io
import struct
import datetime
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


class TestOleTimestamp(unittest.TestCase):
    'Unit tests for the provenance-aware OleTimestamp model'

    def test_zero_filetime_is_missing_not_epoch(self):
        ts = olefile.make_instant(0, 'directory entry', 'modification time', 'X')
        self.assertTrue(ts.is_missing)
        self.assertIsNone(ts.value)
        self.assertIsNone(ts.filetime)

    def test_nonzero_filetime_is_instant(self):
        # FILETIME 130416885353850000 -> 2014-04-11 11:15:35.385 UTC
        ts = olefile.make_instant(130416885353850000, 'directory entry',
                                  'modification time', 'Root Entry')
        self.assertTrue(ts.is_instant)
        self.assertEqual(ts.filetime, 130416885353850000)
        self.assertEqual(ts.value.year, 2014)
        self.assertEqual(ts.source, 'directory entry')
        self.assertEqual(ts.path, 'Root Entry')

    def test_duration_is_distinct_from_instant(self):
        ts = olefile.make_duration(0, 'property set', 'property #10 (total_edit_time)')
        self.assertTrue(ts.is_duration)
        self.assertFalse(ts.is_instant)
        # a zero duration is a real value, not a missing field:
        self.assertEqual(ts.value, datetime.timedelta(0))
        self.assertEqual(ts.filetime, 0)

    def test_missing_never_sorts_before_instants(self):
        instant = olefile.make_instant(130416885000000000, 's', 'l', 'a')
        missing = olefile.make_missing('s', 'l', 'b')
        ordered = sorted([missing, instant])
        self.assertTrue(ordered[0].is_instant)
        self.assertTrue(ordered[-1].is_missing)

    def test_same_microsecond_keeps_raw_order(self):
        # 4 ticks = 400ns apart: identical once truncated to microseconds,
        # but the raw 100ns FILETIME order must be preserved.
        base = 130416885000000000
        first = olefile.make_instant(base, 's', 'l', 'a')
        second = olefile.make_instant(base + 4, 's', 'l', 'b')
        self.assertEqual(first.value, second.value)
        self.assertLess(first, second)
        self.assertEqual([t.filetime for t in sorted([second, first])],
                         [base, base + 4])

    def test_durations_follow_instants(self):
        instant = olefile.make_instant(10, 's', 'l', 'a')
        duration = olefile.make_duration(10 ** 12, 's', 'l', 'd')
        missing = olefile.make_missing('s', 'l', 'm')
        kinds = [t.kind for t in sorted([missing, duration, instant])]
        self.assertEqual(kinds, [
            olefile.TIMESTAMP_KIND_INSTANT,
            olefile.TIMESTAMP_KIND_DURATION,
            olefile.TIMESTAMP_KIND_MISSING])

    def test_text_roundtrip(self):
        ts = olefile.OleTimestamp(
            olefile.TIMESTAMP_KIND_INSTANT, 130416885353850000,
            'source:with:colons', 'field', 'path\\name')
        restored = olefile.OleTimestamp.from_text(ts.to_text())
        self.assertEqual(restored, ts)

    def test_text_roundtrip_missing_and_duration(self):
        for ts in (
                olefile.make_missing('s', 'l', 'p'),
                olefile.make_duration(1234567, 's', 'l', 'p')):
            self.assertEqual(olefile.OleTimestamp.from_text(ts.to_text()), ts)

    def test_from_text_rejects_foreign_lines(self):
        with self.assertRaises(ValueError):
            olefile.OleTimestamp.from_text('not a timeline record')
        with self.assertRaises(ValueError):
            olefile.OleTimestamp.from_text('olets:v1:bogus:1:s:p:l')


class TestTimeline(unittest.TestCase):
    'Tests for timeline collection, export and readback'

    def setUp(self):
        self.ole_file = u"tests/images/test-ole-file.doc"
        self.ole = olefile.OleFileIO(self.ole_file)

    def tearDown(self):
        self.ole.close()

    def test_directory_timestamps_distinguish_missing(self):
        root = self.ole.direntries[0]
        creation, modification = root.timestamps()
        # root entry has no creation time: it must be a missing record,
        # not an event at the 1601 epoch:
        self.assertTrue(creation.is_missing)
        self.assertTrue(modification.is_instant)
        self.assertEqual(modification.filetime, root.modifyTime)
        self.assertEqual(creation.source, 'directory entry')

    def test_metadata_timeline_kinds(self):
        meta = self.ole.get_metadata()
        by_label = {t.label: t for t in meta.timeline}
        edit = by_label['property #10 (total_edit_time)']
        self.assertTrue(edit.is_duration)
        self.assertTrue(by_label['property #12 (create_time)'].is_instant)
        self.assertTrue(by_label['property #13 (last_saved_time)'].is_instant)
        # last_printed is absent in this file: recorded explicitly as missing
        self.assertTrue(by_label['property #11 (last_printed)'].is_missing)

    def test_timeline_order_and_missing_placement(self):
        timeline = self.ole.get_timeline()
        kinds = [t.kind for t in timeline]
        self.assertIn(olefile.TIMESTAMP_KIND_INSTANT, kinds)
        first_missing = next(i for i, t in enumerate(timeline) if t.is_missing)
        self.assertTrue(all(not t.is_missing for t in timeline[:first_missing]))
        # every record carries provenance and, when set, a raw FILETIME
        for event in timeline:
            self.assertTrue(event.source)
            self.assertTrue(event.label)
            if event.is_instant:
                self.assertIsNotNone(event.filetime)

    def test_exclude_missing(self):
        timeline = self.ole.get_timeline(include_missing=False)
        self.assertTrue(timeline)
        self.assertTrue(all(not t.is_missing for t in timeline))

    def test_export_and_readback_restores_order(self):
        timeline = self.ole.get_timeline()
        text = self.ole.export_timeline()
        read_back = olefile.OleFileIO.read_timeline(text)
        self.assertEqual(read_back, timeline)
        self.assertEqual([t.filetime for t in read_back],
                         [t.filetime for t in timeline])
        # reading an iterable of lines and bytes also works:
        self.assertEqual(
            olefile.OleFileIO.read_timeline(text.encode('utf-8')), timeline)

    def test_get_timestamps_delegates_to_entry(self):
        records = self.ole.get_timestamps('WordDocument')
        self.assertEqual(len(records), 2)
        self.assertTrue(all(t.is_missing for t in records))


class TestTimelinePrecision(unittest.TestCase):
    'Byte-level tests for sub-microsecond FILETIME ordering'

    def setUp(self):
        self.ole_file = u"tests/images/test-ole-file.doc"
        self.temp_file = u"tests/images/test-ole-file-timeline.doc"
        copy2(self.ole_file, self.temp_file)

    def tearDown(self):
        if os.path.isfile(self.temp_file):
            os.remove(self.temp_file)

    def _patch_dir_modify_time(self, ole, sid, new_mtime):
        chain = []
        sector = ole.first_dir_sector
        while sector != olefile.ENDOFCHAIN:
            chain.append(sector)
            sector = ole.fat[sector]
        sector = chain[(sid * 128) // ole.sectorsize]
        local_offset = (sid * 128) % ole.sectorsize
        data = bytearray(ole.getsect(sector))
        struct.pack_into('<Q', data, local_offset + 108, new_mtime)
        ole.write_sect(sector, bytes(data))

    def test_same_microsecond_filetimes_keep_order_after_roundtrip(self):
        base = 130416885000000000  # exact microsecond multiple
        first_ticks, second_ticks = base, base + 4  # 400ns apart
        ole = olefile.OleFileIO(self.temp_file, write_mode=True)
        self._patch_dir_modify_time(ole, 1, first_ticks)   # 1Table
        self._patch_dir_modify_time(ole, 2, second_ticks)  # WordDocument
        ole.close()

        ole = olefile.OleFileIO(self.temp_file)
        pair = {t.path: t for t in ole.get_timeline()
                if t.source == 'directory entry'
                and t.label == 'modification time'
                and t.path in ('1Table', 'WordDocument')}
        first = pair['1Table']
        second = pair['WordDocument']
        # both display the same microsecond, but raw precision orders them:
        self.assertEqual(first.value, second.value)
        self.assertLess(first, second)
        self.assertEqual(second.filetime, second_ticks)

        read_back = olefile.OleFileIO.read_timeline(ole.export_timeline())
        restored = {t.path: t for t in read_back
                    if t.path in ('1Table', 'WordDocument')
                    and t.label == 'modification time'}
        self.assertLess(restored['1Table'].filetime,
                        restored['WordDocument'].filetime)
        self.assertEqual(restored['WordDocument'].filetime, second_ticks)
        ole.close()


if __name__ == '__main__':
    unittest.main()
