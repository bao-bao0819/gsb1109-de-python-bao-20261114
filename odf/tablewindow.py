# -*- coding: utf-8 -*-
"""Range-based reading of ODF tables.

ODF encodes runs of empty or identical rows and columns with the
``table:number-rows-repeated`` and ``table:number-columns-repeated``
attributes instead of repeating the XML elements.  Expanding those
attributes eagerly turns a sheet that only carries a handful of
interesting cells into hundreds of thousands of rows and columns, even
when the caller only asks for a small window.

The :class:`TableWindow` in this module walks a table while keeping the
repeat counts as integers, so the work and the memory used are
proportional to the requested window, not to the declared sheet size.
Repeated empty cells still occupy their logical columns - they are simply
skipped with arithmetic instead of being materialised.

Every returned :class:`CellWindow` reports the element that actually
stores the value.  Cells covered by a merged cell
(``table:number-columns-spanned`` / ``table:number-rows-spanned``) point
back at the anchor cell providing the value, including anchors that live
outside the requested window.
"""

from odf.namespaces import TABLENS, OFFICENS
from odf.element import Element
from odf import teletype

_TABLE = (TABLENS, u'table')
_TABLE_ROW = (TABLENS, u'table-row')
_TABLE_COLUMN = (TABLENS, u'table-column')
_TABLE_COLUMNS = (TABLENS, u'table-columns')
_COVERED_CELL = (TABLENS, u'covered-table-cell')

_ROW_CONTAINERS = frozenset([
    (TABLENS, u'table-header-rows'),
    (TABLENS, u'table-row-group'),
    (TABLENS, u'table-rows'),
])


def _int_attr(element, localname, default=1):
    """ Return a positive integer table attribute. """
    value = element.getAttrNS(TABLENS, localname)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def column_letter(index):
    """ Convert a zero-based column index into spreadsheet letters (A, B, ...). """
    letters = []
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters.append(chr(65 + remainder))
    return u''.join(reversed(letters))


def as_coordinate(row, column):
    """ Convert zero-based row/column indexes into an A1-style coordinate. """
    return column_letter(column) + str(row + 1)


class CellWindow(object):
    """ One logical cell inside a :class:`TableWindow`.

    ``row`` and ``column`` are the zero-based logical position inside the
    sheet.  ``source_row`` / ``source_column`` identify the anchor cell
    providing the value; for a covered cell they differ from ``row`` /
    ``column``, and for an empty cell they (and ``source_element``) are
    ``None``.
    """

    __slots__ = (
        'row', 'column', 'value', 'raw_value', 'value_type', 'formula',
        'currency', 'covered', 'source_element', 'source_row',
        'source_column',
    )

    def __init__(self, row, column, value=None, raw_value=None,
                 value_type=None, formula=None, currency=None,
                 covered=False, source_element=None, source_row=None,
                 source_column=None):
        self.row = row
        self.column = column
        self.value = value
        self.raw_value = raw_value
        self.value_type = value_type
        self.formula = formula
        self.currency = currency
        self.covered = covered
        self.source_element = source_element
        self.source_row = source_row
        self.source_column = source_column

    @property
    def coordinate(self):
        """ A1-style coordinate of the logical cell, e.g. ``C12``. """
        return as_coordinate(self.row, self.column)

    @property
    def source_coordinate(self):
        """ A1-style coordinate of the anchor cell, or ``None`` when empty. """
        if self.source_element is None:
            return None
        return as_coordinate(self.source_row, self.source_column)

    def __repr__(self):
        return u"CellWindow(%s, %r, source=%s)" % (
            self.coordinate, self.value, self.source_coordinate)


def _cell_payload(element):
    """ Extract the display text and value attributes from an anchor cell. """
    return (
        teletype.extractText(element) or None,
        element.getAttrNS(OFFICENS, u'value-type'),
        element.getAttrNS(OFFICENS, u'value'),
        element.getAttrNS(TABLENS, u'formula'),
        element.getAttrNS(OFFICENS, u'currency'),
    )


