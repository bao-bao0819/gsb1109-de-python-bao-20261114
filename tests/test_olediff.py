# -*- coding: utf-8 -*-
"""Tests for olediff: content-based delta manifests for OLE directories."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import olefile
import olediff

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_OLE = os.path.join(BASE_DIR, "tests", "images", "test-ole-file.doc")


def streams(**kwargs):
    """Build a stream dict from name -> bytes pairs."""
    return {(name,): data for name, data in kwargs.items()}


class ComputeDiffTest(unittest.TestCase):
    def test_unchanged(self):
        old = streams(a=b"1", b=b"2")
        manifest = olediff.compute_diff(old, dict(old))
        self.assertEqual(sorted(manifest["unchanged"]),
                         [["a"], ["b"]])
        for key in ("modified", "moved", "move_candidates",
                    "added", "removed", "payload"):
            self.assertEqual(manifest[key], [], key)

    def test_modified_in_place(self):
        old = streams(a=b"1", b=b"2")
        new = streams(a=b"1", b=b"changed")
        manifest = olediff.compute_diff(old, new)
        self.assertEqual([e["path"] for e in manifest["modified"]], [["b"]])
        self.assertEqual(manifest["removed"], [])
        self.assertEqual(manifest["added"], [])
        self.assertEqual(manifest["payload"],
                         [olediff._hash(b"changed")])

    def test_simple_move(self):
        old = streams(a=b"same", b=b"other")
        new = streams(c=b"same", b=b"other")
        manifest = olediff.compute_diff(old, new)
        self.assertEqual(manifest["moved"],
                         [{"from": ["a"], "to": ["c"],
                           "sha256": olediff._hash(b"same"), "size": 4}])
        self.assertEqual(manifest["added"], [])
        self.assertEqual(manifest["removed"], [])
        self.assertEqual(manifest["payload"], [])

    def test_duplicates_prefer_same_path(self):
        # Two identical copies in both; one copy moves away.
        old = streams(x=b"dup", y=b"dup")
        new = streams(x=b"dup", z=b"dup")
        manifest = olediff.compute_diff(old, new)
        self.assertEqual(manifest["unchanged"], [["x"]])
        self.assertEqual(manifest["moved"],
                         [{"from": ["y"], "to": ["z"],
                           "sha256": olediff._hash(b"dup"), "size": 3}])
        self.assertEqual(manifest["move_candidates"], [])

    def test_ambiguous_move_reported_as_group(self):
        # Two identical copies relocate to two new paths: no safe pairing.
        old = streams(a=b"dup", b=b"dup")
        new = streams(c=b"dup", d=b"dup")
        manifest = olediff.compute_diff(old, new)
        self.assertEqual(manifest["moved"], [])
        self.assertEqual(len(manifest["move_candidates"]), 1)
        group = manifest["move_candidates"][0]
        self.assertEqual(sorted(group["from"]), [["a"], ["b"]])
        self.assertEqual(sorted(group["to"]), [["c"], ["d"]])
        self.assertEqual(manifest["added"], [])
        self.assertEqual(manifest["removed"], [])

    def test_partial_overlap_group(self):
        # Same path copy paired first; leftovers form the group.
        old = streams(keep=b"dup", a=b"dup", b=b"dup")
        new = streams(keep=b"dup", c=b"dup", d=b"dup")
        manifest = olediff.compute_diff(old, new)
        self.assertEqual(manifest["unchanged"], [["keep"]])
        self.assertEqual(len(manifest["move_candidates"]), 1)
        group = manifest["move_candidates"][0]
        self.assertEqual(sorted(group["from"]), [["a"], ["b"]])
        self.assertEqual(sorted(group["to"]), [["c"], ["d"]])

    def test_group_with_extra_copy(self):
        # 2 old copies, 3 new copies: group plus one added copy.
        old = streams(a=b"dup", b=b"dup")
        new = streams(c=b"dup", d=b"dup", e=b"dup")
        manifest = olediff.compute_diff(old, new)
        self.assertEqual(len(manifest["move_candidates"]), 1)
        group = manifest["move_candidates"][0]
        self.assertEqual(len(group["from"]), 2)
        self.assertEqual(len(group["to"]), 3)
        self.assertEqual(manifest["payload"], [])

    def test_added_and_removed(self):
        old = streams(gone=b"x", stay=b"s")
        new = streams(stay=b"s", fresh=b"y")
        manifest = olediff.compute_diff(old, new)
        self.assertEqual(manifest["removed"], [["gone"]])
        self.assertEqual([e["path"] for e in manifest["added"]], [["fresh"]])
        self.assertEqual(manifest["payload"], [olediff._hash(b"y")])

    def test_swap_is_two_moves(self):
        old = streams(a=b"1", b=b"2")
        new = streams(a=b"2", b=b"1")
        manifest = olediff.compute_diff(old, new)
        pairs = sorted((tuple(e["from"]), tuple(e["to"]))
                       for e in manifest["moved"])
        self.assertEqual(pairs, [(("a",), ("b",)), (("b",), ("a",))])
        self.assertEqual(manifest["modified"], [])


class ApplyDiffTest(unittest.TestCase):
    def _roundtrip(self, old, new):
        manifest = olediff.compute_diff(old, new)
        payload = olediff.build_payload(new, manifest)
        rebuilt = olediff.apply_diff(old, manifest, payload)
        self.assertEqual(rebuilt, new)
        return manifest

    def test_roundtrip_mixed(self):
        old = {
            ("keep",): b"unchanged",
            ("mod",): b"before",
            ("move",): b"reloc",
            ("del",): b"bye",
            ("dup1",): b"dup",
            ("dup2",): b"dup",
        }
        new = {
            ("keep",): b"unchanged",
            ("mod",): b"after",
            ("sub", "move"): b"reloc",
            ("new",): b"brand new",
            ("dupA",): b"dup",
            ("dupB",): b"dup",
        }
        manifest = self._roundtrip(old, new)
        self.assertEqual(manifest["unchanged"], [["keep"]])
        self.assertEqual([e["path"] for e in manifest["modified"]],
                         [["mod"]])
        self.assertEqual(manifest["moved"][0]["from"], ["move"])
        self.assertEqual(manifest["moved"][0]["to"], ["sub", "move"])
        self.assertEqual(manifest["removed"], [["del"]])
        self.assertEqual([e["path"] for e in manifest["added"]], [["new"]])
        self.assertEqual(len(manifest["move_candidates"]), 1)

    def test_roundtrip_swap(self):
        old = streams(a=b"1", b=b"2")
        new = streams(a=b"2", b=b"1")
        self._roundtrip(old, new)

    def test_roundtrip_group_with_extra_copy(self):
        old = streams(a=b"dup", b=b"dup")
        new = streams(c=b"dup", d=b"dup", e=b"dup")
        self._roundtrip(old, new)

    def test_missing_payload_raises(self):
        old = streams(a=b"1")
        new = streams(a=b"2")
        manifest = olediff.compute_diff(old, new)
        with self.assertRaises(ValueError):
            olediff.apply_diff(old, manifest, {})


class OleIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _copy_ole(self, name):
        path = os.path.join(self.tmp, name)
        shutil.copyfile(TEST_OLE, path)
        return path

    def test_self_diff_is_empty(self):
        old, _ = olediff.read_ole(TEST_OLE)
        manifest = olediff.compute_diff(old, dict(old))
        self.assertEqual(len(manifest["unchanged"]), len(old))
        self.assertEqual(manifest["payload"], [])

    def test_modified_stream_roundtrip(self):
        # Rewrite one stream in place (same size) to simulate an edit.
        edited = self._copy_ole("edited.doc")
        ole = olefile.OleFileIO(edited, write_mode=True)
        data = ole.openstream("WordDocument").read()
        ole.write_stream("WordDocument", data[:100][::-1] + data[100:])
        ole.close()

        old_streams, old_storages = olediff.read_ole(TEST_OLE)
        new_streams, new_storages = olediff.read_ole(edited)
        manifest = olediff.compute_diff(old_streams, new_streams,
                                        old_storages, new_storages)
        self.assertEqual([e["path"] for e in manifest["modified"]],
                         [["WordDocument"]])
        self.assertEqual(manifest["moved"], [])
        payload = olediff.build_payload(new_streams, manifest)
        self.assertEqual(olediff.apply_diff(old_streams, manifest, payload),
                         new_streams)

    def test_cli_diff_and_verify(self):
        edited = self._copy_ole("edited.doc")
        ole = olefile.OleFileIO(edited, write_mode=True)
        data = ole.openstream("1Table").read()
        ole.write_stream("1Table", b"\x00" + data[1:])
        ole.close()

        manifest_path = os.path.join(self.tmp, "manifest.json")
        payload_dir = os.path.join(self.tmp, "payload")
        env = dict(os.environ, PYTHONPATH=BASE_DIR)
        rc = subprocess.call(
            [sys.executable, os.path.join(BASE_DIR, "olediff.py"), "diff",
             TEST_OLE, edited, "-m", manifest_path, "-p", payload_dir],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(rc, 0)
        with open(manifest_path) as fp:
            manifest = json.load(fp)
        self.assertEqual([e["path"] for e in manifest["modified"]],
                         [["1Table"]])
        for digest in manifest["payload"]:
            self.assertTrue(os.path.exists(os.path.join(payload_dir, digest)))

        rc = subprocess.call(
            [sys.executable, os.path.join(BASE_DIR, "olediff.py"), "verify",
             TEST_OLE, edited, "-m", manifest_path, "-p", payload_dir],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
