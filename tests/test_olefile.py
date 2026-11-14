"""
Tests for olefile
"""

from __future__ import print_function
import unittest

import os, sys
import io
import hashlib
import struct
import tempfile
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


class TestReplaceStreams(unittest.TestCase):
    """Tests for the batch equal-length stream replacement API."""

    def setUp(self):
        self.ole_file = u"tests/images/test-ole-file.doc"
        self.temp_dir = tempfile.mkdtemp()
        self.source_bytes = self._read_file(self.ole_file)
        self.word_digest = olefile.stream_digest(self.ole_file, 'WordDocument')
        self.table_digest = olefile.stream_digest(self.ole_file, '1Table')

    def tearDown(self):
        for name in os.listdir(self.temp_dir):
            os.remove(os.path.join(self.temp_dir, name))
        os.rmdir(self.temp_dir)

    def _read_file(self, path):
        with open(path, 'rb') as fp:
            return fp.read()

    def _output(self, name='output.doc'):
        return os.path.join(self.temp_dir, name)

    def test_single_stream_replacement(self):
        replacement = b'\xa5' * 4096
        result = olefile.replace_streams(
            self.ole_file,
            [olefile.StreamReplacement('WordDocument', replacement,
                                       self.word_digest)],
            self._output())
        self.assertEqual(result.status, olefile.REPLACEMENT_APPLIED)
        self.assertTrue(result.applied)
        self.assertFalse(result.already_applied)
        self.assertTrue(os.path.isfile(self._output()))
        self.assertEqual(olefile.stream_digest(self._output(), 'WordDocument'),
                         hashlib.sha256(replacement).hexdigest())
        # original file is untouched:
        self.assertEqual(self._read_file(self.ole_file), self.source_bytes)
        self.assertEqual(result.source_digest,
                         hashlib.sha256(self.source_bytes).hexdigest())
        self.assertTrue(os.path.exists(self.ole_file))

    def test_group_replacement_keeps_other_streams(self):
        new_word = b'W' * 4096
        new_table = b'T' * 6438
        result = olefile.replace_streams(
            self.ole_file,
            [
                olefile.StreamReplacement('WordDocument', new_word,
                                          self.word_digest),
                olefile.StreamReplacement('1Table', new_table,
                                          self.table_digest),
            ],
            self._output())
        self.assertEqual(result.status, olefile.REPLACEMENT_APPLIED)
        self.assertEqual(len(result.replaced), 2)
        with olefile.OleFileIO(self._output()) as ole:
            self.assertEqual(ole.openstream('WordDocument').read(), new_word)
            self.assertEqual(ole.openstream('1Table').read(), new_table)
            # untouched streams are identical to the original:
            original = olefile.OleFileIO(self.ole_file)
            try:
                self.assertEqual(ole.openstream('\x05SummaryInformation').read(),
                                 original.openstream('\x05SummaryInformation').read())
            finally:
                original.close()

    def test_minifat_stream_replacement(self):
        compobj = olefile.stream_digest(self.ole_file, '\x01CompObj')
        replacement = b'Z' * 114
        olefile.replace_streams(
            self.ole_file,
            [olefile.StreamReplacement('\x01CompObj', replacement, compobj)],
            self._output())
        self.assertEqual(olefile.stream_digest(self._output(), '\x01CompObj'),
                         hashlib.sha256(replacement).hexdigest())

    def test_stale_digest_rejected_before_writing(self):
        stale = hashlib.sha256(b'not the current contents').hexdigest()
        with self.assertRaises(olefile.StreamPreconditionError) as captured:
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('WordDocument', b'X' * 4096, stale)],
                self._output())
        self.assertEqual(captured.exception.expected_digest, stale)
        self.assertEqual(captured.exception.actual_digest, self.word_digest)
        self.assertEqual(captured.exception.stream_path, ('WordDocument',))
        self.assertFalse(os.path.exists(self._output()))
        self.assertEqual(self._read_file(self.ole_file), self.source_bytes)

    def test_one_stale_digest_rejects_whole_group(self):
        with self.assertRaises(olefile.StreamPreconditionError):
            olefile.replace_streams(
                self.ole_file,
                [
                    olefile.StreamReplacement('WordDocument', b'X' * 4096,
                                              self.word_digest),
                    olefile.StreamReplacement('1Table', b'Y' * 6438,
                                              hashlib.sha256(b'stale').hexdigest()),
                ],
                self._output())
        self.assertFalse(os.path.exists(self._output()))
        self.assertEqual(self._read_file(self.ole_file), self.source_bytes)

    def test_missing_stream_rejected(self):
        with self.assertRaises(olefile.StreamPreconditionError):
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('NoSuchStream', b'X' * 10,
                                           hashlib.sha256(b'').hexdigest())],
                self._output())
        self.assertFalse(os.path.exists(self._output()))

    def test_unequal_length_rejected(self):
        with self.assertRaises(olefile.StreamReplacementError):
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('WordDocument', b'X' * 100,
                                           self.word_digest)],
                self._output())
        self.assertFalse(os.path.exists(self._output()))

    def test_same_stream_twice_rejected(self):
        with self.assertRaises(olefile.StreamOverlapError):
            olefile.replace_streams(
                self.ole_file,
                [
                    olefile.StreamReplacement('WordDocument', b'X' * 4096,
                                              self.word_digest),
                    olefile.StreamReplacement('worddocument', b'Y' * 4096,
                                              self.word_digest),
                ],
                self._output())
        self.assertFalse(os.path.exists(self._output()))

    def test_physically_overlapping_streams_rejected(self):
        # Point a second stream at a sector already used by 1Table. The
        # directory parses fine, but the two names resolve to the same bytes.
        data = bytearray(self.source_bytes)
        with olefile.OleFileIO(self.ole_file) as ole:
            summary_sid = ole._find('\x05SummaryInformation')
            dir_sects = []
            sect = ole.first_dir_sector
            while sect != olefile.ENDOFCHAIN:
                dir_sects.append(sect)
                sect = ole.fat[sect]
            entry_offset = summary_sid * 128
            sector_index = entry_offset // ole.sector_size
            within = entry_offset % ole.sector_size
            pos = (dir_sects[sector_index] + 1) * ole.sector_size + within
        struct.pack_into('<I', data, pos + 116, 9)     # isectStart
        struct.pack_into('<I', data, pos + 120, 4096)  # sizeLow
        patched = bytes(data)
        patched_path = self._output('patched.doc')
        with open(patched_path, 'wb') as fp:
            fp.write(patched)
        with olefile.OleFileIO(patched_path) as ole:
            first = ole.openstream('1Table').read()
            second = ole.openstream('\x05SummaryInformation').read()
        with self.assertRaises(olefile.StreamOverlapError) as captured:
            olefile.replace_streams(
                patched_path,
                [
                    olefile.StreamReplacement('1Table', b'A' * 6438,
                                              hashlib.sha256(first).hexdigest()),
                    olefile.StreamReplacement('\x05SummaryInformation',
                                              b'B' * 4096,
                                              hashlib.sha256(second).hexdigest()),
                ],
                self._output('overlap.doc'))
        self.assertEqual(len(captured.exception.stream_names), 2)
        self.assertFalse(os.path.exists(self._output('overlap.doc')))

    def test_replay_recognized_after_lost_receipt(self):
        replacement = b'Q' * 4096
        group = [olefile.StreamReplacement('WordDocument', replacement,
                                           self.word_digest)]
        first = olefile.replace_streams(self.ole_file, group, self._output())
        second = olefile.replace_streams(self.ole_file, group, self._output())
        self.assertEqual(second.status, olefile.REPLACEMENT_ALREADY_APPLIED)
        self.assertTrue(second.already_applied)
        self.assertFalse(second.applied)
        self.assertEqual(first.output_digest, second.output_digest)
        self.assertEqual(first.group_digest, second.group_digest)
        self.assertEqual(olefile.stream_digest(self._output(), 'WordDocument'),
                         hashlib.sha256(replacement).hexdigest())
        self.assertEqual(self._read_file(self.ole_file), self.source_bytes)

    def test_replay_after_intervening_failure(self):
        replacement = b'Q' * 4096
        group = [olefile.StreamReplacement('WordDocument', replacement,
                                           self.word_digest)]
        first = olefile.replace_streams(self.ole_file, group, self._output())
        # A stale group must fail without disturbing the published version...
        with self.assertRaises(olefile.StreamPreconditionError):
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('WordDocument', b'R' * 4096,
                                           hashlib.sha256(b'stale').hexdigest())],
                self._output())
        # ...and the original group still recognizes its own result.
        retry = olefile.replace_streams(self.ole_file, group, self._output())
        self.assertEqual(retry.status, olefile.REPLACEMENT_ALREADY_APPLIED)
        self.assertEqual(retry.output_digest, first.output_digest)

    def test_replay_is_case_insensitive_for_stream_names(self):
        group = [olefile.StreamReplacement('worddocument', b'Q' * 4096,
                                           self.word_digest)]
        olefile.replace_streams(self.ole_file, group, self._output())
        retry = olefile.replace_streams(self.ole_file, group, self._output())
        self.assertEqual(retry.status, olefile.REPLACEMENT_ALREADY_APPLIED)

    def test_replay_does_not_mask_different_group(self):
        first = olefile.replace_streams(
            self.ole_file,
            [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                       self.word_digest)],
            self._output())
        with self.assertRaises(olefile.OutputStreamConflictError):
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('WordDocument', b'R' * 4096,
                                           self.word_digest)],
                self._output())
        self.assertEqual(olefile.stream_digest(self._output(), 'WordDocument'),
                         hashlib.sha256(b'Q' * 4096).hexdigest())
        self.assertEqual(first.status, olefile.REPLACEMENT_APPLIED)

    def test_existing_unrelated_output_not_overwritten(self):
        conflict_path = self._output('conflict.doc')
        with open(conflict_path, 'wb') as fp:
            fp.write(b'unrelated data')
        with self.assertRaises(olefile.OutputStreamConflictError):
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                           self.word_digest)],
                conflict_path)
        self.assertEqual(self._read_file(conflict_path), b'unrelated data')

    def test_replay_after_source_corrected(self):
        # Publish the first group, then correct the WordDocument in place with
        # the ordinary writer. Resubmitting the original group must still
        # recognize the published file as its own earlier result.
        copy_path = self._output('source-copy.doc')
        copy2(self.ole_file, copy_path)
        group = [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                           self.word_digest)]
        published = olefile.replace_streams(copy_path, group, self._output())
        corrected = b'C' * 4096
        with olefile.OleFileIO(copy_path, write_mode=True) as ole:
            ole.write_stream('WordDocument', corrected)
        new_digest = hashlib.sha256(corrected).hexdigest()
        retry_group = [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                                 self.word_digest)]
        retry = olefile.replace_streams(copy_path, retry_group, self._output())
        self.assertEqual(retry.status, olefile.REPLACEMENT_ALREADY_APPLIED)
        self.assertEqual(retry.group_digest, published.group_digest)
        self.assertEqual(retry.output_digest, published.output_digest)
        self.assertEqual(olefile.stream_digest(self._output(), 'WordDocument'),
                         hashlib.sha256(b'Q' * 4096).hexdigest())
        self.assertEqual(new_digest, olefile.stream_digest(copy_path,
                                                           'WordDocument'))

    def test_output_same_as_source_rejected(self):
        with self.assertRaises(ValueError):
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                           self.word_digest)],
                self.ole_file)
        self.assertEqual(self._read_file(self.ole_file), self.source_bytes)

    def test_no_temp_files_left_after_failure(self):
        with self.assertRaises(olefile.StreamPreconditionError):
            olefile.replace_streams(
                self.ole_file,
                [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                           hashlib.sha256(b'stale').hexdigest())],
                self._output())
        self.assertEqual(os.listdir(self.temp_dir), [])

    def test_no_temp_files_left_behind(self):
        olefile.replace_streams(
            self.ole_file,
            [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                       self.word_digest)],
            self._output())
        leftovers = [name for name in os.listdir(self.temp_dir)
                     if name.startswith('.olefile-replace-')]
        self.assertEqual(leftovers, [])

    def test_old_digest_accepts_digest_object(self):
        with open(self.ole_file, 'rb') as fp:
            data = fp.read()
        digest_object = hashlib.sha512(
            olefile.OleFileIO(io.BytesIO(data)).openstream('WordDocument').read())
        result = olefile.replace_streams(
            self.ole_file,
            [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                       digest_object)],
            self._output())
        self.assertEqual(result.status, olefile.REPLACEMENT_APPLIED)

    def test_imported_records_accepted(self):
        result = olefile.replace_streams(
            self.ole_file,
            {'WordDocument': (b'Q' * 4096, self.word_digest)},
            self._output())
        self.assertEqual(result.status, olefile.REPLACEMENT_APPLIED)

    def test_in_memory_source_requires_output_path(self):
        with olefile.OleFileIO(io.BytesIO(self.source_bytes)) as ole:
            with self.assertRaises(ValueError):
                ole.replace_streams(
                    [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                               self.word_digest)])
            result = ole.replace_streams(
                [olefile.StreamReplacement('WordDocument', b'Q' * 4096,
                                           self.word_digest)],
                self._output())
        self.assertEqual(result.status, olefile.REPLACEMENT_APPLIED)


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
