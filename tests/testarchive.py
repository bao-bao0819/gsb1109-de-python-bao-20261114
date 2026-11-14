#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This is free software.  You may redistribute it under the terms
# of the Apache license and the GNU General Public License Version
# 2 or at your option any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#

import json
import os
import shutil
import tempfile
import unittest
from io import BytesIO
from zipfile import ZipFile

from odf import draw
from odf.archive import (
    STOP_CYCLE, STOP_EXTERNAL, STOP_MISSING, build_archive, save_archive)
from odf.element import Node
from odf.opendocument import (
    IS_IMAGE, OpenDocumentPresentation, OpenDocumentText, load)
from odf.text import P


def _image_frame(href):
    frame = draw.Frame(width=u"1pt", height=u"1pt")
    frame.addElement(draw.Image(href=href))
    return frame


def _object_frame(href):
    frame = draw.Frame(width=u"1pt", height=u"1pt")
    frame.addElement(draw.Object(href=href))
    return frame


def _paragraph(document):
    paragraph = P()
    document.text.addElement(paragraph)
    return paragraph


def _first_element(document):
    for child in document.body.childNodes:
        if child.nodeType == Node.ELEMENT_NODE:
            return child
    return document.body


def _roundtrip(document):
    buffer = BytesIO()
    document.write(buffer)
    buffer.seek(0)
    archive = ZipFile(buffer)
    entries = dict(
        (name, (lambda n: lambda: archive.read(n))(name))
        for name in archive.namelist())
    buffer.seek(0)
    return load(buffer), archive, entries


class TestArchivePictures(unittest.TestCase):

    def test_only_referenced_pictures_are_archived(self):
        document = OpenDocumentText()
        referenced = document.addPictureFromString(b"used-bytes", u"image/png")
        document.addPictureFromString(b"unused-bytes", u"image/png")
        _paragraph(document).addElement(_image_frame(referenced))

        loaded, unused_zip, entries = _roundtrip(document)
        report = build_archive(loaded, entries)

        self.assertEqual(sorted(r.path for r in report), [referenced])
        resource = report.get(referenced)
        self.assertEqual(resource.content, b"used-bytes")
        self.assertEqual(resource.kind, u"image")
        self.assertEqual(len(resource.references), 1)
        self.assertEqual(resource.references[0].target, referenced)
        self.assertIsNone(resource.references[0].stop)

    def test_same_content_twice_is_one_entity_two_chains(self):
        document = OpenDocumentText()
        first = document.addPictureFromString(b"shared", u"image/gif")
        second = document.addPictureFromString(b"shared", u"image/gif")
        paragraph = _paragraph(document)
        paragraph.addElement(_image_frame(first))
        paragraph.addElement(_image_frame(second))

        loaded, unused_zip, entries = _roundtrip(document)
        report = build_archive(loaded, entries)

        self.assertEqual(len(report), 1)
        resource = next(iter(report.resources.values()))
        self.assertEqual(len(resource.references), 2)
        self.assertEqual(
            sorted(ref.target for ref in resource.references),
            sorted([first, second]))
        self.assertEqual(len(report.references), 2)

    def test_same_named_picture_in_subdocuments_differs(self):
        root = OpenDocumentText()
        sub_a = OpenDocumentText()
        sub_b = OpenDocumentText()
        # Same relative manifest name, different bytes per subdocument.
        shared_name = u"Pictures/shared.png"
        sub_a.Pictures[shared_name] = (IS_IMAGE, b"bytes-A", u"image/png")
        sub_b.Pictures[shared_name] = (
            IS_IMAGE, b"bytes-B-different", u"image/png")
        loc_a = root.addObject(sub_a)
        loc_b = root.addObject(sub_b)
        paragraph = _paragraph(root)
        paragraph.addElement(_object_frame(loc_a))
        paragraph.addElement(_object_frame(loc_b))
        para_a = _paragraph(sub_a)
        para_a.addElement(_image_frame(shared_name))
        para_b = _paragraph(sub_b)
        para_b.addElement(_image_frame(shared_name))

        loaded, unused_zip, entries = _roundtrip(root)
        report = build_archive(loaded, entries)

        full_a = u"Object 1/" + shared_name
        full_b = u"Object 2/" + shared_name
        self.assertNotEqual(
            report.get(full_a).content, report.get(full_b).content)
        self.assertEqual(report.get(full_a).content, b"bytes-A")
        self.assertEqual(report.get(full_b).content, b"bytes-B-different")
        containers = {ref.container for ref in report.references if ref.target}
        self.assertEqual(containers, {u"", u"Object 1", u"Object 2"})


