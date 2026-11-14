# -*- coding: utf-8 -*-
# Copyright (C) 2026 European Environment Agency
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Contributor(s):
#

"""Export of named ranges between ODS spreadsheets.

The entry point :func:`export_named_range` copies one named range together
with the worksheet it references from a source spreadsheet into a target
spreadsheet.  Sheet names containing spaces or apostrophes are recognised,
the pointer stored in the copied ``table:named-range`` element is rewritten
to the copied worksheet, and names that already exist in the target document
are left untouched: the incoming copies are renamed instead.
"""

import os
import re

from odf.element import Element, Text, CDATASection
from odf.namespaces import TABLENS, OFFICENS
from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.table import Table, TableRow
from odf.table import NamedRange, NamedExpression, NamedExpressions
from odf.teletype import extractText

NAME_ATTR = (TABLENS, u'name')
CELL_RANGE_ADDRESS_ATTR = (TABLENS, u'cell-range-address')
BASE_CELL_ADDRESS_ATTR = (TABLENS, u'base-cell-address')
ROW_REPEATED_ATTR = (TABLENS, u'number-rows-repeated')
COLUMN_REPEATED_ATTR = (TABLENS, u'number-columns-repeated')

VALUE_TYPE_ATTR = (OFFICENS, u'value-type')
VALUE_ATTR = (OFFICENS, u'value')
DATE_VALUE_ATTR = (OFFICENS, u'date-value')
TIME_VALUE_ATTR = (OFFICENS, u'time-value')
BOOLEAN_VALUE_ATTR = (OFFICENS, u'boolean-value')

TABLE_QNAME = (TABLENS, u'table')
TABLE_CELL_QNAME = (TABLENS, u'table-cell')
COVERED_CELL_QNAME = (TABLENS, u'covered-table-cell')

_CELL_REF_RE = re.compile(r"^\$?([A-Za-z]+)\$?([0-9]+)$")


class NamedRangeError(Exception):
    """Raised when a named range cannot be located or interpreted."""


def _is_path(value):
    return isinstance(value, (str, bytes, os.PathLike))


def _column_index(letters):
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - ord('A') + 1)
    return index - 1


def _split_sheet_qualifier(text):
    """Split ``'Sales Data'.B2`` or ``Sheet1.B2`` into sheet name and rest."""
    value = text.strip()
    if value.startswith('$'):
        value = value[1:]
    if value.startswith("'"):
        chars = []
        position = 1
        while position < len(value):
            char = value[position]
            if char == "'":
                if position + 1 < len(value) and value[position + 1] == "'":
                    chars.append("'")
                    position += 2
                    continue
                break
            chars.append(char)
            position += 1
        else:
            raise NamedRangeError(
                "Unterminated quoted sheet name in %r" % text)
        remainder = value[position + 1:]
        if not remainder.startswith('.'):
            raise NamedRangeError(
                "Missing sheet separator in %r" % text)
        return u''.join(chars), remainder[1:]
    separator = value.find('.')
    if separator == -1:
        raise NamedRangeError(
            "Address %r does not name a worksheet" % text)
    sheet_name = value[:separator]
    if sheet_name.startswith('$'):
        sheet_name = sheet_name[1:]
    return sheet_name, value[separator + 1:]


def _parse_cell_ref(reference):
    match = _CELL_REF_RE.match(reference.strip())
    if not match:
        raise NamedRangeError(
            "Invalid cell reference %r" % reference)
    return _column_index(match.group(1)), int(match.group(2)) - 1


def parse_range_address(address):
    """Parse a ``table:cell-range-address`` value.

    Returns a tuple ``(sheet_name, top_left, bottom_right, first_ref,
    second_ref)`` where the corners are ``(column, row)`` zero based pairs
    and the two refs keep the original dollar anchors.
    """
    if not isinstance(address, str) or not address:
        raise NamedRangeError(
            "Cell range address must be a non-empty string: %r" % address)
    sheet_name, cell_part = _split_sheet_qualifier(address)
    if ':' in cell_part:
        first_ref, second_ref = cell_part.split(':', 1)
        if '.' in second_ref:
            second_sheet, second_ref = _split_sheet_qualifier(second_ref)
            if second_sheet != sheet_name:
                raise NamedRangeError(
                    "Ranges spanning several worksheets are not supported: "
                    "%r" % address)
    else:
        first_ref = second_ref = cell_part
    top_left = _parse_cell_ref(first_ref)
    bottom_right = _parse_cell_ref(second_ref)
    return sheet_name, top_left, bottom_right, first_ref, second_ref


