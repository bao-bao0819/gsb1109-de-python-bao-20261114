#------------------------------------------------------------------------------
# elftools tests
#
# This code is in the public domain
#------------------------------------------------------------------------------
import os
import unittest

from elftools.elf.addressmap import (
    KIND_FILE,
    KIND_MIXED,
    KIND_UNMAPPED,
    KIND_ZERO_FILL,
    LoadSegmentMap,
    merge_provenance,
)
from elftools.elf.elffile import ELFFile


class FakeSegment:
    """ Minimal stand-in for elftools.elf.segments.Segment, for testing
        synthetic (e.g. overlapping) program headers.
    """
    def __init__(self, vaddr, offset, filesz, memsz, ptype='PT_LOAD'):
        self._header = {
            'p_type': ptype,
            'p_vaddr': vaddr,
            'p_offset': offset,
            'p_filesz': filesz,
            'p_memsz': memsz,
        }

    def __getitem__(self, name):
        return self._header[name]


class TestLoadSegmentMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._exe_file = open(os.path.join('test',  # noqa: SIM115
                             'testfiles_for_readelf', 'exe_simple64.elf'),
                             'rb')

    @classmethod
    def tearDownClass(cls):
        cls._exe_file.close()

    def _exe_map(self):
        # exe_simple64.elf PT_LOADs:
        #   #2 vaddr 0x400000 offset 0x0   filesz 0x6c4 memsz 0x6c4
        #   #3 vaddr 0x600e18 offset 0xe18 filesz 0x204 memsz 0x218
        return LoadSegmentMap(ELFFile(self._exe_file).iter_segments())

    def test_file_backed_range(self):
        records = self._exe_map().query(0x400000, 0x4006c4)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.kind, KIND_FILE)
        self.assertEqual((record.start, record.end), (0x400000, 0x4006c4))
        self.assertEqual(len(record.file_sources), 1)
        source = record.file_sources[0]
        self.assertEqual(source.segment_index, 2)
        self.assertEqual(source.file_offset, 0x0)
        self.assertEqual(source.size, 0x6c4)
        self.assertEqual(record.zero_fill_sources, ())

    def test_zero_fill_tail(self):
        # The tail of segment #3 only occupies memory: p_memsz > p_filesz.
        records = self._exe_map().query(0x601000, 0x601030)
        self.assertEqual(len(records), 2)

        self.assertEqual(records[0].kind, KIND_FILE)
        self.assertEqual((records[0].start, records[0].end),
                         (0x601000, 0x60101c))
        self.assertEqual(records[0].file_sources[0].file_offset, 0x1000)
        self.assertEqual(records[0].file_sources[0].size, 0x1c)

        self.assertEqual(records[1].kind, KIND_ZERO_FILL)
        self.assertEqual((records[1].start, records[1].end),
                         (0x60101c, 0x601030))
        self.assertEqual(records[1].file_sources, ())
        self.assertEqual(records[1].zero_fill_sources[0].segment_index, 3)
        self.assertEqual(records[1].zero_fill_sources[0].size, 0x14)

    def test_unmapped_hole(self):
        # The gap between the two PT_LOAD segments is an address hole.
        records = self._exe_map().query(0x4006c4, 0x600e18)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, KIND_UNMAPPED)
        self.assertEqual(records[0].file_sources, ())
        self.assertEqual(records[0].zero_fill_sources, ())

    def test_spanning_query_tiles_range(self):
        records = self._exe_map().query(0x400000, 0x601030)
        kinds = [record.kind for record in records]
        self.assertEqual(kinds,
                         [KIND_FILE, KIND_UNMAPPED, KIND_FILE, KIND_ZERO_FILL])
        for prev, cur in zip(records, records[1:]):
            self.assertEqual(prev.end, cur.start)
        self.assertEqual(records[0].start, 0x400000)
        self.assertEqual(records[-1].end, 0x601030)

    def test_overlapping_segments_list_all_candidates(self):
        # Two PT_LOADs covering the same addresses with different file
        # offsets: both candidates must be reported, not just the first.
        segmap = LoadSegmentMap([
            FakeSegment(vaddr=0x1000, offset=0x200, filesz=0x100,
                        memsz=0x100),
            FakeSegment(vaddr=0x1000, offset=0x900, filesz=0x100,
                        memsz=0x100),
        ])
        records = segmap.query(0x1000, 0x1100)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.kind, KIND_FILE)
        self.assertEqual(len(record.file_sources), 2)
        self.assertEqual(
            [(s.segment_index, s.file_offset) for s in record.file_sources],
            [(0, 0x200), (1, 0x900)])

    def test_partial_overlap_and_mixed_kind(self):
        # Where segment #0 only has zero fill, segment #1 is file-backed.
        segmap = LoadSegmentMap([
            FakeSegment(vaddr=0x1000, offset=0x0, filesz=0x180,
                        memsz=0x200),
            FakeSegment(vaddr=0x1100, offset=0x800, filesz=0x100,
                        memsz=0x100),
        ])
        records = segmap.query(0x1000, 0x1200)
        self.assertEqual([r.kind for r in records],
                         [KIND_FILE, KIND_FILE, KIND_MIXED])
        both = records[1]
        self.assertEqual((both.start, both.end), (0x1100, 0x1180))
        self.assertEqual(len(both.file_sources), 2)
        mixed = records[2]
        self.assertEqual((mixed.start, mixed.end), (0x1180, 0x1200))
        self.assertEqual(len(mixed.file_sources), 1)
        self.assertEqual(mixed.file_sources[0].segment_index, 1)
        self.assertEqual(len(mixed.zero_fill_sources), 1)
        self.assertEqual(mixed.zero_fill_sources[0].segment_index, 0)

    def test_split_query_consistency(self):
        segmap = self._exe_map()
        start, end = 0x400000, 0x601030
        whole = segmap.query(start, end)
        # Splitting at canonical boundaries concatenates back exactly.
        for split in (0x4006c4, 0x600e18, 0x60101c):
            pieces = segmap.query(start, split) + segmap.query(split, end)
            self.assertEqual(pieces, whole)
        # Splitting anywhere else may refine a record at the cut point,
        # but the provenance stays identical after re-merging.
        for split in (0x400001, 0x500000, 0x60102f):
            pieces = segmap.query(start, split) + segmap.query(split, end)
            self.assertEqual(merge_provenance(pieces), whole)

    def test_empty_and_invalid_ranges(self):
        segmap = self._exe_map()
        self.assertEqual(segmap.query(0x400000, 0x400000), [])
        with self.assertRaises(ValueError):
            segmap.query(0x400100, 0x400000)
        with self.assertRaises(ValueError):
            segmap.query(-1, 0x10)

    def test_filesz_clamped_to_memsz(self):
        # A malformed segment with p_filesz > p_memsz exposes only the
        # memory-resident part.
        segmap = LoadSegmentMap([
            FakeSegment(vaddr=0x2000, offset=0x0, filesz=0x300,
                        memsz=0x100),
        ])
        records = segmap.query(0x2000, 0x2200)
        self.assertEqual([r.kind for r in records],
                         [KIND_FILE, KIND_UNMAPPED])
        self.assertEqual(records[0].file_sources[0].size, 0x100)


if __name__ == '__main__':
    unittest.main()
