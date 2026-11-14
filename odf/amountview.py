# -*- coding: utf-8 -*-
#
# Reader-side amount column view for ODF spreadsheets.
#
# This module inspects the tables of a spreadsheet document and decides,
# cell by cell, whether the cell holds a monetary amount.  For every cell
# in an amount column it reports:
#
# * the numeric value exactly as stored in the file,
# * the currency (from the cell's table:currency attribute, or inferred
#   from the currency data style that formats the cell),
# * where the format came from: an explicit cell style takes precedence
#   over the row default, which takes precedence over the column default,
# * or, for cells that cannot be recognised as amounts, the reason why.
#
# Two currencies that happen to share one symbol (for example US and
# Australian dollars both rendering as "$") are never merged: totals are
# computed per currency code, and cells whose currency is only known
# through a symbol are grouped by the (symbol, language, country) triple
# of the data style.  Every total keeps the list of cell references and
# raw values it was computed from, so it can be traced back to the file.

import copy
from decimal import Decimal, InvalidOperation

from odf.element import Node
from odf.namespaces import STYLENS, TABLENS, NUMBERNS, OFFICENS
from odf.style import Style
from odf.table import Table
from odf.teletype import extractText

REASON_NO_VALUE = u'no-value'
REASON_NO_CURRENCY_FORMAT = u'no-currency-format'
REASON_MISSING_CURRENCY = u'missing-currency'
REASON_INVALID_VALUE = u'invalid-value'

REASON_MESSAGES = {
    REASON_NO_VALUE:
        u'no numeric value stored (the cell is empty or only displays text)',
    REASON_NO_CURRENCY_FORMAT:
        u'numeric value without a currency value-type or currency format',
    REASON_MISSING_CURRENCY:
        u'currency value without a currency code and no currency format to infer it from',
    REASON_INVALID_VALUE:
        u'the stored value cannot be parsed as a number',
}

SOURCE_CELL = u'cell'
SOURCE_ROW = u'row'
SOURCE_COLUMN = u'column'

TABLE_CELL = (TABLENS, u'table-cell')
COVERED_TABLE_CELL = (TABLENS, u'covered-table-cell')
TABLE_ROW = (TABLENS, u'table-row')
TABLE_COLUMN = (TABLENS, u'table-column')
CURRENCY_STYLE = (NUMBERNS, u'currency-style')
CURRENCY_SYMBOL = (NUMBERNS, u'currency-symbol')


def column_letter(index):
    """Convert a zero-based column index to a spreadsheet letter (A, B, .. AA)."""
    letters = u''
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord(u'A') + remainder) + letters
    return letters


class AmountCell(object):
    """One logical cell of an amount column."""

    def __init__(self, table, row, column):
        self.table = table          # table name
        self.row = row              # 1-based row number
        self.column = column        # column letter, e.g. u'B'
        self.ref = u'%s.%s%d' % (table, column, row)
        self.value = None           # Decimal value or None
        self.raw_value = None       # value string exactly as stored
        self.currency = None        # ISO code from table:currency, if any
        self.currency_source = None # u'cell' or u'style'
        self.symbol = None          # currency symbol from the data style
        self.symbol_language = None
        self.symbol_country = None
        self.format_source = None   # u'cell', u'row', u'column' or None
        self.style_name = None      # effective cell style name
        self.data_style_name = None # currency data style name, if any
        self.display = None         # text the cell displays, if any
        self.reason = None          # None when recognised as an amount

    @property
    def is_amount(self):
        return self.reason is None

    @property
    def reason_message(self):
        return REASON_MESSAGES.get(self.reason, self.reason)

    @property
    def group_key(self):
        """Aggregation key.  Cells are only summed together when they share
        a currency code, or - when no code is stored - the exact
        (symbol, language, country) of their data style.  A bare symbol is
        never enough, because two currencies may render the same symbol."""
        if self.currency is not None:
            return (u'code', self.currency)
        return (u'symbol', self.symbol, self.symbol_language, self.symbol_country)

    def __repr__(self):
        return u'<AmountCell %s value=%r currency=%r reason=%r>' % (
            self.ref, self.raw_value, self.currency, self.reason)


