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
# Contributor(s):
#

"""Partial views of a spreadsheet built from a chosen set of tracked changes.

An ODS spreadsheet may carry unresolved table change records (insertions,
deletions, movements of rows/columns) in its ``table:tracked-changes``
container.  This module reconstructs the logical coordinates a *current*
cell had in the version represented by a selected subset of those records.

Selection semantics
-------------------
A selected change is a change that remains *applied* in the exported view.
The empty selection therefore reconstructs the original sheet (insertions
dropped, deletions restored, movements undone) while selecting every change
reproduces the current sheet.

The reconstruction is a two pass token replay, performed independently on
the row axis and the column axis:

1. All recorded changes are undone in reverse document order.  Physical
   slots are labelled as either an original index or as a slot created by a
   specific insertion.  Deleted original slots are reintroduced as
   synthetic tokens.
2. Only the selected, dependency-complete changes are re-applied in record
   order.  Because every record stores coordinates relative to the sheet as
   it existed when the change was made, replaying them in order keeps the
   coordinates consistent.

Rules enforced here:

* A selected change whose dependency is not selected (or is missing from the
  document) is *broken*.  The cells covered by a broken change are reported
  in :attr:`SelectedView.unresolvable` instead of receiving fabricated
  coordinates; every unrelated cell is still exported.
* The old values stored in a deletion only exist in versions where that
  deletion is reverted, i.e. where it is not part of the selection.
* Inserted rows/columns have no coordinate in the original sheet.  They are
  represented by :class:`InsertedCoordinate` tokens rather than invented
  integer addresses.
"""

from odf.namespaces import TABLENS
from odf.table import Table, TrackedChanges
from odf.teletype import extractText

ROW = u'row'
COLUMN = u'column'
TABLE = u'table'

INSERTION = u'insertion'
DELETION = u'deletion'
MOVEMENT = u'movement'

# ODS files repeat empty rows/columns up to the full sheet size.  Expanding
# those blindly would allocate gigantic grids, so cap the expansion.
DEFAULT_MAX_REPEAT = 10000


class UnresolvableCell(Exception):
    """Raised when a physical cell cannot be mapped to the selected version."""

    def __init__(self, physical_row, physical_column, reason, change_ids=None):
        self.physical_row = physical_row
        self.physical_column = physical_column
        self.reason = reason
        self.change_ids = tuple(change_ids or ())
        message = u"cell (%d, %d) cannot be rebuilt: %s" % (
            physical_row, physical_column, reason)
        if self.change_ids:
            message += u" (changes: %s)" % u", ".join(self.change_ids)
        Exception.__init__(self, message)


class InsertedCoordinate(object):
    """Logical coordinate of a row/column created by a selected insertion.

    Inserted slots never existed in the original sheet, so they cannot be
    expressed as an original index.  They are identified by the insertion
    change id plus a zero based ordinal inside that insertion.
    """

    __slots__ = ('axis', 'change_id', 'ordinal')

    def __init__(self, axis, change_id, ordinal):
        self.axis = axis
        self.change_id = change_id
        self.ordinal = ordinal

    @property
    def label(self):
        return u"inserted-%s:%s#%d" % (self.axis, self.change_id, self.ordinal)

    def __str__(self):
        return self.label

    def __repr__(self):
        return u"InsertedCoordinate(%r, %r, %r)" % (
            self.axis, self.change_id, self.ordinal)

    def _key(self):
        return (self.axis, self.change_id, self.ordinal)

    def __eq__(self, other):
        return isinstance(other, InsertedCoordinate) and \
            self._key() == other._key()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(('InsertedCoordinate',) + self._key())


class _OriginalToken(object):
    """A row/column that existed in the sheet before any tracked change."""

    __slots__ = ('axis', 'index')

    def __init__(self, axis, index):
        self.axis = axis
        self.index = index

    def _key(self):
        return ('original', self.axis, self.index)

    def __eq__(self, other):
        return isinstance(other, _OriginalToken) and self._key() == other._key()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return u"Original(%s=%d)" % (self.axis, self.index)


