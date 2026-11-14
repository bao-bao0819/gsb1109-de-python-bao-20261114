#-------------------------------------------------------------------------------
# elftools tests
#
# Tests for attributing DWARF line-table file entries to relocated
# source trees (elftools/dwarf/sourceroots.py).
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.dwarf.sourceroots import (
    ChecksumStore,
    SourceRootResolver,
    resolve_dwarf_sources,
    resolve_sources,
)


def _v4_header(entries, directories):
    return {
        'version': 4,
        'include_directory': [directory.encode() for directory in directories],
        'file_entry': [
            {
                'name': name.encode(),
                'dir_index': dir_index,
                'mtime': mtime,
                'length': length,
            }
            for name, dir_index, mtime, length in entries
        ],
    }


class SourceRootsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.tree_a = self.tmpdir / 'archive-treeA'
        self.tree_b = self.tmpdir / 'archive-treeB'
        for tree in (self.tree_a, self.tree_b):
            (tree / 'src' / 'lib').mkdir(parents=True)
            (tree / 'include').mkdir()

        (self.tree_a / 'src' / 'lib' / 'main.c').write_text(
            'int main(void){return 0;}\n')
        (self.tree_b / 'src' / 'lib' / 'main.c').write_text(
            'DIFFERENT BODY WITH OTHER BYTES\n')
        (self.tree_a / 'src' / 'lib' / 'util.c').write_text(
            'void util(void){}\n')
        (self.tree_b / 'src' / 'lib' / 'util.c').write_text(
            'void util(void){ /* B */ }\n')
        (self.tree_a / 'include' / 'h.h').write_text('int h;\n')
        (self.tree_b / 'include' / 'h.h').write_text('int hh;\n')
        readme = 'identical readme\n'
        (self.tree_a / 'README.md').write_text(readme)
        (self.tree_b / 'README.md').write_text(readme)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def _header(self):
        return _v4_header(
            entries=[
                ('main.c', 1, 0,
                    (self.tree_a / 'src' / 'lib' / 'main.c').stat().st_size),
                ('util.c', 1, 0,
                    (self.tree_a / 'src' / 'lib' / 'util.c').stat().st_size),
                ('h.h', 2, 0,
                    (self.tree_a / 'include' / 'h.h').stat().st_size),
                ('../../README.md', 1, 0,
                    len('identical readme\n')),
            ],
            directories=[
                '/builds/job42/out/src/lib',
                '/builds/job42/out/include',
            ],
        )

    @staticmethod
    def _tree_names(origin):
        return sorted(Path(source.tree).name for source in origin.sources)