class CurrencySummary(object):
    """Total of the recognised amounts sharing one group key."""

    def __init__(self, key):
        self.key = key
        self.currency = key[1] if key[0] == u'code' else None
        self.symbol = None if key[0] == u'code' else key[1]
        self.language = None if key[0] == u'code' else key[2]
        self.country = None if key[0] == u'code' else key[3]
        self.total = Decimal(0)
        self.cells = []             # AmountCell objects, in document order

    def add(self, cell):
        self.total += cell.value
        self.cells.append(cell)

    @property
    def label(self):
        if self.currency is not None:
            return self.currency
        return u'symbol %r (language=%s, country=%s)' % (
            self.symbol, self.language, self.country)

    def trace(self):
        """(reference, raw value) pairs the total was computed from."""
        return [(cell.ref, cell.raw_value) for cell in self.cells]

    def verify(self):
        """Re-add the raw values stored in the file and compare with the total."""
        return self.total == sum(Decimal(cell.raw_value) for cell in self.cells)


def _cell_styles(doc):
    if doc.element_dict == {}:
        doc.rebuild_caches()
    styles = {}
    for elt in doc.getElementsByType(Style):
        name = elt.getAttrNS(STYLENS, u'name')
        if name is not None:
            styles[name] = elt
    return styles


def _data_styles(doc):
    """Index of the number:xxx-style elements by their style:name."""
    styles = {}
    for container in (doc.styles, doc.automaticstyles):
        for elt in container.childNodes:
            if elt.nodeType == Node.ELEMENT_NODE and elt.qname[0] == NUMBERNS:
                name = elt.getAttrNS(STYLENS, u'name')
                if name is not None:
                    styles[name] = elt
    return styles


def _data_style_name(cell_style, cell_styles):
    """The style:data-style-name of a cell style, following parent styles."""
    seen = set()
    style = cell_style
    while style is not None and style not in seen:
        seen.add(style)
        data_style = style.getAttrNS(STYLENS, u'data-style-name')
        if data_style is not None:
            return data_style
        parent = style.getAttrNS(STYLENS, u'parent-style-name')
        style = cell_styles.get(parent) if parent is not None else None
    return None


def _currency_symbol(data_style):
    """(symbol, language, country) of a number:currency-style, else None."""
    if data_style is None or data_style.qname != CURRENCY_STYLE:
        return None
    for child in data_style.childNodes:
        if child.nodeType == Node.ELEMENT_NODE and child.qname == CURRENCY_SYMBOL:
            return (extractText(child),
                    child.getAttrNS(NUMBERNS, u'language'),
                    child.getAttrNS(NUMBERNS, u'country'))
    return (u'', None, None)


def _repeated(elt):
    count = elt.getAttrNS(TABLENS, u'number-columns-repeated')
    if count is None:
        count = elt.getAttrNS(TABLENS, u'number-rows-repeated')
    if count is None:
        return 1
    try:
        return max(1, int(count))
    except ValueError:
        return 1


def _value_attr(elt, localpart):
    """Value attributes live in the office namespace since ODF 1.2; older
    files keep them in the table namespace.  Accept both."""
    value = elt.getAttrNS(OFFICENS, localpart)
    if value is None:
        value = elt.getAttrNS(TABLENS, localpart)
    return value


