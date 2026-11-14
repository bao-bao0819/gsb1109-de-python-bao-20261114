#!/usr/bin/env python
# -*- coding: utf-8 -*-
""" Tests for range-based, repeat-aware table reading. """

import os
import os.path
import unittest
from io import BytesIO

from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.table import (
    Table, TableColumn, TableRow, TableCell, CoveredTableCell,
    TableRowGroup, TableHeaderRows,
)
from odf.text import P
from odf.tablewindow import TableWindow, CellWindow, column_letter, as_coordinate


def cell(text=None, **attrs):
    tc = TableCell(**attrs)
    if text is not None:
        tc.addElement(P(text=text))
    return tc


def row(*children, **attrs):
    tr = TableRow(**attrs)
    for child in children:
        tr.addElement(child)
    return tr


class WindowTestBase(unittest.TestCase):

    def build(self, rows, columns=()):
        doc = OpenDocumentSpreadsheet()
        table = Table(name=u"sheet1")
        for column_repeat in columns:
            table.addElement(TableColumn(numbercolumnsrepeated=column_repeat))
        for row_element in rows:
            table.addElement(row_element)
        doc.spreadsheet.addElement(table)
        return table, doc

    def roundtrip(self, doc):
        buffer = BytesIO()
        doc.write(buffer)
        buffer.seek(0)
        loaded = load(buffer)
        return loaded.getElementsByType(Table)[0]


class TestColumnLetters(unittest.TestCase):

    def test_letters_and_coordinates(self):
        self.assertEqual(column_letter(0), u"A")
        self.assertEqual(column_letter(25), u"Z")
        self.assertEqual(column_letter(26), u"AA")
        self.assertEqual(column_letter(701), u"ZZ")
        self.assertEqual(column_letter(702), u"AAA")
        self.assertEqual(as_coordinate(0, 0), u"A1")
        self.assertEqual(as_coordinate(11, 2), u"C12")


class TestRepeatedRowsAndCells(WindowTestBase):

    def _sheet(self):
        rows = [
            row(cell(u"head", valuetype=u"string")),
            # 300 000 empty rows, themselves made of repeated empty cells.
            row(TableCell(numbercolumnsrepeated=4),
                numberrowsrepeated=300000),
            row(cell(u"x", valuetype=u"string"),
                TableCell(numbercolumnsrepeated=5),
                cell(u"y", valuetype=u"string")),
        ]
        table, doc = self.build(rows, columns=(4,))
        return self.roundtrip(doc)

    def test_window_skips_repeated_rows(self):
        table = self._sheet()
        window = TableWindow(table, row_start=300001, column_start=0,
                             row_end=300002, column_end=7)
        result = list(window.rows())
        self.assertEqual(len(result), 1)
        values = [c.value for c in result[0]]
        self.assertEqual(values, [u"x", None, None, None, None, None, u"y"])
        self.assertEqual([c.column for c in result[0]], list(range(7)))
        y_cell = result[0][6]
        self.assertEqual(y_cell.coordinate, u"G300002")
        self.assertFalse(y_cell.covered)
        self.assertEqual(y_cell.source_coordinate, u"G300002")

    def test_repeated_empty_cells_keep_their_columns(self):
        table = self._sheet()
        window = TableWindow(table, row_start=300001, column_start=0,
                             row_end=300002, column_end=7)
        only = list(window.rows())[0]
        empties = only[1:6]
        self.assertTrue(all(c.value is None for c in empties))
        self.assertTrue(all(c.source_element is None for c in empties))
        self.assertEqual([c.column for c in empties], [1, 2, 3, 4, 5])

    def test_memory_does_not_grow_with_repeat_count(self):
        table = self._sheet()
        created = []
        original = CellWindow.__init__

        def counting_init(self, *args, **kwargs):
            created.append(1)
            return original(self, *args, **kwargs)

        CellWindow.__init__ = counting_init
        try:
            window = TableWindow(table, row_start=300000, column_start=1,
                                 row_end=300002, column_end=4)
            rows = list(window.rows())
        finally:
            CellWindow.__init__ = original

        self.assertEqual(len(rows), 2)
        # Two rows times three columns, regardless of the 300 000 repeats.
        self.assertEqual(len(created), 6)

    def test_head_row_before_the_gap(self):
        table = self._sheet()
        window = TableWindow(table, row_start=0, column_start=0,
                             row_end=1, column_end=1)
        first = list(window.rows())[0][0]
        self.assertEqual(first.value, u"head")
        self.assertEqual(first.coordinate, u"A1")

    def test_values_helper(self):
        table = self._sheet()
        values = list(TableWindow(table, 0, 0, row_end=1, column_end=1).values())
        self.assertEqual(values, [[u"head"]])
        records = list(TableWindow.read_range(table, 0, 0, 1, 1))
        self.assertEqual(records[0][0].coordinate, u"A1")
        self.assertEqual(records[0][0].value, u"head")

    def test_open_ended_window_reads_to_last_row(self):
        table = self._sheet()
        rows = list(TableWindow(table, row_start=300001,
                                column_start=6, column_end=7).rows())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0].value, u"y")
        self.assertEqual(rows[0][0].coordinate, u"G300002")

    def test_column_window_inside_repeated_empty_run(self):
        table = self._sheet()
        window = TableWindow(table, row_start=300001, column_start=2,
                             row_end=300002, column_end=5)
        records = list(window.rows())[0]
        self.assertEqual([c.column for c in records], [2, 3, 4])
        self.assertEqual([c.coordinate for c in records],
                         [u"C300002", u"D300002", u"E300002"])
        self.assertTrue(all(c.value is None for c in records))


