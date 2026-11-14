# -*- coding: utf-8 -*-
# Copyright (C) 2026
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

"""Evaluate ODF spreadsheet content-validation rules against submitted values.

The functions here implement the part of ``table:content-validation`` that is
needed before importing data from an ODS file:

* constant lists, for example ``of:cell-content-is-in-list("A","B")``
* cell range lists, for example
  ``of:cell-content-is-in-list([$Sheet1.$C1:.$C2])``

Relative cell references are resolved against the rule's
``table:base-cell-address`` and the position of the cell that owns the rule.
That makes it possible for several rows to share one rule while referring to
a different list for every row.

The result is deliberately three-valued.  A rule can pass, be violated, or it
can be impossible to evaluate (``unverifiable``).  The latter carries a
``reason`` so a caller can tell an empty value from a broken reference.
"""

import re
from decimal import Decimal, InvalidOperation

from odf.namespaces import OFFICENS, TABLENS
from odf.table import ContentValidation, NamedRange, Table

__all__ = [
    'PASS', 'VIOLATION', 'UNVERIFIABLE',
    'ValidationResult', 'ValidationStatus',
    'validate_cell', 'validate_document', 'validate_values',
    'validate_submission', 'evaluate_condition',
]

PASS = 'pass'
VIOLATION = 'violation'
UNVERIFIABLE = 'unverifiable'


class ValidationStatus(object):
    """Namespace for the three status values.

    The class exists so callers can write ``ValidationStatus.PASS`` without
    having to import the module-level constants.  Comparing a status (which is
    a plain string) with these attributes works as expected.
    """
    PASS = PASS
    VIOLATION = VIOLATION
    UNVERIFIABLE = UNVERIFIABLE


class _Empty(object):
    def __repr__(self):
        return '<empty>'

    def __str__(self):
        return ''


EMPTY = _Empty()
_UNSET = object()


class ValidationResult(object):
    """The outcome of checking one submitted value.

    ``status`` is one of :data:`PASS`, :data:`VIOLATION` or
    :data:`UNVERIFIABLE`.  For an unverifiable result ``reason`` explains what
    was missing or unsupported.  ``value`` is the value that was checked, with
    :data:`EMPTY` representing a cell that was not filled in.
    """

    def __init__(self, status, cell_address=None, rule_name=None,
                 value=EMPTY, reason=None, condition=None):
        self.status = status
        self.cell_address = cell_address
        self.rule_name = rule_name
        self.value = value
        self.reason = reason
        self.condition = condition

    @property
    def passed(self):
        return self.status == PASS

    @property
    def valid(self):
        return self.status == PASS

    @property
    def is_valid(self):
        return self.status == PASS

    @property
    def violated(self):
        return self.status == VIOLATION

    @property
    def invalid(self):
        return self.status == VIOLATION

    @property
    def is_invalid(self):
        return self.status == VIOLATION

    @property
    def unverifiable(self):
        return self.status == UNVERIFIABLE

    @property
    def is_unverifiable(self):
        return self.status == UNVERIFIABLE

    def as_dict(self):
        return {
            'status': self.status,
            'cell_address': self.cell_address,
            'rule_name': self.rule_name,
            'value': self.value,
            'reason': self.reason,
            'condition': self.condition,
        }

    def __bool__(self):
        return self.status == PASS

    # Python 2 spelling.
    __nonzero__ = __bool__

    def __repr__(self):
        return 'ValidationResult(%r, cell_address=%r, rule_name=%r, reason=%r)' % (
            self.status, self.cell_address, self.rule_name, self.reason)


# ---------------------------------------------------------------------------
# Cell addresses
# ---------------------------------------------------------------------------

_CELL_WITH_ROW = re.compile(r'^(\$?)([A-Za-z]+)(\$?)([0-9]+)$')
_CELL_COLUMN = re.compile(r'^(\$?)([A-Za-z]+)$')
_CELL_ROW = re.compile(r'^(\$?)([0-9]+)$')


