#-------------------------------------------------------------------------------
# elftools tests
#
# Matching ELF files with external debug information from an offline manifest.
# This code is in the public domain.
#-------------------------------------------------------------------------------
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zlib
from pathlib import Path

from elftools.elf.debuglink import (
    REASON_CHECKSUM_MISMATCH,
    REASON_EXCLUDED_BY_BUILD_ID,
    STATUS_AMBIGUOUS,
    STATUS_MATCHED,
    STATUS_UNIDENTIFIED,
    STATUS_UNMATCHED,
    CandidateManifest,
    DebugInfoMatcher,
    match_debug_files,
    match_debug_info,
    match_manifest,
    report_to_dict,
    write_report,
)
from elftools.elf.elffile import ELFFile


FIXTURES = Path('test') / 'testfiles_for_unittests'
DEBUGLINK_ELF = FIXTURES / 'debuglink'
DEBUGLINK_DEBUG = FIXTURES / 'debuglink.debug'


def read_build_id(path: Path) -> str:
    with ELFFile.load_from_path(path) as elffile:
        build_ids = elffile.get_build_ids()
    assert len(build_ids) == 1
    return build_ids[0]


def patch_build_id(source: Path, destination: Path, new_id: str) -> None:
    """Copy an ELF fixture and replace every copy of its GNU build ID bytes."""
    data = bytearray(source.read_bytes())
    old = bytes.fromhex(read_build_id(source))
    new = bytes.fromhex(new_id)
    assert len(old) == len(new)
    count = data.count(old)
    assert count, 'fixture does not contain the raw build ID'
    data = bytearray(bytes(data).replace(old, new))
    destination.write_bytes(data)


def remove_build_id(source: Path, destination: Path) -> None:
    """Copy an ELF fixture and turn its GNU build-ID notes into another note type."""
    data = bytearray(source.read_bytes())
    build_id = bytes.fromhex(read_build_id(source))
    offset = data.find(build_id)
    assert offset >= 0
    while offset >= 0:
        # Elf_Nhdr: namesz, descsz, type. For GNU notes the 4-byte name is
        # followed directly by the descriptor, so the type is 8 bytes earlier.
        type_offset = offset - 8
        assert data[type_offset:type_offset + 4] == (3).to_bytes(4, 'little')
        data[type_offset:type_offset + 4] = (4).to_bytes(4, 'little')
        offset = data.find(build_id, offset + len(build_id))
    destination.write_bytes(data)


def remove_debuglink_section(source: Path, destination: Path) -> None:
    """Copy an ELF fixture and rename its .gnu_debuglink section.

    The section contents remain, but without the section name the file has no
    debuglink identity.  The replacement name has the same length so that no
    section header or string table offset shifts.
    """
    data = bytearray(source.read_bytes())
    old = b'.gnu_debuglink\x00'
    assert old in data
    data = data.replace(old, b'.xnu_debuglink\x00')
    destination.write_bytes(data)


def remove_identity(source: Path, destination: Path) -> None:
    """Copy an ELF fixture and remove both debuglink and GNU build-ID identity."""
    intermediate = destination.with_name(destination.name + '.buildid')
    remove_build_id(source, intermediate)
    remove_debuglink_section(intermediate, destination)
    intermediate.unlink()


def remove_debug_sections(source: Path, destination: Path) -> None:
    """Copy an ELF fixture and rename its DWARF section names.

    Replacing ``.debug_`` with ``.xebug_`` keeps the string-table offsets
    valid while making every debug section unrecognizable, so build IDs and
    note sections survive but ``has_dwarf_info(strict=True)`` is false.
    """
    data = source.read_bytes()
    assert b'.debug_' in data
    destination.write_bytes(data.replace(b'.debug_', b'.xebug_'))


def force_crc32(data: bytes, target: int) -> bytes:
    """Return data plus four bytes whose CRC32 equals target.

    CRC32 is affine over GF(2), so the four free bytes give exactly 32 binary
    unknowns.  Build a linear basis for the 32 appended-bit vectors and solve
    for the target difference.  This produces a different file with the same
    CRC, which is what an offline archive's CRC collision looks like.
    """
    base = zlib.crc32(data) & 0xffffffff

    def appended_crc(patch: int) -> int:
        # Continuing from `base` models the CRC of `data + patch`.
        return zlib.crc32(patch.to_bytes(4, 'little'), base) & 0xffffffff

    zero_crc = appended_crc(0)
    deltas = [appended_crc(1 << bit) ^ zero_crc for bit in range(32)]

    basis: dict[int, tuple[int, int]] = {}
    for index, vector in enumerate(deltas):
        mask = 1 << index
        while vector:
            pivot = vector.bit_length() - 1
            if pivot in basis:
                other_vector, other_mask = basis[pivot]
                vector ^= other_vector
                mask ^= other_mask
            else:
                basis[pivot] = (vector, mask)
                break

    remainder = target ^ zero_crc
    mask = 0
    while remainder:
        pivot = remainder.bit_length() - 1
        if pivot not in basis:
            raise ValueError('CRC32 target is not reachable')
        other_vector, other_mask = basis[pivot]
        remainder ^= other_vector
        mask ^= other_mask

    result = data + mask.to_bytes(4, 'little')
    assert zlib.crc32(result) & 0xffffffff == target
    return result