class _InsertedToken(object):
    """A physical slot created by an insertion change."""

    __slots__ = ('axis', 'change_id', 'ordinal')

    def __init__(self, axis, change_id, ordinal):
        self.axis = axis
        self.change_id = change_id
        self.ordinal = ordinal

    def as_coordinate(self):
        return InsertedCoordinate(self.axis, self.change_id, self.ordinal)

    def _key(self):
        return ('inserted', self.axis, self.change_id, self.ordinal)

    def __eq__(self, other):
        return isinstance(other, _InsertedToken) and self._key() == other._key()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return u"Inserted(%s:%s#%d)" % (
            self.axis, self.change_id, self.ordinal)


class ChangeRecord(object):
    """Parsed view of a single ``table:tracked-changes`` child record."""

    def __init__(self, change_id, kind):
        self.change_id = change_id
        self.kind = kind
        # insertion / deletion: ROW, COLUMN or TABLE
        self.axis = None
        self.position = None
        self.count = 1
        self.table_index = 0
        # movement geometry as dicts of integer address attributes
        self.source = None
        self.target = None
        self.dependencies = ()
        # list of {'address': (table, row, column) or None, 'value': text}
        self.deleted_cells = []
        # list of {'kind': INSERTION|MOVEMENT, 'id': ..., 'position': ...,
        #          'start': ..., 'end': ...}
        self.cutoffs = []

    def __repr__(self):
        return u"ChangeRecord(%r, %r)" % (self.change_id, self.kind)


def _element_children(element):
    if element is None:
        return ()
    return [c for c in element.childNodes if c.nodeType == c.ELEMENT_NODE]


def _find_children(element, localname):
    return [c for c in _element_children(element)
            if c.qname == (TABLENS, localname)]


def _find_child(element, localname):
    matches = _find_children(element, localname)
    return matches[0] if matches else None


def _attr(element, localname):
    if element is None:
        return None
    return element.getAttrNS(TABLENS, localname)


def _int_attr(element, localname, default=None):
    value = _attr(element, localname)
    if value is None:
        return default
    return int(value)


def _parse_address(element):
    result = {}
    for name in ('table', 'row', 'column', 'start-table', 'start-row',
                 'start-column', 'end-table', 'end-row', 'end-column'):
        value = _int_attr(element, name)
        if value is not None:
            result[name] = value
    return result


def _parse_deleted_cells(change_element):
    records = []
    deletions = _find_child(change_element, 'deletions')
    for content_deletion in _find_children(deletions,
                                           'cell-content-deletion'):
        address_el = _find_child(content_deletion, 'cell-address')
        address = None
        if address_el is not None:
            address = (
                _int_attr(address_el, 'table', 0),
                _int_attr(address_el, 'row'),
                _int_attr(address_el, 'column'),
            )
        old_cell = _find_child(content_deletion,
                               'change-track-table-cell')
        value = extractText(old_cell) if old_cell is not None else u''
        records.append({'address': address, 'value': value})
    return records


def _parse_cutoffs(change_element):
    records = []
    cutoffs = _find_child(change_element, 'cut-offs')
    for cutoff in _element_children(cutoffs):
        localname = cutoff.qname[1]
        if localname == 'insertion-cut-off':
            records.append({
                'kind': INSERTION,
                'id': _attr(cutoff, 'id'),
                'position': _int_attr(cutoff, 'position'),
            })
        elif localname == 'movement-cut-off':
            records.append({
                'kind': MOVEMENT,
                'id': None,
                'position': _int_attr(cutoff, 'position'),
                'start': _int_attr(cutoff, 'start-position'),
                'end': _int_attr(cutoff, 'end-position'),
            })
    return records