class _Address(object):
    def __init__(self, sheet=None, column=None, row=None,
                 column_absolute=False, row_absolute=False):
        self.sheet = sheet
        self.column = column
        self.row = row
        self.column_absolute = column_absolute
        self.row_absolute = row_absolute

    def __repr__(self):
        return '_Address(sheet=%r, column=%r, row=%r)' % (
            self.sheet, self.column, self.row)


class _AddressError(ValueError):
    pass


def _unquote_sheet(text):
    """Read a quoted sheet name and return (name, rest-of-string)."""
    text = text.strip()
    if text.startswith("$'"):
        pos = 1
    elif text.startswith("'"):
        pos = 0
    else:
        return None, text
    pos += 1
    value = []
    while pos < len(text):
        char = text[pos]
        if char == "'":
            if pos + 1 < len(text) and text[pos + 1] == "'":
                value.append("'")
                pos += 2
                continue
            return ''.join(value), text[pos + 1:]
        value.append(char)
        pos += 1
    raise _AddressError('unterminated quoted sheet name')


def _parse_cell_part(part):
    part = part.strip()
    match = _CELL_WITH_ROW.match(part)
    if match:
        return (_column_number(match.group(2)),
                int(match.group(4)),
                match.group(1) == '$',
                match.group(3) == '$')
    match = _CELL_COLUMN.match(part)
    if match:
        return _column_number(match.group(2)), None, match.group(1) == '$', False
    match = _CELL_ROW.match(part)
    if match:
        return None, int(match.group(2)), False, match.group(1) == '$'
    raise _AddressError('invalid cell reference: %s' % part)


def _column_number(letters):
    number = 0
    for char in letters.upper():
        number = number * 26 + (ord(char) - ord('A') + 1)
    if number < 1:
        raise _AddressError('invalid column reference: %s' % letters)
    return number


def _column_name(number):
    result = []
    while number:
        number, remainder = divmod(number - 1, 26)
        result.append(chr(ord('A') + remainder))
    return ''.join(reversed(result)) or 'A'


def _parse_address(text, default_sheet=None):
    """Parse ``Sheet.A1``, ``$Sheet.$A$1``, ``A1`` and friends."""
    text = text.strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1].strip()
    if not text:
        raise _AddressError('empty cell reference')

    sheet = default_sheet
    rest = text
    if text.startswith("'") or text.startswith("$'"):
        sheet, rest = _unquote_sheet(text)
        rest = rest.strip()
        if rest.startswith('.'):
            rest = rest[1:]
        elif rest:
            raise _AddressError('missing separator after sheet name')
    elif text.startswith('.'):
        # .$C2 -- the sheet is inherited from the other end of the range.
        rest = text[1:]
        sheet = default_sheet
    else:
        dot = text.rfind('.')
        if dot >= 0:
            sheet_part = text[:dot].strip()
            rest = text[dot + 1:]
            if sheet_part.startswith('$'):
                sheet_part = sheet_part[1:]
            if sheet_part.startswith("'") or sheet_part.endswith("'"):
                sheet, leftover = _unquote_sheet(sheet_part)
                if leftover.strip():
                    raise _AddressError('invalid sheet name: %s' % sheet_part)
            else:
                sheet = sheet_part
    column, row, column_absolute, row_absolute = _parse_cell_part(rest)
    return _Address(sheet, column, row, column_absolute, row_absolute)


def _split_top_level(text, separator):
    """Split on *separator*, ignoring quoted text and brackets/parentheses."""
    result = []
    start = 0
    quote = None
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
        elif char in '([':
            depth += 1
        elif char in ')]':
            depth -= 1
        elif char == separator and depth == 0:
            result.append(text[start:index])
            start = index + 1
        index += 1
    result.append(text[start:])
    return result


def _parse_range(text, default_sheet=None):
    text = text.strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1].strip()
    parts = _split_top_level(text, ':')
    if len(parts) == 1:
        address = _parse_address(parts[0], default_sheet)
        return address, address
    if len(parts) > 2:
        raise _AddressError('invalid cell range: %s' % text)
    start = _parse_address(parts[0], default_sheet)
    end = _parse_address(parts[1], start.sheet)
    return start, end


# ---------------------------------------------------------------------------
# Reading cells from a document
# ---------------------------------------------------------------------------

