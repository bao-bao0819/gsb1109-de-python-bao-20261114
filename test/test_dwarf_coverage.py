#-------------------------------------------------------------------------------
# elftools tests
#
# Coverage accounting built on top of DWARF range lists. Exercises the
# .debug_ranges (DWARF <= 4) and .debug_rnglists (DWARF 5) encodings,
# base-address changes, source-attributed merging and overlap accounting.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
import os
import unittest

from elftools.dwarf.coverage import (
    CoverageReport,
    CoverageSource,
    CoverageUnit,
    SourceInterval,
    all_coverage_intervals,
    coverage_by_unit,
    coverage_size,
    cu_coverage_intervals,
    die_coverage_intervals,
    merge_adjacent,
    resolve_range_entries,
    union_coverage,
)
from elftools.dwarf.ranges import BaseAddressEntry, RangeEntry
from elftools.elf.elffile import ELFFile

UNITTEST_DIR = os.path.join('test', 'testfiles_for_unittests')
READELF_DIR = os.path.join('test', 'testfiles_for_readelf')
LOCATION_DIR = os.path.join('test', 'testfiles_for_location_info')


def _dwarfinfo(testcase, path):
    # pyelftools parses lazily, so the stream must stay open for the test.
    stream = open(path, 'rb')
    testcase.addCleanup(stream.close)
    return ELFFile(stream).get_dwarf_info()


class TestResolveRangeEntries(unittest.TestCase):
    def setUp(self):
        self.source = CoverageSource(
            cu_offset=0x10, cu_name='a.c', die_offset=0x20,
            die_tag='DW_TAG_subprogram', section_name='.debug_ranges')

    def test_relative_entries_use_initial_base(self):
        entries = [
            RangeEntry(0x00, 8, 0x10, 0x20, False),
            RangeEntry(0x08, 8, 0x30, 0x40, False),
        ]
        intervals = resolve_range_entries(entries, self.source, base=0x1000)
        self.assertEqual(
            [(iv.begin_address, iv.end_address) for iv in intervals],
            [(0x1010, 0x1020), (0x1030, 0x1040)])
        self.assertTrue(all(iv.base_address == 0x1000 for iv in intervals))
        self.assertEqual(intervals[0].raw_entries, (entries[0],))

    def test_base_address_change_only_applies_forward(self):
        base_entry = BaseAddressEntry(0x10, base_address=0x2000)
        entries = [
            RangeEntry(0x00, 8, 0x01, 0x02, False),
            base_entry,
            RangeEntry(0x18, 8, 0x01, 0x02, False),
        ]
        intervals = resolve_range_entries(entries, self.source, base=0x1000)
        self.assertEqual(
            [(iv.begin_address, iv.end_address) for iv in intervals],
            [(0x1001, 0x1002), (0x2001, 0x2002)])
        self.assertEqual(intervals[0].base_address, 0x1000)
        self.assertEqual(intervals[1].base_address, 0x2000)
        # The governing base entry stays reachable through the raw entries.
        self.assertEqual(intervals[1].raw_entries, (base_entry, entries[2]))

    def test_absolute_entries_ignore_base(self):
        entries = [
            BaseAddressEntry(0x00, base_address=0x2000),
            RangeEntry(0x08, 8, 0x5000, 0x5010, True),
        ]
        intervals = resolve_range_entries(entries, self.source, base=0x1000)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(
            (intervals[0].begin_address, intervals[0].end_address),
            (0x5000, 0x5010))
        self.assertIsNone(intervals[0].base_address)

    def test_relative_entry_without_base_is_skipped(self):
        entries = [
            RangeEntry(0x00, 8, 0x10, 0x20, False),
            RangeEntry(0x08, 8, 0x30, 0x40, True),
        ]
        intervals = resolve_range_entries(entries, self.source, base=None)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(
            (intervals[0].begin_address, intervals[0].end_address),
            (0x30, 0x40))

    def test_zero_length_intervals_are_dropped(self):
        entries = [RangeEntry(0x00, 8, 0x10, 0x10, False)]
        self.assertEqual(
            resolve_range_entries(entries, self.source, base=0x1000), [])