def read_changes(document):
    """Return ``(records_in_order, records_by_id)`` from a loaded document.

    If the document has no ``table:tracked-changes`` container then empty
    containers are returned.
    """
    containers = document.getElementsByType(TrackedChanges)
    by_id = {}
    order = []
    if not containers:
        return order, by_id
    container = containers[0]
    for element in _element_children(container):
        localname = element.qname[1]
        if localname not in (INSERTION, DELETION, MOVEMENT):
            continue
        record = ChangeRecord(_attr(element, 'id'), localname)
        dependencies_el = _find_child(element, 'dependencies')
        record.dependencies = tuple(
            _attr(dep, 'id')
            for dep in _find_children(dependencies_el, 'dependency'))
        record.deleted_cells = _parse_deleted_cells(element)
        if localname in (INSERTION, DELETION):
            record.axis = _attr(element, 'type')
            record.position = _int_attr(element, 'position')
            record.count = _int_attr(element, 'count', 1) or 1
            record.table_index = _int_attr(element, 'table', 0) or 0
        if localname == DELETION:
            record.cutoffs = _parse_cutoffs(element)
        if localname == MOVEMENT:
            source_el = _find_child(element, 'source-range-address')
            target_el = _find_child(element, 'target-range-address')
            record.source = _parse_address(source_el)
            record.target = _parse_address(target_el)
        by_id[record.change_id] = record
        order.append(record.change_id)
    return order, by_id


def list_changes(document):
    """Convenience helper returning plain dictionaries for tooling/CLIs."""
    order, by_id = read_changes(document)
    result = []
    for change_id in order:
        record = by_id[change_id]
        result.append({
            'id': record.change_id,
            'kind': record.kind,
            'axis': record.axis,
            'position': record.position,
            'count': record.count,
            'table': record.table_index,
            'dependencies': list(record.dependencies),
        })
    return result


def evaluate_selection(by_id, selected_ids):
    """Split a selection into usable and broken changes.

    A selected change is broken when one of its declared dependencies is
    not selected, missing from the document, or itself broken.  Returns
    ``(usable_ids, broken_info)`` where ``broken_info`` maps the broken
    change id to the id of the first missing dependency.
    """
    selected = set(selected_ids)
    usable = set()
    broken = {}
    for change_id in selected:
        record = by_id.get(change_id)
        if record is None:
            broken[change_id] = change_id
            continue
        missing = None
        for dep_id in record.dependencies:
            if dep_id not in selected or dep_id not in by_id:
                missing = dep_id
                break
        if missing is None:
            usable.add(change_id)
        else:
            broken[change_id] = missing
    # Propagate brokenness: a change is only usable when every dependency is
    # itself usable.  Re-run until the sets stop changing.
    changed = True
    while changed:
        changed = False
        for change_id in list(usable):
            record = by_id[change_id]
            for dep_id in record.dependencies:
                if dep_id in broken or dep_id not in usable:
                    usable.discard(change_id)
                    broken.setdefault(change_id, dep_id)
                    changed = True
                    break
    return usable, broken


def _movement_bounds(record, axis):
    """Return ``(source_start, source_end, target_start, target_end)``."""
    if axis == ROW:
        s_start = record.source.get('start-row', record.source.get('row'))
        s_end = record.source.get('end-row', s_start)
        t_start = record.target.get('start-row', record.target.get('row'))
        t_end = record.target.get('end-row', t_start)
    else:
        s_start = record.source.get('start-column',
                                    record.source.get('column'))
        s_end = record.source.get('end-column', s_start)
        t_start = record.target.get('start-column',
                                    record.target.get('column'))
        t_end = record.target.get('end-column', t_start)
    return s_start, s_end, t_start, t_end


def _apply_movement(tokens, source_start, source_end, target_start):
    """Cut the source range and reinsert it at the target position."""
    moved = tokens[source_start:source_end + 1]
    del tokens[source_start:source_end + 1]
    insertion_point = target_start
    if target_start > source_start:
        insertion_point = target_start - (source_end - source_start + 1)
    for offset, token in enumerate(moved):
        tokens.insert(insertion_point + offset, token)