def _get_attribute(element, namespace, localpart):
    value = element.getAttrNS(namespace, localpart)
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return value


def _to_decimal(value):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _text_content(cell):
    parts = []
    for child in cell.childNodes:
        if child.nodeType in (child.TEXT_NODE, child.CDATA_SECTION_NODE):
            parts.append(str(child))
        elif child.nodeType == child.ELEMENT_NODE:
            parts.append(_text_content(child))
    return ''.join(parts)


def _cell_value(cell):
    """Return a typed cell value, or EMPTY for an unfilled cell."""
    value_type = _get_attribute(cell, OFFICENS, 'value-type')

    if value_type == 'boolean':
        raw = _get_attribute(cell, OFFICENS, 'boolean-value')
        if raw is None:
            return EMPTY
        return raw.strip().lower() in ('true', '1', 'yes')
    if value_type in ('float', 'percentage', 'currency'):
        raw = _get_attribute(cell, OFFICENS, 'value')
        if raw is None:
            raw = _text_content(cell)
        number = _to_decimal(raw) if raw is not None else None
        return EMPTY if number is None else number
    if value_type == 'date':
        raw = _get_attribute(cell, OFFICENS, 'date-value')
        return EMPTY if raw is None else raw
    if value_type == 'time':
        raw = _get_attribute(cell, OFFICENS, 'time-value')
        return EMPTY if raw is None else raw
    if value_type == 'string':
        raw = _get_attribute(cell, OFFICENS, 'string-value')
        if raw is None:
            raw = _get_attribute(cell, OFFICENS, 'value')
        if raw is None:
            raw = _text_content(cell)
        return raw if raw not in (None, '') else EMPTY

    # No explicit type: use explicit value attributes first, then text.
    raw = _get_attribute(cell, OFFICENS, 'value')
    if raw is not None:
        number = _to_decimal(raw)
        if number is not None:
            return number
    raw = _get_attribute(cell, OFFICENS, 'boolean-value')
    if raw is not None:
        return raw.strip().lower() in ('true', '1', 'yes')
    raw = _get_attribute(cell, OFFICENS, 'date-value')
    if raw is not None:
        return raw
    raw = _get_attribute(cell, OFFICENS, 'time-value')
    if raw is not None:
        return raw
    raw = _get_attribute(cell, OFFICENS, 'string-value')
    if raw is not None:
        return raw if raw != '' else EMPTY
    text = _text_content(cell)
    return text if text != '' else EMPTY


def _normalise_submitted(value):
    if value is None:
        return EMPTY
    if value is EMPTY:
        return EMPTY
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    if isinstance(value, str):
        return EMPTY if value == '' else value
    # date/datetime/time values are compared using their ISO representation.
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def _values_equal(left, right):
    if left is EMPTY or right is EMPTY:
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        if isinstance(left, bool) and isinstance(right, bool):
            return left == right
        return False
    if isinstance(left, Decimal) and isinstance(right, Decimal):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    # A numeric cell and a numeric constant compare by value.  Text cells keep
    # their text identity, so the string "0" is not the number zero.
    return left == right


class _Span(object):
    __slots__ = ('row_start', 'row_end', 'column_start', 'column_end', 'cell')

    def __init__(self, row_start, row_end, column_start, column_end, cell):
        self.row_start = row_start
        self.row_end = row_end
        self.column_start = column_start
        self.column_end = column_end
        self.cell = cell


