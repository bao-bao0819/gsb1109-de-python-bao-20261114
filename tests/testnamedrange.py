#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 European Environment Agency
#
# This is free software.  You may redistribute it under the terms
# of the Apache license and the GNU General Public License Version
# 2 or at your option any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public
# License along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Contributor(s):
#

import os
import os.path
import tempfile
import unittest

from odf import table, text
from odf.namedrange import (
    NamedRangeError,
    export_named_range,
    find_named_range,
    get_named_range_corners,
    parse_range_address,
)
from odf.namespaces import TABLENS
from odf.opendocument import OpenDocumentSpreadsheet, load


def make_cell(value):
    if isinstance(value, bool):
        cell = table.TableCell(
            valuetype="boolean", booleanvalue=str(value).lower())
    elif isinstance(value, (int, float)):
        cell = table.TableCell(
            valuetype="float", value=str(value))
    else:
        cell = table.TableCell(valuetype="string")
    cell.addElement(text.P(text=u"" if value is None else str(value)))
    return cell


def make_sheet(sheet_name, grid):
    sheet = table.Table(name=sheet_name)
    sheet.addElement(table.TableColumn())
    rows = table.TableRows()
    sheet.addElement(rows)
    for row_values in grid:
        row = table.TableRow()
        rows.addElement(row)
        for value in row_values:
            row.addElement(make_cell(value))
    return sheet


def add_named_range(spreadsheet, range_name, address):
    container = table.NamedExpressions()
    spreadsheet.insertBefore(
        container,
        next(child for child in spreadsheet.childNodes
             if child.qname == (TABLENS, u'table')))
    container.addElement(
        table.NamedRange(name=range_name, cellrangeaddress=address))


def build_source_document():
    document = OpenDocumentSpreadsheet()
    spreadsheet = document.spreadsheet
    grid = [
        [u"hdr", u"q1", u"q2"],
        [10, 11, 12],
        [20, 21, 22],
        [30, 31, 32],
    ]
    spreadsheet.addElement(make_sheet(u"Sales Q3", grid))
    add_named_range(
        spreadsheet, u"PurchaseArea",
        u"'Sales Q3'.$B$2:$C$4")
    return document


def build_apostrophe_document():
    document = OpenDocumentSpreadsheet()
    spreadsheet = document.spreadsheet
    spreadsheet.addElement(
        make_sheet(u"O'Brien's Data", [[u"a", u"b"], [u"c", u"d"]]))
    add_named_range(
        spreadsheet, u"QuoteArea",
        u"'O''Brien''s Data'.$A$1:$B$2")
    return document


def build_target_document():
    document = OpenDocumentSpreadsheet()
    spreadsheet = document.spreadsheet
    spreadsheet.addElement(
        make_sheet(u"Sales Q3", [
            [u"local", u"local2"],
            [99, 100],
        ]))
    add_named_range(
        spreadsheet, u"PurchaseArea",
        u"'Sales Q3'.$A$1:$B$2")
    return document


class ParseRangeAddressTest(unittest.TestCase):

    def test_quoted_sheet_with_space(self):
        sheet, top_left, bottom_right, first_ref, second_ref = \
            parse_range_address(u"'Sales Q3'.$B$2:$C$4")
        self.assertEqual(sheet, u"Sales Q3")
        self.assertEqual(top_left, (1, 1))
        self.assertEqual(bottom_right, (2, 3))
        self.assertEqual(first_ref, u"$B$2")
        self.assertEqual(second_ref, u"$C$4")

    def test_quoted_sheet_with_doubled_apostrophes(self):
        sheet, _, _, _, _ = parse_range_address(
            u"'O''Brien''s Data'.A1:B2")
        self.assertEqual(sheet, u"O'Brien's Data")

    def test_unquoted_sheet(self):
        sheet, top_left, bottom_right, _, _ = \
            parse_range_address(u"Sheet1.B2:D4")
        self.assertEqual(sheet, u"Sheet1")
        self.assertEqual(top_left, (1, 1))
        self.assertEqual(bottom_right, (3, 3))

    def test_single_cell_address(self):
        sheet, top_left, bottom_right, first_ref, second_ref = \
            parse_range_address(u"'Sales Q3'.B2")
        self.assertEqual(sheet, u"Sales Q3")
        self.assertEqual(top_left, bottom_right)
        self.assertEqual(first_ref, second_ref)

    def test_missing_sheet_raises(self):
        self.assertRaises(
            NamedRangeError, parse_range_address, u"B2:C4")

    def test_bad_reference_raises(self):
        self.assertRaises(
            NamedRangeError, parse_range_address, u"Sheet1.BX:C4")