class TestMergedCells(WindowTestBase):

    def _sheet(self):
        anchor = cell(u"merged", valuetype=u"string",
                      numbercolumnsspanned=2, numberrowsspanned=2)
        rows = [
            row(anchor, CoveredTableCell()),
            row(CoveredTableCell(), CoveredTableCell()),
            row(cell(u"a", valuetype=u"string"),
                cell(u"b", valuetype=u"string")),
        ]
        table, doc = self.build(rows, columns=(2,))
        return self.roundtrip(doc)

    def test_anchor_reports_itself(self):
        table = self._sheet()
        window = TableWindow(table, 0, 0, row_end=1, column_end=2)
        anchor = list(window.rows())[0][0]
        self.assertEqual(anchor.value, u"merged")
        self.assertFalse(anchor.covered)
        self.assertEqual(anchor.source_coordinate, u"A1")
        self.assertIsNotNone(anchor.source_element)

    def test_covered_cell_points_at_anchor_inside_window(self):
        table = self._sheet()
        window = TableWindow(table, 0, 1, row_end=2, column_end=2)
        rows = list(window.rows())
        top_right = rows[0][0]
        bottom_left = rows[1][0]
        anchor_window = TableWindow(table, 0, 0, row_end=1, column_end=1)
        anchor_element = list(anchor_window.rows())[0][0].source_element
        for covered_cell in (top_right, bottom_left):
            self.assertTrue(covered_cell.covered)
            self.assertEqual(covered_cell.value, u"merged")
            self.assertEqual(covered_cell.source_coordinate, u"A1")
            self.assertIs(covered_cell.source_element, anchor_element)

    def test_anchor_outside_window_still_supplies_value(self):
        table = self._sheet()
        # Window starts at row 2 (0-based) / column B: the A1 anchor is
        # outside it but must still be reported as the value source.
        window = TableWindow(table, row_start=1, column_start=1,
                             row_end=2, column_end=2)
        cell_window = list(window.rows())[0][0]
        self.assertTrue(cell_window.covered)
        self.assertEqual(cell_window.coordinate, u"B2")
        self.assertEqual(cell_window.source_coordinate, u"A1")
        self.assertEqual(cell_window.value, u"merged")
        self.assertIsNotNone(cell_window.source_element)
        self.assertEqual(cell_window.source_row, 0)
        self.assertEqual(cell_window.source_column, 0)

    def test_merge_does_not_leak_into_later_rows(self):
        table = self._sheet()
        window = TableWindow(table, row_start=2, column_start=0,
                             row_end=3, column_end=2)
        values = [c.value for c in list(window.rows())[0]]
        self.assertEqual(values, [u"a", u"b"])

    def test_window_can_be_iterated_more_than_once(self):
        table = self._sheet()
        window = TableWindow(table, 0, 0, row_end=2, column_end=2)

        def snapshot():
            return [[(c.value, c.covered, c.source_coordinate) for c in r]
                    for r in window.rows()]

        self.assertEqual(snapshot(), snapshot())

    def test_anchor_column_left_of_window_still_supplies_value(self):
        anchor = cell(u"wide", valuetype=u"string", numbercolumnsspanned=3)
        rows = [row(anchor, CoveredTableCell(numbercolumnsrepeated=2)),
                row(cell(u"z", valuetype=u"string"))]
        table, doc = self.build(rows, columns=(3,))
        table = self.roundtrip(doc)
        window = TableWindow(table, row_start=0, column_start=1,
                             row_end=1, column_end=3)
        records = list(window.rows())[0]
        self.assertEqual([c.coordinate for c in records], [u"B1", u"C1"])
        self.assertTrue(all(c.covered for c in records))
        self.assertTrue(all(c.source_coordinate == u"A1" for c in records))
        self.assertEqual([c.value for c in records], [u"wide", u"wide"])