class _AmountViewBuilder(object):

    def __init__(self, doc):
        self.cell_styles = _cell_styles(doc)
        self.data_styles = _data_styles(doc)

    def effective_style(self, cell, row, columns, col_index):
        """The cell style in effect and where it came from.  An explicit
        table:style-name on the cell wins over the row default, which wins
        over the column default."""
        name = cell.getAttrNS(TABLENS, u'style-name')
        if name is not None:
            return name, SOURCE_CELL
        name = row.getAttrNS(TABLENS, u'default-cell-style-name')
        if name is not None:
            return name, SOURCE_ROW
        if col_index < len(columns):
            name = columns[col_index].getAttrNS(TABLENS, u'default-cell-style-name')
            if name is not None:
                return name, SOURCE_COLUMN
        return None, None

    def describe_cell(self, table_name, row, columns, row_index, col_index, cell):
        """Build the AmountCell for one logical cell, or None when the cell
        carries neither a value, a value-type, text nor a currency format."""
        style_name, source = self.effective_style(cell, row, columns, col_index)
        data_style_name = None
        symbol_info = None
        if style_name is not None:
            cell_style = self.cell_styles.get(style_name)
            data_style_name = _data_style_name(cell_style, self.cell_styles)
            if data_style_name is not None:
                symbol_info = _currency_symbol(self.data_styles.get(data_style_name))

        value_type = _value_attr(cell, u'value-type')
        raw_value = _value_attr(cell, u'value')
        display = extractText(cell).strip()

        if (raw_value is None and value_type is None and not display
                and symbol_info is None):
            return None

        record = AmountCell(table_name, row_index + 1, column_letter(col_index))
        record.raw_value = raw_value
        record.display = display or None
        record.format_source = source
        record.style_name = style_name
        record.data_style_name = data_style_name
        if symbol_info is not None:
            record.symbol, record.symbol_language, record.symbol_country = symbol_info

        currency = _value_attr(cell, u'currency')
        if currency is not None:
            record.currency = currency
            record.currency_source = SOURCE_CELL
        elif symbol_info is not None:
            record.currency_source = u'style'

        if raw_value is not None:
            try:
                record.value = Decimal(raw_value)
            except InvalidOperation:
                record.reason = REASON_INVALID_VALUE
                return record

        if record.value is None:
            record.reason = REASON_NO_VALUE
        elif value_type != u'currency' and symbol_info is None:
            record.reason = REASON_NO_CURRENCY_FORMAT
        elif record.currency is None and symbol_info is None:
            record.reason = REASON_MISSING_CURRENCY
        return record

    def row_records(self, table_name, row, columns, row_index):
        """Records for one instance of a row.  Repeated cells yield one
        record per logical cell so every reference traces back to the file."""
        records = []
        col_index = 0
        for cell in row.childNodes:
            if cell.nodeType != Node.ELEMENT_NODE:
                continue
            if cell.qname == COVERED_TABLE_CELL:
                col_index += _repeated(cell)
                continue
            if cell.qname != TABLE_CELL:
                continue
            repeat = _repeated(cell)
            record = self.describe_cell(table_name, row, columns, row_index, col_index, cell)
            if record is not None:
                records.append(record)
                # Each logical cell may sit in a column with a different
                # default style, so the format source is resolved per cell.
                for offset in range(1, repeat):
                    records.append(self.describe_cell(
                        table_name, row, columns, row_index, col_index + offset, cell))
            col_index += repeat
        return records


def _move_record(record, table_name, row_index, column):
    """Copy a record for another logical cell of a repeated row or cell."""
    clone = copy.copy(record)
    clone.row = row_index + 1
    clone.column = column
    clone.ref = u'%s.%s%d' % (table_name, clone.column, clone.row)
    return clone


def iter_amount_cells(doc, tablename=None):
    """Yield an AmountCell for every logical cell of the document that
    stores a value, displays text or carries a currency format.  Cells
    that are completely empty are skipped."""
    builder = _AmountViewBuilder(doc)
    if doc.element_dict == {}:
        doc.rebuild_caches()
    for tbl in doc.getElementsByType(Table):
        table_name = tbl.getAttrNS(TABLENS, u'name') or u''
        if tablename is not None and table_name != tablename:
            continue
        columns = []
        row_index = 0
        for child in tbl.childNodes:
            if child.nodeType != Node.ELEMENT_NODE:
                continue
            if child.qname == TABLE_COLUMN:
                columns.extend([child] * _repeated(child))
            elif child.qname == TABLE_ROW:
                repeat = _repeated(child)
                records = builder.row_records(table_name, child, columns, row_index)
                for record in records:
                    yield record
                # Remaining instances of a repeated row only matter when
                # the first instance produced records.
                for offset in range(1, repeat):
                    if not records:
                        break
                    for record in records:
                        yield _move_record(record, table_name,
                                           row_index + offset, record.column)
                row_index += repeat


