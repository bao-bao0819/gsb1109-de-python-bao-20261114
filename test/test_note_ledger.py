"""Tests for the reconciled note ledger (NoteLedger / collect_note_ledger).

The ledger merges note section and note segment entry points that point at
the same physical note record, while keeping records that merely have
identical contents at different offsets apart. Entry points that only cover
part of a record must not be treated as complete copies of it.
"""
import os
import struct
import unittest
from io import BytesIO

from elftools.common.utils import roundup
from elftools.elf.elffile import ELFFile
from elftools.elf.notes import (
    NoteLedger,
    NoteRecord,
    NoteSource,
    collect_note_ledger,
)

SHT_NULL = 0
SHT_STRTAB = 3
SHT_NOTE = 7
PT_NOTE = 4
PF_R = 4

# Note blobs live at a fixed, 8-aligned file offset so segment headers in the
# tests can address them independently of the program header table size.
DATA_BASE = 0x1000


def make_note(name, ntype, desc):
    """ Build the on-disk bytes of one note, including 4-byte padding. """
    raw_name = name + b'\x00'
    disk_name = raw_name.ljust(roundup(len(raw_name), 2), b'\x00')
    disk_desc = desc.ljust(roundup(len(desc), 2), b'\x00')
    return (struct.pack('<III', len(raw_name), len(desc), ntype)
            + disk_name + disk_desc)


def build_elf(note_sections, segments):
    """ Assemble a minimal little-endian ELF64 file.

        note_sections is a list of (section_name, blob) or
        (section_name, blob, declared_size) tuples; each blob is placed in
        its own SHT_NOTE section, one after another. declared_size overrides
        the section header size and lets a section claim a range shorter than
        the bytes on disk (a truncated entry point). segments is a list of
        (offset, filesz) PT_NOTE program headers and may describe arbitrary,
        including truncated or duplicated, file ranges.
    """
    ehsize = 64
    phentsize = 56
    shentsize = 64
    phoff = ehsize
    cursor = DATA_BASE
    chunks = []
    section_offsets = []
    section_sizes = []

    def place(data):
        nonlocal cursor
        cursor = roundup(cursor, 3)
        offset = cursor
        chunks.append((offset, data))
        cursor += len(data)
        return offset

    for entry in note_sections:
        section_name, blob = entry[0], entry[1]
        declared_size = entry[2] if len(entry) > 2 else len(blob)
        section_offsets.append(place(blob))
        section_sizes.append(declared_size)

    shstrtab = b'\x00'
    section_name_offsets = []
    for entry in note_sections:
        section_name = entry[0]
        section_name_offsets.append(len(shstrtab))
        shstrtab += section_name.encode() + b'\x00'
    shstrtab_offset = place(shstrtab)

    shnum = len(note_sections) + 2
    shstrndx = shnum - 1
    shoff = roundup(cursor, 3)

    e_ident = b'\x7fELF' + bytes([2, 1, 1, 0]) + b'\x00' * 8
    ehdr = struct.pack('<16sHHIQQQIHHHHHH', e_ident, 2, 62, 1, 0,
                       phoff, shoff, 0, ehsize, phentsize, len(segments),
                       shentsize, shnum, shstrndx)
    phdrs = b''.join(
        struct.pack('<IIQQQQQQ', PT_NOTE, PF_R, offset, 0, 0, size, size, 4)
        for offset, size in segments)

    shdrs = [struct.pack('<IIQQQQIIQQ', 0, SHT_NULL, 0, 0, 0, 0, 0, 0, 0, 0)]
    for entry, offset, name_offset, declared_size in zip(
            note_sections, section_offsets, section_name_offsets,
            section_sizes):
        shdrs.append(struct.pack('<IIQQQQIIQQ', name_offset, SHT_NOTE, 0, 0,
                                 offset, declared_size, 0, 0, 4, 0))
    shdrs.append(struct.pack('<IIQQQQIIQQ', 0, SHT_STRTAB, 0, 0,
                             shstrtab_offset, len(shstrtab), 0, 0, 1, 0))

    image = bytearray(shoff + shentsize * shnum)
    image[0:len(ehdr)] = ehdr
    image[phoff:phoff + len(phdrs)] = phdrs
    for offset, data in chunks:
        image[offset:offset + len(data)] = data
    for index, shdr in enumerate(shdrs):
        offset = shoff + index * shentsize
        image[offset:offset + len(shdr)] = shdr
    return bytes(image), section_offsets


def ledger_for(note_sections, segments):
    data, _ = build_elf(note_sections, segments)
    return collect_note_ledger(ELFFile(BytesIO(data)))