class TestRowContainers(WindowTestBase):

    def test_header_rows_and_groups(self):
        doc = OpenDocumentSpreadsheet()
        table = Table(name=u"sheet1")
        header = TableHeaderRows()
        header.addElement(row(cell(u"h", valuetype=u"string")))
        table.addElement(header)
        group = TableRowGroup()
        group.addElement(row(cell(u"g", valuetype=u"string")))
        table.addElement(group)
        doc.spreadsheet.addElement(table)
        table = self.roundtrip(doc)

        rows = list(TableWindow(table, 0, 0, row_end=2, column_end=1).values())
        self.assertEqual(rows, [[u"h"], [u"g"]])


class TestValueMetadata(WindowTestBase):

    def test_raw_value_formula_and_currency(self):
        number_cell = TableCell(valuetype=u"float", value=u"3.5",
                                formula=u"of:=1+2.5", currency=u"EUR")
        number_cell.addElement(P(text=u"3,5"))
        rows = [row(number_cell)]
        table, doc = self.build(rows, columns=(1,))
        table = self.roundtrip(doc)
        record = list(TableWindow(table, 0, 0, 1, 1).rows())[0][0]
        self.assertEqual(record.value, u"3,5")
        self.assertEqual(record.raw_value, u"3.5")
        self.assertEqual(record.value_type, u"float")
        self.assertEqual(record.formula, u"of:=1+2.5")
        self.assertEqual(record.currency, u"EUR")


class TestBounds(unittest.TestCase):

    def test_invalid_bounds(self):
        doc = OpenDocumentSpreadsheet()
        table = Table(name=u"s")
        doc.spreadsheet.addElement(table)
        with self.assertRaises(ValueError):
            TableWindow(table, row_start=-1)
        with self.assertRaises(ValueError):
            TableWindow(table, column_start=1, column_end=0)
        with self.assertRaises(TypeError):
            TableWindow(doc.spreadsheet)


class TestRealSpreadsheet(unittest.TestCase):

    def test_pythagoras_window(self):
        path = os.path.join(os.path.dirname(__file__),
                            u"examples", u"pythagoras.ods")
        doc = load(path)
        table = doc.getElementsByType(Table)[0]
        rows = list(TableWindow(table, 0, 0, row_end=4, column_end=1).rows())
        self.assertEqual([row_cells[0].value for row_cells in rows],
                         [u"3", u"4", u"5", u"7"])
        hypotenuse = rows[2][0]
        self.assertEqual(hypotenuse.raw_value, u"5")
        self.assertEqual(hypotenuse.value_type, u"float")
        self.assertIn(u"SQRT", hypotenuse.formula)

    def test_repeated_non_empty_cell_in_real_file(self):
        path = os.path.join(os.path.dirname(__file__),
                            u"examples", u"chinese_spreadsheet.ods")
        doc = load(path)
        table = doc.getElementsByType(Table)[0]
        records = list(TableWindow(table, 0, 0, row_end=1, column_end=3).rows())[0]
        self.assertEqual([c.value for c in records], [u"test", u"test", u"test"])
        self.assertEqual([c.coordinate for c in records], [u"A1", u"B1", u"C1"])
        self.assertIs(records[0].source_element, records[2].source_element)
        self.assertEqual(records[2].source_coordinate, u"A1")


if __name__ == u"__main__":
    unittest.main()
