# -------------------------------------------------------------------------------
# Tests for elftools.elf.sizediff
#
# Synthetic ELF files are built in-memory so that NOBITS sections, overlapping
# section ranges and compressed sections can be exercised deterministically.
#
# This code is in the public domain
# -------------------------------------------------------------------------------
import unittest
import zlib
from io import BytesIO

from elftools.construct import Container
from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile
from elftools.elf.sizediff import (
    account_file,
    compare_accounting,
    compare_files,
)
from elftools.elf.structs import ELFStructs


class ElfBuilder:
    """Minimal ELF64 little-endian relocatable object builder."""

    def __init__(self):
        self.structs = ELFStructs(elfclass=64, little_endian=True)
        self.structs.create_basic_structs()
        self.structs.create_advanced_structs('ET_REL', 'EM_X86_64', 'ELFOSABI_SYSV')
        self.sections = []
        self.add_section('', 'SHT_NULL')

    def add_section(
        self,
        name,
        sh_type,
        data=b'',
        *,
        nobits=False,
        flags=0,
        link=0,
        info=0,
        addralign=1,
        entsize=0,
        fixed_offset=None,
    ):
        self.sections.append(
            {
                'name': name,
                'sh_type': sh_type,
                'data': data,
                'nobits': nobits,
                'flags': flags,
                'link': link,
                'info': info,
                'addralign': addralign,
                'entsize': entsize,
                'fixed_offset': fixed_offset,
            }
        )
        return len(self.sections) - 1

    def add_compressed(self, name, payload, *, addralign=1):
        chdr = self.structs.Elf_Chdr.build(
            Container(
                ch_type='ELFCOMPRESS_ZLIB',
                ch_reserved=0,
                ch_size=len(payload),
                ch_addralign=addralign,
            )
        )
        blob = chdr + zlib.compress(payload)
        return self.add_section(
            name,
            'SHT_PROGBITS',
            blob,
            flags=SH_FLAGS.SHF_COMPRESSED,
            addralign=addralign,
        )

    def build(self):
        shstrtab = self._build_shstrtab()
        shstrndx = len(self.sections)
        self.sections.append(
            {
                'name': '.shstrtab',
                'sh_type': 'SHT_STRTAB',
                'data': shstrtab,
                'nobits': False,
                'flags': 0,
                'link': 0,
                'info': 0,
                'addralign': 1,
                'entsize': 0,
                'fixed_offset': None,
            }
        )

        name_offsets = self._name_offsets(shstrtab)
        cursor = self.structs.Elf_Ehdr.sizeof()
        headers = []
        for index, section in enumerate(self.sections):
            offset = 0
            size = len(section['data'])
            if section['nobits']:
                size = section['nobits_size']
                if section['fixed_offset'] is not None:
                    offset = section['fixed_offset']
                else:
                    offset = cursor
            elif section['sh_type'] != 'SHT_NULL':
                if section['fixed_offset'] is not None:
                    offset = section['fixed_offset']
                    cursor = max(cursor, offset + size)
                else:
                    if cursor % section['addralign']:
                        cursor += section['addralign'] - cursor % section['addralign']
                    offset = cursor
                    cursor += size
            link = section['link']
            if isinstance(link, str):
                link = self._index_of(link)
            info = section['info']
            if isinstance(info, str):
                info = self._index_of(info)
            headers.append(
                Container(
                    sh_name=name_offsets[section['name']],
                    sh_type=section['sh_type'],
                    sh_flags=section['flags'],
                    sh_addr=0,
                    sh_offset=offset,
                    sh_size=size,
                    sh_link=link,
                    sh_info=info,
                    sh_addralign=section['addralign'],
                    sh_entsize=section['entsize'],
                )
            )

        if cursor % 8:
            cursor += 8 - cursor % 8
        shoff = cursor
        ehdr = self.structs.Elf_Ehdr.build(
            Container(
                e_ident=Container(
                    EI_MAG=[0x7F, ord('E'), ord('L'), ord('F')],
                    EI_CLASS='ELFCLASS64',
                    EI_DATA='ELFDATA2LSB',
                    EI_VERSION='EV_CURRENT',
                    EI_OSABI='ELFOSABI_SYSV',
                    EI_ABIVERSION=0,
                ),
                e_type='ET_REL',
                e_machine='EM_X86_64',
                e_version='EV_CURRENT',
                e_entry=0,
                e_phoff=0,
                e_shoff=shoff,
                e_flags=0,
                e_ehsize=self.structs.Elf_Ehdr.sizeof(),
                e_phentsize=0,
                e_phnum=0,
                e_shentsize=self.structs.Elf_Shdr.sizeof(),
                e_shnum=len(headers),
                e_shstrndx=shstrndx,
            )
        )

        blob = bytearray(ehdr)
        for section, header in zip(self.sections, headers):
            if section['nobits'] or section['sh_type'] == 'SHT_NULL':
                continue
            if len(blob) < header.sh_offset:
                blob.extend(b'\0' * (header.sh_offset - len(blob)))
            blob[header.sh_offset : header.sh_offset + len(section['data'])] = section[
                'data'
            ]
        if len(blob) < shoff:
            blob.extend(b'\0' * (shoff - len(blob)))
        for header in headers:
            blob.extend(self.structs.Elf_Shdr.build(header))
        return bytes(blob)

    def add_nobits(self, name, size, *, addralign=1):
        index = self.add_section(
            name, 'SHT_NOBITS', b'', nobits=True, addralign=addralign
        )
        self.sections[index]['nobits_size'] = size
        return index

    def _build_shstrtab(self):
        blob = b'\0'
        for section in self.sections:
            if section['name']:
                blob += section['name'].encode('ascii') + b'\0'
        blob += b'.shstrtab\0'
        return blob

    def _name_offsets(self, shstrtab):
        offsets = {'': 0}
        cursor = 1
        for section in self.sections:
            if section['name']:
                offsets[section['name']] = cursor
                cursor += len(section['name']) + 1
        return offsets

    def _index_of(self, name):
        for index, section in enumerate(self.sections):
            if section['name'] == name:
                return index
        raise KeyError(name)


