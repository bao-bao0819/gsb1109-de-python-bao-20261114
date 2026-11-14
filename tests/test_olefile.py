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
from olefile.olefile import VT_I4, VT_I2, VT_LPSTR, VT_LPWSTR


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


class UserDefinedPropertiesTest(unittest.TestCase):
    """Tests for get_userdefined_properties with multiple property sections.

    There is no real sample with two user-defined sections in the test images,
    so the property set streams below are built in memory from the documented
    [MS-OLEPS] layout.
    """

    FMTID_USERDEFINED = bytes.fromhex('05d5cdd59c2e1b10939708002b2cf9ae')
    FMTID_DOCSUM = bytes.fromhex('02d5cdd59c2e1b10939708002b2cf9ae')

    def _lpstr(self, value):
        return struct.pack('<I', len(value) + 1) + value + b'\x00'

    def _lpwstr(self, text):
        raw = text.encode('utf-16-le') + b'\x00\x00'
        return struct.pack('<I', len(raw) // 2) + raw

    def _dictionary(self, names):
        """Build a PID 0 dictionary; names is a list of (id, text)."""
        data = bytearray(struct.pack('<I', len(names)))
        for property_id, text in names:
            raw = text.encode('utf-16-le') + b'\x00\x00'
            data += struct.pack('<II', property_id, len(raw) // 2) + raw
            while len(data) % 4:
                data += b'\x00'
        return bytes(data)

    def _section(self, properties, names, codepage, dictionary_last=False,
                 include_dictionary=True):
        """Build one property section.

        ``properties`` is a list of (id, VT type, encoded value).  PID 0 (the
        name dictionary) and PID 1 (the code page) are added automatically,
        unless ``include_dictionary`` is False.
        """
        property_ids = [0, 1] if include_dictionary else [1]
        property_ids += [item[0] for item in properties]
        count = len(property_ids)
        table_end = 8 + count * 8
        dictionary = self._dictionary(names) if include_dictionary else b''
        codepage_value = struct.pack('<i', codepage)

        body = bytearray()
        offsets = {}
        cursor = table_end
        if dictionary_last:
            for property_id, property_type, value in properties:
                offsets[property_id] = cursor
                chunk = struct.pack('<I', property_type) + value
                body += chunk
                cursor += len(chunk)
                while cursor % 4:
                    body += b'\x00'
                    cursor += 1
            offsets[1] = cursor
            body += struct.pack('<I', VT_I4) + codepage_value
            cursor += 8
            if include_dictionary:
                offsets[0] = cursor
                body += dictionary
        else:
            if include_dictionary:
                offsets[0] = cursor
                body += dictionary
                cursor += len(dictionary)
            offsets[1] = cursor
            body += struct.pack('<I', VT_I4) + codepage_value
            cursor += 8
            for property_id, property_type, value in properties:
                offsets[property_id] = cursor
                chunk = struct.pack('<I', property_type) + value
                body += chunk
                cursor += len(chunk)
                while cursor % 4:
                    body += b'\x00'
                    cursor += 1

        section = bytearray(struct.pack('<II', table_end + len(body), count))
        for property_id in property_ids:
            section += struct.pack('<II', property_id, offsets[property_id])
        section += body
        return bytes(section)

    def _stream(self, sections, fmtids=None):
        """Wrap sections into a property set stream.

        By default every section gets the user-defined FMTID; ``fmtids`` can
        provide one identifier per section instead.
        """
        if fmtids is None:
            fmtids = [self.FMTID_USERDEFINED] * len(sections)
        header = b'\xfe\xff' + b'\x00\x00' + b'\x06\x01\x02\x00'
        header += b'\x00' * 16 + struct.pack('<I', len(sections))
        offset = len(header) + 20 * len(sections)
        table = b''
        body = b''
        for fmtid, section in zip(fmtids, sections):
            table += fmtid + struct.pack('<I', offset + len(body))
            body += section
        return header + table + body

    def _records(self, sections, fmtids=None, **kwargs):
        stream = self._stream(sections, fmtids)

        class _InMemoryOleFile(olefile.OleFileIO):
            def __init__(self):
                # Only what the parser touches; avoids opening a real file.
                self._we_opened_fp = False
                self.fp = None
                self.path_encoding = None
                self._open_stack = None

            def openstream(self, filename):
                return io.BytesIO(stream)

        return olefile.OleFileIO.get_userdefined_properties(
            _InMemoryOleFile(), 'props', **kwargs)

    def test_two_sections_keep_same_named_properties(self):
        # Both sections use property ID 2 but mean different quantities.
        first = self._section(
            [(2, VT_I4, struct.pack('<i', 11)),
             (3, VT_LPSTR, self._lpstr(b'first'))],
            [(2, '记录号'), (3, '读数')], 1252)
        second = self._section(
            [(2, VT_I4, struct.pack('<i', 22)),
             (3, VT_LPSTR, self._lpstr(b'second'))],
            [(2, '记录号'), (3, '读数')], 1252)

        records = self._records([first, second])

        numbered = [r for r in records if r['property_name'] == '记录号']
        # Neither record overwrites the other: same name, two sections.
        self.assertEqual(len(numbered), 2)
        self.assertEqual([r['section_index'] for r in numbered], [0, 1])
        self.assertEqual([r['property_id'] for r in numbered], [2, 2])
        self.assertEqual([r['value'] for r in numbered], [11, 22])

    def test_section_identity_and_raw_id_are_returned(self):
        section = self._section(
            [(7, VT_LPSTR, self._lpstr(b'value'))],
            [(7, 'Name')], 1252)
        record = self._records([section])[0]
        self.assertEqual(record['section_index'], 0)
        self.assertEqual(record['section_fmtid'],
                         'D5CDD505-2E9C-101B-9397-08002B2CF9AE')
        self.assertEqual(record['property_id'], 7)
        self.assertEqual(record['property_name'], 'Name')
        self.assertEqual(record['property_type'], VT_LPSTR)
        self.assertEqual(record['property_type_name'], 'VT_LPSTR')
        self.assertEqual(record['value'], 'value')

    def test_code_page_decodes_names_and_values(self):
        # GBK code page 936, both a Chinese name and a Chinese value.
        section = self._section(
            [(2, VT_LPSTR, self._lpstr('校准'.encode('gbk')))],
            [(2, '名称')], 936)
        record = self._records([section])[0]
        self.assertEqual(record['property_name'], '名称')
        self.assertEqual(record['value'], '校准')

    def test_unicode_code_page_utf16_dictionary(self):
        section = self._section(
            [(2, VT_LPWSTR, self._lpwstr('设备'))],
            [(2, '名称')], 1200)
        record = self._records([section])[0]
        self.assertEqual(record['property_name'], '名称')
        self.assertEqual(record['value'], '设备')

    def test_dictionary_and_code_page_position_do_not_matter(self):
        section = self._section(
            [(2, VT_LPSTR, self._lpstr(b'value'))],
            [(2, '名称')], 1252, dictionary_last=True)
        record = self._records([section])[0]
        self.assertEqual(record['property_name'], '名称')
        self.assertEqual(record['value'], 'value')

    def test_broken_name_keeps_record_addressable(self):
        # A name entry that cannot be decoded must not hide the value.
        section = bytearray(self._section(
            [(2, VT_LPSTR, self._lpstr(b'still here'))],
            [(2, 'broken')], 1252))
        index = section.find(b'b\x00r\x00o\x00k\x00e\x00n\x00\x00\x00')
        self.assertGreaterEqual(index, 0)
        # Replace the last UTF-16 NUL with a non-NUL byte so neither the
        # UTF-16 nor the byte-length reading ends with a valid terminator.
        section[index + 12] = 0x41
        record = self._records([bytes(section)])[0]
        self.assertIsNone(record['property_name'])
        self.assertEqual(record['section_index'], 0)
        self.assertEqual(record['property_id'], 2)
        self.assertEqual(record['value'], 'still here')

    def test_section_without_dictionary_is_skipped(self):
        # A standard section without a name dictionary is not a user-defined
        # property set, even though it shares the stream.
        with_dictionary = self._section(
            [(2, VT_LPSTR, self._lpstr(b'value'))],
            [(2, 'Name')], 1252)
        without_dictionary = self._section(
            [(5, VT_I4, struct.pack('<i', 3))], [], 1252,
            include_dictionary=False)
        records = self._records(
            [with_dictionary, without_dictionary],
            [self.FMTID_USERDEFINED, self.FMTID_DOCSUM])
        self.assertEqual([r['property_name'] for r in records], ['Name'])

    def test_no_userdefined_properties(self):
        self.assertEqual(
            olefile.OleFileIO.get_userdefined_properties(
                type('Empty', (olefile.OleFileIO,),
                     {'openstream': lambda self, name: io.BytesIO(b'')})(),
                'props'),
            [])


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


if __name__ == '__main__':
    unittest.main()
