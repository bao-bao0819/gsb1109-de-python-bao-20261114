#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for the user-field audit machinery."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO

from odf import draw, style, text
from odf.opendocument import OpenDocumentText
from odf.userfieldaudit import (
    MAIN_SCOPE, audit_package, scope_label)

SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    u"odffieldaudit", u"odffieldaudit")


def get_file_path(file_name):
    return os.path.join(os.path.dirname(__file__), "examples", file_name)


def build_document():
    """Build a document exercising every audit rule in memory."""
    doc = OpenDocumentText()

    page_layout = style.PageLayout(name=u"pagelayout")
    doc.automaticstyles.addElement(page_layout)
    master_page = style.MasterPage(
        name=u"Standard", pagelayoutname=page_layout)
    doc.masterstyles.addElement(master_page)

    header = style.Header()
    header_paragraph = text.P()
    header_field = text.UserFieldGet(name=u"批号")
    header_field.addText(u"OLD-HEADER")
    header_paragraph.addElement(header_field)
    header.addElement(header_paragraph)
    master_page.addElement(header)

    declarations = text.UserFieldDecls()
    declarations.addElement(text.UserFieldDecl(
        name=u"批号", valuetype=u"string", stringvalue=u"LOT-NEW"))
    declarations.addElement(text.UserFieldDecl(
        name=u"数量", valuetype=u"float", value=u"42.5"))
    doc.text.addElement(declarations)

    # Plain text that merely spells the same value: not a reference.
    plain = text.P()
    plain.addText(u"批号 LOT-NEW appears as plain text only")
    doc.text.addElement(plain)

    stale = text.P()
    stale_field = text.UserFieldGet(name=u"批号")
    stale_field.addText(u"LOT-OLD")
    stale.addElement(stale_field)
    doc.text.addElement(stale)

    matching = text.P()
    matching_field = text.UserFieldGet(name=u"批号")
    matching_field.addText(u"LOT-NEW")
    matching.addElement(matching_field)
    doc.text.addElement(matching)

    undeclared = text.P()
    ghost_field = text.UserFieldGet(name=u"幽灵字段")
    ghost_field.addText(u"??")
    undeclared.addElement(ghost_field)
    doc.text.addElement(undeclared)

    number_field_holder = text.P()
    number_field = text.UserFieldGet(name=u"数量")
    number_field.addText(u"42.50")
    number_field_holder.addElement(number_field)
    doc.text.addElement(number_field_holder)

    # Embedded object with its own declaration of the same field name.
    subdoc = OpenDocumentText()
    sub_declarations = text.UserFieldDecls()
    sub_declarations.addElement(text.UserFieldDecl(
        name=u"批号", valuetype=u"string", stringvalue=u"SUB-NEW"))
    subdoc.text.addElement(sub_declarations)
    sub_paragraph = text.P()
    sub_field = text.UserFieldGet(name=u"批号")
    sub_field.addText(u"SUB-OLD")
    sub_paragraph.addElement(sub_field)
    subdoc.text.addElement(sub_paragraph)

    # Nested object without a declaration: undeclared in its own scope.
    nested = OpenDocumentText()
    nested_paragraph = text.P()
    nested_field = text.UserFieldGet(name=u"批号")
    nested_field.addText(u"NESTED")
    nested_paragraph.addElement(nested_field)
    nested.text.addElement(nested_paragraph)
    nested_location = subdoc.addObject(nested)
    nested_frame = draw.Frame(
        width=u"5pt", height=u"5pt", anchortype=u"paragraph")
    nested_frame.addElement(draw.Object(href=nested_location))
    subdoc.text.addElement(nested_frame)

    location = doc.addObject(subdoc)
    frame = draw.Frame(width=u"10pt", height=u"10pt",
                       anchortype=u"paragraph")
    frame.addElement(draw.Object(href=location))
    doc.text.addElement(frame)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