def open_elf(data):
    return ELFFile(BytesIO(data))


class TestSizeDiff(unittest.TestCase):
    def account(self, builder, path=''):
        return account_file(open_elf(builder.build()), path)

    def test_nobits_takes_no_disk_bytes(self):
        builder = ElfBuilder()
        builder.add_section('.text', 'SHT_PROGBITS', b'a' * 100)
        builder.add_nobits('.bss', 1000)
        accounting = self.account(builder)

        by_name = {record.name: record for record in accounting.sections}
        self.assertEqual(by_name['.bss'].disk_size, 0)
        self.assertEqual(by_name['.bss'].expanded_size, 1000)
        self.assertEqual(by_name['.text'].disk_size, 100)
        self.assertEqual(accounting.overlap_size, 0)
        self.assertEqual(accounting.disk_declared, accounting.disk_covered)
        self.assertEqual(
            by_name['.text'].expanded_size + by_name['.bss'].expanded_size, 1100
        )
        self.assertEqual(
            accounting.disk_covered + accounting.gap_size, accounting.file_size
        )

    def test_overlapping_ranges_counted_once(self):
        builder = ElfBuilder()
        builder.add_section('.first', 'SHT_PROGBITS', b'a' * 50)
        shared_offset = builder.structs.Elf_Ehdr.sizeof()
        builder.add_section(
            '.second', 'SHT_PROGBITS', b'b' * 50, fixed_offset=shared_offset
        )
        accounting = self.account(builder)

        first = accounting.sections[1]
        second = accounting.sections[2]
        self.assertEqual(first.disk_size + second.disk_size, 100)
        shstrtab = next(
            record for record in accounting.sections if record.name == '.shstrtab'
        )
        # Both sections claim the same 50 bytes, so the declared total counts
        # them twice while the merged total counts them once.
        self.assertEqual(accounting.disk_declared, 100 + shstrtab.disk_size)
        self.assertEqual(accounting.disk_covered, 50 + shstrtab.disk_size)
        self.assertEqual(accounting.overlap_size, 50)
        self.assertEqual(
            accounting.disk_covered + accounting.gap_size, accounting.file_size
        )

    def test_compressed_separates_disk_and_expanded(self):
        payload = bytes((index % 251 for index in range(2000)))

        old = ElfBuilder()
        old.add_section('.debug_info', 'SHT_PROGBITS', payload)
        new = ElfBuilder()
        new.add_compressed('.debug_info', payload)

        report = compare_accounting(self.account(old), self.account(new))
        diff = self._only_diff(report, '.debug_info')
        self.assertTrue(diff.paired)
        self.assertEqual(diff.expanded_delta, 0)
        self.assertLess(diff.disk_delta, 0)
        self.assertEqual(report.expanded_delta, 0)

        new_record = report.new.sections[1]
        self.assertTrue(new_record.compressed)
        self.assertEqual(new_record.data_size, 2000)
        self.assertLess(new_record.sh_size, 2000)

    def test_unique_pairing_requires_type_and_associations(self):
        old = ElfBuilder()
        old.add_section('.dup', 'SHT_PROGBITS', b'a' * 10)
        old.add_section('.linkme', 'SHT_STRTAB', b'b' * 4)
        old.add_section('.rel', 'SHT_REL', b'c' * 16, link='.linkme', entsize=16)

        new = ElfBuilder()
        new.add_section('.dup', 'SHT_PROGBITS', b'a' * 30)
        new.add_section('.linkme', 'SHT_STRTAB', b'b' * 4)
        new.add_section('.rel', 'SHT_REL', b'c' * 32, link='.linkme', entsize=16)

        report = compare_accounting(self.account(old), self.account(new))
        dup = self._only_diff(report, '.dup')
        self.assertTrue(dup.paired)
        self.assertEqual(dup.disk_delta, 20)
        rel = self._only_diff(report, '.rel')
        self.assertTrue(rel.paired)
        self.assertEqual(rel.disk_delta, 16)
        self.assertEqual(rel.old[0].link, '.linkme')

    def test_same_name_different_type_not_paired(self):
        old = ElfBuilder()
        old.add_section('.thing', 'SHT_PROGBITS', b'a' * 10)
        new = ElfBuilder()
        new.add_section('.thing', 'SHT_STRTAB', b'b' * 10)

        report = compare_accounting(self.account(old), self.account(new))
        thing_diffs = [diff for diff in report.pair_diffs if diff.name == '.thing']
        self.assertEqual(len(thing_diffs), 2)
        self.assertTrue(all(not diff.paired for diff in thing_diffs))
        self.assertEqual({diff.old_count for diff in thing_diffs}, {0, 1})
        self.assertEqual({diff.new_count for diff in thing_diffs}, {0, 1})

    def test_ambiguous_duplicates_reported_as_group(self):
        old = ElfBuilder()
        old.add_section('.foo', 'SHT_PROGBITS', b'a' * 10)
        old.add_section('.foo', 'SHT_PROGBITS', b'b' * 10)
        new = ElfBuilder()
        new.add_section('.foo', 'SHT_PROGBITS', b'a' * 20)
        new.add_section('.foo', 'SHT_PROGBITS', b'b' * 30)

        report = compare_accounting(self.account(old), self.account(new))
        foo = self._only_diff(report, '.foo')
        self.assertFalse(foo.paired)
        self.assertEqual((foo.old_count, foo.new_count), (2, 2))
        self.assertEqual(foo.disk_delta, 30)
        self.assertEqual(len(foo.old), 2)

    def test_group_deltas_add_up_to_totals_without_overlap(self):
        old = ElfBuilder()
        old.add_section('.text', 'SHT_PROGBITS', b'a' * 100)
        old.add_nobits('.bss', 500)
        new = ElfBuilder()
        new.add_section('.text', 'SHT_PROGBITS', b'a' * 160)
        new.add_nobits('.bss', 700)

        report = compare_accounting(self.account(old), self.account(new))
        # Per-section/group deltas sum to the declared totals exactly; the
        # unique-byte totals differ only by overlap claims.
        self.assertEqual(
            sum(diff.disk_delta for diff in report.pair_diffs),
            report.disk_declared_delta,
        )
        self.assertEqual(
            sum(diff.expanded_delta for diff in report.pair_diffs),
            report.expanded_delta,
        )
        self.assertEqual(
            report.disk_declared_delta,
            report.disk_delta + report.overlap_delta,
        )
        self.assertEqual(report.disk_declared_delta, 60)
        self.assertEqual(report.overlap_delta, 0)
        self.assertEqual(report.expanded_delta, 260)
        self.assertEqual(report.file_size_delta, report.disk_delta + report.gap_delta)

    def test_gap_growth_isolated_from_section_sizes(self):
        old = ElfBuilder()
        old.add_section('.pad', 'SHT_PROGBITS', b'p' * 10, addralign=1)
        old.add_section('.text', 'SHT_PROGBITS', b'a' * 100, addralign=1)
        old_data = old.build()

        new = ElfBuilder()
        new.add_section('.pad', 'SHT_PROGBITS', b'p' * 10, addralign=1)
        new.add_section('.text', 'SHT_PROGBITS', b'a' * 100, addralign=64)
        new_data = new.build()

        self.assertGreater(len(new_data), len(old_data))
        with open_elf(old_data) as old_elf, open_elf(new_data) as new_elf:
            report = compare_files(old_elf, new_elf, 'old', 'new')
        self.assertEqual(report.disk_delta, 0)
        self.assertGreater(report.gap_delta, 0)
        self.assertEqual(report.file_size_delta, report.gap_delta)
        text = self._only_diff(report, '.text')
        self.assertEqual(text.disk_delta, 0)

    def test_identical_files_have_no_deltas(self):
        builder = ElfBuilder()
        builder.add_section('.text', 'SHT_PROGBITS', b'a' * 100)
        builder.add_nobits('.bss', 40)
        data = builder.build()

        with open_elf(data) as first, open_elf(data) as second:
            report = compare_files(first, second)
        self.assertEqual(report.file_size_delta, 0)
        self.assertEqual(report.disk_declared_delta, 0)
        self.assertEqual(report.overlap_delta, 0)
        self.assertEqual(report.disk_delta, 0)
        self.assertEqual(report.gap_delta, 0)
        self.assertEqual(report.expanded_delta, 0)
        self.assertTrue(
            all(
                diff.disk_delta == 0 and diff.expanded_delta == 0
                for diff in report.pair_diffs
            )
        )

    def test_real_compressed_object_roundtrip(self):
        path = 'test/testfiles_for_unittests/compressed_64.o'
        with open(path, 'rb') as stream:
            accounting = account_file(ELFFile(stream), path)
        debug_info = next(
            record for record in accounting.sections if record.name == '.debug_info'
        )
        self.assertTrue(debug_info.compressed)
        self.assertGreater(debug_info.expanded_size, debug_info.disk_size)
        self.assertEqual(
            accounting.disk_declared,
            accounting.disk_covered + accounting.overlap_size,
        )
        self.assertEqual(
            accounting.disk_covered + accounting.gap_size, accounting.file_size
        )

    def _only_diff(self, report, name):
        matches = [diff for diff in report.pair_diffs if diff.name == name]
        self.assertEqual(len(matches), 1)
        return matches[0]


if __name__ == '__main__':
    unittest.main()
