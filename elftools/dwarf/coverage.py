#-------------------------------------------------------------------------------
# elftools: dwarf/coverage.py
#
# Build code-address coverage from DWARF range lists.
#
# Linker -ffunction-sections / --gc-sections style layouts can scatter one
# compilation unit over several distant address regions, so a CU's coverage is
# a set of intervals rather than [low_pc, high_pc). This module turns the raw
# range lists from .debug_ranges (DWARF <= 4) and .debug_rnglists (DWARF 5)
# into resolved half-open intervals [begin_address, end_address) that carry
# their originating CU/DIE, the base address applied to relative entries, and
# the raw range-list entries they were decoded from.
#
# Relative entries are interpreted against the base address in force at their
# position in the list: the CU's DW_AT_low_pc/DW_AT_entry_pc initially, then
# each DW_RLE_base_address / DW_RLE_base_addressx (or the max-address base
# selection entry in DWARF <= 4) replaces it for the entries that follow.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from ..common.utils import bytes2str
from .descriptions import describe_form_class
from .ranges import BaseAddressEntry, RangeEntry, RangeListsPair

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from .compileunit import CompileUnit
    from .dwarfinfo import DWARFInfo
    from .die import AttributeValue, DIE
    from .ranges import RangeLists


class CoverageSource(NamedTuple):
    """Where a covered interval came from.

    cu_offset:
        Offset of the compilation unit in .debug_info.
    cu_name:
        Value of the CU's DW_AT_name, decoded to str, or None.
    die_offset:
        Offset of the DIE carrying DW_AT_ranges (or the CU top DIE for an
        interval derived from DW_AT_low_pc/DW_AT_high_pc), or None.
    die_tag:
        Tag of that DIE, or None.
    section_name:
        Range-list section the entries were read from ('.debug_ranges' or
        '.debug_rnglists'), or '' for a low_pc/high_pc interval.
    """
    cu_offset: int
    cu_name: str | None
    die_offset: int | None
    die_tag: str | None
    section_name: str


class CoverageUnit(NamedTuple):
    """A compilation unit as an ownership unit for covered bytes.
    """
    cu_offset: int
    cu_name: str | None


class SourceInterval(NamedTuple):
    """A resolved, half-open code interval [begin_address, end_address).

    base_address is the base actually added to a relative entry, or None for
    absolute entries and DW_AT_low_pc/DW_AT_high_pc intervals.

    raw_entries retains the range-list entries the interval was decoded from
    (a BaseAddressEntry followed by a RangeEntry when a list-local base was in
    force, just the RangeEntry otherwise), so provenance survives merging.
    """
    begin_address: int
    end_address: int
    source: CoverageSource
    base_address: int | None
    raw_entries: tuple[RangeEntry | BaseAddressEntry, ...]


def die_coverage_intervals(
    die: DIE,
    cu: CompileUnit,
    range_lists: RangeLists | RangeListsPair | None = None,
    base: int | None = None,
) -> list[SourceInterval]:
    """Resolve the DW_AT_ranges list of one DIE into source intervals.

    range_lists is the DWARFInfo.range_lists() object; it is fetched from the
    CU when omitted. base is the CU default base address; pass it explicitly
    when several DIEs share one lookup to avoid recomputation. Relative entries
    with no resolvable base are skipped rather than misresolved to absolute
    offsets. Returns an empty list when the DIE has no DW_AT_ranges.
    """
    attr = die.attributes.get('DW_AT_ranges')
    if attr is None:
        return []
    if range_lists is None:
        range_lists = cu.dwarfinfo.range_lists()
    if range_lists is None:
        return []
    if base is None:
        base = cu_default_base(cu)

    source = CoverageSource(
        cu_offset=cu.cu_offset,
        cu_name=cu_name(cu),
        die_offset=die.offset,
        die_tag=_die_tag(die),
        section_name=_range_section_name(range_lists, cu),
    )
    entries = range_lists.get_range_list_at_offset(attr.value, cu)
    return resolve_range_entries(entries, source, base)