class _Sheet(object):
    def __init__(self, name, table):
        self.name = name
        self.table = table
        self.spans = []
        self.max_row = 0
        self.max_column = 0
        self._build()

    @staticmethod
    def _repeat(element, attribute):
        raw = _get_attribute(element, TABLENS, attribute)
        if raw is None:
            return 1
        try:
            value = int(raw)
        except ValueError:
            return 1
        return max(1, value)

    def _child_rows(self, element):
        for child in element.childNodes:
            if child.nodeType != child.ELEMENT_NODE:
                continue
            if child.qname == (TABLENS, 'table-row'):
                yield child
            elif child.qname in (
                    (TABLENS, 'table-header-rows'),
                    (TABLENS, 'table-rows'),
                    (TABLENS, 'table-row-group')):
                for row in self._child_rows(child):
                    yield row

    def _build(self):
        self.max_column = max(self.max_column, self._column_count(self.table))

        row_index = 0
        for row in self._child_rows(self.table):
            row_repeat = self._repeat(row, 'number-rows-repeated')
            row_start = row_index + 1
            row_end = row_index + row_repeat
            self.max_row = max(self.max_row, row_end)

            column_index = 0
            for cell in row.childNodes:
                if cell.nodeType != cell.ELEMENT_NODE:
                    continue
                if cell.qname not in (
                        (TABLENS, 'table-cell'),
                        (TABLENS, 'covered-table-cell')):
                    continue
                column_repeat = self._repeat(cell, 'number-columns-repeated')
                row_span = self._repeat(cell, 'number-rows-spanned')
                column_start = column_index + 1
                column_end = column_index + column_repeat
                self.spans.append(_Span(
                    row_start, max(row_end, row_start + row_span - 1),
                    column_start, column_end, cell))
                self.max_column = max(self.max_column, column_end)
                self.max_row = max(self.max_row, row_start + row_span - 1)
                column_index = column_end
            row_index = row_end

    def _column_count(self, element):
        total = 0
        for child in element.childNodes:
            if child.nodeType != child.ELEMENT_NODE:
                continue
            if child.qname == (TABLENS, 'table-column'):
                total += self._repeat(child, 'number-columns-repeated')
            elif child.qname in (
                    (TABLENS, 'table-column-group'),
                    (TABLENS, 'table-header-columns'),
                    (TABLENS, 'table-columns')):
                total += self._column_count(child)
        return total

    def cell_at(self, row, column):
        for span in self.spans:
            if span.row_start <= row <= span.row_end and \
                    span.column_start <= column <= span.column_end:
                return span.cell
        return None

    def has_cell(self, row, column):
        for span in self.spans:
            if span.row_start <= row <= span.row_end and \
                    span.column_start <= column <= span.column_end:
                return True
        return False

    def values_in_range(self, row_start, row_end, column_start, column_end):
        """Return non-empty values and whether the range contains any cells."""
        values = []
        found = False
        for span in self.spans:
            if span.row_end < row_start or span.row_start > row_end:
                continue
            if span.column_end < column_start or span.column_start > column_end:
                continue
            found = True
            value = _cell_value(span.cell)
            if value is not EMPTY and value not in values:
                values.append(value)
        return values, found


def _sheet_names(document):
    """Return {name: table} for every table in the document."""
    result = {}
    tables = _find_elements(document, Table)
    for table in tables:
        name = _get_attribute(table, TABLENS, 'name')
        if name is not None and name not in result:
            result[name] = table
    return result


def _find_elements(document, factory):
    if document is None:
        return []
    getter = getattr(document, 'getElementsByType', None)
    if getter is not None:
        try:
            return list(getter(factory))
        except (AttributeError, AssertionError, TypeError):
            pass
    # An Element can be used directly as the root of the search.
    if hasattr(document, 'childNodes') and hasattr(document, 'qname'):
        return document.getElementsByType(factory)
    return []


class _Context(object):
    def __init__(self, document):
        self.document = document
        self.tables = _sheet_names(document)
        self.sheets = {}
        self.rules = {}
        self.named_ranges = {}
        for rule in _find_elements(document, ContentValidation):
            name = _get_attribute(rule, TABLENS, 'name')
            if name is not None and name not in self.rules:
                self.rules[name] = rule
        for item in _find_elements(document, NamedRange):
            name = _get_attribute(item, TABLENS, 'name')
            if name is not None and name not in self.named_ranges:
                self.named_ranges[name] = item

    def sheet(self, name):
        if name not in self.sheets:
            table = self.tables.get(name)
            self.sheets[name] = _Sheet(name, table) if table is not None else None
        return self.sheets[name]

    def all_sheets(self):
        for name in self.tables:
            sheet = self.sheet(name)
            if sheet is not None:
                yield name, sheet

    def default_sheet(self):
        if len(self.tables) == 1:
            return next(iter(self.tables))
        return None


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

