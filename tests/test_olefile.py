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


if __name__ == '__main__':
    unittest.main()


class TestPlanStreamReads(unittest.TestCase):
    """Tests for planning batched physical reads of several OLE streams."""

    def setUp(self):
        self.ole_file = u"tests/images/test-ole-file.doc"

    def _stream_bytes(self, ole, names):
        return dict((tuple(name), ole.openstream(name).read())
                    for name in names)

    def test_plan_sample_file_matches_direct_reads(self):
        """All planned streams must be byte-identical to openstream()."""
        names = ['WordDocument', '1Table',
                 '\x05SummaryInformation', '\x01CompObj']
        with olefile.OleFileIO(self.ole_file) as ole:
            expected = self._stream_bytes(ole, names)
            plan = ole.plan_stream_reads(names)
            assembled = plan.fetch()
            for name in names:
                self.assertEqual(assembled[name], expected[tuple(name)])
            self.assertEqual(plan.read_count, 1)
            self.assertGreater(plan.extra_bytes, 0)

    def test_extra_bytes_are_unrelated_bridged_bytes(self):
        """Extra bytes only count bytes inside planned reads that no stream uses."""
        names = ['WordDocument', '1Table',
                 '\x05SummaryInformation', '\x01CompObj']
        with olefile.OleFileIO(self.ole_file) as ole:
            expected = self._stream_bytes(ole, names)
            stream_spans = []
            for name in names:
                _, _, extents = ole._stream_mapping(name)
                stream_spans.extend((start, start + length)
                                    for _, start, length in extents)
            plan = ole.plan_stream_reads(names)
            # A stream byte implies its containing sector, so overlapping
            # stream spans are widened the same way the planner does.
            sector_spans = []
            for a, b in stream_spans:
                sector_spans.append((a - a % ole.sector_size,
                                     min((a - a % ole.sector_size)
                                         + ole.sector_size, ole._filesize)))
            unrelated = 0
            for item in plan.ranges:
                start, end = item.offset, item.stop
                covered = [(max(start, a), min(end, b))
                           for a, b in sector_spans if a < end and start < b]
                covered.sort()
                cursor = start
                for a, b in covered:
                    if a > cursor:
                        unrelated += a - cursor
                    cursor = max(cursor, b)
                unrelated += end - cursor
            self.assertEqual(plan.extra_bytes, unrelated)

    def test_budget_bounds_extra_bytes(self):
        """The planned overhead never exceeds the caller budget."""
        names = ['WordDocument', '1Table',
                 '\x05SummaryInformation', '\x01CompObj']
        with olefile.OleFileIO(self.ole_file) as ole:
            expected = self._stream_bytes(ole, names)
            unlimited = ole.plan_stream_reads(names).read_count
            for budget in (0, 100, 1000, 5000):
                plan = ole.plan_stream_reads(names, budget=budget)
                self.assertLessEqual(plan.extra_bytes, budget)
                assembled = plan.fetch()
                for name in names:
                    self.assertEqual(assembled[name], expected[tuple(name)])
                self.assertGreaterEqual(plan.read_count, unlimited)
            # With no budget the plan must never use more extra bytes than
            # needed to make the whole span contiguous.
            self.assertEqual(unlimited, 1)

    def test_zero_budget_does_not_read_gaps(self):
        """A zero budget must not read unrelated gap sectors."""
        names = ['WordDocument', '1Table',
                 '\x05SummaryInformation', '\x01CompObj']
        with olefile.OleFileIO(self.ole_file) as ole:
            plan = ole.plan_stream_reads(names, budget=0)
            self.assertEqual(plan.extra_bytes, 0)
            # Two runs of mandatory sectors, separated by unrelated sectors.
            self.assertEqual(plan.read_count, 2)

    def test_stream_assembly_order_and_read_indices(self):
        """Each stream's pieces follow its logical byte order."""
        names = ['WordDocument', '1Table', '\x01CompObj']
        with olefile.OleFileIO(self.ole_file) as ole:
            plan = ole.plan_stream_reads(names)
            for name in names:
                stream_plan = plan.stream(name)
                self.assertEqual(stream_plan.size,
                                 len(ole.openstream(name).read()))
                self.assertEqual(
                    sum(piece.length for piece in stream_plan.pieces),
                    stream_plan.size)
                for piece in stream_plan.pieces:
                    read = plan.ranges[piece.read_index]
                    self.assertLessEqual(piece.end, read.length)

    def test_forbidden_gap_splits_reads(self):
        """A forbidden gap between two streams must not be crossed."""
        names = ['WordDocument', '1Table',
                 '\x05SummaryInformation', '\x01CompObj']
        with olefile.OleFileIO(self.ole_file) as ole:
            expected = self._stream_bytes(ole, names)
            # This gap lies between 1Table and SummaryInformation.
            gap = (11054, 11264)
            plan = ole.plan_stream_reads(names, budget=100000,
                                         forbidden=[gap])
            for item in plan.ranges:
                self.assertFalse(item.intersects(*gap))
            assembled = plan.fetch()
            for name in names:
                self.assertEqual(assembled[name], expected[tuple(name)])

    def test_forbidden_stream_bytes_are_rejected(self):
        """A forbidden range covering stream data makes the plan impossible."""
        with olefile.OleFileIO(self.ole_file) as ole:
            # WordDocument starts at physical offset 512.
            with self.assertRaises(olefile.ReadPlanError):
                ole.plan_stream_reads(['WordDocument'],
                                      forbidden=[(600, 700)])

    def test_forbidden_across_two_streams_is_rejected(self):
        """Forbidding the range between two streams does not hide data."""
        with olefile.OleFileIO(self.ole_file) as ole:
            with self.assertRaises(olefile.ReadPlanError):
                ole.plan_stream_reads(
                    ['WordDocument', '1Table'],
                    forbidden=[(1000, 4200)])

    def test_read_plan_from_path(self):
        """The module-level helper closes the file but keeps the plan usable."""
        names = ['WordDocument', '1Table']
        plan = olefile.plan_stream_reads(self.ole_file, names, budget=0)
        self.assertEqual(plan.budget, 0)
        with open(self.ole_file, 'rb') as handle:
            assembled = plan.fetch(handle)
        with olefile.OleFileIO(self.ole_file) as ole:
            for name in names:
                self.assertEqual(assembled[name],
                                 ole.openstream(name).read())

    def test_empty_stream_needs_no_read(self):
        """A zero-length stream contributes no physical read."""
        with olefile.OleFileIO(self.ole_file) as ole:
            # The sample file has no empty stream; use a data window instead:
            # requesting no stream yields no read at all.
            plan = ole.plan_stream_reads([])
            self.assertEqual(plan.ranges, ())
            self.assertEqual(plan.extra_bytes, 0)

    def test_duplicate_names_count_once(self):
        """Repeating a stream name must not duplicate reads or pieces."""
        with olefile.OleFileIO(self.ole_file) as ole:
            plan = ole.plan_stream_reads(
                ['WordDocument', 'WordDocument', 'worddocument'])
            self.assertEqual(len(plan.streams), 1)
            self.assertEqual(plan.read_count, 1)

    def test_plan_is_case_insensitive(self):
        """Stream lookup follows openstream()'s case-insensitive behaviour."""
        with olefile.OleFileIO(self.ole_file) as ole:
            plan = ole.plan_stream_reads(['worddocument'])
            self.assertEqual(plan.streams['WordDocument'].size,
                             ole.get_size('WordDocument'))


