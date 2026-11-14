# -*- coding: utf-8 -*-
#
# Read spreadsheet rows through the database-range filter declared in the
# ODS file itself.
#
# A vendor workbook can carry a ``table:database-range`` with a target range
# and a nested ``table:filter``. Loading such a sheet into the warehouse
# without honouring the declaration yields the whole sheet, including the
# region title row and the notes kept outside the table. This module
# reconstructs the declared view:
#
# * only cells inside the database-range target address are considered, so
#   title rows and notes outside the range are never treated as data;
# * the header row (``table:contains-header``) is returned separately;
# * the nested filter (filter-and / filter-or / filter-condition) is
#   evaluated with its grouping preserved;
# * every emitted cell carries its original spreadsheet coordinate;
# * ordering comparisons against empty or non-numeric cells never fall back
#   to zero: they are reported as "empty" / "incomparable" issues and the
#   affected row is excluded instead of silently participating in the
#   comparison. Conditions are evaluated eagerly so the issues are reported
#   even when a sibling OR branch would have kept the row anyway.

from __future__ import unicode_literals

import argparse
import csv
import re
import sys

from odf.element import Node
from odf.namespaces import OFFICENS, TABLENS
from odf.opendocument import load
from odf.table import (
    DatabaseRange,
    Table,
    TableRow,
)
from odf.teletype import extractText

EMPTY = "empty"
INCOMPARABLE = "incomparable"

_NUMERIC_VALUE_TYPES = frozenset(("float", "percentage", "currency"))
_ORDERING_OPERATORS = frozenset(("<", "<=", ">", ">="))
_TEXT_OPERATORS = frozenset(
    ("=", "!=", "contains", "does-not-contain", "begins-with", "ends-with")
)
_OPERATOR_ALIASES = {
    "==": "=",
    "<>": "!=",
    "eq": "=",
    "ne": "!=",
    "lt": "<",
    "le": "<=",
    "gt": ">",
    "ge": ">=",
}


def column_letter(index):
    """Return the 1-based spreadsheet column letter (1 -> A, 27 -> AA)."""
    if index < 1:
        raise ValueError("Column index must be 1-based and positive")
    letters = []
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _as_int(value, default=None):
    if value is None:
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("true", "1", "yes")


class CellView(object):
    """A single cell positioned in the original spreadsheet."""

    __slots__ = (
        "row",
        "column",
        "coordinate",
        "text",
        "value",
        "value_type",
        "is_empty",
    )

    def __init__(self, row, column, text, value, value_type):
        self.row = row
        self.column = column
        self.coordinate = "%s%d" % (column_letter(column), row)
        self.text = text
        self.value = value
        self.value_type = value_type
        self.is_empty = value_type is None and (text is None or text == "")

    def __repr__(self):
        return "CellView(%r, text=%r)" % (self.coordinate, self.text)


class RowView(object):
    """A data row that survived the filter, with its original coordinates."""

    __slots__ = ("row", "coordinate", "cells")

    def __init__(self, row, cells):
        self.row = row
        self.cells = cells
        if cells:
            self.coordinate = "%s%d:%s%d" % (
                column_letter(cells[0].column),
                row,
                column_letter(cells[-1].column),
                row,
            )
        else:
            self.coordinate = str(row)

    def __repr__(self):
        return "RowView(%r, %d cells)" % (self.coordinate, len(self.cells))


class CellIssue(object):
    """A cell that could not participate in a numeric filter condition."""

    __slots__ = (
        "reason",
        "row",
        "column",
        "coordinate",
        "field_number",
        "operator",
        "value_type",
        "text",
    )

    def __init__(self, reason, row, column, field_number, operator,
                 value_type, text):
        self.reason = reason
        self.row = row
        self.column = column
        self.coordinate = "%s%d" % (column_letter(column), row)
        self.field_number = field_number
        self.operator = operator
        self.value_type = value_type
        self.text = text

    def __repr__(self):
        return "CellIssue(%s, %s, operator=%r)" % (
            self.reason,
            self.coordinate,
            self.operator,
        )


