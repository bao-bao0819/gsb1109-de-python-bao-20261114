#-------------------------------------------------------------------------------
# elftools: elf/addressmap.py
#
# Mapping sampled virtual address ranges back to their provenance in an ELF
# file: file-backed bytes of PT_LOAD segments, memory-only zero fill, or
# unmapped address holes.
#
# This code is in the public domain
#-------------------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .segments import Segment

# Kinds of provenance reported by LoadSegmentMap.query.
KIND_FILE = 'file'            # backed by file bytes of one or more segments
KIND_ZERO_FILL = 'zero_fill'  # memory-only zero bytes (p_memsz > p_filesz)
KIND_UNMAPPED = 'unmapped'    # address hole: no PT_LOAD covers it
KIND_MIXED = 'mixed'          # overlapping segments disagree (file + zero fill)


class FileBytesSource(NamedTuple):
    """ One candidate mapping of an address interval to bytes of the ELF
        file. ``file_offset`` is the offset of the first byte of the
        interval; the interval's bytes are
        ``[file_offset, file_offset + size)`` in the file.
    """
    segment_index: int  # index in the ELF program header table
    file_offset: int
    size: int


class ZeroFillSource(NamedTuple):
    """ One candidate mapping of an address interval to memory-only zero
        fill contributed by a PT_LOAD segment (the part of the segment
        beyond p_filesz).
    """
    segment_index: int  # index in the ELF program header table
    size: int


class AddressProvenance(NamedTuple):
    """ Provenance of the half-open address interval [start, end).

        ``kind`` is one of the KIND_* constants. When several PT_LOAD
        segments cover the same addresses, all candidate sources are listed
        (sorted by segment index); none is preferred over the others.
    """
    start: int
    end: int
    kind: str
    file_sources: tuple[FileBytesSource, ...]
    zero_fill_sources: tuple[ZeroFillSource, ...]


class _Load(NamedTuple):
    vaddr: int
    file_end: int   # vaddr + min(p_filesz, p_memsz)
    mem_end: int    # vaddr + p_memsz
    offset: int     # p_offset
    index: int      # program header index


class LoadSegmentMap:
    """ Provenance map built from the PT_LOAD segments of an ELF file.

        Addresses are virtual addresses (p_vaddr). Intervals are half-open:
        a record [start, end) describes addresses start <= a < end.

        Queries are partition-consistent: the provenance reported for any
        address never depends on how a sampled range is split into pieces.
        Records are emitted at canonical boundaries (segment starts,
        file/zero-fill transitions and segment ends) and never merged, so
        concatenating adjacent queries may only refine records at the cut
        points; merge_provenance() normalizes such concatenations back to
        the canonical form:

            merge_provenance(query(a, b) + query(b, c)) == query(a, c)
    """

    def __init__(self, segments: Iterable[Segment]) -> None:
        self._loads: list[_Load] = []
        for index, segment in enumerate(segments):
            if segment['p_type'] != 'PT_LOAD':
                continue
            memsz = segment['p_memsz']
            if memsz == 0:
                continue
            # A malformed segment may claim p_filesz > p_memsz; only the
            # memory-resident part is observable.
            filesz = min(segment['p_filesz'], memsz)
            vaddr = segment['p_vaddr']
            self._loads.append(_Load(
                vaddr=vaddr,
                file_end=vaddr + filesz,
                mem_end=vaddr + memsz,
                offset=segment['p_offset'],
                index=index))
        self._loads.sort(key=lambda load: (load.vaddr, load.index))

    def query(self, start: int, end: int) -> list[AddressProvenance]:
        """ Return the provenance records covering [start, end), in address
            order. The records tile the queried range exactly.
        """
        if start < 0 or end < 0:
            raise ValueError('addresses must be non-negative')
        if end < start:
            raise ValueError('empty or negative range: end < start')
        if start == end:
            return []

        points = {start, end}
        for load in self._loads:
            for boundary in (load.vaddr, load.file_end, load.mem_end):
                if start < boundary < end:
                    points.add(boundary)
        ordered = sorted(points)

        records = []
        for lo, hi in zip(ordered, ordered[1:]):
            file_sources = []
            zero_fill_sources = []
            for load in self._loads:
                if load.vaddr <= lo < load.mem_end:
                    if lo < load.file_end:
                        file_sources.append(FileBytesSource(
                            segment_index=load.index,
                            file_offset=load.offset + (lo - load.vaddr),
                            size=hi - lo))
                    else:
                        zero_fill_sources.append(ZeroFillSource(
                            segment_index=load.index,
                            size=hi - lo))
            if file_sources and zero_fill_sources:
                kind = KIND_MIXED
            elif file_sources:
                kind = KIND_FILE
            elif zero_fill_sources:
                kind = KIND_ZERO_FILL
            else:
                kind = KIND_UNMAPPED
            records.append(AddressProvenance(
                start=lo,
                end=hi,
                kind=kind,
                file_sources=tuple(file_sources),
                zero_fill_sources=tuple(zero_fill_sources)))
        return records


def _mergeable(left: AddressProvenance, right: AddressProvenance) -> bool:
    if left.kind != right.kind or left.end != right.start:
        return False
    if len(left.file_sources) != len(right.file_sources):
        return False
    if len(left.zero_fill_sources) != len(right.zero_fill_sources):
        return False
    for left_src, right_src in zip(left.file_sources, right.file_sources):
        if (left_src.segment_index != right_src.segment_index or
                left_src.file_offset + left_src.size != right_src.file_offset):
            return False
    for left_src, right_src in zip(left.zero_fill_sources,
                                   right.zero_fill_sources):
        if left_src.segment_index != right_src.segment_index:
            return False
    return True


def merge_provenance(
        records: Iterable[AddressProvenance]) -> list[AddressProvenance]:
    """ Coalesce adjacent records (in address order) that describe the same
        provenance. Used to normalize the concatenation of queries over
        adjacent ranges, whose cut points may split a canonical record in
        two. Records that are not adjacent, or whose sources differ, are
        kept apart.
    """
    merged: list[AddressProvenance] = []
    for record in records:
        if merged and _mergeable(merged[-1], record):
            left = merged[-1]
            merged[-1] = AddressProvenance(
                start=left.start,
                end=record.end,
                kind=left.kind,
                file_sources=tuple(
                    FileBytesSource(
                        segment_index=left_src.segment_index,
                        file_offset=left_src.file_offset,
                        size=left_src.size + right_src.size)
                    for left_src, right_src in zip(left.file_sources,
                                                   record.file_sources)),
                zero_fill_sources=tuple(
                    ZeroFillSource(
                        segment_index=left_src.segment_index,
                        size=left_src.size + right_src.size)
                    for left_src, right_src in zip(left.zero_fill_sources,
                                                   record.zero_fill_sources)))
        else:
            merged.append(record)
    return merged