def amount_view(doc, tablename=None, column=None):
    """The amount column view: every reported cell, in document order.
    Optionally restricted to one table and/or one column letter."""
    cells = iter_amount_cells(doc, tablename=tablename)
    if column is not None:
        column = column.upper()
        cells = (cell for cell in cells if cell.column == column)
    return list(cells)


def amount_columns(cells):
    """Group the view by (table, column).  Only columns holding at least
    one recognised amount are included."""
    grouped = {}
    order = []
    for cell in cells:
        key = (cell.table, cell.column)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(cell)
    return [(key, grouped[key]) for key in order
            if any(cell.is_amount for cell in grouped[key])]


def summarize(cells):
    """Per-currency totals of the recognised amounts.  Groups are keyed by
    currency code, or by the exact (symbol, language, country) of the data
    style when no code is stored, so two currencies that merely share a
    symbol are never added together.  Each summary keeps the cells it was
    computed from; use verify() to check the total against the raw values."""
    groups = {}
    order = []
    for cell in cells:
        if not cell.is_amount:
            continue
        key = cell.group_key
        if key not in groups:
            groups[key] = CurrencySummary(key)
            order.append(key)
        groups[key].add(cell)
    return [groups[key] for key in order]


def symbol_conflicts(summaries):
    """Symbols used by more than one summary group.  Totals for these
    symbols must not be merged, because the symbol does not identify a
    single currency."""
    seen = {}
    conflicts = []
    for summary in summaries:
        symbol = summary.symbol
        if symbol is None:
            # Look up the symbol the group's cells display, if any.
            symbols = set(cell.symbol for cell in summary.cells if cell.symbol)
            symbol = symbols.pop() if len(symbols) == 1 else None
        if symbol is None:
            continue
        if symbol in seen:
            if seen[symbol] not in conflicts:
                conflicts.append(seen[symbol])
            conflicts.append(summary)
        else:
            seen[symbol] = summary
    return conflicts


def format_report(cells):
    """Render the amount column view as plain text."""
    lines = []
    for (table, column), column_cells in amount_columns(cells):
        lines.append(u'Table %s, column %s' % (table, column))
        for cell in column_cells:
            if cell.is_amount:
                currency = cell.currency or u'%s (currency code unknown)' % cell.symbol
                if cell.format_source is None:
                    origin = u'no cell format'
                else:
                    origin = u'format from %s style (%s)' % (
                        cell.format_source, cell.data_style_name or u'no data style')
                lines.append(u'  %s\t%s %s\t%s'
                             % (cell.ref, cell.raw_value, currency, origin))
            else:
                lines.append(u'  %s\tnot an amount: %s'
                             % (cell.ref, cell.reason_message))
        lines.append(u'')
    summaries = summarize(cells)
    if summaries:
        lines.append(u'Totals by currency:')
        for summary in summaries:
            trace = u', '.join(u'%s=%s' % (ref, raw) for ref, raw in summary.trace())
            lines.append(u'  %s\t%s\tfrom %s' % (summary.label, summary.total, trace))
    conflicts = symbol_conflicts(summaries)
    if conflicts:
        symbols = sorted(set(summary.symbol for summary in conflicts
                             if summary.symbol is not None))
        lines.append(u'Warning: symbol(s) %s are used by more than one currency '
                     u'group; their totals are reported separately and must not '
                     u'be merged by symbol.' % u', '.join(symbols))
    return u'\n'.join(lines)