class TestSourceRootResolution(SourceRootsTestBase):
    def test_directory_index_and_prefix_mapping(self):
        resolution = SourceRootResolver(
            [self.tree_b, self.tree_a]).resolve_line_program(
                self._header(), 'main.o')

        self.assertEqual(resolution.cu_name, 'main.o')
        self.assertEqual(resolution.version, 4)
        # File-table indices are 1-based before DWARF v5.
        self.assertEqual(
            [origin.file_index for origin in resolution.origins],
            [1, 2, 3, 4])
        self.assertEqual(
            [origin.build_path for origin in resolution.origins],
            [
                '/builds/job42/out/src/lib/main.c',
                '/builds/job42/out/src/lib/util.c',
                '/builds/job42/out/include/h.h',
                '/builds/job42/out/README.md',
            ])
        self.assertTrue(all(
            source.build_prefix == '/builds/job42/out'
            for source in resolution.mappings))

    def test_sources_unique_but_shared_file_stays_ambiguous(self):
        resolution = SourceRootResolver(
            [self.tree_b, self.tree_a]).resolve_line_program(self._header())
        by_path = {origin.build_path: origin for origin in resolution.origins}

        main_origin = by_path['/builds/job42/out/src/lib/main.c']
        util_origin = by_path['/builds/job42/out/src/lib/util.c']
        header_origin = by_path['/builds/job42/out/include/h.h']
        readme_origin = by_path['/builds/job42/out/README.md']

        self.assertEqual(main_origin.status, 'unique')
        self.assertEqual(util_origin.status, 'unique')
        self.assertEqual(header_origin.status, 'unique')
        self.assertEqual(self._tree_names(main_origin), ['archive-treeA'])
        self.assertEqual(self._tree_names(util_origin), ['archive-treeA'])
        self.assertEqual(self._tree_names(header_origin), ['archive-treeA'])

        # The README is byte-identical in both trees and its recorded size
        # cannot separate them: keep both candidate directories instead of
        # claiming a whole-tree match from one coincidental file.
        self.assertEqual(readme_origin.status, 'ambiguous')
        self.assertEqual(
            self._tree_names(readme_origin),
            ['archive-treeA', 'archive-treeB'])
        self.assertEqual(resolution.unmatched_origins(), ())

    def test_size_mismatch_rejects_file(self):
        header = self._header()
        # Claim a size that neither tree has for util.c.
        header['file_entry'][1]['length'] = 999999
        resolution = SourceRootResolver(
            [self.tree_a, self.tree_b]).resolve_line_program(header)
        util_origin = next(
            origin for origin in resolution.origins
            if origin.build_path.endswith('util.c'))
        self.assertEqual(util_origin.status, 'unmatched')
        self.assertEqual(util_origin.sources, ())
        self.assertIn(
            'size_mismatch',
            [candidate.reason for candidate in resolution.rejected])

    def test_checksum_must_match(self):
        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        checksums = {
            'src/lib/main.c':
                digest(self.tree_a / 'src' / 'lib' / 'main.c'),
            'src/lib/util.c':
                digest(self.tree_a / 'src' / 'lib' / 'util.c'),
            'include/h.h':
                digest(self.tree_a / 'include' / 'h.h'),
        }
        resolution = SourceRootResolver(
            [self.tree_a, self.tree_b], checksums).resolve_line_program(
                self._header())
        for origin in resolution.origins:
            if origin.build_path.endswith('README.md'):
                continue
            self.assertEqual(origin.status, 'unique')
            self.assertEqual(origin.sources[0].checksum[0], 'sha256')

        bad = dict(checksums)
        bad['src/lib/main.c'] = '00' * 32
        bad_resolution = SourceRootResolver(
            [self.tree_a, self.tree_b], bad).resolve_line_program(self._header())
        main_origin = next(
            origin for origin in bad_resolution.origins
            if origin.build_path.endswith('main.c'))
        self.assertEqual(main_origin.status, 'unmatched')
        self.assertTrue(any(
            candidate.reason == 'checksum_mismatch'
            for candidate in bad_resolution.rejected))

    def test_checksum_picks_otherwise_identical_path(self):
        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        header = self._header()
        for entry in header['file_entry']:
            entry['length'] = 0
        checksums = {
            'src/lib/main.c':
                digest(self.tree_b / 'src' / 'lib' / 'main.c'),
        }
        resolution = SourceRootResolver(
            [self.tree_a, self.tree_b], checksums).resolve_line_program(header)
        main_origin = next(
            origin for origin in resolution.origins
            if origin.build_path.endswith('main.c'))
        self.assertEqual(main_origin.status, 'unique')
        self.assertEqual(self._tree_names(main_origin), ['archive-treeB'])

    def test_tree_name_prefixed_checksum_only_confirms_one_tree(self):
        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        header = self._header()
        for entry in header['file_entry']:
            entry['length'] = 0
        checksums = {
            'archive-treeB/src/lib/main.c':
                digest(self.tree_b / 'src' / 'lib' / 'main.c'),
        }
        resolution = SourceRootResolver(
            [self.tree_a, self.tree_b], checksums).resolve_line_program(header)
        main_origin = next(
            origin for origin in resolution.origins
            if origin.build_path.endswith('main.c'))
        # B is confirmed; A was never contradicted, so both remain.
        self.assertEqual(main_origin.status, 'ambiguous')
        self.assertEqual(
            self._tree_names(main_origin),
            ['archive-treeA', 'archive-treeB'])

    def test_manifest_parsing_and_algorithm_inference(self):
        md5_digest = hashlib.md5(b'data').hexdigest()
        sha1_digest = hashlib.sha1(b'data').hexdigest()
        store = ChecksumStore.from_manifest([
            '# comment',
            f'{md5_digest}  a/file.c',
            f'{sha1_digest} *b/file.h',
        ])
        (self.tree_a / 'a').mkdir(exist_ok=True)
        (self.tree_a / 'a' / 'file.c').write_bytes(b'data')
        algorithm, digest = store.verify(
            self.tree_a,
            self.tree_a / 'a' / 'file.c',
            'a/file.c')
        self.assertEqual(algorithm, 'md5')
        self.assertEqual(digest, md5_digest)

        explicit = ChecksumStore({'x': ('sha224', 'ab' * 28)})
        self.assertEqual(
            explicit._checksums['x'], ('sha224', 'ab' * 28))

    def test_partial_coverage_across_trees(self):
        t1 = self.tmpdir / 'tree1'
        t2 = self.tmpdir / 'tree2'
        (t1 / 'src' / 'lib').mkdir(parents=True)
        (t2 / 'include').mkdir(parents=True)
        (t1 / 'src' / 'lib' / 'main.c').write_text('a' * 10)
        (t2 / 'include' / 'h.h').write_text('b' * 5)

        header = _v4_header(
            entries=[
                ('main.c', 1, 0, 10),
                ('h.h', 2, 0, 5),
            ],
            directories=[
                '/builds/job42/out/src/lib',
                '/builds/job42/out/include',
            ],
        )
        resolution = SourceRootResolver([t1, t2]).resolve_line_program(header)
        attributed = {
            Path(origin.build_path).name:
                sorted(Path(source.tree).name for source in origin.sources)
            for origin in resolution.origins
        }
        self.assertEqual(attributed, {
            'main.c': ['tree1'],
            'h.h': ['tree2'],
        })
        self.assertEqual(resolution.unmatched_origins(), ())

    def test_relative_paths_have_no_prefix(self):
        header = _v4_header(
            entries=[('main.c', 0, 0, 0)],
            directories=[])
        resolution = SourceRootResolver(
            [self.tree_a]).resolve_line_program(header)
        origin = resolution.origins[0]
        self.assertEqual(origin.build_path, 'main.c')
        self.assertEqual(origin.status, 'unmatched')
        relative_root = self.tmpdir / 'relroot'
        relative_root.mkdir()
        (relative_root / 'main.c').write_text('int main(void){return 0;}\n')
        resolution = SourceRootResolver(
            [relative_root]).resolve_line_program(header)
        origin = resolution.origins[0]
        self.assertEqual(origin.status, 'unique')
        self.assertEqual(origin.sources[0].build_prefix, '')
        self.assertEqual(origin.sources[0].relative_path, 'main.c')

    def test_dominating_prefix_beats_single_file_alternative(self):
        # The tree carries the project under job42/out, plus an unrelated
        # duplicate that the root prefix could explain for only one file.
        root = self.tmpdir / 'prefix-tree'
        (root / 'job42' / 'out').mkdir(parents=True)
        (root / 'builds' / 'job42' / 'out').mkdir(parents=True)
        (root / 'job42' / 'out' / 'a.c').write_text('a')
        (root / 'job42' / 'out' / 'b.c').write_text('b')
        (root / 'builds' / 'job42' / 'out' / 'a.c').write_text('a')
        header = _v4_header(
            entries=[('a.c', 1, 0, 1), ('b.c', 1, 0, 1)],
            directories=['/builds/job42/out'])
        resolution = SourceRootResolver([root]).resolve_line_program(header)
        for origin in resolution.origins:
            self.assertEqual(origin.status, 'unique')
            self.assertEqual(
                origin.sources[0].build_prefix, '/builds')
            self.assertEqual(
                origin.sources[0].relative_path,
                'job42/out/' + origin.name)

    def test_equal_prefix_hypotheses_both_survive(self):
        root = self.tmpdir / 'tie-tree'
        root.mkdir()
        (root / 'f.c').write_text('f')
        (root / 'g.c').write_text('g')
        header = _v4_header(
            entries=[('f.c', 1, 0, 1), ('g.c', 2, 0, 1)],
            directories=['/alpha', '/beta'])
        resolution = SourceRootResolver([root]).resolve_line_program(header)
        prefixes = {
            origin.name: origin.sources[0].build_prefix
            for origin in resolution.origins}
        self.assertEqual(prefixes, {'f.c': '/alpha', 'g.c': '/beta'})
        self.assertEqual(
            sorted(match.build_prefix for match in resolution.mappings),
            ['/alpha', '/beta'])


