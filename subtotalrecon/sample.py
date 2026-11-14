# -*- coding: utf-8 -*-
"""Build a sample financial table as ODS, for demos and tests.

Layout: store details grouped per city (declared via table:table-row-group),
a subtotal row after each city, and a grand total as the last row.  Two
adjacent cities deliberately total the same amount, and one subtotal
carries a manual adjustment, to exercise the parser's guarantees.
"""

from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableCell, TableRow, TableRowGroup
from odf.text import P

# (city, [(store, amount), ...], stored subtotal)
SAMPLE = [
    ("杭州", [("杭州一店", "100"), ("杭州二店", "150")], "250"),
    ("南京", [("南京一店", "90"), ("南京二店", "160")], "250"),  # same as 杭州
    ("苏州", [("苏州一店", "200")], "230"),  # manual adjustment +30
]
GRAND_STORED = "730"  # 250 + 250 + 230


def _text_cell(text):
    cell = TableCell(valuetype="string")
    cell.addElement(P(text=text))
    return cell


def _number_cell(value):
    return TableCell(valuetype="float", value=value)


def _row(cells):
    row = TableRow()
    for cell in cells:
        row.addElement(cell)
    return row


def build_document():
    doc = OpenDocumentSpreadsheet()
    table = Table(name="财务表")
    table.addElement(_row([_text_cell("门店"), _text_cell("城市"), _text_cell("金额")]))

    for city, stores, stored in SAMPLE:
        group = TableRowGroup()
        for store, amount in stores:
            group.addElement(
                _row([_text_cell(store), _text_cell(city), _number_cell(amount)])
            )
        table.addElement(group)
        table.addElement(
            _row([_text_cell(city + "小计"), _text_cell(""), _number_cell(stored)])
        )

    table.addElement(
        _row([_text_cell("总计"), _text_cell(""), _number_cell(GRAND_STORED)])
    )
    doc.spreadsheet.addElement(table)
    return doc


def write_sample(path):
    build_document().save(path)
    return path


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "financial-sample.ods"
    write_sample(target)
    print("wrote", target)