def _build_address(sheet_name, first_ref, second_ref):
    quoted = u"'%s'" % sheet_name.replace(u"'", u"''")
    if first_ref == second_ref:
        return u"%s.%s" % (quoted, first_ref)
    return u"%s.%s:%s" % (quoted, first_ref, second_ref)


def _repeated(element, attr):
    value = element.getAttrNS(attr[0], attr[1])
    if value is None:
        return 1
    try:
        return int(value)
    except (TypeError, ValueError):
        raise NamedRangeError(
            "Invalid repetition count %r on %s" % (value, element.tagName))


def _row_at(sheet, row_index):
    current = 0
    for row in sheet.getElementsByType(TableRow):
        next_index = current + _repeated(row, ROW_REPEATED_ATTR)
        if row_index < next_index:
            return row
        current = next_index
    return None


def _cell_at(row, column_index):
    current = 0
    for child in row.childNodes:
        if not isinstance(child, Element):
            continue
        if child.qname not in (TABLE_CELL_QNAME, COVERED_CELL_QNAME):
            continue
        next_index = current + _repeated(child, COLUMN_REPEATED_ATTR)
        if column_index < next_index:
            return child
        current = next_index
    return None


def _cell_value(cell):
    if cell.qname == COVERED_CELL_QNAME:
        return None
    value_type = cell.getAttrNS(VALUE_TYPE_ATTR[0], VALUE_TYPE_ATTR[1])
    if value_type in (u'float', u'percentage', u'currency'):
        return cell.getAttrNS(VALUE_ATTR[0], VALUE_ATTR[1])
    if value_type == u'date':
        return cell.getAttrNS(DATE_VALUE_ATTR[0], DATE_VALUE_ATTR[1])
    if value_type == u'time':
        return cell.getAttrNS(TIME_VALUE_ATTR[0], TIME_VALUE_ATTR[1])
    if value_type == u'boolean':
        return cell.getAttrNS(BOOLEAN_VALUE_ATTR[0], BOOLEAN_VALUE_ATTR[1])
    text = extractText(cell)
    return text if text else None


def get_cell_value(sheet, column, row):
    """Return the value stored at a zero based sheet coordinate."""
    row_element = _row_at(sheet, row)
    if row_element is None:
        return None
    cell_element = _cell_at(row_element, column)
    if cell_element is None:
        return None
    return _cell_value(cell_element)


def find_named_range(document, range_name):
    """Return the ``table:named-range`` element called ``range_name``."""
    spreadsheet = getattr(document, 'spreadsheet', None)
    if spreadsheet is None:
        raise NamedRangeError("Document is not a spreadsheet")
    for named_range in spreadsheet.getElementsByType(NamedRange):
        if named_range.getAttrNS(NAME_ATTR[0], NAME_ATTR[1]) == range_name:
            return named_range
    raise NamedRangeError(
        "Named range %r does not exist in the document" % range_name)


def get_named_range_corners(document, range_name):
    """Return the four corner values of a named range in reading order."""
    named_range = find_named_range(document, range_name)
    address = named_range.getAttrNS(
        CELL_RANGE_ADDRESS_ATTR[0], CELL_RANGE_ADDRESS_ATTR[1])
    sheet_name, (left, top), (right, bottom), _, _ = parse_range_address(
        address)
    sheet = find_sheet(document, sheet_name)
    return [
        get_cell_value(sheet, left, top),
        get_cell_value(sheet, right, top),
        get_cell_value(sheet, left, bottom),
        get_cell_value(sheet, right, bottom),
    ]


def find_sheet(document, sheet_name):
    """Return the worksheet element whose ``table:name`` matches."""
    spreadsheet = getattr(document, 'spreadsheet', None)
    if spreadsheet is None:
        raise NamedRangeError("Document is not a spreadsheet")
    for sheet in spreadsheet.getElementsByType(Table):
        if sheet.getAttrNS(NAME_ATTR[0], NAME_ATTR[1]) == sheet_name:
            return sheet
    raise NamedRangeError(
        "Worksheet %r does not exist in the document" % sheet_name)


def _deep_copy(node):
    if isinstance(node, CDATASection):
        return CDATASection(node.data)
    if isinstance(node, Text):
        return Text(node.data)
    if not isinstance(node, Element):
        raise NamedRangeError(
            "Cannot copy node of type %s" % node.nodeType)
    clone = Element(qname=node.qname, check_grammar=False)
    for (namespace, localpart), value in node.attributes.items():
        clone.setAttrNS(namespace, localpart, value)
    for child in node.childNodes:
        clone.addElement(_deep_copy(child), check_grammar=False)
    return clone