class TestUserFieldAudit(unittest.TestCase):

    def setUp(self):
        self.buffer = build_document()
        self.result = audit_package(self.buffer)

    def _references(self, name, scope):
        return [r for r in self.result.references
                if r.name == name and r.scope == scope]

    def test_declarations_carry_type_and_value(self):
        batch = self.result.declarations_for(u"批号")
        scopes = dict((d.scope, d) for d in batch)
        self.assertEqual(2, len(batch))
        self.assertEqual(
            (u"string", u"LOT-NEW"),
            (scopes[MAIN_SCOPE].value_type, scopes[MAIN_SCOPE].value))
        self.assertEqual(
            (u"string", u"SUB-NEW"),
            (scopes[u"Object 1"].value_type,
             scopes[u"Object 1"].value))

        number = self.result.declarations_for(u"数量")
        self.assertEqual(1, len(number))
        self.assertEqual(u"float", number[0].value_type)
        self.assertEqual(u"42.5", number[0].value)

    def test_plain_text_is_not_a_reference(self):
        # The plain paragraph exists, but spelling the value inside it
        # creates no extra reference: only genuine field elements count.
        body_batch = [
            r for r in self._references(u"批号", MAIN_SCOPE)
            if r.region == u"body"]
        self.assertEqual(2, len(body_batch))
        archive = zipfile.ZipFile(BytesIO(self.buffer.getvalue()))
        self.assertIn(
            u"plain text only",
            archive.read(u"content.xml").decode("utf-8"))

    def test_reference_display_text_is_captured(self):
        body_batch = dict(
            (r.display_text, r)
            for r in self._references(u"批号", MAIN_SCOPE)
            if r.region == u"body")
        self.assertEqual(u"LOT-OLD", body_batch[u"LOT-OLD"].display_text)
        self.assertEqual(u"LOT-NEW", body_batch[u"LOT-NEW"].display_text)

    def test_stale_string_reference_is_detected(self):
        stale = self.result.stale_references
        stale_locations = [(r.name, r.display_text) for r in stale]
        self.assertIn((u"批号", u"LOT-OLD"), stale_locations)
        self.assertIn((u"批号", u"OLD-HEADER"), stale_locations)
        self.assertIn((u"批号", u"SUB-OLD"), stale_locations)
        for reference in stale:
            self.assertEqual(u"stale", reference.status)

    def test_matching_string_reference_is_fresh(self):
        matching = [
            r for r in self._references(u"批号", MAIN_SCOPE)
            if r.region == u"body" and r.display_text == u"LOT-NEW"]
        self.assertEqual(1, len(matching))
        self.assertEqual(u"matches", matching[0].status)

    def test_non_string_difference_is_not_called_stale(self):
        number = self._references(u"数量", MAIN_SCOPE)
        self.assertEqual(1, len(number))
        self.assertEqual(u"display-differs", number[0].status)
        self.assertEqual(
            [], [r for r in self.result.stale_references
                 if r.name == u"数量"])

    def test_undeclared_references_are_listed(self):
        undeclared = self.result.undeclared_references
        names_scopes = sorted((r.name, r.scope) for r in undeclared)
        self.assertEqual(
            [(u"幽灵字段", MAIN_SCOPE),
             (u"批号", u"Object 1/Object 1")],
            names_scopes)
        for reference in undeclared:
            self.assertFalse(reference.declared)
            self.assertEqual(u"undeclared", reference.status)

    def test_embedded_object_uses_its_own_declaration(self):
        sub_reference = self._references(u"批号", u"Object 1")
        self.assertEqual(1, len(sub_reference))
        self.assertEqual(u"SUB-NEW",
                         sub_reference[0].declaration.value)
        self.assertEqual(
            u"Object 1/content.xml",
            sub_reference[0].declaration.part)

    def test_header_reference_is_located_in_styles_part(self):
        header_refs = [
            r for r in self.result.references
            if r.part == u"styles.xml"]
        self.assertEqual(1, len(header_refs))
        reference = header_refs[0]
        self.assertIn(u"header", reference.region)
        self.assertIn(u"Standard", reference.region)
        self.assertEqual(u"OLD-HEADER", reference.display_text)

    def test_locations_point_back_to_the_actual_element(self):
        archive = zipfile.ZipFile(BytesIO(self.buffer.getvalue()))
        for item in self.result.references + self.result.declarations:
            line_text = archive.read(item.part).decode(
                "utf-8").split(u"\n")[item.line - 1]
            self.assertEqual(
                u"<", line_text[item.column - 1],
                u"location %s does not point at an element" %
                item.location)

    def test_filter_by_field_name(self):
        self.buffer.seek(0)
        filtered = audit_package(self.buffer, [u"数量"])
        self.assertEqual([u"数量"], filtered.field_names())
        self.assertEqual(1, len(filtered.declarations))
        self.assertEqual(1, len(filtered.references))

    def test_bytesio_input(self):
        self.buffer.seek(0)
        result = audit_package(BytesIO(self.buffer.getvalue()))
        self.assertTrue(result.declarations)
        self.assertTrue(result.references)

    def test_non_zip_input_raises(self):
        self.assertRaises(
            TypeError, audit_package, BytesIO(b"not a zip at all"))

    def test_shipped_example_documents(self):
        example = audit_package(get_file_path(u"userfields.odt"))
        self.assertEqual(
            [u"username", u"firstname", u"lastname", u"address"],
            [d.name for d in example.declarations])
        self.assertEqual(4, len(example.references))
        self.assertEqual([], example.undeclared_references)
        self.assertEqual([], example.stale_references)

        empty = audit_package(get_file_path(u"no_userfields.odt"))
        self.assertEqual([], empty.declarations)
        self.assertEqual([], empty.references)

    def test_scope_label(self):
        self.assertEqual(u"main document", scope_label(MAIN_SCOPE))
        self.assertEqual(
            u'embedded object "Object 1"', scope_label(u"Object 1"))