_CONDITION = re.compile(r'^(?:[A-Za-z_][\w.-]*:)?\s*([A-Za-z_][\w.-]*)\s*\((.*)\)\s*$', re.S)
_NUMBER = re.compile(r'^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$')


class _Constant(object):
    def __init__(self, value):
        self.value = value


class _RangeSource(object):
    def __init__(self, text):
        self.text = text


class _NamedSource(object):
    def __init__(self, name):
        self.name = name


_CELL_REFERENCE = re.compile(
    r"^(?:(?:\$?(?:[A-Za-z]+(?:\.[A-Za-z]+)?|\$?'[^']+')\$?\.)?"
    r"(?:\$?[A-Za-z]{1,3}\$?[0-9]+|\$?[A-Za-z]{1,3}|\$?[0-9]+))$")


def _looks_like_cell_reference(text):
    if _CELL_REFERENCE.match(text):
        return True
    if text.startswith('$'):
        return bool(_CELL_REFERENCE.match(text[1:]))
    return False


def _parse_argument(argument):
    text = argument.strip()
    if not text:
        raise ValueError('empty argument in validation condition')
    if text.startswith('[') and text.endswith(']'):
        return _RangeSource(text[1:-1].strip())
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        quote = text[0]
        body = text[1:-1]
        body = body.replace(quote + quote, quote)
        body = body.replace('\\' + quote, quote)
        return _Constant(body)
    if _NUMBER.match(text):
        return _Constant(Decimal(text))
    lowered = text.lower()
    if lowered == 'true':
        return _Constant(True)
    if lowered == 'false':
        return _Constant(False)
    if ':' in text or _looks_like_cell_reference(text):
        return _RangeSource(text)
    return _NamedSource(text)


def _parse_condition(condition):
    if condition is None:
        raise ValueError('validation rule has no condition')
    match = _CONDITION.match(condition.strip())
    if not match:
        raise ValueError('unsupported validation condition: %s' % condition)
    function_name = match.group(1).lower().replace('_', '-')
    arguments = match.group(2)

    # OpenFormula uses either a comma or a semicolon as the argument
    # separator.  Both may appear inside a quoted string, so try both splits
    # and keep the one where every top-level piece parses as an argument.
    candidates = []
    for separator in (',', ';'):
        try:
            candidates.append([
                _parse_argument(part)
                for part in _split_top_level(arguments, separator)])
        except ValueError:
            continue
    if not candidates:
        raise ValueError('invalid arguments in validation condition: %s' % condition)
    # Prefer the split that produced more arguments, which is the one that
    # actually found a top-level separator.
    parsed = max(candidates, key=len)
    return function_name, parsed


def _resolve_address(address, base, target_sheet, target_column, target_row):
    sheet = address.sheet
    if sheet is None:
        sheet = target_sheet
    column = address.column
    if column is not None and not address.column_absolute:
        column += target_column - base.column
    row = address.row
    if row is not None and not address.row_absolute:
        row += target_row - base.row
    return sheet, column, row


def _range_bounds(start, end, base, target_sheet, target_column, target_row):
    start_sheet, start_column, start_row = _resolve_address(
        start, base, target_sheet, target_column, target_row)
    end_sheet, end_column, end_row = _resolve_address(
        end, base, start_sheet or target_sheet, target_column, target_row)

    if start_sheet is None:
        start_sheet = target_sheet
    if end_sheet is None:
        end_sheet = start_sheet
    if start_sheet != end_sheet:
        raise ValueError('cell range spans more than one sheet')

    if start_column is None:
        start_column = 1
    if end_column is None:
        end_column = None
    if start_row is None:
        start_row = 1
    if end_row is None:
        end_row = None

    if start_column is not None and start_column < 1:
        raise ValueError('cell range starts before column A')
    if start_row is not None and start_row < 1:
        raise ValueError('cell range starts before row 1')
    if end_column is not None and end_column < start_column:
        start_column, end_column = end_column, start_column
    if end_row is not None and end_row < start_row:
        start_row, end_row = end_row, start_row
    return start_sheet, start_column, start_row, end_column, end_row


class _EvaluationError(Exception):
    pass


