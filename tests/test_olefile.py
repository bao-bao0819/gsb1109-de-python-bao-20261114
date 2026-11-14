"""
Tests for olefile
"""

from __future__ import print_function
import unittest

import os, sys
import io
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


class PropertyRecoveryTest(unittest.TestCase):
    """Recovery-mode reading of property streams with damaged entries."""

    SUMMARY = '\x05SummaryInformation'
    STREAM_SIZE = 4096

    def setUp(self):
        self.ole_file = u"tests/images/test-ole-file.doc"
        with open(self.ole_file, 'rb') as f:
            self.raw = f.read()

    # --- helpers -----------------------------------------------------------
    def build_section(self, entries, trailing=b''):
        """Build a section with table entries as (pid, vt, payload-after-type)."""
        import struct
        n = len(entries)
        table = struct.pack('<I', n)
        data_start = 8 + 8 * n
        body = b''
        offsets = []
        for pid, vt, payload in entries:
            offsets.append(data_start + len(body))
            body += struct.pack('<I', vt) + payload
        for (pid, vt, payload), off in zip(entries, offsets):
            table += struct.pack('<II', pid, off)
        return b'****' + table + body + trailing

    def build_stream(self, section, declared_size=None):
        import struct
        size = len(section) if declared_size is None else declared_size
        data = b'\x00' * 28
        data += b'\x00' * 16 + struct.pack('<I', 48)
        data += struct.pack('<I', size) + section[4:]
        self.assertLessEqual(len(data), self.STREAM_SIZE)
        return data + b'\x00' * (self.STREAM_SIZE - len(data))

    def open_with_stream(self, data, **kwargs):
        ole = olefile.OleFileIO(io.BytesIO(self.raw), write_mode=True, **kwargs)
        ole.write_stream(self.SUMMARY, data)
        return ole

    def recover(self, entries, trailing=b''):
        section = self.build_section(entries, trailing)
        ole = self.open_with_stream(self.build_stream(section))
        try:
            return ole.getproperties(self.SUMMARY, recover=True)
        finally:
            ole.close()

    # --- baseline compatibility -------------------------------------------
    def test_recovery_matches_strict_on_valid_stream(self):
        ole = olefile.OleFileIO(self.ole_file)
        try:
            strict = ole.getproperties(self.SUMMARY)
            recovered = ole.getproperties(self.SUMMARY, recover=True)
            self.assertIsInstance(strict, dict)
            self.assertIsInstance(recovered, olefile.PropertyRecoveryResult)
            self.assertEqual(strict, recovered.values)
            self.assertEqual([], recovered.errors)
            self.assertEqual([], recovered.ambiguities)
        finally:
            ole.close()

    def test_recovery_preserves_value_types(self):
        result = self.recover([
            (1, 3, struct_pack_i32(42)),
            (2, 30, struct_pack_i32(3) + b'hi\x00'),
            (3, 64, struct_pack_i32(100) + struct_pack_i32(0)),
        ])
        self.assertIsInstance(result.values[1], int)
        self.assertIsInstance(result.values[2], bytes)
        self.assertIsInstance(result.values[3], int)

    def test_strict_mode_still_returns_plain_dict(self):
        ole = olefile.OleFileIO(self.ole_file)
        try:
            result = ole.getproperties(self.SUMMARY)
            self.assertEqual(dict, type(result))
        finally:
            ole.close()

    # --- oversized variable-length attribute ------------------------------
    def test_oversized_length_does_not_hide_following_value(self):
        # id 2 claims 40 payload bytes but only 3 belong to it; id 3 follows
        # immediately and is complete on its own.
        result = self.recover([
            (1, 3, struct_pack_i32(42)),
            (2, 30, struct_pack_i32(40) + b'AB\x00'),
            (3, 3, struct_pack_i32(7)),
        ], trailing=b'\x00' * 200)
        self.assertEqual({1: 42, 3: 7}, dict(result.values))
        self.assertEqual(1, len(result.errors))
        err = result.errors[0]
        self.assertEqual(2, err.property_id)
        self.assertEqual('length_overrun', err.kind)
        self.assertIsNotNone(err.offset)
        self.assertEqual([], result.ambiguities)

    def test_oversized_length_beyond_section_is_isolated(self):
        result = self.recover([
            (1, 3, struct_pack_i32(11)),
            (2, 30, struct_pack_i32(4000) + b'xx\x00'),
            (3, 3, struct_pack_i32(33)),
        ])
        self.assertEqual({1: 11, 3: 33}, dict(result.values))
        self.assertEqual(1, len(result.errors))
        self.assertEqual(2, result.errors[0].property_id)
        self.assertEqual('value_truncated', result.errors[0].kind)
        self.assertGreater(result.errors[0].declared_end,
                           result.errors[0].available_end)

    # --- out-of-range offset ----------------------------------------------
    def test_offset_out_of_range_does_not_affect_following_entries(self):
        section = bytearray(self.build_section([
            (1, 3, struct_pack_i32(9)),
            (2, 3, struct_pack_i32(10)),
            (3, 3, struct_pack_i32(11)),
        ]))
        # second table entry: offset field at 16 + 4
        struct_pack_into_i32(section, 20, 0xFFFF)
        ole = self.open_with_stream(self.build_stream(bytes(section)))
        try:
            result = ole.getproperties(self.SUMMARY, recover=True)
        finally:
            ole.close()
        self.assertEqual({1: 9, 3: 11}, dict(result.values))
        self.assertEqual(1, len(result.errors))
        err = result.errors[0]
        self.assertEqual(2, err.property_id)
        self.assertEqual(0xFFFF, err.offset)
        self.assertEqual('offset_out_of_range', err.kind)
        self.assertEqual([], result.ambiguities)

    # --- ambiguous entries sharing the same content -----------------------
    def test_two_entries_claiming_same_content_are_ambiguous(self):
        section = bytearray(self.build_section([
            (1, 3, struct_pack_i32(42)),
            (2, 3, b''),
        ]))
        # point both entries at the same value offset (24)
        struct_pack_into_i32(section, 12, 24)
        struct_pack_into_i32(section, 20, 24)
        ole = self.open_with_stream(self.build_stream(bytes(section)))
        try:
            result = ole.getproperties(self.SUMMARY, recover=True)
        finally:
            ole.close()
        self.assertEqual({}, dict(result.values))
        self.assertEqual(1, len(result.ambiguities))
        ambiguity = result.ambiguities[0]
        self.assertEqual((1, 2), ambiguity.property_ids)
        self.assertEqual((1, 2), ambiguity.ids)
        self.assertEqual(24, ambiguity.offset)
        self.assertGreater(ambiguity.length, 0)
        self.assertIn(1, ambiguity.spans)
        self.assertIn(2, ambiguity.spans)

    # --- strict errors preserved ------------------------------------------
    def test_strict_header_error_still_raises(self):
        ole = olefile.OleFileIO(io.BytesIO(self.raw), write_mode=True,
                                raise_defects=olefile.DEFECT_INCORRECT)
        try:
            data = bytearray(self.build_stream(
                self.build_section([(1, 3, struct_pack_i32(1))])))
            # corrupt the section offset in the property stream header
            struct_pack_into_i32(data, 28 + 16, 0xFFFFFF)
            ole.write_stream(self.SUMMARY, bytes(data))
            # legacy strict mode raises the underlying exception type here
            with self.assertRaises(Exception):
                ole.getproperties(self.SUMMARY)
        finally:
            ole.close()

    def test_recovery_header_error_is_reported_not_raised(self):
        data = bytearray(self.build_stream(self.build_section([])))
        struct_pack_into_i32(data, 28 + 16, 0xFFFFFF)
        ole = self.open_with_stream(bytes(data))
        try:
            result = ole.getproperties(self.SUMMARY, recover=True)
        finally:
            ole.close()
        self.assertEqual({}, dict(result.values))
        self.assertEqual(1, len(result.errors))
        self.assertEqual('header_error', result.errors[0].kind)


    def test_recovery_mode_aliases(self):
        ole = olefile.OleFileIO(self.ole_file)
        try:
            for kwargs in (dict(recovery=True), dict(recovery_mode=True),
                           dict(strict=False), dict(lenient=True),
                           dict(mode='recover')):
                result = ole.getproperties(self.SUMMARY, **kwargs)
                self.assertIsInstance(result,
                                      olefile.PropertyRecoveryResult)
            self.assertIsInstance(ole.getproperties_recover(self.SUMMARY),
                                  olefile.PropertyRecoveryResult)
            self.assertIsInstance(ole.recover_properties(self.SUMMARY),
                                  olefile.PropertyRecoveryResult)
            self.assertIsInstance(ole.getproperties(self.SUMMARY, mode='strict'),
                                  dict)
            with self.assertRaises(ValueError):
                ole.getproperties(self.SUMMARY, mode='unknown')
        finally:
            ole.close()

    def test_error_reports_offsets_inside_stream(self):
        result = self.recover([
            (1, 3, struct_pack_i32(2)),
            (2, 30, struct_pack_i32(50) + b'x'),
            (3, 3, struct_pack_i32(9)),
        ], trailing=b'\x00' * 80)
        err = result.errors[0]
        self.assertEqual(2, err.property_id)
        self.assertEqual(48, err['section_offset'])
        self.assertEqual(40, err['offset'])
        self.assertEqual(err['section_offset'] + err['offset'],
                         err['stream_offset'])
        self.assertEqual((2, 40), tuple(err))
        self.assertEqual({2: 40}, result.error_offsets)

    def test_duplicate_property_id_keeps_first_value(self):
        result = self.recover([
            (5, 3, struct_pack_i32(1)),
            (5, 3, struct_pack_i32(2)),
        ])
        self.assertEqual({5: 1}, dict(result.values))
        self.assertEqual(1, len(result.errors))
        self.assertEqual('duplicate_property_id',
                         result.errors[0].kind)

    def test_ambiguity_entries_are_reported_in_errors_too(self):
        section = bytearray(self.build_section([
            (1, 3, struct_pack_i32(42)),
            (2, 3, b''),
        ]))
        struct_pack_into_i32(section, 12, 24)
        struct_pack_into_i32(section, 20, 24)
        ole = self.open_with_stream(self.build_stream(bytes(section)))
        try:
            result = ole.getproperties(self.SUMMARY, recover=True)
        finally:
            ole.close()
        self.assertEqual(1, len(result.ambiguities))
        self.assertEqual(2, len(result.errors))
        for err in result.errors:
            self.assertEqual('ambiguous', err.kind)
            self.assertEqual(24, err.offset)
        self.assertEqual({1, 2}, result.ambiguous_ids)
        self.assertFalse(result.ok)

    def test_complete_entries_with_overlapping_spans_are_ambiguous(self):
        # id 1 owns bytes 24..32, id 2 starts at 30 inside it and is complete.
        section = bytearray(self.build_section([
            (1, 3, struct_pack_i32(1)),
            (2, 3, struct_pack_i32(2)),
        ], trailing=b'\x00' * 100))
        struct_pack_into_i32(section, 12, 24)
        struct_pack_into_i32(section, 20, 30)
        ole = self.open_with_stream(self.build_stream(bytes(section)))
        try:
            result = ole.getproperties(self.SUMMARY, recover=True)
        finally:
            ole.close()
        self.assertEqual({}, dict(result.values))
        self.assertEqual(1, len(result.ambiguities))
        self.assertEqual((1, 2), result.ambiguities[0].property_ids)
        self.assertEqual({1, 2}, result.ambiguous_ids)

    def test_same_start_ambiguity_keeps_candidate_values(self):
        section = bytearray(self.build_section([
            (1, 3, struct_pack_i32(42)),
            (2, 3, b''),
        ]))
        struct_pack_into_i32(section, 12, 24)
        struct_pack_into_i32(section, 20, 24)
        ole = self.open_with_stream(self.build_stream(bytes(section)))
        try:
            result = ole.getproperties(self.SUMMARY, recover=True)
        finally:
            ole.close()
        ambiguity = result.ambiguities[0]
        self.assertEqual({1: 42, 2: 42}, ambiguity.values)
        self.assertEqual({}, dict(result.values))

    def test_vector_value_is_measured_and_recovered(self):
        from olefile.olefile import VT_VECTOR
        payload = (struct_pack_i32(2) + struct_pack_i32(2) + b'a\x00' +
                   struct_pack_i32(3) + b'bb\x00')
        result = self.recover([
            (1, VT_VECTOR | 30, payload),
            (2, 3, struct_pack_i32(5)),
        ])
        self.assertEqual({1: [b'a', b'bb'], 2: 5}, dict(result.values))
        self.assertEqual([], result.errors)

    def test_truncated_vector_does_not_hide_later_value(self):
        from olefile.olefile import VT_VECTOR
        payload = (struct_pack_i32(2) + struct_pack_i32(2) + b'a\x00' +
                   struct_pack_i32(400) + b'bb\x00')
        result = self.recover([
            (1, VT_VECTOR | 30, payload),
            (2, 3, struct_pack_i32(5)),
        ])
        self.assertEqual({2: 5}, dict(result.values))
        self.assertEqual(1, len(result.errors))
        self.assertEqual(1, result.errors[0].property_id)
        self.assertEqual('value_truncated', result.errors[0].kind)


def struct_pack_i32(value):
    import struct
    return struct.pack('<i', value)


def struct_pack_into_i32(buf, offset, value):
    import struct
    struct.pack_into('<I', buf, offset, value)


if __name__ == '__main__':
    unittest.main()