def resolve_range_entries(
    entries: Sequence[RangeEntry | BaseAddressEntry],
    source: CoverageSource,
    base: int | None,
) -> list[SourceInterval]:
    """Walk one parsed range list, applying base-address changes in order.

    A BaseAddressEntry replaces the current base for every subsequent entry;
    it is never added to the previous base or applied retroactively. Absolute
    RangeEntries (all DWARF 5 start/startx encodings) ignore the base. The
    base used for each emitted interval, together with the governing base
    entry and the range entry, is kept on the result.
    """
    intervals: list[SourceInterval] = []
    current_base = base
    base_entry: BaseAddressEntry | None = None
    for entry in entries:
        if isinstance(entry, BaseAddressEntry):
            current_base = entry.base_address
            base_entry = entry
            continue

        if entry.is_absolute:
            begin = entry.begin_offset
            end = entry.end_offset
            applied_base: int | None = None
            raw: tuple[RangeEntry | BaseAddressEntry, ...] = (entry,)
        else:
            if current_base is None:
                # Relative entry without any DW_AT_low_pc/entry_pc or base
                # selection entry: the spec makes this unreachable in a valid
                # file, so leave it out instead of treating the offset as an
                # absolute address.
                continue
            begin = current_base + entry.begin_offset
            end = current_base + entry.end_offset
            applied_base = current_base
            raw = (base_entry, entry) if base_entry is not None else (entry,)

        if end > begin:
            intervals.append(SourceInterval(begin, end, source, applied_base, raw))
    return intervals


def cu_coverage_intervals(
    cu: CompileUnit,
    range_lists: RangeLists | RangeListsPair | None = None,
) -> list[SourceInterval]:
    """All coverage intervals attributed to one compilation unit.

    Covers both encodings a CU may use: a contiguous DW_AT_low_pc /
    DW_AT_high_pc pair on the top DIE, and every DW_AT_ranges list in the
    CU's DIE tree (v4 .debug_ranges and/or v5 .debug_rnglists, including
    multiple lists with different encodings inside the same object).
    """
    dwarfinfo = cu.dwarfinfo
    if range_lists is None:
        range_lists = dwarfinfo.range_lists()
    base = cu_default_base(cu)
    top_die = cu.get_top_DIE()
    intervals: list[SourceInterval] = []

    low_pc_attr = top_die.attributes.get('DW_AT_low_pc')
    high_pc_attr = top_die.attributes.get('DW_AT_high_pc')
    if low_pc_attr is not None and high_pc_attr is not None:
        end_address = _high_pc_end(low_pc_attr.value, high_pc_attr)
        if end_address > low_pc_attr.value:
            source = CoverageSource(
                cu_offset=cu.cu_offset,
                cu_name=cu_name(cu),
                die_offset=top_die.offset,
                die_tag=_die_tag(top_die),
                section_name='',
            )
            intervals.append(SourceInterval(
                low_pc_attr.value, end_address, source, None, ()))

    if range_lists is not None:
        for die in cu.iter_DIEs():
            if 'DW_AT_ranges' in die.attributes:
                intervals.extend(
                    die_coverage_intervals(die, cu, range_lists, base))
    return intervals


def all_coverage_intervals(dwarfinfo: DWARFInfo) -> list[SourceInterval]:
    """Resolve coverage intervals for every compilation unit in the file.
    """
    range_lists = dwarfinfo.range_lists()
    intervals: list[SourceInterval] = []
    for cu in dwarfinfo.iter_CUs():
        intervals.extend(cu_coverage_intervals(cu, range_lists))
    return intervals


