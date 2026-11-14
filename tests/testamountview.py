#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Søren Roug, European Environment Agency
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

import unittest
from decimal import Decimal
from io import BytesIO

from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.number import CurrencyStyle, CurrencySymbol, Number
from odf.style import Style
from odf.table import Table, TableColumn, TableRow, TableCell
from odf.text import P
from odf import amountview


def sales_document():
    """A sales detail sheet mixing the ways amounts show up in a file:

    - B2/B3 store amounts with an explicit cell style,
    - B4 only displays a currency symbol as text,
    - B5 is formatted through the column default style,
    - B6 is formatted through the row default style,
    - B7 has an explicit non-currency style overriding the column default,
    - B8:D8 are repeated cells storing amounts without any style,
    - B9/B10 come from a repeated row.
    """
    doc = OpenDocumentSpreadsheet()

    usd = CurrencyStyle(name=u"cur-usd")
    usd.addElement(CurrencySymbol(language=u"en", country=u"US", text=u"$"))
    usd.addElement(Number(decimalplaces=u"2", minintegerdigits=u"1"))
    doc.styles.addElement(usd)

    eur = CurrencyStyle(name=u"cur-eur")
    eur.addElement(Number(decimalplaces=u"2", minintegerdigits=u"1"))
    eur.addElement(CurrencySymbol(language=u"de", country=u"DE", text=u"€"))
    doc.styles.addElement(eur)

    # Australian dollars render the same symbol as US dollars.
    aud = CurrencyStyle(name=u"cur-aud")
    aud.addElement(CurrencySymbol(language=u"en", country=u"AU", text=u"$"))
    aud.addElement(Number(decimalplaces=u"2", minintegerdigits=u"1"))
    doc.styles.addElement(aud)

    ce_usd = Style(name=u"ceUSD", family=u"table-cell", datastylename=u"cur-usd")
    doc.automaticstyles.addElement(ce_usd)
    ce_eur = Style(name=u"ceEUR", family=u"table-cell", datastylename=u"cur-eur")
    doc.automaticstyles.addElement(ce_eur)
    ce_aud_col = Style(name=u"ceAUDcol", family=u"table-cell", datastylename=u"cur-aud")
    doc.automaticstyles.addElement(ce_aud_col)
    ce_aud_row = Style(name=u"ceAUDrow", family=u"table-cell", datastylename=u"cur-aud")
    doc.automaticstyles.addElement(ce_aud_row)
    ce_plain = Style(name=u"cePlain", family=u"table-cell")
    doc.automaticstyles.addElement(ce_plain)

    table = Table(name=u"Sales")
    table.addElement(TableColumn())                       # A: item
    table.addElement(TableColumn(defaultcellstylename=u"ceAUDcol"))  # B: amount
    table.addElement(TableColumn())                       # C: quantity
    table.addElement(TableColumn())                       # D

    def text_cell(text):
        cell = TableCell(valuetype=u"string")
        cell.addElement(P(text=text))
        return cell

    row = TableRow()
    row.addElement(text_cell(u"Item"))
    row.addElement(text_cell(u"Amount"))
    row.addElement(text_cell(u"Qty"))
    table.addElement(row)

    row = TableRow()
    row.addElement(text_cell(u"Widget"))
    cell = TableCell(valuetype=u"currency", currency=u"USD", value=u"100.50",
                     stylename=u"ceUSD")
    cell.addElement(P(text=u"$100.50"))
    row.addElement(cell)
    row.addElement(TableCell(valuetype=u"float", value=u"5"))
    table.addElement(row)

    row = TableRow()
    row.addElement(text_cell(u"Gadget"))
    cell = TableCell(valuetype=u"currency", currency=u"EUR", value=u"20",
                     stylename=u"ceEUR")
    cell.addElement(P(text=u"20,00 €"))
    row.addElement(cell)
    table.addElement(row)

    row = TableRow()
    row.addElement(text_cell(u"Doohickey"))
    cell = TableCell()
    cell.addElement(P(text=u"$50.00"))
    row.addElement(cell)
    table.addElement(row)

    row = TableRow()
    row.addElement(text_cell(u"Thingamajig"))
    cell = TableCell(valuetype=u"currency", currency=u"AUD", value=u"7.25")
    cell.addElement(P(text=u"$7.25"))
    row.addElement(cell)
    table.addElement(row)

    row = TableRow(defaultcellstylename=u"ceAUDrow")
    row.addElement(text_cell(u"Gizmo"))
    cell = TableCell(valuetype=u"float", value=u"3")
    cell.addElement(P(text=u"$3.00"))
    row.addElement(cell)
    table.addElement(row)

    row = TableRow()
    row.addElement(text_cell(u"Whatsit"))
    cell = TableCell(valuetype=u"currency", value=u"42", stylename=u"cePlain")
    cell.addElement(P(text=u"42"))
    row.addElement(cell)
    table.addElement(row)

    row = TableRow()
    row.addElement(text_cell(u"Misc"))
    cell = TableCell(valuetype=u"currency", currency=u"EUR", value=u"1",
                     numbercolumnsrepeated=u"3")
    cell.addElement(P(text=u"1,00 €"))
    row.addElement(cell)
    table.addElement(row)

    row = TableRow(numberrowsrepeated=u"2")
    row.addElement(text_cell(u"Extra"))
    cell = TableCell(valuetype=u"currency", currency=u"USD", value=u"2",
                     stylename=u"ceUSD")
    cell.addElement(P(text=u"$2.00"))
    row.addElement(cell)
    table.addElement(row)

    doc.spreadsheet.addElement(table)
    return doc


