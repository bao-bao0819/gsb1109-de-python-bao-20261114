#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Tests for reading spreadsheet rows through the database-range filter
# declared inside the ODS file.

import os
import tempfile
import unittest
from io import BytesIO

from odf.dbfilter import (
    EMPTY,
    INCOMPARABLE,
    column_letter,
    main,
    parse_range_address,
    read_rows,
)
from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import (
    DatabaseRange,
    DatabaseRanges,
    Filter,
    FilterAnd,
    FilterCondition,
    FilterOr,
    Table,
    TableCell,
    TableColumn,
    TableRow,
    TableRows,
)
from odf.text import P


def _cell(value, valuetype=None):
    if value is None:
        return TableCell()
    if valuetype is None:
        valuetype = "float"
    if valuetype == "float":
        cell = TableCell(valuetype="float", value=str(value))
    else:
        cell = TableCell(valuetype="string")
    cell.addElement(P(text=str(value)))
    return cell


def _row(values):
    row = TableRow()
    for value in values:
        if isinstance(value, tuple):
            row.addElement(_cell(value[0], value[1]))
        else:
            row.addElement(_cell(value))
    return row


def _save(doc):
    stream = BytesIO()
    doc.write(stream)
    stream.seek(0)
    return stream


def _build_sales_doc():
    """Region sheet with a title row, header row, data, and an outside note.

    Layout (1-based):
      row 1: region title (outside the database range)
      row 2: header  地区 / 商品 / 数量
      row 3: 北区 苹果 10
      row 4: 西区 梨   5
      row 5: 东区 桃   8        (fails the region group)
      row 6: 北区 香蕉 0        (fails the quantity condition)
      row 7: 西区 橙子 缺货     (text in the quantity column)
      row 8: 北区 葡萄 None     (empty quantity cell)
      row 9: outside note
    Filter: (region == 北区 OR region == 西区) AND quantity > 0
    """
    doc = OpenDocumentSpreadsheet()
    table = Table(name="销售明细")
    table.addElement(TableColumn())
    table.addElement(TableColumn())
    table.addElement(TableColumn())
    rows = TableRows()
    table.addElement(rows)

    title = TableRow()
    title_cell = TableCell(valuetype="string")
    title_cell.addElement(P(text="华北大区销售统计表（标题行）"))
    title.addElement(title_cell)
    rows.addElement(title)

    rows.addElement(_row([("地区", "string"), ("商品", "string"),
                          ("数量", "string")]))
    rows.addElement(_row([("北区", "string"), ("苹果", "string"), 10]))
    rows.addElement(_row([("西区", "string"), ("梨", "string"), 5]))
    rows.addElement(_row([("东区", "string"), ("桃", "string"), 8]))
    rows.addElement(_row([("北区", "string"), ("香蕉", "string"), 0]))
    rows.addElement(_row([("西区", "string"), ("橙子", "string"),
                          ("缺货", "string")]))
    rows.addElement(_row([("北区", "string"), ("葡萄", "string"), None]))

    note = TableRow()
    note_cell = TableCell(valuetype="string")
    note_cell.addElement(P(text="附注：本表数据由供应商系统导出"))
    note.addElement(note_cell)
    rows.addElement(note)

    db_ranges = DatabaseRanges()
    db_range = DatabaseRange(
        name="销售区域筛选",
        targetrangeaddress="$销售明细.$A$2:$C$8",
        containsheader="true",
        displayfilterbuttons="true",
    )
    flt = Filter(conditionsource="self")
    group_and = FilterAnd()
    group_or = FilterOr()
    group_or.addElement(FilterCondition(
        operator="=", fieldnumber="0", value="北区", datatype="text"))
    group_or.addElement(FilterCondition(
        operator="=", fieldnumber="0", value="西区", datatype="text"))
    group_and.addElement(group_or)
    group_and.addElement(FilterCondition(
        operator=">", fieldnumber="2", value="0", datatype="number"))
    flt.addElement(group_and)
    db_range.addElement(flt)
    db_ranges.addElement(db_range)
    doc.spreadsheet.addElement(db_ranges)
    doc.spreadsheet.addElement(table)
    return _save(doc)