class TestMergingAndUnion(unittest.TestCase):
    def _interval(self, begin, end, source, raw_offset=0):
        entry = RangeEntry(raw_offset, 8, begin, end, True)
        return SourceInterval(begin, end, source, None, (entry,))

    def test_adjacent_same_source_merges_and_keeps_raw_entries(self):
        source = CoverageSource(1, 'a.c', 2, 'DW_TAG_subprogram', '.debug_ranges')
        first = self._interval(0x100, 0x200, source, raw_offset=0x10)
        second = self._interval(0x200, 0x300, source, raw_offset=0x20)
        merged = merge_adjacent([second, first])
        self.assertEqual(len(merged), 1)
        self.assertEqual(
            (merged[0].begin_address, merged[0].end_address),
            (0x100, 0x300))
        raw_offsets = [e.entry_offset for e in merged[0].raw_entries]
        self.assertEqual(raw_offsets, [0x10, 0x20])

    def test_different_sources_are_never_merged(self):
        source_a = CoverageSource(1, 'a.c', 2, 'DW_TAG_subprogram', '.debug_ranges')
        source_b = CoverageSource(3, 'b.c', 4, 'DW_TAG_subprogram', '.debug_ranges')
        intervals = [
            self._interval(0x100, 0x200, source_a),
            self._interval(0x200, 0x300, source_b),
        ]
        self.assertEqual(len(merge_adjacent(intervals)), 2)

    def test_union_counts_overlap_once(self):
        source_a = CoverageSource(1, 'a.c', 2, 'DW_TAG_subprogram', '.debug_ranges')
        source_b = CoverageSource(3, 'b.c', 4, 'DW_TAG_subprogram', '.debug_ranges')
        intervals = [
            self._interval(0x100, 0x300, source_a),
            self._interval(0x200, 0x400, source_b),
        ]
        self.assertEqual(union_coverage(intervals), [(0x100, 0x400)])
        self.assertEqual(coverage_size(intervals), 0x300)

    def test_per_unit_keeps_both_attributions(self):
        unit_a = CoverageUnit(1, 'a.c')
        unit_b = CoverageUnit(3, 'b.c')
        source_a = CoverageSource(1, 'a.c', 2, 'DW_TAG_subprogram', '.debug_ranges')
        source_b = CoverageSource(3, 'b.c', 4, 'DW_TAG_subprogram', '.debug_ranges')
        intervals = [
            self._interval(0x100, 0x300, source_a),
            self._interval(0x200, 0x400, source_b),
        ]
        report = CoverageReport(intervals)
        # The 0x100 shared bytes are counted once in total ...
        self.assertEqual(report.total_size(), 0x300)
        # ... but once per owning unit, so both units claim them.
        per_unit = report.per_unit_size()
        self.assertEqual(per_unit[unit_a], 0x200)
        self.assertEqual(per_unit[unit_b], 0x200)
        self.assertEqual(sum(per_unit.values()), 0x400)
        owners = report.intervals_at(0x250)
        self.assertEqual(
            {iv.source.cu_offset for iv in owners}, {1, 3})
        self.assertEqual(
            {unit for unit in coverage_by_unit(intervals)},
            {unit_a, unit_b})