class TestAmountView(unittest.TestCase):

    def setUp(self):
        self.doc = sales_document()
        self.view = amountview.amount_view(self.doc)
        self.by_ref = dict((cell.ref, cell) for cell in self.view)

    def test_explicit_cell_style(self):
        cell = self.by_ref[u"Sales.B2"]
        self.assertTrue(cell.is_amount)
        self.assertEqual(cell.value, Decimal(u"100.50"))
        self.assertEqual(cell.currency, u"USD")
        self.assertEqual(cell.format_source, u"cell")
        self.assertEqual(cell.data_style_name, u"cur-usd")
        self.assertEqual(cell.symbol, u"$")

    def test_text_only_symbol_is_not_an_amount(self):
        cell = self.by_ref[u"Sales.B4"]
        self.assertFalse(cell.is_amount)
        self.assertEqual(cell.reason, amountview.REASON_NO_VALUE)
        self.assertEqual(cell.display, u"$50.00")

    def test_column_default_style(self):
        cell = self.by_ref[u"Sales.B5"]
        self.assertTrue(cell.is_amount)
        self.assertEqual(cell.currency, u"AUD")
        self.assertEqual(cell.format_source, u"column")
        self.assertEqual(cell.data_style_name, u"cur-aud")

    def test_row_default_style(self):
        cell = self.by_ref[u"Sales.B6"]
        self.assertTrue(cell.is_amount)
        self.assertEqual(cell.format_source, u"row")
        self.assertIsNone(cell.currency)
        self.assertEqual(cell.symbol, u"$")
        self.assertEqual(cell.symbol_country, u"AU")

    def test_explicit_style_beats_column_default(self):
        # B7 sits in a column whose default is a currency style, but its
        # explicit plain style wins, so no currency can be determined.
        cell = self.by_ref[u"Sales.B7"]
        self.assertFalse(cell.is_amount)
        self.assertEqual(cell.format_source, u"cell")
        self.assertEqual(cell.reason, amountview.REASON_MISSING_CURRENCY)

    def test_number_without_currency_format(self):
        cell = self.by_ref[u"Sales.C2"]
        self.assertFalse(cell.is_amount)
        self.assertEqual(cell.reason, amountview.REASON_NO_CURRENCY_FORMAT)

    def test_repeated_cells_and_rows(self):
        for ref in (u"Sales.B8", u"Sales.C8", u"Sales.D8"):
            self.assertEqual(self.by_ref[ref].raw_value, u"1")
            self.assertTrue(self.by_ref[ref].is_amount)
        # The repeated cell inherits the column default only in column B.
        self.assertEqual(self.by_ref[u"Sales.B8"].format_source, u"column")
        self.assertIsNone(self.by_ref[u"Sales.C8"].format_source)
        self.assertIsNone(self.by_ref[u"Sales.D8"].format_source)
        for ref in (u"Sales.B9", u"Sales.B10"):
            self.assertEqual(self.by_ref[ref].raw_value, u"2")
            self.assertTrue(self.by_ref[ref].is_amount)

    def test_amount_columns(self):
        columns = dict(amountview.amount_columns(self.view))
        self.assertEqual(set(columns), set([
            (u"Sales", u"B"), (u"Sales", u"C"), (u"Sales", u"D")]))
        self.assertNotIn((u"Sales", u"A"), columns)

    def test_totals_per_currency_not_per_symbol(self):
        summaries = amountview.summarize(self.view)
        by_label = dict((summary.label, summary) for summary in summaries)
        self.assertEqual(by_label[u"USD"].total, Decimal(u"104.50"))
        self.assertEqual(by_label[u"EUR"].total, Decimal(u"23"))
        self.assertEqual(by_label[u"AUD"].total, Decimal(u"7.25"))
        # The style-only AUD cell stays in its own group even though US
        # and Australian dollars share the "$" symbol.
        symbol_groups = [s for s in summaries if s.currency is None]
        self.assertEqual(len(symbol_groups), 1)
        self.assertEqual(symbol_groups[0].total, Decimal(u"3"))
        self.assertEqual(symbol_groups[0].symbol, u"$")
        conflicts = amountview.symbol_conflicts(summaries)
        self.assertTrue(any(s.symbol == u"$" for s in conflicts))

    def test_totals_trace_back_to_raw_values(self):
        summaries = amountview.summarize(self.view)
        by_label = dict((summary.label, summary) for summary in summaries)
        self.assertEqual(by_label[u"USD"].trace(), [
            (u"Sales.B2", u"100.50"), (u"Sales.B9", u"2"), (u"Sales.B10", u"2")])
        self.assertEqual(by_label[u"EUR"].trace(), [
            (u"Sales.B3", u"20"), (u"Sales.B8", u"1"),
            (u"Sales.C8", u"1"), (u"Sales.D8", u"1")])
        for summary in summaries:
            self.assertTrue(summary.verify())

    def test_reload_keeps_view(self):
        buffer = BytesIO()
        self.doc.write(buffer)
        buffer.seek(0)
        reloaded = load(buffer)
        view = amountview.amount_view(reloaded)
        self.assertEqual(
            [(c.ref, c.raw_value, c.currency, c.format_source, c.reason)
             for c in view],
            [(c.ref, c.raw_value, c.currency, c.format_source, c.reason)
             for c in self.view])

    def test_report_mentions_reasons_and_warning(self):
        report = amountview.format_report(self.view)
        self.assertIn(u"Sales.B4", report)
        self.assertIn(u"not an amount", report)
        self.assertIn(u"Totals by currency:", report)
        self.assertIn(u"must not be merged by symbol", report)


if __name__ == '__main__':
    unittest.main()