class AxisReplay(object):
    """Token replay for one axis (rows or columns) of one table."""

    def __init__(self, axis, physical_size, ordered_ids, by_id, table_index,
                 usable, broken):
        self.axis = axis
        self.physical_size = physical_size
        self.ordered_ids = ordered_ids
        self.by_id = by_id
        self.table_index = table_index
        self.usable = usable
        self.broken = broken
        # physical index -> token label
        self.physical_tokens = []
        # token -> position in the reconstructed (partial view) axis
        self.view_positions = {}
        # tokens in reconstructed order
        self.view_tokens = []
        # physical index -> ids of broken changes covering that slot
        self.broken_physical = {}
        # (restored token, owning change id) for broken deletions
        self.broken_restored = []
        self._replay()

    def _axis_records(self):
        records = []
        for change_id in self.ordered_ids:
            record = self.by_id[change_id]
            if record.kind == MOVEMENT:
                if self._movement_on_axis(record):
                    records.append(record)
            elif record.axis == self.axis and \
                    record.table_index == self.table_index:
                records.append(record)
        return records

    def _movement_on_table(self, record):
        address = record.source or {}
        return address.get('table', 0) == self.table_index

    def _movement_on_axis(self, record):
        if not self._movement_on_table(record) or not record.source:
            return False
        if self.axis == ROW:
            return record.source.get('start-row',
                                     record.source.get('row')) is not None
        return record.source.get('start-column',
                                 record.source.get('column')) is not None

    def _cutoff_shift(self, cutoff):
        """Coordinate shift a cut-off change contributes when active."""
        if cutoff['kind'] == INSERTION:
            referenced = self.by_id.get(cutoff['id'])
            if referenced is not None:
                return referenced.count
            return 1
        if cutoff['start'] is not None and cutoff['end'] is not None:
            return max(0, cutoff['end'] - cutoff['start'])
        return 1

    def _cutoff_active(self, cutoff):
        """Whether the change behind a cut-off is replayed in this view."""
        if cutoff['kind'] == INSERTION:
            return cutoff['id'] in self.usable
        start = cutoff['start']
        end = cutoff['end']
        position = cutoff['position']
        for record in self._axis_records():
            if record.kind != MOVEMENT:
                continue
            s_start, s_end, t_start, t_end = \
                _movement_bounds(record, self.axis)
            if start is not None and (s_start, s_end) == (start, end):
                return record.change_id in self.usable
            if position is not None and s_start == position:
                return record.change_id in self.usable
        # Unknown movement cut-off: assume inactive so the recorded position
        # is shifted back rather than deleting the wrong baseline slot.
        return False

    def _effective_deletion_position(self, record):
        """Translate a deletion position into baseline/view coordinates.

        The recorded position includes shifts from earlier insertions and
        movements that the deletion explicitly cuts off.  Shifts of changes
        that are not active in this selection must be removed again.
        """
        position = record.position
        for cutoff in record.cutoffs:
            if not self._cutoff_active(cutoff):
                position -= self._cutoff_shift(cutoff)
        return position

    def _replay(self):
        records = self._axis_records()
        # Undo pass: opaque physical ids first, synthetic tokens get negative
        # ids so they can never collide with a physical slot.
        tokens = list(range(self.physical_size))
        synthetic_seq = [-1]
        inserted_physical = {}  # physical id -> (change id, ordinal)
        restored_synthetic = {}  # synthetic id -> change id that removed it

        def new_synthetic(change_id):
            synthetic_seq[0] -= 1
            token_id = synthetic_seq[0]
            restored_synthetic[token_id] = change_id
            return token_id

        for record in reversed(records):
            if record.kind == INSERTION:
                start = record.position
                end = start + record.count
                removed = tokens[start:end]
                del tokens[start:end]
                for ordinal, physical_id in enumerate(removed):
                    inserted_physical[physical_id] = (record.change_id,
                                                      ordinal)
            elif record.kind == DELETION:
                start = record.position
                for ordinal in range(record.count):
                    tokens.insert(start + ordinal,
                                  new_synthetic(record.change_id))
            elif record.kind == MOVEMENT:
                s_start, s_end, t_start, t_end = \
                    _movement_bounds(record, self.axis)
                # undo: remove the moved block from its target and put it
                # back at the source position.
                moved = tokens[t_start:t_end + 1]
                del tokens[t_start:t_end + 1]
                for offset, token_id in enumerate(moved):
                    tokens.insert(s_start + offset, token_id)

        # Label baseline tokens with original indices in baseline order.
        labels = {}
        original_index = 0
        for token_id in tokens:
            if token_id in inserted_physical:
                change_id, ordinal = inserted_physical[token_id]
                labels[token_id] = _InsertedToken(self.axis, change_id,
                                                  ordinal)
            else:
                labels[token_id] = _OriginalToken(self.axis, original_index)
                original_index += 1

        self.physical_tokens = [labels.get(i) for i in
                                range(self.physical_size)]
        baseline = [labels[token_id] for token_id in tokens]

        # Apply pass: replay selected, dependency-complete changes.
        view = list(baseline)
        for record in records:
            if record.change_id not in self.usable:
                continue
            if record.kind == INSERTION:
                start = record.position
                for ordinal in range(record.count):
                    view.insert(start + ordinal,
                                _InsertedToken(self.axis, record.change_id,
                                               ordinal))
            elif record.kind == DELETION:
                start = self._effective_deletion_position(record)
                del view[start:start + record.count]
            elif record.kind == MOVEMENT:
                s_start, s_end, t_start, t_end = \
                    _movement_bounds(record, self.axis)
                _apply_movement(view, s_start, s_end, t_start)
        self.view_tokens = view
        self.view_positions = {token: pos
                               for pos, token in enumerate(view)}
        self._mark_broken(records, labels, restored_synthetic)

    def _mark_broken(self, records, labels, restored_synthetic):
        for record in records:
            if record.change_id not in self.broken:
                continue
            if record.kind == INSERTION:
                for physical_id in range(self.physical_size):
                    token = self.physical_tokens[physical_id]
                    if isinstance(token, _InsertedToken) and \
                            token.change_id == record.change_id:
                        self.broken_physical.setdefault(
                            physical_id, []).append(record.change_id)
            elif record.kind == MOVEMENT:
                s_start, s_end, t_start, t_end = \
                    _movement_bounds(record, self.axis)
                for physical_id in range(t_start, t_end + 1):
                    self.broken_physical.setdefault(
                        physical_id, []).append(record.change_id)
            elif record.kind == DELETION:
                for token_id, owner in restored_synthetic.items():
                    if owner == record.change_id:
                        self.broken_restored.append(
                            (labels[token_id], record.change_id))

    def logical_key(self, token):
        """Stable logical coordinate for a baseline/view token."""
        if isinstance(token, _InsertedToken):
            return token.as_coordinate()
        return token.index

    def map_physical(self, physical_index):
        """Return ``(logical_key, view_position)`` or ``(None, None)``.

        ``None`` means the slot does not exist in the selected version
        (an inactive insertion or a row/column removed by an active
        deletion).
        """
        token = self.physical_tokens[physical_index]
        position = self.view_positions.get(token)
        if position is None:
            return None, None
        return self.logical_key(token), position


