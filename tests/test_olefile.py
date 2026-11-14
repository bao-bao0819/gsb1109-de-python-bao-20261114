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


class TestGetStreamFragments(unittest.TestCase):
    'Tests for the OleFileIO.get_stream_fragments method'

    def setUp(self):
        self.ole_file = u"tests/images/test-ole-file.doc"

    @staticmethod
    def reassemble(ole, fragments):
        'Read fragments from the OLE file and concatenate them'
        data = []
        for file_offset, length in fragments:
            ole.fp.seek(file_offset)
            data.append(ole.fp.read(length))
        return b"".join(data)

    def test_full_stream_matches_openstream(self):
        'Fragments of a whole stream reassemble to the openstream content'
        ole = olefile.OleFileIO(self.ole_file)
        for path in ole.listdir():
            expected = ole.openstream(path).read()
            fragments, broken_at = ole.get_stream_fragments(path)
            self.assertIsNone(broken_at)
            self.assertEqual(self.reassemble(ole, fragments), expected)
        ole.close()

    def test_half_open_ranges(self):
        'Fragments of a half-open range reassemble to the matching slice'
        ole = olefile.OleFileIO(self.ole_file)
        for path in ole.listdir():
            data = ole.openstream(path).read()
            nb_bytes = len(data)
            step = max(1, nb_bytes // 7)
            for offset in range(0, nb_bytes + 1, step):
                for size in (0, 1, 63, 64, 65, 511, 512, 513, 4096, nb_bytes):
                    fragments, broken_at = ole.get_stream_fragments(
                        path, offset, size)
                    self.assertIsNone(broken_at)
                    self.assertEqual(self.reassemble(ole, fragments),
                                     data[offset:offset + size])
        ole.close()

    def test_fragments_are_maximal(self):
        'Consecutive fragments are never physically contiguous'
        ole = olefile.OleFileIO(self.ole_file)
        for path in ole.listdir():
            fragments, broken_at = ole.get_stream_fragments(path)
            for first, second in zip(fragments, fragments[1:]):
                self.assertNotEqual(first[0] + first[1], second[0])
        ole.close()

    def test_mini_stream_maps_through_root_storage(self):
        'Mini stream fragments are located inside the root storage sectors'
        ole = olefile.OleFileIO(self.ole_file)
        nb_root_sect = (ole.root.size + ole.sectorsize - 1) // ole.sectorsize
        root_chain, complete = ole._follow_chain(ole.root.isectStart,
                                                 ole.fat, nb_root_sect)
        self.assertTrue(complete)
        root_sectors = set(root_chain)
        for path in ole.listdir():
            entry = ole.direntries[ole._find(path)]
            if not entry.is_minifat:
                continue
            fragments, broken_at = ole.get_stream_fragments(path)
            self.assertIsNone(broken_at)
            for file_offset, length in fragments:
                sector = file_offset // ole.sectorsize - 1
                self.assertIn(sector, root_sectors)
        ole.close()

    def test_range_clamped_to_stream_size(self):
        'A size beyond the end of the stream is clamped'
        ole = olefile.OleFileIO(self.ole_file)
        data = ole.openstream('worddocument').read()
        fragments, broken_at = ole.get_stream_fragments(
            'worddocument', 100, len(data) * 2)
        self.assertIsNone(broken_at)
        self.assertEqual(self.reassemble(ole, fragments), data[100:])
        ole.close()

    def test_empty_ranges(self):
        'Empty or out-of-stream ranges return no fragments and no defect'
        ole = olefile.OleFileIO(self.ole_file)
        size = ole.get_size('worddocument')
        for offset, length in ((0, 0), (size, 10), (size + 100, None)):
            fragments, broken_at = ole.get_stream_fragments(
                'worddocument', offset, length)
            self.assertEqual(fragments, [])
            self.assertIsNone(broken_at)
        ole.close()

    def test_not_a_stream(self):
        ole = olefile.OleFileIO(self.ole_file)
        with self.assertRaises(IOError):
            ole.get_stream_fragments('macros')
        ole.close()

    def test_stream_not_found(self):
        ole = olefile.OleFileIO(self.ole_file)
        with self.assertRaises(IOError):
            ole.get_stream_fragments('no_such_stream')
        ole.close()

    def test_negative_offset_or_size(self):
        ole = olefile.OleFileIO(self.ole_file)
        with self.assertRaises(ValueError):
            ole.get_stream_fragments('worddocument', -1, 10)
        with self.assertRaises(ValueError):
            ole.get_stream_fragments('worddocument', 0, -1)
        ole.close()

    def test_broken_fat_chain(self):
        'A corrupt FAT chain is reported from the first lost logical byte'
        ole = olefile.OleFileIO(self.ole_file)
        data = ole.openstream('1Table').read()
        entry = ole.direntries[ole._find('1Table')]
        # kill the FAT link after the first sector of the stream:
        ole.fat[entry.isectStart] = olefile.FREESECT
        fragments, broken_at = ole.get_stream_fragments('1Table')
        self.assertEqual(broken_at, ole.sectorsize)
        self.assertEqual(self.reassemble(ole, fragments),
                         data[:ole.sectorsize])
        # a range fully before the defect is still verified:
        fragments, broken_at = ole.get_stream_fragments('1Table', 100, 200)
        self.assertIsNone(broken_at)
        self.assertEqual(self.reassemble(ole, fragments), data[100:300])
        # a range starting after the defect cannot be verified at all:
        fragments, broken_at = ole.get_stream_fragments(
            '1Table', ole.sectorsize + 100, 100)
        self.assertEqual(broken_at, ole.sectorsize)
        self.assertEqual(fragments, [])
        ole.close()

    def test_broken_minifat_chain(self):
        'A corrupt MiniFAT chain is reported from the first lost mini sector'
        ole = olefile.OleFileIO(self.ole_file)
        data = ole.openstream('\x01compobj').read()
        entry = ole.direntries[ole._find('\x01compobj')]
        self.assertTrue(entry.is_minifat)
        ole.loadminifat()
        # kill the MiniFAT link after the first mini sector of the stream:
        ole.minifat[entry.isectStart] = olefile.ENDOFCHAIN
        fragments, broken_at = ole.get_stream_fragments('\x01compobj')
        self.assertEqual(broken_at, ole.minisectorsize)
        self.assertEqual(self.reassemble(ole, fragments),
                         data[:ole.minisectorsize])
        ole.close()

    def test_broken_root_chain(self):
        'A corrupt root chain is reported where the mini stream stops'
        ole = olefile.OleFileIO(self.ole_file)
        # the mini stream of this file only holds 2 mini sectors, so an
        # empty root chain breaks the mapping from logical byte 0:
        ole.root.isectStart = olefile.ENDOFCHAIN
        fragments, broken_at = ole.get_stream_fragments('\x01compobj')
        self.assertEqual(broken_at, 0)
        self.assertEqual(fragments, [])
        ole.close()

    def test_broken_root_chain_mid_stream(self):
        'Root chain defect is reported at the first unverifiable mini sector'
        import array
        ole = olefile.OleFileIO()
        ole.sectorsize = 512
        ole.minisectorsize = 64
        # root chain: sectors 3 then 7 (1024 bytes of mini stream):
        ole.fat = array.array('I', [olefile.ENDOFCHAIN] * 16)
        ole.fat[3] = 7
        ole.fat[7] = olefile.ENDOFCHAIN
        # mini stream of 10 mini sectors (640 bytes):
        ole.minifat = array.array('I', list(range(1, 10))
                                  + [olefile.ENDOFCHAIN])

        class FakeEntry(object):
            pass
        entry = FakeEntry()
        entry.entry_type = olefile.STGTY_STREAM
        entry.is_minifat = True
        entry.isectStart = 0
        entry.size = 640
        ole.direntries = {0: entry}
        ole._find = lambda filename: 0
        ole.root = FakeEntry()
        ole.root.isectStart = 3

        fragments, broken_at = ole.get_stream_fragments('fake')
        self.assertIsNone(broken_at)
        self.assertEqual(fragments, [(4 * 512, 512), (8 * 512, 128)])
        # cut the root chain after its first sector: mini stream bytes
        # from 512 (logical stream offset 512) cannot be verified anymore:
        ole.fat[3] = olefile.ENDOFCHAIN
        fragments, broken_at = ole.get_stream_fragments('fake')
        self.assertEqual(broken_at, 512)
        self.assertEqual(fragments, [(4 * 512, 512)])


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