class TestRealFixtures(unittest.TestCase):
    def test_dwarf2_relative_ranges_in_sample_exe(self):
        di = _dwarfinfo(self, os.path.join('examples', 'sample_exe64.elf'))
        range_lists = di.range_lists()
        cu = next(cu for cu in di.iter_CUs() if cu.cu_offset == 0x1a2)
        die = next(d for d in cu.iter_DIEs()
                   if 'DW_AT_ranges' in d.attributes)
        intervals = die_coverage_intervals(die, cu, range_lists)
        # Matches examples/reference_output/dwarf_range_lists.out
        self.assertEqual(
            (intervals[0].begin_address, intervals[0].end_address),
            (0x40053a, 0x400548))
        self.assertEqual(intervals[0].base_address, 0x400520)
        self.assertEqual(intervals[0].source.section_name, '.debug_ranges')
        self.assertEqual(intervals[0].source.die_offset, die.offset)
        self.assertTrue(intervals[0].raw_entries)

        report = CoverageReport.from_dwarfinfo(di)
        # One CU also has low_pc == high_pc (empty), so only three CU spans
        # carry low_pc/high_pc bytes; every interval stays well-formed.
        self.assertTrue(all(
            iv.end_address > iv.begin_address for iv in report))
        self.assertEqual(
            report.total_size(), coverage_size(all_coverage_intervals(di)))

    def test_dwarf4_base_address_selection_entries(self):
        path = os.path.join(UNITTEST_DIR, 'note_tc3xxx_blinky.elf')
        di = _dwarfinfo(self, path)
        intervals = all_coverage_intervals(di)
        base_driven = [iv for iv in intervals
                       if iv.base_address is not None and iv.raw_entries
                       and isinstance(iv.raw_entries[0], BaseAddressEntry)]
        self.assertTrue(base_driven)
        sample = base_driven[0]
        base_entry = sample.raw_entries[0]
        range_entry = sample.raw_entries[1]
        self.assertEqual(
            (sample.begin_address, sample.end_address),
            (base_entry.base_address + range_entry.begin_offset,
             base_entry.base_address + range_entry.end_offset))

    def test_dwarf5_multiple_encodings_in_one_object(self):
        path = os.path.join(READELF_DIR, 'dwarf_v5ops.so.elf')
        di = _dwarfinfo(self, path)
        intervals = all_coverage_intervals(di)
        range_intervals = [iv for iv in intervals
                           if iv.source.section_name]
        # One object mixes relative offset pairs (base-driven) with absolute
        # start/startx entries, all carried by .debug_rnglists.
        self.assertTrue(any(iv.base_address is not None
                            for iv in range_intervals))
        self.assertTrue(any(iv.base_address is None
                            for iv in range_intervals))
        self.assertTrue(all(
            iv.source.section_name == '.debug_rnglists'
            for iv in range_intervals))
        # Every range-derived interval points back at a raw range-list entry.
        self.assertTrue(all(iv.raw_entries for iv in range_intervals))
        # A CU is scattered over distant regions: its union keeps several
        # disjoint spans instead of one min/max block.
        report = CoverageReport.from_dwarfinfo(di)
        self.assertTrue(any(
            len(spans) > 1 for spans in report.by_unit().values()))

    def test_mixed_v4_and_v5_sections(self):
        path = os.path.join(READELF_DIR, 'dwarf_test_versions_mix.so.elf')
        di = _dwarfinfo(self, path)
        intervals = all_coverage_intervals(di)
        sections = {iv.source.section_name for iv in intervals
                    if iv.source.section_name}
        self.assertEqual(sections, {'.debug_ranges', '.debug_rnglists'})

        report = CoverageReport.from_dwarfinfo(di)
        # Without merging, raw per-entry intervals are at least as numerous.
        unmerged = CoverageReport.from_dwarfinfo(di, merge=False)
        self.assertGreaterEqual(len(unmerged), len(report))
        # Global total never exceeds the sum of per-unit attributions.
        self.assertLessEqual(
            report.total_size(), sum(report.per_unit_size().values()))

    def test_two_objects_partially_overlapping(self):
        # Relocatable objects number their code from zero, so combining two
        # objects yields genuine cross-unit overlaps without editing bytes.
        paths = [
            os.path.join(UNITTEST_DIR, 'dwarf_gnuops1.o'),
            os.path.join(UNITTEST_DIR, 'arm_exidx_test.o'),
        ]
        intervals = []
        streams = []
        try:
            for path in paths:
                stream = open(path, 'rb')
                streams.append(stream)
                intervals.extend(
                    all_coverage_intervals(ELFFile(stream).get_dwarf_info()))
        finally:
            for stream in streams:
                stream.close()

        report = CoverageReport(intervals)
        per_unit = report.per_unit_size()
        self.assertEqual(len(per_unit), 2)
        # The shared low-address region is counted once overall ...
        self.assertEqual(
            report.total_size(),
            sum(end - begin for begin, end in report.union_spans()))
        self.assertLess(report.total_size(), sum(per_unit.values()))
        # ... but both units keep their attribution to the overlapping byte.
        owners = report.intervals_at(0)
        # Nested DIEs may each cover byte 0, but they belong to exactly two
        # compilation units (one per object, both numbered from offset zero).
        owning_units = {
            (iv.source.cu_name) for iv in owners}
        self.assertEqual(len(owning_units), 2)
        self.assertTrue(all(iv.source.cu_offset == 0 for iv in owners))
        for unit in per_unit:
            self.assertIn(unit, report.by_unit())

    def test_constant_form_high_pc(self):
        path = os.path.join(LOCATION_DIR, 'test-dwarf4.o')
        di = _dwarfinfo(self, path)
        cu = next(iter(di.iter_CUs()))
        top = cu.get_top_DIE()
        low_pc = top.attributes['DW_AT_low_pc'].value
        high_pc_attr = top.attributes['DW_AT_high_pc']
        intervals = cu_coverage_intervals(cu, di.range_lists())
        low_high = [iv for iv in intervals if iv.source.section_name == '']
        self.assertTrue(low_high)
        self.assertEqual(
            low_high[0].end_address, low_pc + high_pc_attr.value)
        self.assertEqual(low_high[0].begin_address, low_pc)


if __name__ == '__main__':
    unittest.main()