class TestCommandLineInterface(unittest.TestCase):

    def setUp(self):
        self.buffer = build_document()
        handle, self.path = tempfile.mkstemp(u".odt")
        os.close(handle)
        with open(self.path, "wb") as target:
            target.write(self.buffer.getvalue())

    def tearDown(self):
        os.unlink(self.path)

    def _run(self, *arguments):
        return subprocess.Popen(
            [sys.executable, SCRIPT] + list(arguments),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_text_report_flags_issues_with_exit_code_one(self):
        process = self._run(self.path)
        stdout, stderr = process.communicate()
        self.assertEqual(1, process.returncode)
        output = stdout.decode("utf-8")
        self.assertIn(u"undeclared reference", output)
        self.assertIn(u"STATUS: stale", output)
        self.assertEqual(u"", stderr.decode("utf-8"))

    def test_json_report_is_an_array_of_documents(self):
        process = self._run(u"--json", self.path)
        stdout, stderr = process.communicate()
        self.assertEqual(1, process.returncode)
        documents = json.loads(stdout.decode("utf-8"))
        self.assertEqual(1, len(documents))
        statuses = {
            reference[u"status"]
            for reference in documents[0][u"references"]}
        self.assertIn(u"undeclared", statuses)
        self.assertIn(u"stale", statuses)

    def test_field_filter_limits_output(self):
        process = self._run(u"-f", u"幽灵字段", u"--json", self.path)
        stdout, _ = process.communicate()
        documents = json.loads(stdout.decode("utf-8"))
        names = {
            reference[u"name"]
            for reference in documents[0][u"references"]}
        self.assertEqual({u"幽灵字段"}, names)

    def test_clean_document_exits_zero(self):
        process = self._run(get_file_path(u"userfields.odt"))
        stdout, _ = process.communicate()
        self.assertEqual(0, process.returncode)
        self.assertIn(u"STATUS: matches", stdout.decode("utf-8"))

    def test_missing_argument_prints_usage(self):
        process = self._run()
        stdout, stderr = process.communicate()
        self.assertEqual(2, process.returncode)
        self.assertIn(u"Usage:", stderr.decode("utf-8"))
        self.assertEqual(b"", stdout)

    def test_bad_file_is_reported_without_aborting_batch(self):
        handle, bad_path = tempfile.mkstemp(u".bin")
        os.write(handle, b"not a zip")
        os.close(handle)
        try:
            process = self._run(bad_path, self.path)
            stdout, _ = process.communicate()
        finally:
            os.unlink(bad_path)
        self.assertEqual(1, process.returncode)
        output = stdout.decode("utf-8")
        self.assertIn(u"ERROR:", output)
        self.assertIn(bad_path, output)
        self.assertIn(self.path, output)


if __name__ == "__main__":
    unittest.main()