class TestRangeAddress(unittest.TestCase):

    def test_full_address(self):
        sheet, col1, row1, col2, row2 = parse_range_address(
            "$销售明细.$A$2:$C$8")
        self.assertEqual(sheet, "销售明细")
        self.assertEqual((col1, row1, col2, row2), (1, 2, 3, 8))

    def test_single_cell_and_quoted_sheet(self):
        sheet, col1, row1, col2, row2 = parse_range_address(
            "'My Sheet'.K12")
        self.assertEqual(sheet, "My Sheet")
        self.assertEqual((col1, row1, col2, row2), (11, 12, 11, 12))

    def test_column_letter(self):
        self.assertEqual(column_letter(1), "A")
        self.assertEqual(column_letter(26), "Z")
        self.assertEqual(column_letter(27), "AA")
        self.assertEqual(column_letter(52), "AZ")


class TestFilteredRead(unittest.TestCase):

    def setUp(self):
        self.stream = _build_sales_doc()
        self.result = read_rows(self.stream)

    def test_range_metadata(self):
        self.assertEqual(self.result.table_name, "销售明细")
        self.assertEqual(self.result.range_name, "销售区域筛选")
        self.assertTrue(self.result.contains_header)
        self.assertEqual(self.result.data_row_count, 6)

    def test_header_is_separate_and_not_data(self):
        self.assertIsNotNone(self.result.header)
        self.assertEqual(
            [cell.text for cell in self.result.header],
            ["地区", "商品", "数量"])
        header_coordinates = [cell.coordinate for cell in self.result.header]
        self.assertEqual(header_coordinates, ["A2", "B2", "C2"])
        self.assertTrue(all(row.row != 2 for row in self.result.rows))

    def test_nested_grouping_preserved(self):
        # Only 北区/西区 rows with quantity strictly greater than zero pass.
        kept = [(row.cells[0].text, row.cells[2].text)
                for row in self.result.rows]
        self.assertEqual(kept, [("北区", "10"), ("西区", "5")])
        # 东区 fails the OR group; 北区/香蕉(0) fails quantity > 0.
        kept_rows = [row.row for row in self.result.rows]
        self.assertEqual(kept_rows, [3, 4])

    def test_original_coordinates_attached(self):
        first = self.result.rows[0]
        self.assertEqual(first.coordinate, "A3:C3")
        self.assertEqual(
            [cell.coordinate for cell in first.cells],
            ["A3", "B3", "C3"])

    def test_title_row_and_outside_note_are_not_data(self):
        seen_text = [
            cell.text for row in self.result.rows for cell in row.cells]
        header_text = [cell.text for cell in self.result.header]
        self.assertNotIn("华北大区销售统计表（标题行）", seen_text)
        self.assertNotIn("附注：本表数据由供应商系统导出", seen_text)
        self.assertNotIn("华北大区销售统计表（标题行）", header_text)
        self.assertTrue(all(2 <= row.row <= 8 for row in self.result.rows))

    def test_text_quantity_is_incomparable_not_zero(self):
        issue = next(i for i in self.result.issues
                     if i.reason == INCOMPARABLE)
        self.assertEqual(issue.coordinate, "C7")
        self.assertEqual(issue.field_number, 2)
        self.assertEqual(issue.operator, ">")
        self.assertEqual(issue.text, "缺货")
        # The row must not be treated as 0 > 0 ... it is excluded outright.
        self.assertTrue(all(row.row != 7 for row in self.result.rows))

    def test_empty_quantity_is_reported_empty(self):
        issue = next(i for i in self.result.issues if i.reason == EMPTY)
        self.assertEqual(issue.coordinate, "C8")
        self.assertEqual(issue.field_number, 2)
        self.assertTrue(all(row.row != 8 for row in self.result.rows))

    def test_zero_quantity_is_a_normal_false_comparison(self):
        zero_issue = [i for i in self.result.issues
                      if i.coordinate == "C6"]
        self.assertEqual(zero_issue, [])
        self.assertTrue(all(row.row != 6 for row in self.result.rows))

    def test_no_filter_returns_every_data_row_in_range(self):
        self.stream.seek(0)
        unfiltered = read_rows(self.stream, apply_filter=False)
        self.assertEqual(unfiltered.data_row_count, 6)
        self.assertEqual(unfiltered.filtered_row_count, 6)
        self.assertEqual(unfiltered.issues, [])
        self.assertEqual(
            [row.row for row in unfiltered.rows], [3, 4, 5, 6, 7, 8])
        # Title row and note stay outside the range even without filter.
        self.assertTrue(all(2 <= row.row <= 8
                            for row in unfiltered.rows))


