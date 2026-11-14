#-------------------------------------------------------------------------------
# elftools: elf/notes.py
#
# ELF notes
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING

from ..common.utils import bytes2str, roundup, struct_parse
from ..construct import CString
from .enums import ENUM_ANDROID_NOTE_N_TYPE, ENUM_CORE_NOTE_N_TYPE, ENUM_NOTE_N_TYPE

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ..construct.lib.container import Container
    from .elffile import ELFFile


# Note type numbers have meaning only within their owner's namespace.
_NOTE_TYPE_NAMES = {
    owner: {value: name for name, value in enum.items() if name != '_default_'}
    for owner, enum in (
        ('GNU', ENUM_NOTE_N_TYPE),
        ('Android', ENUM_ANDROID_NOTE_N_TYPE),
    )
}
_CORE_NOTE_TYPE_NAMES = {
    value: name for name, value in ENUM_CORE_NOTE_N_TYPE.items() if name != '_default_'
}


def iter_notes(elffile: ELFFile, offset: int, size: int) -> Iterator[Container]:
    """ Yield complete notes within a section or segment.

        n_type is a symbolic name for known types in the owner's namespace,
        or the numeric type for unknown types and owners.
    """
    end = offset + size
    nhdr_size = elffile.structs.Elf_Nhdr.sizeof()
    # Note: a note's name and data are 4-byte aligned, but it's possible there's
    # additional padding at the end to satisfy the alignment requirement of the segment.
    while offset + nhdr_size <= end:
        note: Container = struct_parse(
            elffile.structs.Elf_Nhdr,
            elffile.stream,
            stream_pos=offset)
        disk_namesz = roundup(note['n_namesz'], 2)
        disk_descsz = roundup(note['n_descsz'], 2)
        note_end = offset + nhdr_size + disk_namesz + disk_descsz
        # Validate the whole note, including padding, before reading its body.
        if note_end > end:
            break

        name_data = elffile.stream.read(disk_namesz)
        if len(name_data) != disk_namesz:
            break
        if note['n_namesz']:
            note['n_name'] = bytes2str(CString('').parse(name_data))
        else:
            note['n_name'] = None

        note_types = _NOTE_TYPE_NAMES.get(note['n_name'], {})
        if note['n_name'] == 'CORE' and elffile['e_type'] == 'ET_CORE':
            note_types = _CORE_NOTE_TYPE_NAMES
        note['n_type'] = note_types.get(note['n_type'], note['n_type'])

        desc_data = elffile.stream.read(disk_descsz)
        if len(desc_data) != disk_descsz:
            break
        desc_data = desc_data[:note['n_descsz']]
        note['n_descdata'] = desc_data
        # Structured decoders must stay within this descriptor, even when its
        # contents are malformed and more bytes follow in the file.
        if note['n_type'] == 'NT_GNU_ABI_TAG':
            note['n_desc'] = struct_parse(elffile.structs.Elf_abi,
                                          BytesIO(desc_data))
        elif note['n_type'] == 'NT_GNU_BUILD_ID':
            note['n_desc'] = bytes(desc_data).hex()
        elif note['n_type'] == 'NT_GNU_GOLD_VERSION':
            note['n_desc'] = bytes2str(desc_data)
        elif note['n_type'] == 'NT_PRPSINFO':
            note['n_desc'] = struct_parse(elffile.structs.Elf_Prpsinfo,
                                          BytesIO(desc_data))
        elif note['n_type'] == 'NT_FILE':
            note['n_desc'] = struct_parse(elffile.structs.Elf_Nt_File,
                                          BytesIO(desc_data))
        elif note['n_type'] == 'NT_GNU_PROPERTY_TYPE_0':
            off = 0
            desc_stream = BytesIO(desc_data)
            props: list[Container] = []
            while off < len(desc_data):
                p: Container = struct_parse(elffile.structs.Elf_Prop, desc_stream, off)
                off += roundup(p.pr_datasz + 8, 2 if elffile.elfclass == 32 else 3)
                props.append(p)
            note['n_desc'] = props
        else:
            note['n_desc'] = desc_data
        note['n_offset'] = offset
        note['n_size'] = note_end - offset
        offset = note_end
        yield note


