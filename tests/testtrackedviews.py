#!/usr/bin/env python
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

import unittest
from io import BytesIO, StringIO

from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.office import ChangeInfo
from odf.table import (
    CellAddress,
    CellContentDeletion,
    ChangeTrackTableCell,
    CutOffs,
    Deletions,
    Dependencies,
    Dependency,
    Deletion,
    Insertion,
    InsertionCutOff,
    Movement,
    SourceRangeAddress,
    Table,
    TableCell,
    TableRow,
    TargetRangeAddress,
    TrackedChanges,
)
from odf.text import P
from odf import trackedviews


def text_cell(value):
    cell = TableCell(valuetype="string")
    cell.addElement(P(text=value))
    return cell


def row_with(value):
    row = TableRow()
    row.addElement(text_cell(value))
    return row


def change_info():
    return ChangeInfo()


def saved_cell(table, row, column, old_value):
    deletion = CellContentDeletion()
    deletion.addElement(CellAddress(table=table, row=row, column=column))
    old_cell = ChangeTrackTableCell(valuetype="string")
    old_cell.addElement(P(text=old_value))
    deletion.addElement(old_cell)
    return deletion


def build_document(rows, changes, table_name="Sheet1"):
    document = OpenDocumentSpreadsheet()
    table = Table(name=table_name)
    for value in rows:
        table.addElement(row_with(value))
    document.spreadsheet.addElement(table)

    container = TrackedChanges()
    for change in changes:
        container.addElement(change)
    document.spreadsheet.addElement(container)

    buffer = BytesIO()
    document.write(buffer)
    buffer.seek(0)
    return load(buffer)


def combined_document():
    """Original rows A,B,C,D.

    Current physical order is D,A,X,C where:
    * I1 inserts row X at position 1;
    * D1 deletes original row B (recorded position 2);
    * M1 moves original row D (source 3) to position 0 and depends on I1.
    """
    insertion = Insertion(id="I1", type="row", position=1, count=1)
    insertion.addElement(change_info())

    deletion = Deletion(id="D1", type="row", position=2)
    deletion.addElement(change_info())
    deletions = Deletions()
    deletions.addElement(saved_cell(0, 1, 0, "B"))
    deletion.addElement(deletions)

    movement = Movement(id="M1")
    movement.addElement(change_info())
    dependencies = Dependencies()
    dependencies.addElement(Dependency(id="I1"))
    movement.addElement(dependencies)
    movement.addElement(
        SourceRangeAddress(startrow=3, endrow=3, table=0))
    movement.addElement(
        TargetRangeAddress(startrow=0, endrow=0, table=0))

    return build_document(
        ["D", "A", "X", "C"], [insertion, deletion, movement])


class TestChangeParsing(unittest.TestCase):

    def test_list_changes_reports_kind_and_dependencies(self):
        document = combined_document()
        changes = trackedviews.list_changes(document)
        self.assertEqual(
            [(c["id"], c["kind"]) for c in changes],
            [("I1", "insertion"), ("D1", "deletion"),
             ("M1", "movement")])
        movement = changes[2]
        self.assertEqual(movement["dependencies"], ["I1"])
        self.assertEqual(movement["position"], None)
        insertion = changes[0]
        self.assertEqual(insertion["axis"], "row")
        self.assertEqual(insertion["position"], 1)


class TestReplaySelection(unittest.TestCase):

    def setUp(self):
        self.document = combined_document()

    def values(self, selected):
        view = trackedviews.SelectedView(self.document,
                                         selected_ids=selected)
        return [row[0] for row in view.to_matrix()], view

    def test_empty_selection_reconstructs_original_sheet(self):
        values, view = self.values([])
        self.assertEqual(values, ["A", "B", "C", "D"])
        self.assertEqual(view.unresolvable, [])

    def test_selecting_every_change_reproduces_current_sheet(self):
        values, view = self.values(["I1", "D1", "M1"])
        self.assertEqual(values, ["D", "A", None, "C"])
        self.assertEqual(view.broken, {})
        self.assertEqual(view.unresolvable, [])

    def test_insertion_only_keeps_original_coordinates(self):
        view = trackedviews.SelectedView(self.document,
                                         selected_ids=["I1"])
        values = [row[0] for row in view.to_matrix()]
        self.assertEqual(values, ["A", None, "B", "C", "D"])
        inserted = [key for key in view.row_keys
                    if isinstance(key, trackedviews.InsertedCoordinate)]
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0].change_id, "I1")
        self.assertEqual(str(inserted[0]), "inserted-row:I1#0")

    def test_active_deletion_keeps_old_value_hidden(self):
        values, view = self.values(["D1"])
        self.assertNotIn("B", values)
        self.assertEqual(values, ["A", None, "D"])
        # No restored cell carries the saved old value while the deletion
        # remains applied.
        restored = [entry for entry in view.cells.values()
                    if entry["value"] == "B"]
        self.assertEqual(restored, [])

    def test_movement_without_prerequisite_is_unresolvable(self):
        view = trackedviews.SelectedView(self.document,
                                         selected_ids=["M1"])
        self.assertEqual(view.broken, {"M1": "I1"})
        broken = view.unresolvable
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]["physical_row"], 0)
        self.assertEqual(broken[0]["physical_column"], 0)
        self.assertIn("M1", broken[0]["change_ids"])

        # Unrelated areas must still be exported: A and C survive even
        # though the moved row cannot be rebuilt.
        values = [value for row in view.to_matrix() for value in row]
        self.assertIn("A", values)
        self.assertIn("C", values)
        self.assertNotIn("D", values)

    def test_get_value_maps_back_to_logical_coordinate(self):
        view = trackedviews.SelectedView(self.document, selected_ids=[])
        row, column, value = view.get_value(1, 0)
        self.assertEqual((row, column, value), (0, 0, "A"))

        broken_view = trackedviews.SelectedView(
            self.document, selected_ids=["M1"])
        self.assertRaises(trackedviews.UnresolvableCell,
                          broken_view.get_value, 0, 0)


