# -------------------------------------------------------------------------------
# elftools: elf/sizediff.py
#
# Size accounting and pairwise comparison of ELF files.
#
# Two notions of size are kept apart:
#   * on-disk size: bytes actually occupied inside the file (SHT_NOBITS
#     sections occupy none), with overlapping section ranges merged so that
#     every file byte is counted at most once;
#   * expanded size: the logical size of the section data, using the
#     decompressed size (ch_size) for SHF_COMPRESSED sections instead of their
#     on-disk sh_size. SHT_NOBITS sections do contribute here, since they
#     reserve address space when the image is loaded.
#
# Sections with the same name are paired between two files only when their
# type and their associated sections (sh_link / sh_info, resolved to names)
# identify a unique counterpart. Ambiguous groups are reported as a whole.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
# -------------------------------------------------------------------------------
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .elffile import ELFFile


@dataclass(frozen=True)
class SectionRecord:
    """Size accounting information for a single section."""

    name: str
    index: int
    type: str | int
    link: str | int
    info: str | int
    offset: int
    sh_size: int
    data_size: int
    compressed: bool
    nobits: bool

    @property
    def disk_size(self) -> int:
        """Bytes this section occupies in the file (before overlap merging)."""
        if self.nobits or self.type == 'SHT_NULL':
            return 0
        return self.sh_size

    @property
    def expanded_size(self) -> int:
        """Logical size of the section data, decompressed when compressed."""
        if self.type == 'SHT_NULL':
            return 0
        return self.data_size


@dataclass
class FileAccounting:
    """Size accounting for one ELF file."""

    path: str
    file_size: int
    sections: list[SectionRecord] = field(default_factory=list)
    # Sum of per-section on-disk claims; bytes claimed by several sections
    # are counted once per claim.
    disk_declared: int = 0
    # Bytes claimed by non-NOBITS sections, after merging overlaps: every
    # file byte is counted at most once.
    disk_covered: int = 0
    # Sum of logical section sizes (NOBITS included, compressed expanded).
    expanded_total: int = 0
    # Bytes inside the file that no section claims: headers, padding, gaps.
    unclaimed: int = 0

    @property
    def gap_size(self) -> int:
        """File bytes not covered by any section (headers and inter-section
        gaps included).
        """
        return self.unclaimed

    @property
    def overlap_size(self) -> int:
        """Bytes declared more than once by overlapping sections."""
        return self.disk_declared - self.disk_covered


@dataclass(frozen=True)
class PairDiff:
    """Size difference for a uniquely paired section or an ambiguous group."""

    name: str
    old_count: int
    new_count: int
    disk_delta: int
    expanded_delta: int
    paired: bool
    old: tuple[SectionRecord, ...] = ()
    new: tuple[SectionRecord, ...] = ()


@dataclass
class SizeReport:
    """Result of comparing two files."""

    old: FileAccounting
    new: FileAccounting
    pair_diffs: list[PairDiff]

    @property
    def file_size_delta(self) -> int:
        return self.new.file_size - self.old.file_size

    @property
    def disk_delta(self) -> int:
        return self.new.disk_covered - self.old.disk_covered

    @property
    def disk_declared_delta(self) -> int:
        return self.new.disk_declared - self.old.disk_declared

    @property
    def overlap_delta(self) -> int:
        return self.new.overlap_size - self.old.overlap_size

    @property
    def gap_delta(self) -> int:
        return self.new.gap_size - self.old.gap_size

    @property
    def expanded_delta(self) -> int:
        return self.new.expanded_total - self.old.expanded_total


def account_file(elffile: ELFFile, path: str = '') -> FileAccounting:
    """Build a :class:`FileAccounting` from an open :class:`ELFFile`."""
    records = [_record_for(elffile, index) for index in range(elffile.num_sections())]
    disk_declared = sum(record.disk_size for record in records)
    disk_covered = _merged_span(records)
    expanded_total = sum(record.expanded_size for record in records)
    file_size = elffile.stream_len
    accounting = FileAccounting(
        path=path,
        file_size=file_size,
        sections=records,
        disk_declared=disk_declared,
        disk_covered=disk_covered,
        expanded_total=expanded_total,
        unclaimed=max(0, file_size - disk_covered),
    )
    return accounting