@dataclass(frozen=True)
class NoteSource:
    """ A single section or segment entry point that exposes a note.

        kind is 'section' or 'segment'. index is the section or segment
        index in the corresponding header table, and name is the section
        name (None for segments). The source spans [offset, offset + size)
        in the file.
    """
    kind: str
    index: int
    name: str | None
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def label(self) -> str:
        if self.kind == 'section':
            return f"section #{self.index} ({self.name})"
        return f"segment #{self.index}"


@dataclass
class NoteRecord:
    """ One physical note record together with every entry point covering it.

        note holds the parsed note container (as produced by iter_notes);
        its n_offset/n_size fields describe the on-disk record including
        alignment padding. sources lists the section/segment entry points
        whose ranges fully contain this record. A record reachable from
        more than one source is a single physical record seen through
        several entry points, not several notes.
    """
    note: Container
    sources: list[NoteSource] = field(default_factory=list)

    @property
    def offset(self) -> int:
        return self.note['n_offset']

    @property
    def size(self) -> int:
        return self.note['n_size']

    @property
    def end(self) -> int:
        return self.offset + self.size

    @property
    def is_duplicate_entry(self) -> bool:
        """ True when more than one entry point fully covers this record. """
        return len(self.sources) > 1


class NoteLedger:
    """ Reconciled inventory of the notes reachable from note sections and
        note segments.

        A PT_NOTE segment typically covers the same file bytes as one or
        more SHT_NOTE sections, so iterating both tables reports the same
        physical record several times. The ledger merges entry points that
        point at the same physical record while keeping distinct records
        apart, including records that happen to have identical contents but
        live at different file offsets.

        Only entry points whose range fully contains a record count as
        sources for it: an entry point that overlaps just part of a record
        (for example a truncated section/segment ending inside it) cannot
        have read that record completely, so it neither merges with nor
        suppresses the record read fully through another entry point.
    """

    def __init__(self, elffile: ELFFile) -> None:
        self._elffile = elffile
        self._sources = self._collect_sources(elffile)
        self._records: list[NoteRecord] = []
        # Keyed by the absolute file offset of the physical record.
        self._by_offset: dict[int, NoteRecord] = {}
        self._build()

    @staticmethod
    def _collect_sources(elffile: ELFFile) -> list[NoteSource]:
        # Sections first, then segments, so the inventory reflects the
        # section table before the segment table; records themselves are
        # ordered by file offset.
        from .sections import NoteSection
        from .segments import NoteSegment

        sources: list[NoteSource] = []
        for index, section in enumerate(elffile.iter_sections()):
            if isinstance(section, NoteSection):
                sources.append(NoteSource(
                    kind='section',
                    index=index,
                    name=section.name,
                    offset=section['sh_offset'],
                    size=section['sh_size']))
        for index, segment in enumerate(elffile.iter_segments()):
            if isinstance(segment, NoteSegment):
                sources.append(NoteSource(
                    kind='segment',
                    index=index,
                    name=None,
                    offset=segment['p_offset'],
                    size=segment['p_filesz']))
        return sources

    def _build(self) -> None:
        for source in self._sources:
            for note in iter_notes(self._elffile, source.offset, source.size):
                record = self._by_offset.get(note['n_offset'])
                if record is None:
                    record = NoteRecord(note=note, sources=[source])
                    self._by_offset[note['n_offset']] = record
                    self._records.append(record)
                else:
                    # iter_notes validates the whole record, including its
                    # alignment padding, before yielding it, so a yield means
                    # this source covers the record fully. Guard the
                    # assumption anyway against malformed metadata.
                    if not self._contains(source, record):
                        continue
                    if source not in record.sources:
                        record.sources.append(source)
        self._records.sort(key=lambda item: item.offset)

    @staticmethod
    def _contains(source: NoteSource, record: NoteRecord) -> bool:
        return (source.offset <= record.offset and
                record.end <= source.end)

    @property
    def sources(self) -> Sequence[NoteSource]:
        """ All note section/segment entry points, sections first. """
        return self._sources

    def records(self) -> list[NoteRecord]:
        """ Every distinct physical note record, ordered by file offset. """
        return list(self._records)

    def __iter__(self) -> Iterator[NoteRecord]:
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)


def collect_note_ledger(elffile: ELFFile) -> NoteLedger:
    """ Build a reconciled note ledger from both header tables.

        See NoteLedger for the merging rules: entry points covering the same
        physical record are merged, identical records at different offsets
        are kept separate, and partial coverage never hides a record that
        another entry point read in full.
    """
    return NoteLedger(elffile)
