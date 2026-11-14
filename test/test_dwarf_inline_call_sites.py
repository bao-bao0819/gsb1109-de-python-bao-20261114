#-------------------------------------------------------------------------------
# elftools tests
#
# Concrete inline call-site table tests.
#
# This code is in the public domain
#-------------------------------------------------------------------------------
import os
import unittest

from elftools.dwarf.inline import (
    AddressRange,
    InlineCallSite,
    InlineCallSiteTable,
    Name,
    build_inline_call_site_table,
)
from elftools.elf.elffile import ELFFile


_TEST_DIR = os.path.join('test', 'testfiles_for_unittests')
_READELF_DIR = os.path.join('test', 'testfiles_for_readelf')


def _dwarf(path):
    with open(path, 'rb') as f:
        elf = ELFFile(f)
        return elf.get_dwarf_info()


def _path(directory, filename):
    return os.path.join(directory, filename)


def _make_row(
    offset,
    name=Name('inner'),
    ranges=(AddressRange(0x100, 0x110),),
    caller=None,
    depth=0,
):
    row = InlineCallSite(
        _FakeDIE(offset),
        declaration=None,
        declaration_offset=None,
        name=name,
        name_raw=name.raw if isinstance(name, Name) else None,
        linkage_name=None,
        type_die=None,
        type_name=None,
        decl_file=None,
        decl_line=None,
        decl_column=None,
        call_file=None,
        call_line=None,
        call_column=None,
        call_pc=None,
        call_return_pc=None,
        ranges=list(ranges),
        parent=None,
        call_file_name=None,
        decl_file_name=None,
    )
    row._caller = caller
    row._depth = depth
    return row


class _FakeCU:
    cu_offset = 0


class _FakeDIE:
    def __init__(self, offset):
        self.offset = offset
        self.cu = _FakeCU()
        self.attributes = {}