class FilterResult(object):
    """The reconstructed view of one database range."""

    __slots__ = (
        "table_name",
        "range_name",
        "range_address",
        "contains_header",
        "header",
        "rows",
        "issues",
        "data_row_count",
        "filtered_row_count",
    )

    def __init__(self, table_name, range_name, range_address,
                 contains_header, header, rows, issues,
                 data_row_count, filtered_row_count):
        self.table_name = table_name
        self.range_name = range_name
        self.range_address = range_address
        self.contains_header = contains_header
        self.header = header
        self.rows = rows
        self.issues = issues
        self.data_row_count = data_row_count
        self.filtered_row_count = filtered_row_count


_RANGE_ADDRESS_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<sheet>'(?:[^']|'')+'|[^.']+)\.)?   # optional sheet name
    \$?(?P<col1>[A-Z]+)\$?(?P<row1>[0-9]+)    # first cell
    (?::
      (?:(?P<sheet2>'(?:[^']|'')+'|[^.']+)\.)?
      \$?(?P<col2>[A-Z]+)\$?(?P<row2>[0-9]+)
    )?
    \s*$
    """,
    re.VERBOSE,
)


def parse_range_address(address):
    """Parse an ODF cell range address into ``(sheet, col1, row1, col2, row2)``.

    Columns and rows are returned as 1-based integers. A single-cell address
    yields identical start and end coordinates.
    """
    if address is None:
        raise ValueError("Empty range address")
    match = _RANGE_ADDRESS_RE.match(str(address))
    if match is None:
        raise ValueError("Unsupported range address: %r" % address)

    def col_index(letters):
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - ord("A") + 1)
        return index

    sheet = match.group("sheet")
    if sheet is not None:
        sheet = sheet.replace("''", "'").strip("'")
        if sheet.startswith("$"):
            sheet = sheet[1:]
    col1 = col_index(match.group("col1"))
    row1 = int(match.group("row1"))
    col2 = col_index(match.group("col2") or match.group("col1"))
    row2 = int(match.group("row2") or match.group("row1"))
    return sheet, col1, row1, col2, row2


def list_database_ranges(doc):
    """Return ``(table_name, range_element)`` pairs declared in the document."""
    try:
        spreadsheet = doc.spreadsheet
    except AttributeError:
        return []
    found = []
    for db_range in spreadsheet.getElementsByType(DatabaseRange):
        address = db_range.getAttrNS(TABLENS, "target-range-address")
        sheet = None
        if address:
            try:
                sheet = parse_range_address(address)[0]
            except ValueError:
                sheet = None
        found.append((sheet, db_range))
    return found


def _table_rows(table_element):
    rows = []
    for child in table_element.getElementsByType(TableRow):
        rows.append(child)
    return rows


def _row_cells(row_element):
    cells = []
    for child in row_element.childNodes:
        if child.nodeType != Node.ELEMENT_NODE:
            continue
        if child.qname in (
            (TABLENS, "table-cell"),
            (TABLENS, "covered-table-cell"),
        ):
            cells.append(child)
    return cells


def _cell_view(cell_element, row, column):
    value_type = cell_element.getAttrNS(OFFICENS, "value-type")
    text = extractText(cell_element)
    value = None
    if value_type in _NUMERIC_VALUE_TYPES:
        raw = cell_element.getAttrNS(OFFICENS, "value")
        if raw is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = None
    elif value_type == "boolean":
        raw = cell_element.getAttrNS(OFFICENS, "boolean-value")
        if raw is not None:
            value = _as_bool(raw)
    elif value_type == "string":
        value = cell_element.getAttrNS(OFFICENS, "string-value")
        if value is None:
            value = text
    elif value_type in ("date", "time"):
        value = cell_element.getAttrNS(
            OFFICENS,
            "date-value" if value_type == "date" else "time-value",
        )
    return CellView(row, column, text, value, value_type)


def _empty_view(row, column):
    return CellView(row, column, "", None, None)


def _build_row_views(row_element, row_number, col1, col2):
    views = []
    current_col = 0
    for cell_element in _row_cells(row_element):
        cell_repeat = max(1, _as_int(
            cell_element.getAttrNS(
                TABLENS, "number-columns-repeated"), 1))
        current_col += cell_repeat
        if current_col < col1:
            continue
        start_col = max(
            col1, current_col - cell_repeat + 1)
        end_col = min(col2, current_col)
        for column in range(start_col, end_col + 1):
            views.append(_cell_view(cell_element, row_number, column))
        if current_col >= col2:
            break
    width = col2 - col1 + 1
    while len(views) < width:
        views.append(_empty_view(row_number, col1 + len(views)))
    return views


def _iter_range_grid(table_element, bounds):
    """Yield ``(row_number, [CellView])`` for the rows inside ``bounds``.

    Repeated rows and repeated cells are expanded while honouring the range
    boundary, cells missing at the end of a row are padded as empty cells,
    and rows beyond the last stored row are synthesized as empty rows, as
    ODF treats the unspecified part of a sheet as empty.
    """
    col1, row1, col2, row2 = bounds
    current_row = 0
    for row_element in _table_rows(table_element):
        repeat = max(1, _as_int(
            row_element.getAttrNS(TABLENS, "number-rows-repeated"), 1))
        start = current_row + 1
        end = current_row + repeat
        current_row = end
        if end < row1:
            continue
        if start > row2:
            break
        for row_number in range(max(start, row1), min(end, row2) + 1):
            yield row_number, _build_row_views(
                row_element, row_number, col1, col2)
    if current_row < row2:
        for row_number in range(max(current_row + 1, row1), row2 + 1):
            yield row_number, [
                _empty_view(row_number, column)
                for column in range(col1, col2 + 1)
            ]


def _normalize_operator(operator):
    if operator is None:
        raise ValueError("Filter condition is missing table:operator")
    normalized = str(operator).strip().lower()
    normalized = _OPERATOR_ALIASES.get(normalized, normalized)
    if normalized not in _TEXT_OPERATORS and \
            normalized not in _ORDERING_OPERATORS:
        raise ValueError("Unsupported filter operator: %r" % operator)
    return normalized


def _compare_text(cell, operator, operand, case_sensitive):
    text = cell.text or ""
    wanted = "" if operand is None else str(operand)
    if not case_sensitive:
        text = text.lower()
        wanted = wanted.lower()
    if operator == "=":
        return text == wanted
    if operator == "!=":
        return text != wanted
    if operator == "contains":
        return wanted in text
    if operator == "does-not-contain":
        return wanted not in text
    if operator == "begins-with":
        return text.startswith(wanted)
    if operator == "ends-with":
        return text.endswith(wanted)
    raise ValueError("Unsupported text operator: %r" % operator)


def _compare_numbers(cell, operator, operand):
    try:
        wanted = float(operand)
    except (TypeError, ValueError):
        raise ValueError(
            "Filter value %r is not numeric for operator %r"
            % (operand, operator))
    actual = cell.value
    if operator == "=":
        return actual == wanted
    if operator == "!=":
        return actual != wanted
    if operator == "<":
        return actual < wanted
    if operator == "<=":
        return actual <= wanted
    if operator == ">":
        return actual > wanted
    if operator == ">=":
        return actual >= wanted
    raise ValueError("Unsupported numeric operator: %r" % operator)


class _Condition(object):

    def __init__(self, element, range_col1):
        self.operator = _normalize_operator(
            element.getAttrNS(TABLENS, "operator"))
        field_number = _as_int(element.getAttrNS(TABLENS, "field-number"))
        if field_number is None:
            raise ValueError(
                "Filter condition is missing table:field-number")
        self.field_number = field_number
        self.column = range_col1 + field_number
        self.operand = element.getAttrNS(TABLENS, "value")
        self.data_type = element.getAttrNS(TABLENS, "data-type")
        self.case_sensitive = _as_bool(
            element.getAttrNS(TABLENS, "case-sensitive"))

    def evaluate(self, row_map, issues):
        cell = row_map.get(self.column)
        if cell is None:
            cell = _empty_view(
                next(iter(row_map.values())).row if row_map else 1,
                self.column)
        needs_number = (
            self.data_type == "number"
            or self.operator in _ORDERING_OPERATORS
        )
        if needs_number:
            if cell.is_empty:
                issues.append(CellIssue(
                    EMPTY, cell.row, cell.column, self.field_number,
                    self.operator, cell.value_type, cell.text))
                return EMPTY
            if cell.value_type not in _NUMERIC_VALUE_TYPES or \
                    cell.value is None:
                issues.append(CellIssue(
                    INCOMPARABLE, cell.row, cell.column,
                    self.field_number, self.operator,
                    cell.value_type, cell.text))
                return INCOMPARABLE
            return _compare_numbers(cell, self.operator, self.operand)
        return _compare_text(
            cell, self.operator, self.operand, self.case_sensitive)


class _Group(object):

    def __init__(self, kind, children):
        self.kind = kind
        self.children = children

    def evaluate(self, row_map, issues):
        results = [child.evaluate(row_map, issues)
                   for child in self.children]
        if self.kind == "and":
            pending_issue = None
            for result in results:
                if result is False:
                    return False
                if result in (EMPTY, INCOMPARABLE) and pending_issue is None:
                    pending_issue = result
            if pending_issue is not None:
                return pending_issue
            return True
        pending_issue = None
        for result in results:
            if result is True:
                return True
            if result in (EMPTY, INCOMPARABLE) and pending_issue is None:
                pending_issue = result
        if pending_issue is not None:
            return pending_issue
        return False


def _build_filter_node(element, range_col1):
    local = element.qname[1]
    if local == "filter-condition":
        return _Condition(element, range_col1)
    if local in ("filter-and", "filter-or"):
        children = [
            _build_filter_node(child, range_col1)
            for child in element.childNodes
            if child.nodeType == Node.ELEMENT_NODE
            and child.qname[0] == TABLENS
            and child.qname[1] in (
                "filter-and", "filter-or", "filter-condition")
        ]
        if not children:
            raise ValueError("Empty <%s> group in filter" % local)
        return _Group(local[7:], children)
    raise ValueError("Unexpected filter element: %s" % local)


def _build_filter(db_range, range_col1):
    for child in db_range.childNodes:
        if child.nodeType == Node.ELEMENT_NODE and \
                child.qname == (TABLENS, "filter"):
            nodes = [
                grandchild for grandchild in child.childNodes
                if grandchild.nodeType == Node.ELEMENT_NODE
                and grandchild.qname[0] == TABLENS
                and grandchild.qname[1] in (
                    "filter-and", "filter-or", "filter-condition")
            ]
            if not nodes:
                return None
            if len(nodes) == 1 and nodes[0].qname[1] != "filter-condition":
                return _build_filter_node(nodes[0], range_col1)
            return _Group(
                "and",
                [_build_filter_node(node, range_col1) for node in nodes])
    return None


def _resolve_table(doc, sheet_name, table_hint):
    tables = doc.spreadsheet.getElementsByType(Table)
    if table_hint is not None:
        if isinstance(table_hint, int):
            try:
                return tables[table_hint]
            except IndexError:
                raise ValueError(
                    "Sheet index %d out of range (document has %d sheets)"
                    % (table_hint, len(tables)))
        for candidate in tables:
            if candidate.getAttrNS(TABLENS, "name") == table_hint:
                return candidate
        raise ValueError("Sheet %r not found" % table_hint)
    if sheet_name is not None:
        for candidate in tables:
            if candidate.getAttrNS(TABLENS, "name") == sheet_name:
                return candidate
    if not tables:
        raise ValueError("Document contains no tables")
    return tables[0]


def _resolve_range(doc, range_name):
    ranges = doc.spreadsheet.getElementsByType(DatabaseRange)
    if not ranges:
        raise ValueError("Document declares no table:database-range")
    if range_name is None:
        return ranges[0]
    for candidate in ranges:
        if candidate.getAttrNS(TABLENS, "name") == range_name:
            return candidate
    raise ValueError("Database range %r not found" % range_name)


def read_rows(odffile, table=None, range_name=None, apply_filter=True):
    """Read the rows of a declared database range.

    :param odffile: file path or readable stream accepted by
        ``odf.opendocument.load``.
    :param table: optional sheet name or 0-based index overriding the sheet
        named in the range address.
    :param range_name: ``table:name`` of the database range; the first range
        is used when omitted.
    :param apply_filter: when false, every data row in the range is returned
        and the declared filter is ignored (issues are still empty).
    :returns: :class:`FilterResult`.
    """
    doc = load(odffile)
    db_range = _resolve_range(doc, range_name)
    address = db_range.getAttrNS(TABLENS, "target-range-address")
    sheet, col1, row1, col2, row2 = parse_range_address(address)
    table_element = _resolve_table(doc, sheet, table)
    table_name = table_element.getAttrNS(TABLENS, "name")
    contains_header = _as_bool(
        db_range.getAttrNS(TABLENS, "contains-header"))
    filter_root = _build_filter(db_range, col1) if apply_filter else None

    header = None
    rows = []
    issues = []
    data_row_count = 0
    for row_number, cells in _iter_range_grid(
            table_element, (col1, row1, col2, row2)):
        if contains_header and header is None and row_number == row1:
            header = cells
            continue
        data_row_count += 1
        if filter_root is None:
            rows.append(RowView(row_number, cells))
            continue
        row_map = dict((cell.column, cell) for cell in cells)
        outcome = filter_root.evaluate(row_map, issues)
        if outcome is True:
            rows.append(RowView(row_number, cells))

    deduped = []
    seen = set()
    for issue in issues:
        key = (issue.row, issue.column, issue.reason, issue.operator)
        if key not in seen:
            seen.add(key)
            deduped.append(issue)

    return FilterResult(
        table_name=table_name,
        range_name=db_range.getAttrNS(TABLENS, "name"),
        range_address=address,
        contains_header=contains_header,
        header=header,
        rows=rows,
        issues=deduped,
        data_row_count=data_row_count,
        filtered_row_count=len(rows),
    )


def _write_csv(result, stream):
    writer = csv.writer(stream)
    if result.header is not None:
        writer.writerow(
            ["coordinate"] + [cell.coordinate for cell in result.header])
        writer.writerow(
            ["header"] + [cell.text for cell in result.header])
    for row in result.rows:
        writer.writerow(
            [row.coordinate] + [cell.text for cell in row.cells])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read ODS rows through its declared database-range "
                    "filter, keeping original cell coordinates.")
    parser.add_argument("odffile", help="path to the .ods file")
    parser.add_argument("--table", dest="table", default=None,
                        help="sheet name or 0-based index override")
    parser.add_argument("--range", dest="range_name", default=None,
                        help="table:name of the database range")
    parser.add_argument("--no-filter", dest="apply_filter",
                        action="store_false", default=True,
                        help="return every data row in the range")
    args = parser.parse_args(argv)
    table_hint = args.table
    if table_hint is not None and re.match(r"^\d+$", table_hint):
        table_hint = int(table_hint)
    result = read_rows(
        args.odffile,
        table=table_hint,
        range_name=args.range_name,
        apply_filter=args.apply_filter,
    )
    _write_csv(result, sys.stdout)
    for issue in result.issues:
        sys.stderr.write(
            "%s: cell %s is %s (value-type=%r, text=%r), cannot apply %s\n"
            % (issue.reason.upper(), issue.coordinate, issue.reason,
               issue.value_type, issue.text, issue.operator))
    return 0


if __name__ == "__main__":
    sys.exit(main())