def _range_values(context, source, base, target_sheet, target_column, target_row):
    try:
        start, end = _parse_range(source, target_sheet)
    except _AddressError as error:
        raise _EvaluationError(str(error))

    try:
        sheet_name, column_start, row_start, column_end, row_end = _range_bounds(
            start, end, base, target_sheet, target_column, target_row)
    except ValueError as error:
        raise _EvaluationError(str(error))

    if sheet_name is None:
        raise _EvaluationError(
            'cell range does not name a sheet and no sheet context is available')
    sheet = context.sheet(sheet_name)
    if sheet is None:
        raise _EvaluationError(
            "referenced sheet '%s' does not exist" % sheet_name)

    if column_end is None:
        column_end = max(sheet.max_column, column_start)
    if row_end is None:
        row_end = max(sheet.max_row, row_start)

    if column_end > sheet.max_column or row_end > sheet.max_row:
        raise _EvaluationError(
            "referenced range '%s!%s%d:%s%d' is missing or outside the table" % (
                sheet_name, _column_name(column_start), row_start,
                _column_name(column_end), row_end))

    values, found = sheet.values_in_range(
        row_start, row_end, column_start, column_end)
    if not found:
        raise _EvaluationError(
            "referenced range '%s!%s%d:%s%d' is missing or outside the table" % (
                sheet_name, _column_name(column_start), row_start,
                _column_name(column_end), row_end))
    return values


def _named_range_values(context, source, base, target_sheet,
                        target_column, target_row):
    item = context.named_ranges.get(source.name)
    if item is None:
        raise _EvaluationError(
            "named range '%s' does not exist" % source.name)
    address = _get_attribute(item, TABLENS, 'cell-range-address')
    if address is None:
        raise _EvaluationError(
            "named range '%s' has no cell range address" % source.name)
    return _range_values(context, address, base, target_sheet,
                         target_column, target_row)


def _rule_base(rule, context, target_sheet, target_column, target_row):
    raw = _get_attribute(rule, TABLENS, 'base-cell-address')
    if raw is None:
        return _Address(target_sheet, target_column, target_row,
                        True, True)
    try:
        base = _parse_address(raw, target_sheet)
    except _AddressError as error:
        raise _EvaluationError('invalid base cell address: %s' % error)
    if base.column is None or base.row is None:
        raise _EvaluationError('base cell address must name a single cell')
    if base.sheet is None:
        base.sheet = target_sheet
    return base


def _allow_empty(rule):
    raw = _get_attribute(rule, TABLENS, 'allow-empty-cell')
    if raw is None:
        return True
    return raw.strip().lower() not in ('false', '0', 'no')


def _evaluate(rule, context, target_sheet, target_column, target_row,
              value=_UNSET):
    """Evaluate a rule for one cell position and return a ValidationResult."""
    rule_name = _get_attribute(rule, TABLENS, 'name')
    condition = _get_attribute(rule, TABLENS, 'condition')

    if value is _UNSET:
        value = EMPTY

    # An empty cell is a separate case from a cell that contains a value.
    # In particular, the number zero is not empty.  ``table:allow-empty-cell``
    # decides the outcome without consulting the condition.
    if value is EMPTY:
        if _allow_empty(rule):
            return ValidationResult(
                PASS, rule_name=rule_name, value=EMPTY,
                condition=condition)
        return ValidationResult(
            VIOLATION, rule_name=rule_name, value=EMPTY,
            reason='empty cell is not allowed by the rule',
            condition=condition)

    try:
        base = _rule_base(rule, context, target_sheet,
                          target_column, target_row)
        if base.sheet is None:
            base.sheet = target_sheet
        function_name, arguments = _parse_condition(condition)
    except (_EvaluationError, ValueError) as error:
        return ValidationResult(
            UNVERIFIABLE, rule_name=rule_name, value=value,
            reason=str(error), condition=condition)

    if function_name != 'cell-content-is-in-list':
        return ValidationResult(
            UNVERIFIABLE, rule_name=rule_name, value=value,
            reason="unsupported validation condition function '%s'" % function_name,
            condition=condition)

    allowed = []
    try:
        for argument in arguments:
            if isinstance(argument, _Constant):
                allowed.append(argument.value)
            elif isinstance(argument, _RangeSource):
                allowed.extend(_range_values(
                    context, argument.text, base, target_sheet,
                    target_column, target_row))
            else:
                allowed.extend(_named_range_values(
                    context, argument, base, target_sheet,
                    target_column, target_row))
    except _EvaluationError as error:
        return ValidationResult(
            UNVERIFIABLE, rule_name=rule_name, value=value,
            reason=str(error), condition=condition)

    for candidate in allowed:
        if _values_equal(value, candidate):
            return ValidationResult(
                PASS, rule_name=rule_name, value=value,
                condition=condition)
    return ValidationResult(
        VIOLATION, rule_name=rule_name, value=value,
        reason='value is not in the allowed list',
        condition=condition)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _format_address(sheet, column, row):
    return '%s.%s%d' % (sheet, _column_name(column), row)