class TestArchiveObjects(unittest.TestCase):

    def _nested_documents(self):
        root = OpenDocumentText()
        child = OpenDocumentText()
        grandchild = OpenDocumentText()
        child_loc = root.addObject(child)
        child.addObject(grandchild)
        _paragraph(root).addElement(_object_frame(child_loc))
        # Relative to the child subdocument, as real ODF producers write it.
        _paragraph(child).addElement(_object_frame(u"./Object 1"))
        return root, child, grandchild

    def test_nested_subobjects_are_loaded_and_walked(self):
        root, unused_child, unused_grand = self._nested_documents()
        loaded, unused_zip, entries = _roundtrip(root)

        self.assertEqual(len(loaded.childobjects), 1)
        nested = loaded.childobjects[0]
        self.assertEqual(len(nested.childobjects), 1)
        report = build_archive(loaded, entries)
        self.assertIn(u"Object 1/", report.resources)
        self.assertIn(u"Object 1/Object 1/", report.resources)
        for resource in report:
            if resource.kind == u"object":
                self.assertTrue(resource.references)

    def test_reference_chain_records_every_hop(self):
        root, unused_child, unused_grand = self._nested_documents()
        loaded, unused_zip, entries = _roundtrip(root)
        report = build_archive(loaded, entries)

        grand = report.get(u"Object 1/Object 1/")
        reference = grand.references[0]
        chain = reference.chain
        # Root -> child -> grandchild: one root marker plus two element hops.
        self.assertEqual(chain[0], u"/")
        self.assertEqual(len(chain), 3)
        self.assertTrue(all(u"draw:object" in step for step in chain[1:]))
        self.assertEqual(reference.target, u"Object 1/Object 1/")
        self.assertEqual(reference.container, u"Object 1")


class TestArchiveBoundaries(unittest.TestCase):

    def _document_with_link(self, href):
        root = OpenDocumentText()
        _paragraph(root).addElement(_image_frame(href))
        return _roundtrip(root)

    def test_external_references_stop_outside_package(self):
        for href in (u"http://example.com/a.png",
                     u"https://example.com/a.png",
                     u"file:///etc/passwd",
                     u"//host/shared/a.png",
                     u"/etc/passwd",
                     u"data:image/png;base64,QQ==",
                     u"vnd.sun.star:foo|bar"):
            loaded, unused_zip, entries = self._document_with_link(href)
            report = build_archive(loaded, entries)
            self.assertEqual(len(report), 0, href)
            self.assertEqual(len(report.references), 1, href)
            reference = report.references[0]
            self.assertEqual(reference.stop, STOP_EXTERNAL, href)
            self.assertIsNone(reference.target, href)

    def test_missing_entry_is_reported_not_raised(self):
        loaded, unused_zip, entries = self._document_with_link(
            u"Pictures/does-not-exist.png")
        report = build_archive(loaded, entries)
        self.assertEqual(len(report), 0)
        self.assertEqual(report.unresolved[0].stop, STOP_MISSING)

    def test_in_package_parent_traversal_resolves(self):
        root = OpenDocumentText()
        picture = root.addPictureFromString(b"root-pic", u"image/png")
        child = OpenDocumentText()
        child_loc = root.addObject(child)
        _paragraph(root).addElement(_object_frame(child_loc))
        name = picture.split(u"/", 1)[1]
        _paragraph(child).addElement(_image_frame(u"../Pictures/" + name))

        loaded, unused_zip, entries = _roundtrip(root)
        report = build_archive(loaded, entries)
        sub_references = [r for r in report.references
                          if r.container == u"Object 1"]
        self.assertEqual(len(sub_references), 1)
        self.assertIsNone(sub_references[0].stop)
        self.assertEqual(sub_references[0].target, picture)

    def test_escaping_parent_traversal_stops(self):
        root = OpenDocumentText()
        child = OpenDocumentText()
        child_loc = root.addObject(child)
        _paragraph(root).addElement(_object_frame(child_loc))
        _paragraph(child).addElement(
            _image_frame(u"../../Pictures/escape.png"))

        loaded, unused_zip, entries = _roundtrip(root)
        report = build_archive(loaded, entries)
        sub_references = [r for r in report.references
                          if r.container == u"Object 1"]
        self.assertEqual(sub_references[0].stop, STOP_EXTERNAL)

    def test_cycle_is_recorded_and_terminates(self):
        root = OpenDocumentText()
        child = OpenDocumentText()
        grandchild = OpenDocumentText()
        child_loc = root.addObject(child)
        child.addObject(grandchild)
        _paragraph(root).addElement(_object_frame(child_loc))
        _paragraph(child).addElement(_object_frame(u"./Object 1"))

        loaded, unused_zip, entries = _roundtrip(root)
        deepest = loaded.childobjects[0].childobjects[0]
        # The deepest object points back at the package root.
        cycle_para = P()
        _first_element(deepest).addElement(cycle_para)
        cycle_para.addElement(_object_frame(u"../.."))

        report = build_archive(loaded, entries)  # must return, not recurse
        cycles = [r for r in report.references if r.stop == STOP_CYCLE]
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].href, u"../..")

    def test_diamond_keeps_both_chains_to_one_entity(self):
        root = OpenDocumentText()
        child = OpenDocumentText()
        grandchild = OpenDocumentText()
        child_loc = root.addObject(child)
        child.addObject(grandchild)
        _paragraph(root).addElement(_object_frame(child_loc))
        paragraph = _paragraph(child)
        paragraph.addElement(_object_frame(u"./Object 1"))
        paragraph.addElement(_object_frame(u"Object 1/"))

        loaded, unused_zip, entries = _roundtrip(root)
        report = build_archive(loaded, entries)
        grand = report.get(u"Object 1/Object 1/")
        self.assertEqual(len(grand.references), 2)
        self.assertTrue(all(r.stop is None for r in grand.references))