class TestInlineCallSiteTable(unittest.TestCase):
    def test_duplicate_instances_sharing_a_declaration_are_both_kept(self):
        dwarf = _dwarf(_path(_TEST_DIR, 'arm_exidx_test.so.elf'))
        table = dwarf.inline_call_sites()

        first = table.row_at(0x84FD)
        second = table.row_at(0x8534)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        self.assertEqual(first.declaration_offset, second.declaration_offset)
        self.assertEqual(first.name, '~exception_ptr')
        self.assertEqual(second.name, '~exception_ptr')
        self.assertEqual(first.ranges, [AddressRange(8, 14)])
        self.assertEqual(second.ranges, [AddressRange(38, 50)])
        self.assertEqual(first.call_line, 50)
        self.assertEqual(second.call_line, 52)
        self.assertEqual(len(table.by_declaration(first.declaration)), 2)

    def test_name_and_type_come_from_the_abstract_declaration(self):
        dwarf = _dwarf(_path(_READELF_DIR, 'dwarf5-clang.o.elf'))
        table = dwarf.inline_call_sites()

        first = table.row_at(0x75)
        second = table.row_at(0xF0)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

        self.assertEqual(first.name, 'clamp')
        self.assertEqual(first.name, b'clamp')
        self.assertEqual(first.type, 'int')
        self.assertEqual(second.name, 'clamp')
        self.assertEqual(second.type, 'int')
        self.assertEqual(first.call_line, 19)
        self.assertEqual(second.call_line, 19)
        self.assertEqual(first.call_file_name, 'dwarf5-clang.c')
        self.assertEqual(first.decl_file_name, 'dwarf5-clang.h')

    def test_each_instance_keeps_its_own_ranges_and_call_site(self):
        dwarf = _dwarf(_path(_TEST_DIR, 'dwarf_gnuops1.o'))
        table = dwarf.inline_call_sites()

        first = table.row_at(0x21133)
        second = table.row_at(0x21246)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.declaration_offset, second.declaration_offset)
        self.assertEqual(first.ranges, [AddressRange(7713, 7727)])
        self.assertEqual(second.ranges, [AddressRange(7813, 7838)])
        self.assertEqual(first.call_line, 1378)
        self.assertEqual(second.call_line, 1378)
        self.assertEqual(first.call_file_name, 'ada-exp.y')

    def test_nested_callers_are_recorded(self):
        dwarf = _dwarf(_path(_TEST_DIR, 'arm_exidx_test.so.elf'))
        table = dwarf.inline_call_sites()

        outer = table.row_at(0xC7D0)
        middle = table.row_at(0xC7EA)
        inner = table.row_at(0xC803)
        self.assertIsNotNone(outer)
        self.assertIsNotNone(middle)
        self.assertIsNotNone(inner)

        self.assertIsNone(outer.caller)
        self.assertIs(middle.caller, outer)
        self.assertIs(inner.caller, middle)
        self.assertEqual(outer.depth, 0)
        self.assertEqual(middle.depth, 1)
        self.assertEqual(inner.depth, 2)
        self.assertEqual(inner.caller_offset, middle.offset)
        self.assertEqual([r.offset for r in inner.caller_chain], [middle.offset, outer.offset])
        self.assertTrue(outer.dominates(inner))

    def test_outer_gap_remains_outer_but_covered_inner_address_goes_inner(self):
        dwarf = _dwarf(_path(_READELF_DIR, 'dwarf5-clang.o.elf'))
        table = dwarf.inline_call_sites()

        outer = table.row_at(0xCF)
        inner = table.row_at(0xF0)
        self.assertEqual(outer.ranges, [
            AddressRange(359, 571),
            AddressRange(609, 700),
        ])
        self.assertEqual(inner.ranges, [
            AddressRange(462, 538),
            AddressRange(652, 665),
            AddressRange(687, 700),
        ])

        gap = table.at(560)
        self.assertIs(gap, outer)

        nested = table.at(500)
        self.assertIs(nested, inner)

        nested_gap = table.at(680)
        self.assertIs(nested_gap, outer)

        outside = table.at(1000)
        self.assertIsNone(outside)

        self.assertIs(table.at_in_cu(500, outer.die.cu), inner)

    def test_outer_gap_remains_outer_in_a_synthetic_tree(self):
        outer = _make_row(1, name=Name('outer'), ranges=(AddressRange(0, 100),))
        inner = _make_row(
            2,
            name=Name('inner'),
            ranges=(AddressRange(10, 20),),
            caller=outer,
            depth=1,
        )
        table = InlineCallSiteTable([outer, inner])

        self.assertIs(table.at(50), outer)
        self.assertIs(table.at(15), inner)

    def test_innermost_actual_range_wins_for_unrelated_rows(self):
        outer = _make_row(1, name=Name('outer'), ranges=(AddressRange(0, 100),))
        inner = _make_row(2, name=Name('inner'), ranges=(AddressRange(10, 20),))
        table = InlineCallSiteTable([outer, inner])

        self.assertIs(table.at(15), inner)
        self.assertIs(table.at(50), outer)
        self.assertEqual(table.all_at(15), [inner, outer])
        self.assertEqual(table.lookup(15), inner)
        self.assertEqual(table.get(15), inner)
        self.assertIsNone(table.get(200))

    def test_rows_are_not_keyed_by_declaration_or_name(self):
        declaration = _FakeDIE(0x42)
        first = _make_row(10, name=Name('same'), ranges=(AddressRange(0, 4),))
        second = _make_row(20, name=Name('same'), ranges=(AddressRange(8, 12),))
        first.declaration = declaration
        second.declaration = declaration
        table = InlineCallSiteTable([first, second])

        self.assertEqual(len(table), 2)
        self.assertEqual([r.offset for r in table.by_name('same')], [10, 20])
        self.assertEqual([r.offset for r in table.by_declaration(0x42)], [10, 20])
        self.assertEqual(table[0].offset, 10)
        self.assertEqual(table[1].offset, 20)

    def test_broken_declaration_keeps_instance_data(self):
        dwarf = _dwarf(_path(_TEST_DIR, 'arm_exidx_test.so.elf'))
        table = dwarf.inline_call_sites()
        row = table.row_at(0x84FD)

        original = row.declaration
        original_offset = row.declaration_offset
        broken = InlineCallSite(
            row.die,
            declaration=None,
            declaration_offset=original_offset,
            name=None,
            name_raw=None,
            linkage_name=None,
            type_die=None,
            type_name=None,
            decl_file=None,
            decl_line=None,
            decl_column=None,
            call_file=row.call_file,
            call_line=row.call_line,
            call_column=row.call_column,
            call_pc=None,
            call_return_pc=None,
            ranges=row.ranges,
            parent=row.parent,
            call_file_name=row.call_file_name,
            decl_file_name=None,
        )

        self.assertIsNotNone(original)
        self.assertEqual(broken.offset, 0x84FD)
        self.assertEqual(broken.call_line, 50)
        self.assertEqual(broken.ranges, [AddressRange(8, 14)])
        self.assertEqual(broken.declaration_offset, original_offset)
        self.assertEqual(broken.call_file_name, row.call_file_name)

    def test_broken_declaration_reference_is_tolerated(self):
        dwarf = _dwarf(_path(_TEST_DIR, 'arm_exidx_test.so.elf'))
        row = dwarf.inline_call_sites().row_at(0x84FD)

        class BrokenReference:
            form = 'DW_FORM_ref4'
            raw_value = 0x7FFFFFFF

        original = row.die.attributes['DW_AT_abstract_origin']
        row.die.attributes['DW_AT_abstract_origin'] = BrokenReference()
        try:
            rebuilt = build_inline_call_site_table(
                dwarf,
                cus=[row.die.cu],
            ).row_at(row.offset)
        finally:
            row.die.attributes['DW_AT_abstract_origin'] = original

        self.assertIsNotNone(rebuilt)
        self.assertIsNone(rebuilt.declaration)
        self.assertIsNone(rebuilt.name)
        self.assertEqual(rebuilt.call_line, 50)
        self.assertEqual(rebuilt.ranges, [AddressRange(8, 14)])
        self.assertEqual(rebuilt.call_file_name, row.call_file_name)

    def test_broken_reference_keeps_all_instance_owned_fields(self):
        dwarf = _dwarf(_path(_TEST_DIR, 'arm_exidx_test.so.elf'))
        row = dwarf.inline_call_sites().row_at(0x84FD)
        expected = (
            row.offset, row.cu_offset, row.call_file, row.call_line,
            row.call_column, row.parent_offset, row.ranges,
            row.call_file_name, row.depth, row.caller_offset,
        )

        row.die.attributes.pop('DW_AT_abstract_origin')
        rebuilt_table = build_inline_call_site_table(dwarf, cus=[row.die.cu])
        rebuilt = rebuilt_table.row_at(row.offset)
        observed = (
            rebuilt.offset, rebuilt.cu_offset, rebuilt.call_file,
            rebuilt.call_line, rebuilt.call_column, rebuilt.parent_offset,
            rebuilt.ranges, rebuilt.call_file_name, rebuilt.depth,
            rebuilt.caller_offset,
        )

        self.assertIsNone(rebuilt.declaration)
        self.assertIsNone(rebuilt.declaration_offset)
        self.assertIsNone(rebuilt.name)
        self.assertIsNone(rebuilt.type)
        self.assertEqual(observed, expected)

    def test_declaration_chain_fills_missing_instance_metadata(self):
        dwarf = _dwarf(_path(_TEST_DIR, 'arm_exidx_test.so.elf'))
        table = dwarf.inline_call_sites()
        row = table.row_at(0x84FD)

        self.assertIsNotNone(row.declaration)
        self.assertEqual(row.name, '~exception_ptr')
        self.assertEqual(row.linkage_name, '_ZNSt13exception_ptrD2Ev')
        self.assertEqual(row.decl_line, 16)
        self.assertIsNotNone(row.decl_file_name)
        self.assertEqual(row.declaration.offset, row.declaration_offset)
        self.assertEqual(row.offset, 0x84FD)

    def test_table_entries_are_in_die_order(self):
        dwarf = _dwarf(_path(_READELF_DIR, 'dwarf5-clang.o.elf'))
        table = dwarf.inline_call_sites()
        offsets = [row.offset for row in table]

        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(offsets, [0x75, 0xCF, 0xF0, 0x10E])
        self.assertEqual(len(table.rows), 4)


if __name__ == '__main__':
    unittest.main()