class TestPlanStreamReadsLayouts(unittest.TestCase):
    """Read planning on synthetic, deliberately fragmented layouts."""

    def setUp(self):
        from tests.fixtures.ole_builder import build_ole
        self.build_ole = build_ole
        self.tmpdir = os.path.join(_thismodule_dir, '.plan-tmp')
        if not os.path.isdir(self.tmpdir):
            os.makedirs(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build(self, name, streams, order):
        path = os.path.join(self.tmpdir, name)
        self.build_ole(path, streams, order)
        return path

    def test_fragmented_fat_streams(self):
        """Interleaved FAT chains are rebuilt from the cheapest reads."""
        streams = {'Alpha': b'A' * 5120, 'Beta': b'B' * 4096,
                   'Gamma': b'C' * 2560}
        remaining = {'Alpha': 10, 'Beta': 8, 'Gamma': 5}
        order = []
        while any(remaining.values()):
            for name in ('Alpha', 'Beta', 'Gamma'):
                if remaining[name]:
                    order.append('free')
                    order.append(('stream', name))
                    remaining[name] -= 1
        path = self._build('fragmented.ole', streams, order)
        with olefile.OleFileIO(path) as ole:
            names = list(streams)
            expected = dict((name, ole.openstream(name).read())
                            for name in names)
            for budget in (0, 512, 2000, 100000):
                plan = ole.plan_stream_reads(names, budget=budget)
                self.assertLessEqual(plan.extra_bytes, budget)
                assembled = plan.fetch()
                for name in names:
                    self.assertEqual(assembled[name], expected[name])
            zero = ole.plan_stream_reads(names, budget=0)
            self.assertEqual(zero.extra_bytes, 0)
            self.assertGreater(zero.read_count, 1)
            unlimited = ole.plan_stream_reads(names)
            self.assertEqual(unlimited.read_count, 1)

    def test_minifat_streams(self):
        """MiniFAT streams map to physical offsets inside the mini stream."""
        streams = {'Small1': bytes(range(256)) * 6,
                   'Small2': b'S' * 700,
                   'Small3': b'T' * 600}
        order = [('stream', 'Small1'), ('stream', 'Small2'),
                 ('stream', 'Small1'), ('stream', 'Small3'),
                 ('stream', 'Small2'), ('stream', 'Small1'),
                 ('stream', 'Small3')]
        path = self._build('mini.ole', streams, dict(order))
        with olefile.OleFileIO(path) as ole:
            names = list(streams)
            expected = dict((name, ole.openstream(name).read())
                            for name in names)
            plan = ole.plan_stream_reads(names, budget=0)
            self.assertEqual(plan.extra_bytes, 0)
            # The two small streams share sectors only within the ministream.
            for piece in plan.streams['Small1'].pieces:
                self.assertLess(piece.length, ole.minisectorcutoff)
            assembled = plan.fetch()
            for name in names:
                self.assertEqual(assembled[name], expected[name])

    def test_shared_physical_chain_counts_once(self):
        """Two directory entries pointing at one chain are read once."""
        import struct
        streams = {'One': b'1' * 5120, 'Two': b'2' * 5120}
        order = [('stream', 'One')] * 10 + [('stream', 'Two')] * 10
        path = self._build('shared.ole', streams, order)
        with open(path, 'rb') as handle:
            data = bytearray(handle.read())
        # Directory sector is the one before the FAT; find it from the header.
        first_dir = struct.unpack_from('<I', data, 48)[0]
        dir_offset = (first_dir + 1) * 512
        # Entry 2 starts at +256; patch its start sector and size to entry 1.
        first_stream = struct.unpack_from('<I', data,
                                          dir_offset + 128 + 116)[0]
        struct.pack_into('<I', data, dir_offset + 256 + 116, first_stream)
        struct.pack_into('<I', data, dir_offset + 256 + 120, 5120)
        with open(path, 'wb') as handle:
            handle.write(bytes(data))
        with olefile.OleFileIO(path) as ole:
            self.assertEqual(ole.openstream('One').read(),
                             ole.openstream('Two').read())
            plan = ole.plan_stream_reads(['One', 'Two'], budget=0)
            self.assertEqual(plan.read_count, 1)
            self.assertEqual(plan.extra_bytes, 0)
            assembled = plan.fetch()
            self.assertEqual(assembled['One'], ole.openstream('One').read())
            self.assertEqual(assembled['Two'], ole.openstream('Two').read())

    def test_streams_in_same_sector_read_once(self):
        """Two small streams packed in one physical sector need one read."""
        streams = {'Tiny1': b'x' * 64, 'Tiny2': b'y' * 32}
        path = self._build('same-sector.ole', streams, [])
        with olefile.OleFileIO(path) as ole:
            plan = ole.plan_stream_reads(['Tiny1', 'Tiny2'], budget=0)
            self.assertEqual(plan.read_count, 1)
            self.assertEqual(len(plan.ranges), 1)
            self.assertEqual(plan.ranges[0].length, ole.sector_size)
            self.assertEqual(plan.extra_bytes, 0)
            self.assertEqual(plan.streams['Tiny1'].read_indices, (0,))
            self.assertEqual(plan.streams['Tiny2'].read_indices, (0,))
            assembled = plan.fetch()
            self.assertEqual(assembled['Tiny1'], streams['Tiny1'])
            self.assertEqual(assembled['Tiny2'], streams['Tiny2'])

    def test_forbidden_gap_never_crossed(self):
        """Forbidden interior bytes split reads even when budget is unlimited."""
        streams = {'Left': b'L' * 5120, 'Right': b'R' * 5120}
        order = [('stream', 'Left')] * 10 + ['free'] * 2 \
            + [('stream', 'Right')] * 10
        path = self._build('forbidden.ole', streams, order)
        with olefile.OleFileIO(path) as ole:
            left = ole.openstream('Left').read()
            right = ole.openstream('Right').read()
            # Two free sectors sit between the streams; that whole gap is
            # forbidden, so the plan must read the two streams separately.
            span = ((1 + 10) * 512, (1 + 12) * 512)
            plan = ole.plan_stream_reads(['Left', 'Right'],
                                         forbidden=[span])
            for item in plan.ranges:
                self.assertFalse(item.intersects(*span))
            assembled = plan.fetch()
            self.assertEqual(assembled['Left'], left)
            self.assertEqual(assembled['Right'], right)