class TestNestedGroupingAndIssueReporting(unittest.TestCase):
    """Filter: region in (北区, 西区) AND (quantity > 5 OR commodity == 梨)."""

    def _doc(self, quantity, commodity, region):
        doc = OpenDocumentSpreadsheet()
        table = Table(name="嵌套表")
        table.addElement(TableColumn(numbercolumnsrepeated="3"))
        rows = TableRows()
        table.addElement(rows)
        rows.addElement(_row([("地区", "string"), ("商品", "string"),
                              ("数量", "string")]))
        rows.addElement(_row([(region, "string"), (commodity, "string"),
                              quantity]))

        group_and = FilterAnd()
        group_region = FilterOr()
        group_region.addElement(FilterCondition(
            operator="=", fieldnumber="0", value="北区", datatype="text"))
        group_region.addElement(FilterCondition(
            operator="=", fieldnumber="0", value="西区", datatype="text"))
        group_detail = FilterOr()
        group_detail.addElement(FilterCondition(
            operator=">", fieldnumber="2", value="5", datatype="number"))
        group_detail.addElement(FilterCondition(
            operator="=", fieldnumber="1", value="梨", datatype="text"))
        group_and.addElement(group_region)
        group_and.addElement(group_detail)
        flt = Filter()
        flt.addElement(group_and)

        db_ranges = DatabaseRanges()
        db_range = DatabaseRange(
            name="嵌套区域",
            targetrangeaddress="$嵌套表.$A$1:$C$2",
            containsheader="true",
        )
        db_range.addElement(flt)
        db_ranges.addElement(db_range)
        doc.spreadsheet.addElement(db_ranges)
        doc.spreadsheet.addElement(table)
        return _save(doc)

    def test_or_inside_and_grouping(self):
        # quantity 10 > 5 -> detail OR true; region 北区 -> kept.
        result = read_rows(self._doc(10, "苹果", "北区"))
        self.assertEqual([r.row for r in result.rows], [2])

        # quantity 3 fails > 5, but commodity 梨 satisfies the OR branch.
        result = read_rows(self._doc(3, "梨", "西区"))
        self.assertEqual([r.row for r in result.rows], [2])

        # Neither detail branch holds -> excluded, even for a valid region.
        result = read_rows(self._doc(3, "桃", "北区"))
        self.assertEqual(result.rows, [])

        # Region group fails -> excluded before any rescue from detail.
        result = read_rows(self._doc(10, "苹果", "东区"))
        self.assertEqual(result.rows, [])

    def test_text_quantity_issue_reported_even_when_or_branch_rescues(self):
        # quantity is text so > 5 is incomparable, but 梨 keeps the row via
        # the text branch of the OR. The issue is still reported eagerly.
        result = read_rows(self._doc(("缺货", "string"), "梨", "西区"))
        self.assertEqual([r.row for r in result.rows], [2])
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].reason, INCOMPARABLE)
        self.assertEqual(result.issues[0].coordinate, "C2")

    def test_empty_quantity_issue_excludes_row_without_rescue(self):
        # Empty quantity; commodity is not 梨, so nothing rescues the row.
        result = read_rows(self._doc(None, "苹果", "北区"))
        self.assertEqual(result.rows, [])
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].reason, EMPTY)
        self.assertEqual(result.issues[0].coordinate, "C2")