def merge_adjacent(
    intervals: Iterable[SourceInterval],
) -> list[SourceInterval]:
    """Coalesce touching (or overlapping) intervals from the same source.

    Intervals of different sources are never merged, even when they cover the
    same bytes, so both attributions stay queryable. Each merged interval
    keeps the concatenated raw_entries of everything folded into it.
    """
    ordered = sorted(
        intervals,
        key=lambda iv: (
            iv.source.cu_offset,
            iv.source.die_offset if iv.source.die_offset is not None else -1,
            iv.begin_address,
            iv.end_address))
    merged: list[SourceInterval] = []
    for interval in ordered:
        if merged and merged[-1].source == interval.source \
                and interval.begin_address <= merged[-1].end_address:
            previous = merged[-1]
            if previous.base_address == interval.base_address:
                merged_base = previous.base_address
            else:
                # Folded fragments with different (or absolute/relative)
                # bases: no single base describes the union; the individual
                # bases remain available through raw_entries.
                merged_base = None
            merged[-1] = SourceInterval(
                min(previous.begin_address, interval.begin_address),
                max(previous.end_address, interval.end_address),
                previous.source,
                merged_base,
                previous.raw_entries + interval.raw_entries,
            )
        else:
            merged.append(interval)
    merged.sort(key=lambda iv: (
        iv.begin_address,
        iv.end_address,
        iv.source.cu_offset,
        iv.source.die_offset if iv.source.die_offset is not None else -1))
    return merged


def union_coverage(
    intervals: Iterable[SourceInterval],
) -> list[tuple[int, int]]:
    """Union of all intervals into disjoint spans; bytes covered by several
    sources appear exactly once. Returns sorted [begin, end) tuples.
    """
    spans = sorted((iv.begin_address, iv.end_address) for iv in intervals)
    merged: list[tuple[int, int]] = []
    for begin, end in spans:
        if end <= begin:
            continue
        if merged and begin <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    return merged


def coverage_size(intervals: Iterable[SourceInterval]) -> int:
    """Total number of distinct covered bytes across all sources.
    """
    return sum(end - begin for begin, end in union_coverage(intervals))


def coverage_by_source(
    intervals: Iterable[SourceInterval],
) -> dict[CoverageSource, list[SourceInterval]]:
    """Group intervals by source, merging adjacent ones within each source.

    Sizes computed from these groups count bytes once per owning source, so a
    byte claimed by two CUs is counted in both groups even though it counts
    once in coverage_size().
    """
    groups: dict[CoverageSource, list[SourceInterval]] = {}
    for interval in intervals:
        groups.setdefault(interval.source, []).append(interval)
    return {source: merge_adjacent(group) for source, group in groups.items()}


def interval_unit(interval: SourceInterval) -> CoverageUnit:
    """Compilation unit owning an interval.
    """
    return CoverageUnit(interval.source.cu_offset, interval.source.cu_name)


def coverage_by_unit(
    intervals: Iterable[SourceInterval],
) -> dict[CoverageUnit, list[tuple[int, int]]]:
    """Union all intervals of each compilation unit into disjoint spans.

    A byte overlapping two CUs appears in both units' spans, while being a
    single byte inside each unit's union.
    """
    groups: dict[CoverageUnit, list[SourceInterval]] = {}
    for interval in intervals:
        groups.setdefault(interval_unit(interval), []).append(interval)
    return {unit: union_coverage(group) for unit, group in groups.items()}