def _expand_repeated(element, repeat_attr, max_repeat):
    repeat = _int_attr(element, repeat_attr, 1) or 1
    if repeat <= 0:
        return 0
    return min(repeat, max_repeat)


def _collect_rows(table_element, max_repeat):
    rows = []

    def walk(element):
        for child in _element_children(element):
            localname = child.qname[1]
            if localname == 'table-row':
                count = _expand_repeated(child, 'number-rows-repeated',
                                         max_repeat)
                rows.extend([child] * count)
            elif localname in ('table-row-group', 'table-rows',
                               'table-header-rows'):
                walk(child)

    walk(table_element)
    return rows


def _column_width(table_element, rows, max_repeat):
    width = 0
    for column in _find_children(table_element, 'table-column'):
        width += _expand_repeated(column, 'number-columns-repeated',
                                  max_repeat)
    for row in rows:
        row_width = 0
        for cell in _element_children(row):
            if cell.qname[1] not in ('table-cell', 'covered-table-cell'):
                continue
            row_width += _expand_repeated(cell, 'number-columns-repeated',
                                          max_repeat)
        width = max(width, row_width)
    return width


class SelectedView(object):
    """Reconstructed view of one table for a selected set of changes.

    :param document: document returned by :func:`odf.opendocument.load`
    :param selected_ids: iterable of change ids to keep applied.  ``None``
        means every change recorded in the document.
    :param table: table name or zero based index; defaults to the first table.
    :param max_repeat: guard cap for ``number-*-repeated`` expansion
    """

    def __init__(self, document, selected_ids=None, table=0,
                 max_repeat=DEFAULT_MAX_REPEAT):
        self.document = document
        self.max_repeat = max_repeat
        order, by_id = read_changes(document)
        self.change_order = order
        self.changes_by_id = by_id
        if selected_ids is None:
            selected_ids = order
        self.requested = tuple(selected_ids)
        self.usable, self.broken = evaluate_selection(by_id, self.requested)
        self.unresolvable = []
        self.cells = {}
        self.row_keys = []
        self.column_keys = []
        self._build(table)

    def _select_table(self, table):
        tables = self.document.getElementsByType(Table)
        if not tables:
            raise ValueError("document contains no tables")
        if isinstance(table, int):
            return tables[table], table
        for position, candidate in enumerate(tables):
            if _attr(candidate, 'name') == table:
                return candidate, position
        raise ValueError("no table named %r" % table)

    def _record_unresolvable(self, physical_row, physical_column, reason,
                             change_ids=None):
        entry = {
            'table': self.table_name,
            'physical_row': physical_row,
            'physical_column': physical_column,
            'reason': reason,
            'change_ids': tuple(change_ids or ()),
        }
        if entry not in self.unresolvable:
            self.unresolvable.append(entry)

    def _build(self, table):
        table_element, table_index = self._select_table(table)
        self.table_index = table_index
        self.table_name = _attr(table_element, 'name')
        rows = _collect_rows(table_element, self.max_repeat)
        width = _column_width(table_element, rows, self.max_repeat)

        row_axis = AxisReplay(ROW, len(rows), self.change_order,
                              self.changes_by_id, table_index,
                              self.usable, self.broken)
        column_axis = AxisReplay(COLUMN, width, self.change_order,
                                 self.changes_by_id, table_index,
                                 self.usable, self.broken)
        self._row_axis = row_axis
        self._column_axis = column_axis

        self.row_keys = [row_axis.logical_key(token)
                         for token in row_axis.view_tokens]
        self.column_keys = [column_axis.logical_key(token)
                            for token in column_axis.view_tokens]

        self._scan_cells(rows, row_axis, column_axis)
        self._restore_deleted_cells(row_axis, column_axis)

    def _scan_cells(self, rows, row_axis, column_axis):
        for physical_row, row in enumerate(rows):
            physical_column = 0
            for cell in _element_children(row):
                if cell.qname[1] not in ('table-cell', 'covered-table-cell'):
                    continue
                repeat = _expand_repeated(cell, 'number-columns-repeated',
                                          self.max_repeat)
                is_covered = cell.qname[1] == 'covered-table-cell'
                for offset in range(repeat):
                    self._place_cell(cell, is_covered, physical_row,
                                     physical_column, row_axis, column_axis)
                    physical_column += 1

    def _place_cell(self, cell, is_covered, physical_row, physical_column,
                    row_axis, column_axis):
        broken_changes = list(row_axis.broken_physical.get(physical_row, ()))
        broken_changes.extend(
            column_axis.broken_physical.get(physical_column, ()))
        if broken_changes:
            self._record_unresolvable(
                physical_row, physical_column,
                u"change depends on a revision that is not in the selection",
                broken_changes)
            return

        row_key, row_view = row_axis.map_physical(physical_row)
        column_key, column_view = column_axis.map_physical(
            physical_column)
        if row_key is None or column_key is None:
            # Slot does not exist in the selected version.
            return
        if is_covered:
            return
        self.cells[(row_view, column_view)] = {
            'row': row_key,
            'column': column_key,
            'value': extractText(cell),
            'physical_row': physical_row,
            'physical_column': physical_column,
        }

    def _restore_deleted_cells(self, row_axis, column_axis):
        for change_id in self.change_order:
            record = self.changes_by_id[change_id]
            if record.kind != DELETION or change_id in self.usable or \
                    change_id in self.broken:
                # Active or broken deletion: its old values stay hidden.
                continue
            for saved in record.deleted_cells:
                if saved['address'] is None:
                    self._record_unresolvable(
                        -1, -1,
                        u"deleted value of change %s has no cell address"
                        % change_id,
                        (change_id,))
                    continue
                table_index, original_row, original_column = saved['address']
                if table_index != self.table_index:
                    continue
                row_view = row_axis.view_positions.get(
                    _OriginalToken(ROW, original_row))
                column_view = column_axis.view_positions.get(
                    _OriginalToken(COLUMN, original_column))
                if row_view is None or column_view is None:
                    self._record_unresolvable(
                        -1, -1,
                        u"deleted value of change %s cannot be located"
                        % change_id,
                        (change_id,))
                    continue
                self.cells[(row_view, column_view)] = {
                    'row': original_row,
                    'column': original_column,
                    'value': saved['value'],
                    'physical_row': None,
                    'physical_column': None,
                }
        for token, change_id in row_axis.broken_restored:
            self._record_unresolvable(
                -1, -1,
                u"restored row depends on a revision that is not selected",
                (change_id,))
        for token, change_id in column_axis.broken_restored:
            self._record_unresolvable(
                -1, -1,
                u"restored column depends on a revision that is not selected",
                (change_id,))

    def get_value(self, physical_row, physical_column):
        """Map a physical cell to ``(logical_row, logical_column, value)``.

        Raises :class:`UnresolvableCell` when the coordinate cannot be
        rebuilt for the selected version.
        """
        for entry in self.unresolvable:
            if entry['physical_row'] == physical_row and \
                    entry['physical_column'] == physical_column:
                raise UnresolvableCell(
                    physical_row, physical_column, entry['reason'],
                    entry['change_ids'])
        row_key, row_view = self._row_axis.map_physical(physical_row)
        column_key, column_view = self._column_axis.map_physical(
            physical_column)
        if row_key is None or column_key is None:
            raise UnresolvableCell(
                physical_row, physical_column,
                u"slot does not exist in the selected version")
        entry = self.cells.get((row_view, column_view), {})
        return row_key, column_key, entry.get('value')

    def coordinate_label(self, key):
        """Stable text rendering for a row/column key (used by exports)."""
        if isinstance(key, InsertedCoordinate):
            return key.label
        return str(key)

    def to_matrix(self):
        """Return the view as a list of rows of values.

        Values follow the reconstructed logical ordering.  Slots that have no
        value are ``None``; unresolvable cells do not appear here, they remain
        available through :attr:`unresolvable`.
        """
        matrix = []
        for row_view, row_key in enumerate(self.row_keys):
            row_values = []
            for column_view, column_key in enumerate(self.column_keys):
                entry = self.cells.get((row_view, column_view))
                row_values.append(entry['value'] if entry else None)
            matrix.append(row_values)
        return matrix

    def to_csv(self, stream):
        """Write :meth:`to_matrix` as CSV using the stdlib csv dialect."""
        import csv
        writer = csv.writer(stream)
        for row in self.to_matrix():
            writer.writerow(
                [u'' if value is None else value for value in row])