class TestRepeatedRowsAndCells(unittest.TestCase):

    def _doc(self):
        doc = OpenDocumentSpreadsheet()
        table = Table(name="重复表")
        table.addElement(TableColumn(numbercolumnsrepeated="3"))
        rows = TableRows()
        table.addElement(rows)

        header = TableRow()
        for label in ("地区", "商品", "数量"):
            cell = TableCell(valuetype="string")
            cell.addElement(P(text=label))
            header.addElement(cell)
        rows.addElement(header)

        repeated = TableRow(numberrowsrepeated="2")
        repeated.addElement(_cell("北区", "string"))
        repeated.addElement(_cell("苹果", "string"))
        repeated.addElement(_cell(3))
        rows.addElement(repeated)

        blank = TableRow()
        blank.addElement(_cell("西区", "string"))
        blank.addElement(TableCell(numbercolumnsrepeated="2"))
        rows.addElement(blank)

        db_ranges = DatabaseRanges()
        db_range = DatabaseRange(
            name="重复区域",
            targetrangeaddress="$重复表.$A$1:$C$4",
            containsheader="true",
        )
        flt = Filter()
        flt.addElement(FilterCondition(
            operator=">", fieldnumber="2", value="0", datatype="number"))
        db_range.addElement(flt)
        db_ranges.addElement(db_range)
        doc.spreadsheet.addElement(db_ranges)
        doc.spreadsheet.addElement(table)
        return _save(doc)

    def test_repeats_expand_with_original_coordinates(self):
        result = read_rows(self._doc())
        self.assertEqual([row.row for row in result.rows], [2, 3])
        second = result.rows[1]
        self.assertEqual(
            [cell.coordinate for cell in second.cells],
            ["A3", "B3", "C3"])
        # The repeated empty cells in row 4 cannot compare against zero.
        reasons = [(i.coordinate, i.reason) for i in result.issues]
        self.assertIn(("C4", EMPTY), reasons)
        self.assertEqual(result.rows[0].cells[2].text, "3")


class TestRangeWithoutFilter(unittest.TestCase):

    def _doc(self):
        doc = OpenDocumentSpreadsheet()
        table = Table(name="无筛选")
        table.addElement(TableColumn(numbercolumnsrepeated="2"))
        rows = TableRows()
        table.addElement(rows)
        rows.addElement(_row([("姓名", "string"), ("分数", "string")]))
        rows.addElement(_row([("甲", "string"), 9]))
        db_ranges = DatabaseRanges()
        db_ranges.addElement(DatabaseRange(
            name="裸区域",
            targetrangeaddress="$无筛选.$A$1:$B$2",
            containsheader="true",
        ))
        doc.spreadsheet.addElement(db_ranges)
        doc.spreadsheet.addElement(table)
        return _save(doc)

    def test_range_without_filter_returns_all_data(self):
        result = read_rows(self._doc())
        self.assertEqual(result.filtered_row_count, 1)
        self.assertEqual(
            [cell.text for cell in result.rows[0].cells], ["甲", "9"])
        self.assertEqual(result.issues, [])


class TestCommandLine(unittest.TestCase):

    def test_cli_emits_coordinates_and_issue_report(self):
        stream = _build_sales_doc()
        with tempfile.NamedTemporaryFile(suffix=".ods", delete=False) as fh:
            fh.write(stream.getvalue())
            path = fh.name
        try:
            from contextlib import redirect_stderr, redirect_stdout
            out, err = BytesIO(), BytesIO()
            import sys
            class _TextIO(object):
                def __init__(self, binary):
                    self.binary = binary
                def write(self, data):
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    self.binary.write(data)
            with redirect_stdout(_TextIO(out)), redirect_stderr(_TextIO(err)):
                rc = main([path])
            self.assertEqual(rc, 0)
            output = out.getvalue().decode("utf-8")
            errors = err.getvalue().decode("utf-8")
            self.assertIn("A3:C3", output)
            self.assertIn("北区", output)
            self.assertIn("INCOMPARABLE", errors)
            self.assertIn("C7", errors)
            self.assertIn("EMPTY", errors)
            self.assertIn("C8", errors)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