def compare_files(
    old_file: ELFFile, new_file: ELFFile, old_path: str = '', new_path: str = ''
) -> SizeReport:
    """Compare two open ELF files and return a :class:`SizeReport`."""
    old_account = account_file(old_file, old_path)
    new_account = account_file(new_file, new_path)
    return compare_accounting(old_account, new_account)


def compare_accounting(
    old_account: FileAccounting, new_account: FileAccounting
) -> SizeReport:
    """Compare two :class:`FileAccounting` objects built separately."""
    old_groups = _group_records(old_account.sections)
    new_groups = _group_records(new_account.sections)
    pair_diffs: list[PairDiff] = []

    for key in sorted(set(old_groups) | set(new_groups), key=_key_sort_key):
        old_records = old_groups.get(key, ())
        new_records = new_groups.get(key, ())
        paired = len(old_records) == 1 and len(new_records) == 1
        pair_diffs.append(
            PairDiff(
                name=key[0],
                old_count=len(old_records),
                new_count=len(new_records),
                disk_delta=(
                    sum(record.disk_size for record in new_records)
                    - sum(record.disk_size for record in old_records)
                ),
                expanded_delta=(
                    sum(record.expanded_size for record in new_records)
                    - sum(record.expanded_size for record in old_records)
                ),
                paired=paired,
                old=tuple(old_records),
                new=tuple(new_records),
            )
        )

    return SizeReport(old_account, new_account, pair_diffs)


def _record_for(elffile: ELFFile, index: int) -> SectionRecord:
    section = elffile.get_section(index)
    section_type = section['sh_type']
    return SectionRecord(
        name=section.name,
        index=index,
        type=section_type,
        link=_resolve_association(elffile, section['sh_link']),
        info=_resolve_association(elffile, section['sh_info']),
        offset=section['sh_offset'],
        sh_size=section['sh_size'],
        data_size=section.data_size,
        compressed=bool(section.compressed),
        nobits=section_type == 'SHT_NOBITS',
    )


def _resolve_association(elffile: ELFFile, index: int) -> str | int:
    """Resolve a sh_link/sh_info section index to the target section name;
    fall back to the raw index when it does not point at a section.
    """
    if 0 < index < elffile.num_sections():
        return elffile.get_section(index).name
    return index


def _merged_span(records: list[SectionRecord]) -> int:
    """Total number of distinct file bytes claimed by sections, merging
    overlapping or adjacent ranges. NOBITS and NULL sections contribute
    nothing.
    """
    intervals = sorted(
        (record.offset, record.offset + record.disk_size)
        for record in records
        if record.disk_size > 0
    )
    total = 0
    current_start = 0
    current_end = 0
    for start, end in intervals:
        if start >= current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        elif end > current_end:
            current_end = end
    total += current_end - current_start
    return total


def _group_records(
    records: list[SectionRecord],
) -> dict[tuple[str, str | int, str | int, str | int], tuple[SectionRecord, ...]]:
    """Group sections by (name, type, link, info). These are the attributes
    that are allowed to identify a matching section across files.
    """
    groups: dict[tuple[str, str | int, str | int, str | int], list[SectionRecord]] = {}
    for record in records:
        if record.type == 'SHT_NULL':
            continue
        key = (record.name, record.type, record.link, record.info)
        groups.setdefault(key, []).append(record)
    return {key: tuple(group) for key, group in groups.items()}


def _key_sort_key(
    key: tuple[str, str | int, str | int, str | int],
) -> tuple[str, str, str]:
    name, section_type, link, info = key
    return (name, str(section_type), f'{link!r}:{info!r}')