def _unique_name(existing_names, base_name, separator):
    if base_name not in existing_names:
        return base_name
    counter = 2
    while True:
        candidate = u"%s%s%d" % (base_name, separator, counter)
        if candidate not in existing_names:
            return candidate
        counter += 1


def _target_sheet_names(spreadsheet):
    return [
        sheet.getAttrNS(NAME_ATTR[0], NAME_ATTR[1])
        for sheet in spreadsheet.getElementsByType(Table)
    ]


def _target_named_names(spreadsheet):
    names = []
    for element_type in (NamedRange, NamedExpression):
        for element in spreadsheet.getElementsByType(element_type):
            names.append(element.getAttrNS(NAME_ATTR[0], NAME_ATTR[1]))
    return names


def _named_expressions_container(spreadsheet):
    containers = spreadsheet.getElementsByType(NamedExpressions)
    if containers:
        return containers[0]
    container = NamedExpressions()
    first_table = next(
        (child for child in spreadsheet.childNodes
         if isinstance(child, Element) and child.qname == TABLE_QNAME),
        None)
    if first_table is None:
        spreadsheet.addElement(container)
    else:
        spreadsheet.insertBefore(container, first_table)
    return container


def export_named_range(source, range_name, target=None):
    """Export one named range and its worksheet into another spreadsheet.

    ``source`` is an :class:`odf.opendocument.OpenDocument` spreadsheet or a
    path that can be loaded with :func:`odf.opendocument.load`.  ``target``
    is the destination spreadsheet, either as a document instance or a path
    to an existing file; a fresh empty spreadsheet is created when it is
    omitted.  A target given as a path is loaded and returned in the result,
    leaving the caller in charge of saving it.

    The worksheet referenced by the range is deep copied, and the copied
    ``table:named-range`` keeps pointing at it.  Should the target already
    contain a worksheet or named expression with the same name, the existing
    definitions are preserved and the incoming copies are renamed.

    Returns a dictionary describing the export, including the target
    document under the ``document`` key.
    """
    source_document = load(source) if _is_path(source) else source
    if target is None:
        target = OpenDocumentSpreadsheet()
    elif _is_path(target):
        target = load(target)
    target_spreadsheet = getattr(target, 'spreadsheet', None)
    if target_spreadsheet is None:
        raise NamedRangeError("Target document is not a spreadsheet")

    named_range = find_named_range(source_document, range_name)
    address = named_range.getAttrNS(
        CELL_RANGE_ADDRESS_ATTR[0], CELL_RANGE_ADDRESS_ATTR[1])
    (sheet_name, top_left, bottom_right,
     first_ref, second_ref) = parse_range_address(address)
    source_sheet = find_sheet(source_document, sheet_name)

    sheet_copy = _deep_copy(source_sheet)
    new_sheet_name = _unique_name(
        _target_sheet_names(target_spreadsheet), sheet_name, u' ')
    sheet_copy.setAttrNS(NAME_ATTR[0], NAME_ATTR[1], new_sheet_name)
    target_spreadsheet.addElement(sheet_copy)

    range_copy = _deep_copy(named_range)
    new_range_name = _unique_name(
        _target_named_names(target_spreadsheet), range_name, u'_')
    range_copy.setAttrNS(NAME_ATTR[0], NAME_ATTR[1], new_range_name)
    new_address = _build_address(new_sheet_name, first_ref, second_ref)
    range_copy.setAttrNS(
        CELL_RANGE_ADDRESS_ATTR[0], CELL_RANGE_ADDRESS_ATTR[1],
        new_address)
    base_address = range_copy.getAttrNS(
        BASE_CELL_ADDRESS_ATTR[0], BASE_CELL_ADDRESS_ATTR[1])
    if base_address is not None:
        base_sheet, _, _, base_first, base_second = parse_range_address(
            base_address)
        if base_sheet == sheet_name:
            range_copy.setAttrNS(
                BASE_CELL_ADDRESS_ATTR[0], BASE_CELL_ADDRESS_ATTR[1],
                _build_address(new_sheet_name, base_first, base_second))

    container = _named_expressions_container(target_spreadsheet)
    container.addElement(range_copy)

    return {
        u'document': target,
        u'range_name': new_range_name,
        u'sheet_name': new_sheet_name,
        u'source_range_name': range_name,
        u'source_sheet_name': sheet_name,
        u'cell_range_address': new_address,
        u'top_left': top_left,
        u'bottom_right': bottom_right,
    }