class TestBuildIds(unittest.TestCase):
    def test_only_gnu_namespace_build_ids(self):
        path = Path('test') / 'testfiles_for_readelf' / 'note_owners.o.elf'
        with ELFFile.load_from_path(path) as elffile:
            self.assertEqual(elffile.get_build_ids(), ['78563412'])
            self.assertEqual(elffile.get_build_id(), '78563412')

    def test_missing_build_id(self):
        path = FIXTURES / 'dwarfv5_basic.elf'
        with tempfile.TemporaryDirectory() as tmpdir:
            stripped = Path(tmpdir) / 'no_id.elf'
            remove_build_id(path, stripped)
            with ELFFile.load_from_path(stripped) as elffile:
                self.assertEqual(elffile.get_build_ids(), [])
                self.assertIsNone(elffile.get_build_id())


class TestDebugInfoMatching(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def candidate_dir(self, name='candidates'):
        path = self.tmp / name
        path.mkdir()
        return path

    def test_matches_by_debuglink_and_build_id(self):
        result = match_debug_info(DEBUGLINK_ELF, [DEBUGLINK_DEBUG])

        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertTrue(result.matched)
        self.assertFalse(result.ambiguous)
        self.assertEqual(result.matched_by, ('build_id', 'debuglink'))
        self.assertEqual(result.build_id, read_build_id(DEBUGLINK_ELF))
        self.assertIsNotNone(result.debuglink)
        self.assertEqual(result.debuglink.filename, 'debuglink.debug')
        self.assertEqual(result.debuglink.checksum, 0x29b7c5f1)
        self.assertEqual(result.selected.path, str(DEBUGLINK_DEBUG))
        self.assertEqual(result.selected.checksum, 0x29b7c5f1)
        self.assertIn('CRC32 0x29b7c5f1', ' '.join(result.selected.evidence))

    def test_filename_match_with_bad_checksum_is_rejected(self):
        candidate = self.candidate_dir() / 'debuglink.debug'
        candidate.write_bytes(DEBUGLINK_DEBUG.read_bytes() + b'\0')

        result = match_debug_info(DEBUGLINK_ELF, [candidate])

        self.assertEqual(result.status, STATUS_UNMATCHED)
        self.assertIsNone(result.selected)
        self.assertEqual(result.candidates[0].rejection_reason,
                         REASON_CHECKSUM_MISMATCH)
        self.assertTrue(result.candidates[0].filename_matches)
        self.assertFalse(result.candidates[0].checksum_matches)
        self.assertIn('checksum does not', ' '.join(result.candidates[0].evidence))

    def test_build_id_excludes_crc_colliding_candidate(self):
        directory = self.candidate_dir()
        target_crc = 0x29b7c5f1
        good = directory / 'debuglink.debug'
        good.write_bytes(force_crc32(DEBUGLINK_DEBUG.read_bytes(), target_crc))

        other_id = '11' * 20
        altered = self.tmp / 'altered.debug'
        patch_build_id(DEBUGLINK_DEBUG, altered, other_id)
        bad = self.candidate_dir('other') / 'debuglink.debug'
        bad.write_bytes(force_crc32(altered.read_bytes(), target_crc))

        self.assertNotEqual(good.read_bytes(), bad.read_bytes())
        self.assertEqual(zlib.crc32(good.read_bytes()) & 0xffffffff, target_crc)
        self.assertEqual(zlib.crc32(bad.read_bytes()) & 0xffffffff, target_crc)

        result = match_debug_info(DEBUGLINK_ELF, [bad, good])

        # The CRC collision makes both candidates match by name and checksum.
        # Only the GNU build ID distinguishes the builds, so it must be cited
        # as the reason for excluding the candidate from the other build.
        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertEqual(result.selected.path, str(good))
        rejected = [candidate for candidate in result.candidates
                    if candidate.path == str(bad)][0]
        self.assertEqual(rejected.rejection_reason, REASON_EXCLUDED_BY_BUILD_ID)
        self.assertFalse(rejected.eligible)
        self.assertTrue(rejected.checksum_matches)
        self.assertFalse(rejected.build_id_matches)
        self.assertEqual(rejected.build_id, other_id)
        self.assertIn(other_id, ' '.join(rejected.evidence))
        self.assertTrue(any(
            other_id in line and 'differs' in line
            for line in rejected.evidence))

    def test_crc_collision_without_build_id_stays_ambiguous(self):
        directory = self.candidate_dir()
        target_crc = 0x29b7c5f1

        # Remove the build-ID note from the ELF, leaving only .gnu_debuglink.
        elfs = self.tmp / 'elfs'
        elfs.mkdir()
        elf = elfs / 'debuglink'
        remove_build_id(DEBUGLINK_ELF, elf)

        first = directory / 'debuglink.debug'
        first.write_bytes(force_crc32(DEBUGLINK_DEBUG.read_bytes(), target_crc))

        altered = self.tmp / 'altered.debug'
        patch_build_id(DEBUGLINK_DEBUG, altered, '22' * 20)
        second = self.candidate_dir('other') / 'debuglink.debug'
        second.write_bytes(force_crc32(altered.read_bytes(), target_crc))

        result = match_debug_info(elf, [first, second])

        self.assertEqual(result.status, STATUS_AMBIGUOUS)
        self.assertIsNone(result.selected)
        self.assertEqual(result.debuglink.checksum, target_crc)
        self.assertEqual({candidate.checksum for candidate in result.candidates},
                         {target_crc})
        self.assertEqual(len({candidate.sha256 for candidate in result.candidates}), 2)
        self.assertTrue(any('ambiguous' in line.lower() for line in result.evidence))

    def test_identical_contents_are_one_match(self):
        first_dir = self.candidate_dir('one')
        second_dir = self.candidate_dir('two')
        first = first_dir / 'debuglink.debug'
        second = second_dir / 'debuglink.debug'
        shutil.copyfile(DEBUGLINK_DEBUG, first)
        shutil.copyfile(DEBUGLINK_DEBUG, second)

        result = match_debug_info(DEBUGLINK_ELF, [first, second])

        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertEqual(result.selected.sha256, result.candidates[0].sha256)
        self.assertEqual(len({candidate.sha256 for candidate in result.candidates}), 1)
        self.assertIn('Duplicate content', ' '.join(result.evidence))

    def test_unrelated_build_does_not_block_another_pair(self):
        unrelated = FIXTURES / 'dwarfv5_basic.elf'
        results = match_debug_files(
            [DEBUGLINK_ELF, unrelated],
            [DEBUGLINK_DEBUG],
        )

        self.assertEqual(results[0].status, STATUS_MATCHED)
        self.assertEqual(results[1].status, STATUS_UNMATCHED)
        self.assertEqual(results[0].selected.path, str(DEBUGLINK_DEBUG))

    def test_ambiguous_build_does_not_block_other_matches(self):
        # Two archive ELFs have a usable build ID and one has none. The same
        # two same-named, same-CRC candidates are ambiguous for the ELF without
        # a build ID, but that must not stop the other two ELFs from being
        # delivered independently.
        archive = self.tmp / 'archive'
        archive.mkdir()
        candidate_dir = archive / 'debug'
        candidate_dir.mkdir()
        other_dir = archive / 'other'
        other_dir.mkdir()
        target_crc = 0x29b7c5f1
        good = candidate_dir / 'debuglink.debug'
        good.write_bytes(force_crc32(DEBUGLINK_DEBUG.read_bytes(), target_crc))

        other_id = '44' * 20
        altered = self.tmp / 'altered.debug'
        patch_build_id(DEBUGLINK_DEBUG, altered, other_id)
        bad = other_dir / 'debuglink.debug'
        bad.write_bytes(force_crc32(altered.read_bytes(), target_crc))

        elf_one = archive / 'one'
        shutil.copyfile(DEBUGLINK_ELF, elf_one)
        elf_two = archive / 'two'
        shutil.copyfile(DEBUGLINK_ELF, elf_two)
        ambiguous = archive / 'third'
        remove_build_id(DEBUGLINK_ELF, ambiguous)

        results = match_debug_files([elf_one, elf_two, ambiguous], [good, bad])

        self.assertEqual([result.status for result in results[:2]],
                         [STATUS_MATCHED, STATUS_MATCHED])
        self.assertEqual(results[0].selected.path, str(good))
        self.assertEqual(results[1].selected.path, str(good))
        self.assertEqual(results[2].status, STATUS_AMBIGUOUS)
        self.assertEqual(results[2].matched_by, ())

    def test_invalid_elf_reports_error_without_candidates(self):
        broken = self.tmp / 'broken.bin'
        broken.write_bytes(b'not an ELF file')

        result = match_debug_info(broken, [DEBUGLINK_DEBUG])

        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.selected)
        self.assertEqual(result.candidates, [])
        self.assertIn('Cannot parse ELF file', result.evidence[0])

    def test_no_build_id_falls_back_to_debuglink(self):
        directory = self.candidate_dir()
        candidate = directory / 'debuglink.debug'
        shutil.copyfile(DEBUGLINK_DEBUG, candidate)
        elf_dir = self.tmp / 'elfs'
        elf_dir.mkdir()
        elf = elf_dir / 'debuglink'
        remove_build_id(DEBUGLINK_ELF, elf)

        result = match_debug_info(elf, [candidate])

        # The debuglink remains a valid identity even when no build ID is
        # available to corroborate it.
        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertEqual(result.matched_by, ('debuglink',))
        self.assertEqual(result.candidates[0].build_id_matches, None)

    def test_candidate_without_build_id_cannot_be_excluded(self):
        directory = self.tmp / 'with_id'
        directory.mkdir()
        target_crc = 0x29b7c5f1
        good = directory / 'debuglink.debug'
        good.write_bytes(force_crc32(DEBUGLINK_DEBUG.read_bytes(), target_crc))

        other_dir = self.tmp / 'without_id'
        other_dir.mkdir()
        altered = self.tmp / 'altered.debug'
        patch_build_id(DEBUGLINK_DEBUG, altered, '33' * 20)
        no_id = other_dir / 'debuglink.debug'
        without_build_id = self.tmp / 'no_build_id.debug'
        remove_build_id(altered, without_build_id)
        no_id.write_bytes(force_crc32(without_build_id.read_bytes(), target_crc))

        result = match_debug_info(DEBUGLINK_ELF, [good, no_id])

        self.assertEqual(result.status, STATUS_AMBIGUOUS)
        self.assertIsNone(result.selected)
        self.assertEqual(len({candidate.sha256 for candidate in result.candidates}), 2)
        self.assertTrue(any('cannot be positively excluded' in line
                            for line in result.evidence))

    def test_build_id_only_candidate_must_contain_debug_info(self):
        directory = self.candidate_dir()
        # Rename the real debug file: no debuglink name match remains, so only
        # the GNU build ID can select it.
        renamed_debug = directory / 'renamed.bin'
        shutil.copyfile(DEBUGLINK_DEBUG, renamed_debug)

        result = match_debug_info(DEBUGLINK_ELF, [renamed_debug])

        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertEqual(result.matched_by, ('build_id',))
        self.assertTrue(result.candidates[0].has_debug_info)
        self.assertFalse(result.candidates[0].filename_matches)

        # A file with the matching build ID but no DWARF sections is not usable
        # debug information and must not be selected.
        stripped = directory / 'stripped.debug'
        remove_debug_sections(DEBUGLINK_DEBUG, stripped)
        result = match_debug_info(DEBUGLINK_ELF, [stripped])

        self.assertEqual(result.status, STATUS_UNMATCHED)
        candidate = result.candidates[0]
        self.assertEqual(candidate.rejection_reason, 'no_debug_info')
        self.assertTrue(candidate.build_id_matches)
        self.assertFalse(candidate.has_debug_info)

    def test_elf_without_any_identity_never_selects(self):
        directory = self.candidate_dir()
        candidate = directory / 'debuglink.debug'
        shutil.copyfile(DEBUGLINK_DEBUG, candidate)
        elf_dir = self.tmp / 'elfs'
        elf_dir.mkdir()
        elf = elf_dir / 'debuglink'
        remove_identity(DEBUGLINK_ELF, elf)

        result = match_debug_info(elf, [candidate])

        self.assertEqual(result.status, STATUS_UNIDENTIFIED)
        self.assertIsNone(result.selected)
        self.assertIsNone(result.debuglink)
        self.assertEqual(result.build_ids, ())
        self.assertEqual(result.candidates[0].rejection_reason,
                         'no_identity')
        self.assertIn('no candidate can be selected',
                      ' '.join(result.evidence))