class ExportNamedRangeTest(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.mkdtemp()

    def tearDown(self):
        for filename in os.listdir(self.tempdir):
            os.unlink(os.path.join(self.tempdir, filename))
        os.rmdir(self.tempdir)

    def _path(self, name):
        return os.path.join(self.tempdir, name)

    def test_export_into_fresh_document_and_reload(self):
        source = build_source_document()
        result = export_named_range(source, u"PurchaseArea")
        target = result[u'document']
        target_path = self._path(u"fresh.ods")
        target.save(target_path)

        reloaded = load(target_path)
        named_range = find_named_range(reloaded, u"PurchaseArea")
        address = named_range.getAttrNS(TABLENS, u'cell-range-address')
        self.assertEqual(address, u"'Sales Q3'.$B$2:$C$4")
        self.assertEqual(
            get_named_range_corners(reloaded, u"PurchaseArea"),
            [u'11', u'12', u'31', u'32'])

    def test_corner_values_match_source(self):
        source = build_source_document()
        result = export_named_range(source, u"PurchaseArea")
        reloaded_path = self._path(u"corners.ods")
        result[u'document'].save(reloaded_path)
        reloaded = load(reloaded_path)
        source_corners = get_named_range_corners(source, u"PurchaseArea")
        target_corners = get_named_range_corners(reloaded, u"PurchaseArea")
        self.assertEqual(target_corners, source_corners)
        self.assertEqual(target_corners, [u'11', u'12', u'31', u'32'])

    def test_apostrophe_sheet_name_is_recognised(self):
        source = build_apostrophe_document()
        result = export_named_range(source, u"QuoteArea")
        self.assertEqual(result[u'sheet_name'], u"O'Brien's Data")
        target_path = self._path(u"quotes.ods")
        result[u'document'].save(target_path)
        reloaded = load(target_path)
        self.assertEqual(
            get_named_range_corners(reloaded, u"QuoteArea"),
            [u'a', u'b', u'c', u'd'])
        named_range = find_named_range(reloaded, u"QuoteArea")
        self.assertEqual(
            named_range.getAttrNS(TABLENS, u'cell-range-address'),
            u"'O''Brien''s Data'.$A$1:$B$2")

    def test_existing_same_named_sheet_and_range_are_preserved(self):
        source = build_source_document()
        target = build_target_document()
        result = export_named_range(
            source, u"PurchaseArea", target=target)

        self.assertEqual(result[u'sheet_name'], u"Sales Q3 2")
        self.assertEqual(result[u'range_name'], u"PurchaseArea_2")

        target_path = self._path(u"conflict.ods")
        target.save(target_path)
        reloaded = load(target_path)

        original = find_named_range(reloaded, u"PurchaseArea")
        self.assertEqual(
            original.getAttrNS(TABLENS, u'cell-range-address'),
            u"'Sales Q3'.$A$1:$B$2")
        self.assertEqual(
            get_named_range_corners(reloaded, u"PurchaseArea"),
            [u'local', u'local2', u'99', u'100'])

        imported = find_named_range(reloaded, u"PurchaseArea_2")
        self.assertEqual(
            imported.getAttrNS(TABLENS, u'cell-range-address'),
            u"'Sales Q3 2'.$B$2:$C$4")
        self.assertEqual(
            get_named_range_corners(reloaded, u"PurchaseArea_2"),
            [u'11', u'12', u'31', u'32'])

    def test_export_accepts_file_path(self):
        source = build_source_document()
        source_path = self._path(u"source.ods")
        source.save(source_path)
        result = export_named_range(source_path, u"PurchaseArea")
        self.assertEqual(result[u'range_name'], u"PurchaseArea")
        self.assertEqual(
            get_named_range_corners(
                result[u'document'], u"PurchaseArea"),
            [u'11', u'12', u'31', u'32'])

    def test_target_path_keeps_existing_definitions(self):
        source = build_source_document()
        target_path = self._path(u"target.ods")
        build_target_document().save(target_path)
        result = export_named_range(
            source, u"PurchaseArea", target=target_path)
        self.assertEqual(result[u'range_name'], u"PurchaseArea_2")
        self.assertEqual(
            get_named_range_corners(
                result[u'document'], u"PurchaseArea"),
            [u'local', u'local2', u'99', u'100'])

    def test_unknown_named_range_raises(self):
        source = build_source_document()
        self.assertRaises(
            NamedRangeError,
            export_named_range, source, u"DoesNotExist")

    def test_repeated_export_keeps_generating_unique_names(self):
        source = build_source_document()
        target = build_target_document()
        first = export_named_range(source, u"PurchaseArea", target=target)
        second = export_named_range(
            source, u"PurchaseArea", target=target)
        self.assertEqual(first[u'range_name'], u"PurchaseArea_2")
        self.assertEqual(second[u'range_name'], u"PurchaseArea_3")
        self.assertEqual(first[u'sheet_name'], u"Sales Q3 2")
        self.assertEqual(second[u'sheet_name'], u"Sales Q3 3")


if __name__ == u"__main__":
    unittest.main()
