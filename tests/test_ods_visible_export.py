#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ods_visible_export 的行为测试：

- 手工隐藏、收起的多级行组、重复声明的行都按逻辑行号解释；
- 只展开内层组时，外层仍收起的记录保持隐藏；
- 分两段导出再拼接，行号与整表导出完全一致。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableRow, TableRowGroup, TableCell
from odf.text import P

import ods_visible_export as ove


def data_row(text, repeat=None, visibility=None):
    row = TableRow()
    if repeat is not None:
        row.setAttribute('numberrowsrepeated', repeat)
    if visibility is not None:
        row.setAttribute('visibility', visibility)
    cell = TableCell(valuetype='string')
    cell.addElement(P(text=text))
    row.addElement(cell)
    return row


def empty_row(repeat):
    row = TableRow()
    row.setAttribute('numberrowsrepeated', repeat)
    row.addElement(TableCell())
    return row


def build_table():
    """逻辑行布局：

    1-2   普通数据行
    3     手工隐藏（visibility=collapse）
    4-6   一行重复声明 3 次
    7-9   第 1 层行组收起（内含一个展开的第 2 层组，第 8 行仍不可见）
    10-11 手工隐藏（visibility=filter），重复声明 2 次
    12    普通数据行
    13-17 空白行重复声明 5 次
    """
    table = Table(name='营业一部')
    table.addElement(data_row('A1'))
    table.addElement(data_row('A2'))
    table.addElement(data_row('H1', visibility='collapse'))
    table.addElement(data_row('R', repeat=3))
    outer = TableRowGroup()
    outer.setAttribute('display', False)
    outer.addElement(data_row('G1'))
    inner = TableRowGroup()
    inner.setAttribute('display', True)
    inner.addElement(data_row('G2'))
    outer.addElement(inner)
    outer.addElement(data_row('G3'))
    table.addElement(outer)
    table.addElement(data_row('F', repeat=2, visibility='filter'))
    table.addElement(data_row('Z'))
    table.addElement(empty_row(5))
    return table


def reason_map(hidden_ranges):
    mapping = {}
    for item in hidden_ranges:
        for number in range(item.start, item.end + 1):
            mapping[number] = list(item.reasons)
    return mapping


class TestLogicalRows(unittest.TestCase):

    def setUp(self):
        self.table = build_table()

    def test_row_numbers_expand_repeats(self):
        rows = list(ove.iter_logical_rows(self.table))
        self.assertEqual([r.number for r in rows], list(range(1, 18)))
        self.assertEqual([r.cells[0] for r in rows[3:6]], ['R', 'R', 'R'])

    def test_visible_rows(self):
        visible, _ = ove.analyze_table(self.table)
        self.assertEqual([r.number for r in visible], [1, 2, 4, 5, 6, 12])
        self.assertEqual(visible[2].cells, ['R'])

    def test_inner_expanded_group_stays_hidden_under_collapsed_outer(self):
        _, hidden = ove.analyze_table(self.table)
        mapping = reason_map(hidden)
        for number in (7, 8, 9):
            self.assertEqual(mapping[number], [ove.REASON_GROUP % 1])

    def test_hidden_ranges(self):
        _, hidden = ove.analyze_table(self.table)
        self.assertEqual(
            [(h.start, h.end, h.reasons) for h in hidden],
            [(3, 3, [ove.REASON_MANUAL]),
             (7, 9, [ove.REASON_GROUP % 1]),
             (10, 11, [ove.REASON_MANUAL])])

    def test_empty_visible_rows_skipped_unless_requested(self):
        visible, _ = ove.analyze_table(self.table)
        self.assertNotIn(13, [r.number for r in visible])
        visible_all, _ = ove.analyze_table(self.table, include_empty=True)
        self.assertEqual([r.number for r in visible_all],
                         [1, 2, 4, 5, 6, 12, 13, 14, 15, 16, 17])

    def test_segmented_export_matches_full_table(self):
        full_visible, full_hidden = ove.analyze_table(self.table)
        full_map = reason_map(full_hidden)
        for cut in (1, 3, 6, 8, 10, 12, 16):
            head_visible, head_hidden = ove.analyze_table(
                self.table, from_row=1, to_row=cut)
            tail_visible, tail_hidden = ove.analyze_table(
                self.table, from_row=cut + 1)
            joined = [r.number for r in head_visible + tail_visible]
            self.assertEqual(joined, [r.number for r in full_visible],
                             'cut at row %d' % cut)
            joined_map = reason_map(head_hidden)
            joined_map.update(reason_map(tail_hidden))
            self.assertEqual(joined_map, full_map, 'cut at row %d' % cut)


class TestCli(unittest.TestCase):

    def test_main_writes_csv_and_report(self):
        doc = OpenDocumentSpreadsheet()
        doc.spreadsheet.addElement(build_table())
        tmpdir = tempfile.mkdtemp()
        ods_path = os.path.join(tmpdir, 'sample.ods')
        csv_path = os.path.join(tmpdir, 'visible.csv')
        report_path = os.path.join(tmpdir, 'hidden.txt')
        doc.save(ods_path)
        rc = ove.main([ods_path, '--table', '营业一部',
                       '--csv', csv_path, '--report', report_path])
        self.assertEqual(rc, 0)
        with open(csv_path, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines[0], 'row,c1')
        self.assertEqual([line.split(',')[0] for line in lines[1:]],
                         ['1', '2', '4', '5', '6', '12'])
        with open(report_path, encoding='utf-8') as fh:
            report = fh.read()
        self.assertIn('第3行：手工隐藏', report)
        self.assertIn('第7-9行：第1层行组收起', report)
        self.assertIn('第10-11行：手工隐藏', report)


if __name__ == '__main__':
    unittest.main()