def _locate(context, cell, sheet=None):
    """Find (sheet-name, column, row, cell-element, error) for a cell."""
    if isinstance(cell, (tuple, list)) and len(cell) == 2:
        sheet, cell = cell
    if isinstance(cell, str):
        address = cell
        try:
            parsed = _parse_address(address, sheet or context.default_sheet())
        except _AddressError as error:
            return None, None, None, None, str(error)
        if parsed.sheet is None:
            return None, None, None, None, \
                'no sheet given for cell reference %s' % address
        if parsed.column is None or parsed.row is None:
            return None, None, None, None, \
                'cell reference must name a single cell: %s' % address
        target = context.sheet(parsed.sheet)
        if target is None:
            return None, None, None, None, \
                "sheet '%s' does not exist" % parsed.sheet
        return (parsed.sheet, parsed.column, parsed.row,
                target.cell_at(parsed.row, parsed.column), None)

    element = cell
    position = None
    if sheet is not None:
        target = context.sheet(sheet)
        if target is not None:
            for span in target.spans:
                if span.cell is element:
                    position = (sheet, span.column_start, span.row_start)
                    break
    if position is None:
        for name, target in context.all_sheets():
            for span in target.spans:
                if span.cell is element:
                    position = (name, span.column_start, span.row_start)
                    break
            if position is not None:
                break
    if position is None:
        return None, None, None, element, 'cell is not attached to a sheet'
    return position[0], position[1], position[2], element, None


def _validate_position(context, sheet_name, column, row, cell, value=_UNSET):
    if cell is None:
        return ValidationResult(
            UNVERIFIABLE,
            cell_address=_format_address(sheet_name, column, row),
            value=value,
            reason='no cell exists at this position')
    rule_name = _get_attribute(cell, TABLENS, 'content-validation-name')
    if rule_name is None:
        return ValidationResult(
            UNVERIFIABLE,
            cell_address=_format_address(sheet_name, column, row),
            value=value,
            reason='no content validation rule is bound to this cell')
    rule = context.rules.get(rule_name)
    if rule is None:
        return ValidationResult(
            UNVERIFIABLE,
            cell_address=_format_address(sheet_name, column, row),
            rule_name=rule_name,
            value=value,
            reason="content validation rule '%s' does not exist" % rule_name)
    if value is _UNSET:
        value = _cell_value(cell)
    else:
        value = _normalise_submitted(value)
    result = _evaluate(rule, context, sheet_name, column, row, value)
    result.cell_address = _format_address(sheet_name, column, row)
    return result


def validate_cell(document, cell, value=_UNSET, sheet=None):
    """Validate one cell or submitted value.

    ``document`` is an :class:`odf.opendocument.OpenDocument`, an
    ``office:spreadsheet`` element, or anything else that implements
    ``getElementsByType``.

    ``cell`` can be a ``table:table-cell`` element or an address such as
    ``"Sheet1.B2"``.  When ``value`` is omitted the value currently stored in
    the cell is used; pass ``value=0`` (or any other value, including ``None``
    for an empty submission) to validate a value that is about to be imported.

    The returned :class:`ValidationResult` has status ``pass``, ``violation``
    or ``unverifiable``.
    """
    context = _Context(document)
    submitted = value
    if value is not _UNSET:
        submitted = _normalise_submitted(value)
    sheet_name, column, row, element, error = _locate(
        context, cell, sheet=sheet)
    if error is not None:
        return ValidationResult(
            UNVERIFIABLE, value=submitted, reason=error)
    return _validate_position(
        context, sheet_name, column, row, element, submitted)