class TestArchivePages(unittest.TestCase):

    def test_reference_remembers_enclosing_page(self):
        presentation = OpenDocumentPresentation()
        page = draw.Page(masterpagename=u"Default", name=u"Slide1")
        picture = presentation.addPictureFromString(
            b"slide-pic", u"image/png")
        page.addElement(_image_frame(picture))
        presentation.presentation.addElement(page)

        loaded, unused_zip, entries = _roundtrip(presentation)
        report = build_archive(loaded, entries)
        reference = report.references[0]
        self.assertEqual(reference.page, u"Slide1")
        self.assertEqual(
            reference.chain[-1],
            u'draw:image[@xlink:href="%s"]' % reference.href)


class TestSaveArchive(unittest.TestCase):

    def setUp(self):
        self.outputdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.outputdir, ignore_errors=True)

    def test_writes_entries_and_index(self):
        root = OpenDocumentText()
        picture = root.addPictureFromString(b"pic-bytes", u"image/png")
        child = OpenDocumentText()
        child_picture = child.addPictureFromString(
            b"child-bytes", u"image/png")
        child_loc = root.addObject(child)
        _paragraph(root).addElement(_object_frame(child_loc))
        _paragraph(root).addElement(_image_frame(picture))
        _paragraph(child).addElement(_image_frame(child_picture))

        loaded, archive, entries = _roundtrip(root)
        report = save_archive(loaded, self.outputdir, archive)

        written = []
        for base, unused_dirs, files in os.walk(self.outputdir):
            for name in files:
                written.append(os.path.relpath(
                    os.path.join(base, name), self.outputdir))
        self.assertIn(u"archive-index.json", written)
        self.assertIn(picture, written)
        self.assertTrue(
            any(path.startswith(u"Object 1/") and
                path.endswith(u"content.xml") for path in written))
        with open(os.path.join(self.outputdir, u"archive-index.json")) as fd:
            index = json.load(fd)
        self.assertEqual(
            len(index[u"resources"]), len(report.resources))
        for entry in index[u"resources"]:
            self.assertNotIn(u"content", entry)  # index stays JSON/text only


if __name__ == '__main__':
    unittest.main()
