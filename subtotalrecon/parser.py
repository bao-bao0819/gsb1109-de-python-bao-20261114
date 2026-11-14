# -*- coding: utf-8 -*-
"""Parse an ODS financial table into a :class:`~subtotalrecon.model.Ledger`.

Row classification combines two sources of truth:

* the subtotal hierarchy *declared* by the sheet (``table:table-row-group``
  outline groups around each city's detail rows), and
* the *actual* row order: details accumulate until a subtotal row closes
  the segment, and the grand total row closes the table.

Amounts are never used to decide membership -- two adjacent cities may
total exactly the same, so equality proves nothing about parentage.
"""

import re
from decimal import Decimal, InvalidOperation

from odf.element import Element
from odf.namespaces import OFFICENS, TABLENS
from odf.opendocument import load
from odf.table import Table
from odf.teletype import extractText

from .model import Detail, GrandTotal, Ledger, Subtotal

GRAND_LABEL = re.compile(r"总计|合计|grand\s*total", re.IGNORECASE)
SUBTOTAL_LABEL = re.compile(r"小计|subtotal", re.IGNORECASE)

MAX_REPEAT = 1024

ROW_GROUP_Q = (TABLENS, "table-row-group")
ROW_Q = (TABLENS, "table-row")
CELL_QNAMES = {(TABLENS, "table-cell"), (TABLENS, "covered-table-cell")}
# Containers that may hold rows besides the table itself.
ROW_CONTAINER_QNAMES = {
    (TABLENS, "table"),
    (TABLENS, "table-header-rows"),
    (TABLENS, "table-rows"),
}


class StructureError(ValueError):
    """The sheet's declared hierarchy and its actual rows disagree."""


def _repeat(element, attr, default=1):
    value = element.getAttrNS(TABLENS, attr)
    if value is None:
        return default
    return min(int(value), MAX_REPEAT)


def _cell_text(cell):
    return extractText(cell).strip()


def _cell_amount(cell):
    raw = cell.getAttrNS(OFFICENS, "value")
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _read_row(row):
    """Return (texts, amounts) of a table row, repeats expanded."""
    texts = []
    amounts = []
    for cell in row.childNodes:
        if not isinstance(cell, Element) or cell.qname not in CELL_QNAMES:
            continue
        for _ in range(_repeat(cell, "number-columns-repeated")):
            text = _cell_text(cell)
            if text:
                texts.append(text)
            amount = _cell_amount(cell)
            if amount is not None:
                amounts.append(amount)
    return texts, amounts


def _walk_rows(element, groups=()):
    """Yield (row, groups) in document order, descending into row groups.

    ``groups`` is the stack of enclosing ``table:table-row-group`` elements
    -- the subtotal hierarchy declared by the sheet.
    """
    for child in element.childNodes:
        if not isinstance(child, Element):
            continue
        if child.qname == ROW_GROUP_Q:
            yield from _walk_rows(child, groups + (child,))
        elif child.qname == ROW_Q:
            for _ in range(_repeat(child, "number-rows-repeated")):
                yield child, groups
        elif child.qname in ROW_CONTAINER_QNAMES:
            yield from _walk_rows(child, groups)


def _city_of(label):
    city = SUBTOTAL_LABEL.sub("", label)
    return city.strip(" ：:()()") or label.strip()


def parse_ods(source, sheet_name=None):
    """Parse a financial table from an ODS file into a Ledger.

    ``source`` is a path or file-like object accepted by ``odf.load``.
    """
    doc = load(source)
    tables = doc.spreadsheet.getElementsByType(Table)
    if sheet_name is not None:
        tables = [t for t in tables if t.getAttrNS(TABLENS, "name") == sheet_name]
    if not tables:
        raise StructureError("no table found in %r" % (source,))
    return parse_table(tables[0])


def parse_table(table):
    details = []
    subtotals = []
    grand = None
    pending = []  # details since the last subtotal, with declared groups
    group_owner = {}  # declared group element -> subtotal row_id

    for row_index, (row, groups) in enumerate(_walk_rows(table), start=1):
        texts, amounts = _read_row(row)
        label = texts[0] if texts else ""
        amount = amounts[-1] if amounts else None
        row_id = "r%d" % row_index

        if GRAND_LABEL.search(label):
            if grand is not None:
                raise StructureError("second grand total at row %d" % row_index)
            if pending:
                raise StructureError(
                    "details at rows %s never closed by a subtotal"
                    % ", ".join(d.row_id for d, _ in pending)
                )
            if amount is None:
                raise StructureError("grand total row %d has no amount" % row_index)
            grand = GrandTotal(row_id=row_id, row_index=row_index, stored=amount)
        elif SUBTOTAL_LABEL.search(label):
            if amount is None:
                raise StructureError("subtotal row %d has no amount" % row_index)
            member_ids = [d.row_id for d, _ in pending]
            member_groups = {id(g) for _, gs in pending for g in gs}
            if len(member_groups) > 1:
                raise StructureError(
                    "subtotal %r at row %d mixes declared groups"
                    % (label, row_index)
                )
            level = max((len(gs) for _, gs in pending), default=0) or 1
            subtotal = Subtotal(
                row_id=row_id,
                row_index=row_index,
                city=_city_of(label),
                level=level,
                stored=amount,
                member_ids=member_ids,
            )
            for _, gs in pending:
                for g in gs:
                    owner = group_owner.setdefault(id(g), row_id)
                    if owner != row_id:
                        raise StructureError(
                            "declared row group is split between subtotals "
                            "%r and %r" % (owner, row_id)
                        )
            subtotals.append(subtotal)
            pending = []
        elif amount is not None:
            store = label
            city = texts[1] if len(texts) > 1 else ""
            detail = Detail(
                row_id=row_id,
                row_index=row_index,
                store=store,
                city=city,
                amount=amount,
            )
            details.append(detail)
            pending.append((detail, groups))
        # rows without an amount (headers, blanks) carry no data

    if grand is None:
        raise StructureError("no grand total row found")
    if pending:
        raise StructureError(
            "details at rows %s never closed by a subtotal"
            % ", ".join(d.row_id for d, _ in pending)
        )

    ledger = Ledger(details, subtotals, grand)
    # Details inherit their city from the owning subtotal when the sheet
    # leaves the city column blank.
    for st in ledger.subtotals:
        for member_id in st.member_ids:
            detail = ledger.details[member_id]
            if not detail.city:
                detail.city = st.city
    return ledger