class TableWindow(object):
    """ Lazily read a rectangular window of a table.

    The bounds are half-open zero-based index ranges: ``row_start`` and
    ``column_start`` are inclusive, ``row_end`` and ``column_end``
    exclusive.  An end of ``None`` means "up to the last available row /
    column".  Repeated rows, columns and cells are never expanded, so the
    cost stays proportional to the window size rather than the declared
    repeat counts.
    """

    def __init__(self, table, row_start=0, column_start=0,
                 row_end=None, column_end=None):
        if not isinstance(table, Element) or table.qname != _TABLE:
            raise TypeError(u"Expected a table:table element")
        self._check_index(u'row_start', row_start)
        self._check_index(u'column_start', column_start)
        self._check_end(u'row_end', row_end, row_start)
        self._check_end(u'column_end', column_end, column_start)

        self._table = table
        self.row_start = row_start
        self.column_start = column_start
        self.row_end = row_end
        self.column_end = column_end
        self.declared_columns = self._count_columns(table)
        self._observed_width = self.declared_columns

    @staticmethod
    def _check_index(name, value):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(u"%s must be a non-negative integer" % name)

    @staticmethod
    def _check_end(name, value, start):
        if value is None:
            return
        if not isinstance(value, int) or isinstance(value, bool) or value < start:
            raise ValueError(u"%s must be an integer not smaller than the start" % name)

    @staticmethod
    def _count_columns(table):
        """ Logical column count declared by table-column elements. """
        count = 0
        for child in table.childNodes:
            if not isinstance(child, Element):
                continue
            if child.qname == _TABLE_COLUMN:
                count += _int_attr(child, u'number-columns-repeated')
            elif child.qname == _TABLE_COLUMNS:
                for sub in child.childNodes:
                    if isinstance(sub, Element) and sub.qname == _TABLE_COLUMN:
                        count += _int_attr(sub, u'number-columns-repeated')
        return count

    @staticmethod
    def _iter_row_entries(container):
        """ Yield ``(row_element, row_repeat)`` pairs in logical order.

        Descends into header rows, row groups and table-rows wrappers
        without expanding their contents.
        """
        for child in container.childNodes:
            if not isinstance(child, Element):
                continue
            if child.qname == _TABLE_ROW:
                yield child, _int_attr(child, u'number-rows-repeated')
            elif child.qname in _ROW_CONTAINERS:
                block = list(TableWindow._iter_row_entries(child))
                for _unused in range(_int_attr(child, u'number-rows-repeated')):
                    for entry in block:
                        yield entry

    def __iter__(self):
        return self._iter_cells()

    def _row_is_blank(self, row_element):
        """ True when a row element carries no values and no merges.

        A blank row may still contain repeated empty ``table-cell``
        elements; only its declared repeat counts matter, so it can be
        skipped with arithmetic instead of scanned for every copy.
        """
        logical_width = 0
        for child in row_element.childNodes:
            if not isinstance(child, Element):
                continue
            if child.qname == _COVERED_CELL:
                return False
            if child.getAttrNS(OFFICENS, u'value-type') is not None:
                return False
            if _int_attr(child, u'number-columns-spanned', 1) > 1 or \
               _int_attr(child, u'number-rows-spanned', 1) > 1 or \
               _int_attr(child, u'number-matrix-columns-spanned', 1) > 1 or \
               _int_attr(child, u'number-matrix-rows-spanned', 1) > 1:
                return False
            if teletype.extractText(child):
                return False
            logical_width += _int_attr(child, u'number-columns-repeated')
        if logical_width > self._observed_width:
            self._observed_width = logical_width
        return True

    def _span_covers_row(self, spans, row_index):
        for span in spans:
            if span[0] <= row_index < span[3]:
                return True
        return False

    def _emit_empty_rows(self, first_row, last_row):
        """ Yield empty cells for a run of rows without any cell elements. """
        width = self.column_end
        if width is None:
            width = self._observed_width
        low = max(first_row, self.row_start)
        for row_index in range(low, last_row):
            for column in range(self.column_start, width):
                yield self._empty_record(row_index, column)

    def _iter_cells(self):
        row_index = 0
        # Active merged regions for this iteration only, as
        # (anchor_row, anchor_column, element, row_end, column_end).
        spans = []
        for row_element, row_repeat in self._iter_row_entries(self._table):
            if self.row_end is not None and row_index >= self.row_end:
                break
            block_end = row_index + row_repeat
            if self.row_end is not None:
                block_end = min(block_end, self.row_end)

            if self._row_is_blank(row_element) and not self._span_covers_row(spans, row_index):
                # Repeated empty rows: advance by arithmetic only, never
                # materialising the declared repeat count.
                for record in self._emit_empty_rows(row_index, block_end):
                    yield record
                row_index += row_repeat
                continue

            for offset in range(block_end - row_index):
                current_row = row_index + offset
                emit = current_row >= self.row_start
                for record in self._scan_row(
                        row_element, current_row, emit, spans):
                    yield record
            row_index += row_repeat

    def rows(self):
        """ Yield one list of :class:`CellWindow` objects per logical row. """
        current = None
        current_row = None
        for cell in self:
            if cell.row != current_row:
                if current is not None:
                    yield current
                current = []
                current_row = cell.row
            current.append(cell)
        if current is not None:
            yield current

    def values(self):
        """ Yield the window as nested lists of display values. """
        for row in self.rows():
            yield [cell.value for cell in row]

    def _expire_spans(self, spans, row_index):
        # Columns reset to zero on every row, so spans must only be
        # retired once their row range has been fully scanned.
        spans[:] = [span for span in spans if row_index < span[3]]

    def _active_span(self, spans, row_index, column):
        for span in spans:
            if (span[0] <= row_index < span[3]
                    and span[1] <= column < span[4]):
                return span
        return None

    def _in_window(self, column):
        return column >= self.column_start and (
            self.column_end is None or column < self.column_end)

    def _empty_record(self, row_index, column):
        return CellWindow(row_index, column)

    def _anchor_record(self, row_index, column, element, source_column=None):
        value, value_type, raw_value, formula, currency = _cell_payload(element)
        if value is None and value_type is None and raw_value is None:
            # An explicit but empty cell carries no value and therefore no
            # provenance worth reporting.
            return CellWindow(row_index, column)
        if source_column is None:
            source_column = column
        return CellWindow(
            row_index, column, value=value, raw_value=raw_value,
            value_type=value_type, formula=formula, currency=currency,
            covered=False, source_element=element,
            source_row=row_index, source_column=source_column)

    def _covered_record(self, row_index, column, span):
        anchor_row, anchor_column, element = span[0], span[1], span[2]
        value, value_type, raw_value, formula, currency = _cell_payload(element)
        return CellWindow(
            row_index, column, value=value, raw_value=raw_value,
            value_type=value_type, formula=formula, currency=currency,
            covered=True, source_element=element,
            source_row=anchor_row, source_column=anchor_column)

    def _scan_row(self, row_element, row_index, emit, spans):
        self._expire_spans(spans, row_index)
        cells = [child for child in row_element.childNodes
                 if isinstance(child, Element)]
        cell_count = len(cells)
        cell_index = 0
        column = 0
        # Remaining copies of the current covered-cell element.  Kept
        # separate so a repeat run cannot spill past the merge it belongs to.
        covered_left = 0

        while True:
            span = self._active_span(spans, row_index, column)

            if span is not None:
                if covered_left == 0:
                    if (cell_index < cell_count
                            and cells[cell_index].qname == _COVERED_CELL):
                        covered_left = _int_attr(
                            cells[cell_index], u'number-columns-repeated')
                        cell_index += 1
                    else:
                        # Minimal ODF: the covered cell element is omitted.
                        covered_left = 1
                # A covered run never extends beyond its own merge region.
                repeat = min(covered_left, span[4] - column)
                covered_left -= repeat
                if emit:
                    end = column + repeat
                    high = end if self.column_end is None else min(end, self.column_end)
                    for covered_column in range(max(column, self.column_start), high):
                        yield self._covered_record(row_index, covered_column, span)
                column += repeat
                continue

            if cell_index >= cell_count:
                if column > self._observed_width:
                    self._observed_width = column
                if emit and self.column_end is not None:
                    for empty_column in range(max(column, self.column_start),
                                              self.column_end):
                        yield self._empty_record(row_index, empty_column)
                return

            cell = cells[cell_index]
            cell_index += 1

            if cell.qname == _COVERED_CELL:
                # Covered cell without a live merge: treat it as empty.
                repeat = _int_attr(cell, u'number-columns-repeated')
                if emit:
                    end = column + repeat
                    high = end if self.column_end is None else min(end, self.column_end)
                    for empty_column in range(max(column, self.column_start), high):
                        yield self._empty_record(row_index, empty_column)
                column += repeat
                continue

            repeat = _int_attr(cell, u'number-columns-repeated')
            columns_spanned = _int_attr(cell, u'number-columns-spanned', 1)
            if columns_spanned == 1:
                columns_spanned = _int_attr(
                    cell, u'number-matrix-columns-spanned', 1)
            rows_spanned = _int_attr(cell, u'number-rows-spanned', 1)
            if rows_spanned == 1:
                rows_spanned = _int_attr(cell, u'number-matrix-rows-spanned', 1)
            merged = columns_spanned > 1 or rows_spanned > 1

            if merged:
                for _unused in range(repeat):
                    spans.append((
                        row_index, column, cell,
                        row_index + rows_spanned, column + columns_spanned))
                    if emit and self._in_window(column):
                        yield self._anchor_record(row_index, column, cell)
                    column += 1
            else:
                end = column + repeat
                if emit:
                    high = end if self.column_end is None else min(end, self.column_end)
                    for anchor_column in range(max(column, self.column_start), high):
                        # Every copy shares the same XML element, whose own
                        # position is the first column of the run.
                        yield self._anchor_record(
                            row_index, anchor_column, cell,
                            source_column=column)
                column = end

    @staticmethod
    def read_range(table, row_start=0, column_start=0,
                   row_end=None, column_end=None):
        """ Convenience wrapper returning windowed rows as generators.

        Equivalent to ``TableWindow(...).rows()`` but usable as a plain
        function when only the values and cell provenance are needed.
        """
        return TableWindow(
            table, row_start, column_start, row_end, column_end).rows()