class TestNoteLedger(unittest.TestCase):
    def test_merges_section_and_segment_pointing_at_same_record(self):
        note = make_note(b'GNU', 3, b'\x11' * 8)
        ledger = ledger_for([('.note.gnu.build-id', note)],
                            [(DATA_BASE, len(note))])
        self.assertEqual(len(ledger), 1)
        record = ledger.records()[0]
        self.assertEqual(record.offset, DATA_BASE)
        self.assertEqual(record.size, len(note))
        self.assertTrue(record.is_duplicate_entry)
        self.assertEqual([source.kind for source in record.sources],
                         ['section', 'segment'])
        self.assertEqual(record.sources[0].name, '.note.gnu.build-id')
        self.assertIsNone(record.sources[1].name)

    def test_segment_spanning_several_sections_merges_each_record(self):
        note_a = make_note(b'GNU', 1, struct.pack('<IIII', 0, 2, 6, 4))
        note_b = make_note(b'SuSE', 0x45434143, b'\x01\x00\x0a\x02')
        note_c = make_note(b'GNU', 3, b'\xab' * 20)
        blob = note_a + note_b + note_c
        base = DATA_BASE
        ledger = ledger_for(
            [('.note.ABI-tag', note_a),
             ('.note.SuSE', note_b),
             ('.note.gnu.build-id', note_c)],
            [(base, len(blob))])
        self.assertEqual(len(ledger), 3)
        offsets = [base, base + len(note_a), base + len(note_a) + len(note_b)]
        self.assertEqual([record.offset for record in ledger], offsets)
        for record in ledger:
            self.assertEqual(len(record.sources), 2)
            self.assertEqual({source.kind for source in record.sources},
                             {'section', 'segment'})

    def test_identical_records_at_different_offsets_stay_separate(self):
        note = make_note(b'GNU', 0x100, b'\xde\xad\xbe\xef')
        blob = note + note
        base = DATA_BASE
        ledger = ledger_for([('.note.dup', blob)],
                            [(base, len(blob))])
        self.assertEqual(len(ledger), 2)
        first, second = ledger.records()
        self.assertEqual(first.offset, base)
        self.assertEqual(second.offset, base + len(note))
        self.assertNotEqual(first.offset, second.offset)
        self.assertEqual(first.note['n_descdata'], second.note['n_descdata'])
        self.assertEqual(first.note['n_namesz'], second.note['n_namesz'])
        self.assertEqual(first.note['n_descsz'], second.note['n_descsz'])
        self.assertEqual(first.note['n_type'], second.note['n_type'])

    def test_truncated_segment_keeps_record_read_fully_by_section(self):
        note_a = make_note(b'GNU', 3, b'\x11' * 8)
        note_b = make_note(b'AB', 0x100, b'\x22' * 5)
        blob = note_a + note_b
        base = DATA_BASE
        # The segment ends inside note B, so it can only have read note A.
        truncated_size = len(note_a) + 8
        self.assertLess(truncated_size, len(blob))
        ledger = ledger_for([('.note', blob)],
                            [(base, truncated_size)])
        self.assertEqual(len(ledger), 2)
        record_a, record_b = ledger.records()

        self.assertEqual(record_a.offset, base)
        self.assertEqual(len(record_a.sources), 2)
        self.assertEqual({source.kind for source in record_a.sources},
                         {'section', 'segment'})

        # The truncated tail stays with the section that read it in full;
        # the segment must not swallow or suppress note B.
        self.assertEqual(record_b.offset, base + len(note_a))
        self.assertEqual(len(record_b.sources), 1)
        self.assertEqual(record_b.sources[0].kind, 'section')
        self.assertEqual(record_b.note['n_descdata'], b'\x22' * 5)

    def test_segment_truncated_inside_first_record_does_not_hide_it(self):
        note = make_note(b'GNU', 3, b'\x33' * 16)
        base = DATA_BASE
        # The segment does not even cover the whole first record.
        ledger = ledger_for([('.note', note)], [(base, 8)])
        self.assertEqual(len(ledger), 1)
        record = ledger.records()[0]
        self.assertEqual(record.offset, base)
        self.assertEqual(len(record.sources), 1)
        self.assertEqual(record.sources[0].kind, 'section')

    def test_truncated_section_tail_stays_with_full_segment(self):
        note_a = make_note(b'GNU', 3, b'\x11' * 8)
        note_b = make_note(b'AB', 0x100, b'\x22' * 5)
        blob = note_a + note_b
        base = DATA_BASE
        # Symmetric to the truncated-segment case: the section ends inside
        # note B, while the segment covers both records completely.
        truncated_section_size = len(note_a) + 8
        ledger = ledger_for(
            [('.note', blob, truncated_section_size)],
            [(base, len(blob))])
        self.assertEqual(len(ledger), 2)
        record_a, record_b = ledger.records()

        self.assertEqual(record_a.offset, base)
        self.assertEqual(len(record_a.sources), 2)

        # The incomplete tail belongs to the segment that read it in full;
        # the short section does not hide note B.
        self.assertEqual(record_b.offset, base + len(note_a))
        self.assertEqual(len(record_b.sources), 1)
        self.assertEqual(record_b.sources[0].kind, 'segment')
        self.assertEqual(record_b.note['n_descdata'], b'\x22' * 5)

    def test_section_truncated_inside_first_record_does_not_hide_it(self):
        note = make_note(b'GNU', 3, b'\x33' * 16)
        base = DATA_BASE
        ledger = ledger_for(
            [('.note', note, 8)],
            [(base, len(note))])
        self.assertEqual(len(ledger), 1)
        record = ledger.records()[0]
        self.assertEqual(record.offset, base)
        self.assertEqual(len(record.sources), 1)
        self.assertEqual(record.sources[0].kind, 'segment')

    def test_trailing_segment_padding_does_not_break_merge(self):
        path = os.path.join(
            'test', 'testfiles_for_unittests',
            'note_with_segment_padding', 'main.elf')
        with ELFFile.load_from_path(path) as elf:
            ledger = collect_note_ledger(elf)
        self.assertEqual(len(ledger), 1)
        record = ledger.records()[0]
        # The record is 20 bytes; the entry points reserve 24 (4 bytes of
        # 8-byte-alignment padding after the note).
        self.assertEqual(record.size, 20)
        for source in record.sources:
            self.assertEqual(source.size, 24)
            self.assertLessEqual(record.end, source.end)
        self.assertEqual(len(record.sources), 2)

    def test_duplicate_segments_all_become_sources(self):
        note = make_note(b'GNU', 3, b'\x44' * 4)
        ledger = ledger_for([('.note', note)],
                            [(DATA_BASE, len(note)), (DATA_BASE, len(note))])
        self.assertEqual(len(ledger), 1)
        record = ledger.records()[0]
        self.assertEqual(len(record.sources), 3)
        self.assertEqual([source.kind for source in record.sources],
                         ['section', 'segment', 'segment'])
        self.assertEqual(len({(source.kind, source.index)
                              for source in record.sources}), 3)

    def test_sources_listed_sections_first(self):
        note_a = make_note(b'GNU', 3, b'\x55' * 4)
        note_b = make_note(b'AB', 1, b'\x66' * 4)
        ledger = ledger_for([('.note.a', note_a), ('.note.b', note_b)],
                            [(DATA_BASE, len(note_a) + len(note_b))])
        kinds = [source.kind for source in ledger.sources]
        self.assertEqual(kinds, ['section', 'section', 'segment'])
        self.assertEqual([source.index for source in ledger.sources],
                         [1, 2, 0])

    def test_no_note_entry_points_gives_empty_ledger(self):
        ledger = ledger_for([], [])
        self.assertEqual(len(ledger), 0)
        self.assertEqual(ledger.records(), [])
        self.assertEqual(list(ledger), [])
        self.assertEqual(list(ledger.sources), [])

    def test_source_and_record_helpers(self):
        source = NoteSource(kind='section', index=2, name='.note.x',
                            offset=100, size=40)
        self.assertEqual(source.end, 140)
        self.assertEqual(source.label, 'section #2 (.note.x)')
        segment_source = NoteSource(kind='segment', index=5, name=None,
                                    offset=100, size=40)
        self.assertEqual(segment_source.label, 'segment #5')

        note = make_note(b'GNU', 3, b'\x77' * 4)
        data, offsets = build_elf([('.note.x', note)], [(DATA_BASE, len(note))])
        ledger = collect_note_ledger(ELFFile(BytesIO(data)))
        record = ledger.records()[0]
        self.assertIsInstance(record, NoteRecord)
        self.assertEqual(record.end, record.offset + record.size)
        self.assertEqual(record.offset, offsets[0])

    def test_sample_executable_build_id_is_not_double_counted(self):
        path = os.path.join('examples', 'sample_exe64.elf')
        with ELFFile.load_from_path(path) as elf:
            ledger = collect_note_ledger(elf)
        build_ids = [
            record for record in ledger
            if record.note['n_type'] == 'NT_GNU_BUILD_ID']
        self.assertEqual(len(build_ids), 1)
        record = build_ids[0]
        self.assertEqual(len(record.sources), 2)
        # Three physical notes, each seen once through a section and once
        # through the single PT_NOTE segment that spans all three.
        self.assertEqual(len(ledger), 3)
        self.assertTrue(all(record.is_duplicate_entry for record in ledger))

    def test_core_file_records_have_segment_sources_only(self):
        path = os.path.join('test', 'testfiles_for_unittests',
                            'core_linux64.elf')
        with ELFFile.load_from_path(path) as elf:
            ledger = collect_note_ledger(elf)
        self.assertEqual(len(ledger.sources), 1)
        self.assertEqual(ledger.sources[0].kind, 'segment')
        self.assertEqual(len(ledger), 7)
        for record in ledger:
            self.assertEqual(len(record.sources), 1)
            self.assertFalse(record.is_duplicate_entry)

    def test_ledger_class_is_public_api(self):
        note = make_note(b'GNU', 3, b'\x88' * 4)
        data, _ = build_elf([('.note', note)], [(DATA_BASE, len(note))])
        ledger = NoteLedger(ELFFile(BytesIO(data)))
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger.records()[0].note['n_type'],
                         'NT_GNU_BUILD_ID')


if __name__ == '__main__':
    unittest.main()