class TestCandidateManifest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_json_manifest_resolves_relative_paths_and_matches(self):
        shutil.copyfile(DEBUGLINK_DEBUG, self.tmp / 'debuglink.debug')
        manifest_path = self.tmp / 'manifest.json'
        manifest_path.write_text(json.dumps({
            'directories': ['debug'],
            'elfs': ['elfs/program'],
            'recursive': True,
        }))
        (self.tmp / 'debug').mkdir()
        (self.tmp / 'elfs').mkdir()
        shutil.copyfile(DEBUGLINK_DEBUG, self.tmp / 'debug' / 'debuglink.debug')
        shutil.copyfile(DEBUGLINK_ELF, self.tmp / 'elfs' / 'program')

        manifest = CandidateManifest.from_file(manifest_path)
        results = match_manifest(manifest)

        self.assertEqual(manifest.elfs, (str(self.tmp / 'elfs' / 'program'),))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, STATUS_MATCHED)
        self.assertEqual(
            results[0].selected.path,
            str(self.tmp / 'debug' / 'debuglink.debug'))

        report_path = self.tmp / 'report.json'
        write_report(results, report_path, manifest=manifest)
        report = json.loads(report_path.read_text())
        self.assertEqual(report['counts'][STATUS_MATCHED], 1)
        self.assertEqual(report['manifest']['directories'],
                         [str(self.tmp / 'debug')])
        result = report['results'][0]
        self.assertEqual(result['identity']['debuglink']['checksum'], 0x29b7c5f1)
        self.assertEqual(result['identity']['build_id'],
                         read_build_id(DEBUGLINK_ELF))
        self.assertEqual(result['matched_by'], ['build_id', 'debuglink'])
        self.assertTrue(result['selected']['path'].endswith('debuglink.debug'))

    def test_text_manifest_accepts_files_and_directories(self):
        directory = self.tmp / 'candidates'
        directory.mkdir()
        shutil.copyfile(DEBUGLINK_DEBUG, directory / 'debuglink.debug')
        elf = self.tmp / 'program'
        shutil.copyfile(DEBUGLINK_ELF, elf)
        manifest_path = self.tmp / 'manifest.txt'
        manifest_path.write_text(
            '# offline archive\n\n'
            'candidates\n'
            'program\n')

        manifest = CandidateManifest.from_file(manifest_path)

        self.assertEqual(manifest.directories, (str(directory),))
        self.assertIn(str(elf), manifest.files)
        matcher = DebugInfoMatcher(manifest)
        result = matcher.match(elf)
        self.assertEqual(result.status, STATUS_MATCHED)

    def test_report_helper_is_json_safe(self):
        result = match_debug_info(DEBUGLINK_ELF, [DEBUGLINK_DEBUG])
        report = report_to_dict([result])
        encoded = json.dumps(report)
        self.assertIn('debuglink.debug', encoded)
        self.assertEqual(report['counts'][STATUS_MATCHED], 1)

    def test_manifest_reports_missing_entries(self):
        manifest_path = self.tmp / 'manifest.json'
        manifest_path.write_text(json.dumps({
            'directories': ['missing-dir'],
            'files': ['missing-file'],
            'elfs': ['missing-elf'],
        }))

        manifest = CandidateManifest.from_file(manifest_path)

        self.assertEqual(manifest.warnings, (
            f'Manifest directory does not exist: {self.tmp / "missing-dir"}',
            f'Manifest file does not exist: {self.tmp / "missing-file"}',
            f'Manifest ELF does not exist: {self.tmp / "missing-elf"}',
        ))
        self.assertEqual(manifest.to_dict()['warnings'], list(manifest.warnings))

    def test_elf_candidate_directory_does_not_select_the_elf(self):
        archive = self.tmp / 'archive'
        archive.mkdir()
        elf = archive / 'debuglink'
        shutil.copyfile(DEBUGLINK_ELF, elf)
        shutil.copyfile(DEBUGLINK_DEBUG, archive / 'debuglink.debug')
        (archive / 'unrelated.bin').write_bytes(b'not an ELF')

        result = match_debug_info(elf, CandidateManifest.from_paths(
            directories=[archive]))

        self.assertEqual(result.status, STATUS_MATCHED)
        self.assertEqual(result.selected.path, str(archive / 'debuglink.debug'))
        self_candidate = [candidate for candidate in result.candidates
                          if candidate.path == str(elf)][0]
        self.assertEqual(self_candidate.rejection_reason, 'self_reference')
        self.assertFalse(self_candidate.selected)
        unrelated = [candidate for candidate in result.candidates
                     if candidate.path.endswith('unrelated.bin')][0]
        self.assertFalse(unrelated.is_elf)
        self.assertFalse(unrelated.selected)


if __name__ == '__main__':
    unittest.main()