def _iter_bound_cells(context):
    for sheet_name, sheet in context.all_sheets():
        for span in sheet.spans:
            if _get_attribute(span.cell, TABLENS,
                              'content-validation-name') is None:
                continue
            # Empty repeated cells are real cells, but validating millions of
            # them would be pointless.  Report one result per distinct value.
            rows = range(span.row_start, span.row_end + 1)
            columns = range(span.column_start, span.column_end + 1)
            for row in rows:
                for column in columns:
                    yield sheet_name, column, row, span.cell


def validate_document(document):
    """Validate every cell in *document* that has a validation rule.

    Returns a list of :class:`ValidationResult`, one per bound cell.
    """
    context = _Context(document)
    results = []
    for sheet_name, column, row, cell in _iter_bound_cells(context):
        results.append(_validate_position(
            context, sheet_name, column, row, cell))
    return results


def validate_values(document, values, sheet=None):
    """Validate submitted values before importing them.

    ``values`` may be a mapping or an iterable of ``(address, value)`` pairs.
    ``address`` can be an ODF address such as ``"Sheet1.B2"`` or a cell
    element.  The result is a list of :class:`ValidationResult` in input order.
    Values whose cell has no rule are reported as ``unverifiable`` rather than
    silently accepted.
    """
    context = _Context(document)
    if hasattr(values, 'items'):
        items = list(values.items())
    else:
        items = list(values)

    results = []
    for raw_address, value in items:
        sheet_name, column, row, element, error = _locate(
            context, raw_address, sheet=sheet)
        if error is not None:
            results.append(ValidationResult(
                UNVERIFIABLE, cell_address=str(raw_address),
                value=_normalise_submitted(value), reason=error))
            continue
        result = _validate_position(
            context, sheet_name, column, row, element,
            _normalise_submitted(value))
        results.append(result)
    return results


# A descriptive alias for callers that model this as an import gate.
validate_submission = validate_values


def evaluate_condition(condition, value, base_cell_address=None,
                       document=None, sheet=None, cell_address=None,
                       allow_empty_cell=True):
    """Evaluate a single condition against *value*.

    This is a low-level convenience helper for callers that already know the
    condition and want to test a submitted value without building an ODS
    document.  ``base_cell_address`` is the rule's ``table:base-cell-address``;
    ``cell_address`` is the cell the value is submitted for.  A ``document`` is
    only needed when the condition refers to a cell range or a named range.
    """
    context = _Context(document)
    default_sheet = context.default_sheet() if sheet is None else sheet
    cell_column = cell_row = 1

    if cell_address is not None:
        try:
            parsed = _parse_address(cell_address, default_sheet)
        except _AddressError as error:
            return ValidationResult(
                UNVERIFIABLE, value=_normalise_submitted(value),
                reason=str(error), condition=condition)
        if default_sheet is None:
            default_sheet = parsed.sheet
        if parsed.column is not None:
            cell_column = parsed.column
        if parsed.row is not None:
            cell_row = parsed.row

    if default_sheet is None:
        default_sheet = ''

    rule = _ConditionRule(condition, base_cell_address, allow_empty_cell)
    return _evaluate(rule, context, default_sheet, cell_column, cell_row,
                     _normalise_submitted(value))


class _ConditionRule(object):
    """A minimal rule object for :func:`evaluate_condition`."""

    def __init__(self, condition, base_cell_address, allow_empty_cell):
        self._attributes = {
            (TABLENS, 'name'): None,
            (TABLENS, 'condition'): condition,
            (TABLENS, 'base-cell-address'): base_cell_address,
            (TABLENS, 'allow-empty-cell'): (
                'true' if allow_empty_cell else 'false'),
        }

    def getAttrNS(self, namespace, localpart):
        return self._attributes.get((namespace, localpart))