class CoverageReport:
    """Resolved coverage of a whole DWARFInfo object.

    Intervals are merged per source at construction so adjacent fragments of
    one scattered CU read as one span, while the union view and per-source
    view keep global and per-attribution accounting separate.
    """
    def __init__(
        self,
        intervals: Iterable[SourceInterval],
        merge: bool = True,
    ) -> None:
        self._intervals: list[SourceInterval] = (
            merge_adjacent(intervals) if merge else list(intervals))

    @classmethod
    def from_dwarfinfo(
        cls,
        dwarfinfo: DWARFInfo,
        merge: bool = True,
    ) -> CoverageReport:
        return cls(all_coverage_intervals(dwarfinfo), merge=merge)

    @property
    def intervals(self) -> tuple[SourceInterval, ...]:
        return tuple(self._intervals)

    def sources(self) -> list[CoverageSource]:
        seen: dict[CoverageSource, None] = {}
        for interval in self._intervals:
            seen.setdefault(interval.source, None)
        return list(seen.keys())

    def union_spans(self) -> list[tuple[int, int]]:
        return union_coverage(self._intervals)

    def total_size(self) -> int:
        return sum(end - begin for begin, end in self.union_spans())

    def by_source(self) -> dict[CoverageSource, list[SourceInterval]]:
        return coverage_by_source(self._intervals)

    def by_unit(self) -> dict[CoverageUnit, list[tuple[int, int]]]:
        return coverage_by_unit(self._intervals)

    def per_source_size(self) -> dict[CoverageSource, int]:
        return {
            source: sum(iv.end_address - iv.begin_address for iv in group)
            for source, group in self.by_source().items()
        }

    def per_unit_size(self) -> dict[CoverageUnit, int]:
        """Distinct bytes attributed to each compilation unit.

        Overlapping CUs each count the shared byte here, while total_size()
        counts it once across the whole file.
        """
        return {
            unit: sum(end - begin for begin, end in spans)
            for unit, spans in self.by_unit().items()
        }

    def intervals_at(self, address: int) -> list[SourceInterval]:
        """Every interval covering address; overlapping CUs all match, so a
        byte with two owners returns two attributions.
        """
        return [
            iv for iv in self._intervals
            if iv.begin_address <= address < iv.end_address
        ]

    def __iter__(self) -> Iterator[SourceInterval]:
        return iter(self._intervals)

    def __len__(self) -> int:
        return len(self._intervals)


def cu_default_base(cu: CompileUnit) -> int | None:
    """Default base address for relative range entries in a CU.

    DW_AT_low_pc wins; DW_AT_entry_pc is used when low_pc is absent (same
    fallback readelf uses). A CU may legitimately have neither if every list
    carries its own base selection entry or only uses absolute entries.
    """
    attributes = cu.get_top_DIE().attributes
    low_pc = attributes.get('DW_AT_low_pc')
    if low_pc is not None:
        return low_pc.value
    entry_pc = attributes.get('DW_AT_entry_pc')
    if entry_pc is not None:
        return entry_pc.value
    return None


def cu_name(cu: CompileUnit) -> str | None:
    name_attr = cu.get_top_DIE().attributes.get('DW_AT_name')
    if name_attr is None:
        return None
    return bytes2str(name_attr.value)


def _high_pc_end(low_pc: int, high_pc_attr: AttributeValue) -> int:
    # DWARF 4+ section 2.17: an address-form high_pc is absolute; a constant
    # form is an offset added to low_pc.
    if describe_form_class(high_pc_attr.form) == 'address':
        return high_pc_attr.value
    return low_pc + high_pc_attr.value


def _die_tag(die: DIE) -> str | None:
    return die.tag if isinstance(die.tag, str) else None


def _range_section_name(
    range_lists: RangeLists | RangeListsPair,
    cu: CompileUnit,
) -> str:
    if isinstance(range_lists, RangeListsPair):
        use_v5 = cu.header.version >= 5
        section = (cu.dwarfinfo.debug_rnglists_sec if use_v5
                   else cu.dwarfinfo.debug_ranges_sec)
        fallback = '.debug_rnglists' if use_v5 else '.debug_ranges'
    else:
        section = (cu.dwarfinfo.debug_rnglists_sec
                   if range_lists.version >= 5
                   else cu.dwarfinfo.debug_ranges_sec)
        fallback = ('.debug_rnglists'
                    if range_lists.version >= 5 else '.debug_ranges')
    return section.name if section is not None else fallback