class TestDWARFv5Header(unittest.TestCase):
    def test_zero_based_indices_and_compilation_directory(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            root = tmpdir / 'tree'
            (root / 'src').mkdir(parents=True)
            (root / 'gen').mkdir()
            (root / 'src' / 'main.c').write_text('x')
            (root / 'gen' / 'abs.h').write_text('y')
            header = {
                'version': 5,
                'include_directory': (b'/builds/job42/out',),
                'file_entry': (
                    {
                        'name': b'src/main.c',
                        'dir_index': 0,
                        'mtime': None,
                        'length': None,
                    },
                    {
                        'name': b'gen/abs.h',
                        'dir_index': None,
                        'mtime': None,
                        'length': None,
                    },
                ),
            }
            resolution = resolve_sources(header, [root], cu_name='cu5')
            self.assertEqual(resolution.version, 5)
            self.assertEqual(
                [origin.file_index for origin in resolution.origins], [0, 1])
            self.assertEqual(
                [origin.build_path for origin in resolution.origins],
                [
                    '/builds/job42/out/src/main.c',
                    '/builds/job42/out/gen/abs.h',
                ])
            self.assertTrue(all(
                origin.status == 'unique' for origin in resolution.origins))
        finally:
            shutil.rmtree(tmpdir)


class TestRealELFFixture(unittest.TestCase):
    def test_dwarf_info_smoke(self):
        fixture = os.path.join(
            os.path.dirname(__file__),
            'testfiles_for_unittests',
            'dwarf_lineprog_data16.so.elf')
        tmpdir = Path(tempfile.mkdtemp())
        try:
            with open(fixture, 'rb') as stream:
                elf = ELFFile(stream)
                dwarf_info = elf.get_dwarf_info()
                resolutions = resolve_dwarf_sources(dwarf_info, [tmpdir])
            self.assertTrue(resolutions)
            # The empty temporary tree cannot explain any recorded file,
            # but every origin must still be produced and classified.
            for resolution in resolutions:
                for origin in resolution.origins:
                    self.assertEqual(origin.status, 'unmatched')
                    self.assertEqual(origin.sources, ())
        finally:
            shutil.rmtree(tmpdir)


if __name__ == '__main__':
    unittest.main()