class TestColumnDeletion(unittest.TestCase):

    def _document(self):
        deletion = Deletion(id="CD1", type="column", position=1)
        deletion.addElement(change_info())
        deletions = Deletions()
        deletions.addElement(saved_cell(0, 0, 1, "Q"))
        deletion.addElement(deletions)

        document = OpenDocumentSpreadsheet()
        table = Table(name="Cols")
        row = TableRow()
        for value in ("P", "R"):
            row.addElement(text_cell(value))
        table.addElement(row)
        document.spreadsheet.addElement(table)
        container = TrackedChanges()
        container.addElement(deletion)
        document.spreadsheet.addElement(container)
        buffer = BytesIO()
        document.write(buffer)
        buffer.seek(0)
        return load(buffer)

    def test_deleted_column_old_value_only_returns_when_reverted(self):
        document = self._document()
        reverted = trackedviews.SelectedView(document, selected_ids=[])
        self.assertEqual(reverted.to_matrix(), [["P", "Q", "R"]])

        applied = trackedviews.SelectedView(document,
                                            selected_ids=["CD1"])
        self.assertEqual(applied.to_matrix(), [["P", "R"]])


class TestInsertionCutOff(unittest.TestCase):

    def _document(self):
        insertion = Insertion(id="I1", type="row", position=1, count=1)
        insertion.addElement(change_info())

        deletion = Deletion(id="D1", type="row", position=2)
        deletion.addElement(change_info())
        cutoffs = CutOffs()
        cutoffs.addElement(InsertionCutOff(id="I1", position=1))
        deletion.addElement(cutoffs)
        deletions = Deletions()
        deletions.addElement(saved_cell(0, 1, 0, "B"))
        deletion.addElement(deletions)

        return build_document(["A", "X", "C"], [insertion, deletion])

    def test_deletion_position_is_corrected_when_cutoff_inactive(self):
        document = self._document()
        # Only the deletion is applied: the cut-off insertion is absent, so
        # the recorded position must be shifted back to delete B, not C.
        view = trackedviews.SelectedView(document, selected_ids=["D1"])
        self.assertEqual([row[0] for row in view.to_matrix()],
                         ["A", "C"])

        reverted = trackedviews.SelectedView(document, selected_ids=[])
        self.assertEqual([row[0] for row in reverted.to_matrix()],
                         ["A", "B", "C"])


class TestPlainMovement(unittest.TestCase):

    def test_movement_is_undone_and_reapplied(self):
        movement = Movement(id="M1")
        movement.addElement(change_info())
        movement.addElement(
            SourceRangeAddress(startrow=1, endrow=1, table=0))
        movement.addElement(
            TargetRangeAddress(startrow=0, endrow=0, table=0))
        document = build_document(["B", "A"], [movement])

        reverted = trackedviews.SelectedView(document, selected_ids=[])
        self.assertEqual([row[0] for row in reverted.to_matrix()],
                         ["A", "B"])
        applied = trackedviews.SelectedView(document,
                                            selected_ids=["M1"])
        self.assertEqual([row[0] for row in applied.to_matrix()],
                         ["B", "A"])


class TestCsvExport(unittest.TestCase):

    def test_csv_contains_reconstructed_rows(self):
        document = combined_document()
        view = trackedviews.SelectedView(document, selected_ids=[])
        stream = StringIO()
        view.to_csv(stream)
        lines = stream.getvalue().splitlines()
        self.assertEqual(lines, ["A", "B", "C", "D"])


class TestMissingSelection(unittest.TestCase):

    def test_unknown_change_id_is_broken(self):
        document = combined_document()
        view = trackedviews.SelectedView(document,
                                         selected_ids=["does-not-exist"])
        self.assertEqual(view.usable, set())
        self.assertIn("does-not-exist", view.broken)

    def test_broken_dependency_propagates_to_dependent_movement(self):
        insertion = Insertion(id="I1", type="row", position=1, count=1)
        insertion.addElement(change_info())

        first_movement = Movement(id="M1")
        first_movement.addElement(change_info())
        first_deps = Dependencies()
        first_deps.addElement(Dependency(id="I1"))
        first_movement.addElement(first_deps)
        first_movement.addElement(
            SourceRangeAddress(startrow=1, endrow=1, table=0))
        first_movement.addElement(
            TargetRangeAddress(startrow=0, endrow=0, table=0))

        second_movement = Movement(id="M2")
        second_movement.addElement(change_info())
        second_deps = Dependencies()
        second_deps.addElement(Dependency(id="M1"))
        second_movement.addElement(second_deps)
        second_movement.addElement(
            SourceRangeAddress(startrow=0, endrow=0, table=0))
        second_movement.addElement(
            TargetRangeAddress(startrow=2, endrow=2, table=0))

        document = build_document(
            ["A", "B", "C"],
            [insertion, first_movement, second_movement])

        # I1 is missing from the selection, so M1 is broken and M2 must not
        # be treated as usable either.
        view = trackedviews.SelectedView(document,
                                         selected_ids=["M1", "M2"])
        self.assertEqual(view.usable, set())
        self.assertEqual(set(view.broken), {"M1", "M2"})


if __name__ == "__main__":
    unittest.main()
