#-------------------------------------------------------------------------------
# elftools example: symbol_sample_attribution.py
#
# Attribute sampled instruction addresses to functions using the ELF symbol
# table, and reconcile the attribution per section.
#
# Attribution rules:
#   * Symbols with an explicit range (st_size > 0) take priority.
#   * A zero-length symbol may claim addresses up to the next symbol start in
#     the same section (or the section end), but such ranges are marked as
#     inferred.
#   * Equivalent aliases (same start and end) share a single sample count.
#   * Addresses covered by several explicit ranges are reported separately as
#     disputed.
#   * For every section, definite + disputed + unowned counts must equal the
#     number of input samples landing in that section.
#
# Usage:
#   symbol_sample_attribution.py <elf-file> <samples-file>
#   symbol_sample_attribution.py --test <elf-file>
#
# A samples file holds one "<address> <count>" pair per line (addresses may
# be hex); with --test, deterministic samples are synthesized from the symbol
# table itself.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
import argparse
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.constants import SH_FLAGS
from elftools.elf.sections import SymbolTableSection


class RangeGroup:
    """ A set of aliased symbols sharing one address range [start, end).

        `names` lists the aliases; equivalent aliases share a single sample
        count. `inferred` is True when the end was guessed for zero-length
        symbols (next symbol start in the section, or the section end).
    """
    def __init__(self, start, end, inferred):
        self.start = start
        self.end = end
        self.inferred = inferred
        self.names = []

    def label(self):
        tag = ' [inferred]' if self.inferred else ''
        return '{} 0x{:x}-0x{:x}{}'.format(
            ', '.join(self.names), self.start, self.end, tag)


class SectionInfo:
    """ An allocatable section and the function ranges found inside it. """
    def __init__(self, name, start, end, groups):
        self.name = name
        self.start = start
        self.end = end
        self.groups = groups


def build_ranges(symbols, section_end):
    """ Turn (name, value, size) symbol triples of one section into sorted
        RangeGroups. Zero-length symbols are inferred up to the next symbol
        start in the section (or section_end); a zero-length symbol at the
        same start address as an explicit range is treated as its alias.
        Symbols with identical ranges are merged into one alias group.
    """
    entries = sorted(symbols, key=lambda item: item[1])
    starts = sorted({value for _, value, _ in entries})
    groups = {}
    explicit_at = {}

    def get_group(start, end, inferred):
        key = (start, end)
        group = groups.get(key)
        if group is None:
            group = RangeGroup(start, end, inferred)
            groups[key] = group
        if not inferred:
            # An explicit range always wins over an inferred one.
            group.inferred = False
        return group

    def add_name(group, name):
        if name not in group.names:
            group.names.append(name)

    for name, value, size in entries:
        if size > 0:
            group = get_group(value, value + size, False)
            add_name(group, name)
            explicit_at.setdefault(value, []).append(group)

    for name, value, size in entries:
        if size > 0:
            continue
        aliases = explicit_at.get(value)
        if aliases:
            add_name(aliases[0], name)
            continue
        following = [s for s in starts if s > value]
        end = following[0] if following else section_end
        if end <= value:
            continue
        add_name(get_group(value, end, True), name)
    return sorted(groups.values(), key=lambda g: (g.start, g.end))


def attribute_samples(samples, sections):
    """ Attribute (address, count) samples to the section ranges.

        Returns (per_section, outside) where per_section is a list of dicts
        with keys 'definite' ({RangeGroup: count}), 'disputed'
        ({tuple of RangeGroups: count}), 'unowned' (count) and 'input'
        (total count landing in the section); 'outside' is the count of
        samples not covered by any section.
    """
    per_section = [
        {'section': sec, 'definite': {}, 'disputed': {}, 'unowned': 0,
         'input': 0}
        for sec in sections]
    outside = 0
    for addr, count in samples:
        for result in per_section:
            sec = result['section']
            if not (sec.start <= addr < sec.end):
                continue
            result['input'] += count
            explicit = [g for g in sec.groups
                        if not g.inferred and g.start <= addr < g.end]
            inferred = [g for g in sec.groups
                        if g.start <= addr < g.end]
            if len(explicit) == 1:
                hits = [(result['definite'], explicit[0])]
            elif len(explicit) > 1:
                hits = [(result['disputed'], tuple(explicit))]
            elif len(inferred) == 1:
                hits = [(result['definite'], inferred[0])]
            elif len(inferred) > 1:
                hits = [(result['disputed'], tuple(inferred))]
            else:
                result['unowned'] += count
                break
            for bucket, key in hits:
                bucket[key] = bucket.get(key, 0) + count
            break
        else:
            outside += count
    return per_section, outside


def collect_sections(elffile):
    """ Build SectionInfo objects for allocatable sections that contain
        STT_FUNC symbols, using .symtab (falling back to .dynsym).
    """
    symtab = None
    for sec in elffile.iter_sections():
        if isinstance(sec, SymbolTableSection):
            if sec['sh_type'] == 'SHT_SYMTAB':
                symtab = sec
                break
            elif symtab is None:
                symtab = sec
    if symtab is None:
        return []

    func_syms = {}
    for sym in symtab.iter_symbols():
        if sym['st_info']['type'] != 'STT_FUNC':
            continue
        shndx = sym['st_shndx']
        if not isinstance(shndx, int):
            # SHN_UNDEF and other special section indices.
            continue
        func_syms.setdefault(shndx, []).append(
            (sym.name, sym['st_value'], sym['st_size']))

    sections = []
    for idx, sec in enumerate(elffile.iter_sections()):
        if idx not in func_syms:
            continue
        if not (sec['sh_flags'] & SH_FLAGS.SHF_ALLOC):
            continue
        size = sec['sh_size']
        if size == 0 or sec['sh_type'] == 'SHT_NOBITS':
            continue
        start = sec['sh_addr']
        groups = build_ranges(func_syms[idx], start + size)
        sections.append(SectionInfo(sec.name, start, start + size, groups))
    return sections


def synthesize_samples(sections):
    """ Deterministically synthesize samples from the ranges themselves:
        every group is hit at its start and middle, the first uncovered gap
        of each section gets one unowned sample, and one sample lands outside
        all sections.
    """
    samples = []
    for sec in sections:
        for group in sec.groups:
            samples.append((group.start, 3))
            if group.end - group.start > 1:
                samples.append((group.start + (group.end - group.start) // 2,
                                2))
        cursor = sec.start
        for start, end in sorted((g.start, g.end) for g in sec.groups):
            if start > cursor:
                samples.append(((cursor + start) // 2, 1))
                break
            cursor = max(cursor, end)
        else:
            if cursor < sec.end:
                samples.append(((cursor + sec.end) // 2, 1))
    samples.append((0x1, 4))
    return samples


def read_samples(path):
    """ Read "<address> <count>" pairs; '#' starts a comment. """
    samples = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 2:
                raise ValueError(
                    '{}:{}: expected "<address> <count>"'.format(path, lineno))
            samples.append((int(parts[0], 0), int(parts[1], 0)))
    return samples


def format_report(per_section, outside):
    lines = []
    totals = {'definite': 0, 'disputed': 0, 'unowned': 0, 'input': 0}
    for result in per_section:
        sec = result['section']
        if not sec.groups and result['input'] == 0:
            continue
        lines.append('Section {} [0x{:x}, 0x{:x})'.format(
            sec.name, sec.start, sec.end))
        lines.append('  definite:')
        for group in sec.groups:
            count = result['definite'].get(group, 0)
            lines.append('    {}: {}'.format(group.label(), count))
        lines.append('  disputed:')
        if result['disputed']:
            for candidates, count in sorted(
                    result['disputed'].items(),
                    key=lambda item: (item[0][0].start, item[0][0].end)):
                names = ' vs '.join(', '.join(g.names) for g in candidates)
                lines.append('    {}: {}'.format(names, count))
        else:
            lines.append('    (none)')
        definite_sum = sum(result['definite'].values())
        disputed_sum = sum(result['disputed'].values())
        unowned = result['unowned']
        accounted = definite_sum + disputed_sum + unowned
        status = 'OK' if accounted == result['input'] else 'MISMATCH'
        lines.append('  unowned: {}'.format(unowned))
        lines.append('  check: definite {} + disputed {} + unowned {} = {} '
                     '(input {}) {}'.format(definite_sum, disputed_sum,
                                            unowned, accounted,
                                            result['input'], status))
        totals['definite'] += definite_sum
        totals['disputed'] += disputed_sum
        totals['unowned'] += unowned
        totals['input'] += result['input']
    if outside:
        lines.append('Unowned outside any section: {}'.format(outside))
        totals['unowned'] += outside
        totals['input'] += outside
    accounted = totals['definite'] + totals['disputed'] + totals['unowned']
    status = 'OK' if accounted == totals['input'] else 'MISMATCH'
    lines.append('Total: definite {} + disputed {} + unowned {} = {} '
                 '(input {}) {}'.format(totals['definite'],
                                        totals['disputed'],
                                        totals['unowned'], accounted,
                                        totals['input'], status))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Attribute sampled instruction addresses to functions '
                    'using the ELF symbol table.')
    parser.add_argument(
        '--test', action='store_true',
        help='synthesize deterministic samples instead of reading a '
             'samples file')
    parser.add_argument('elf_file')
    parser.add_argument('samples_file', nargs='?')
    args = parser.parse_args()

    with open(args.elf_file, 'rb') as f:
        sections = collect_sections(ELFFile(f))

    if args.test:
        samples = synthesize_samples(sections)
    else:
        if args.samples_file is None:
            parser.error('a samples file is required without --test')
        samples = read_samples(args.samples_file)

    per_section, outside = attribute_samples(samples, sections)
    print(format_report(per_section, outside))


if __name__ == '__main__':
    sys.exit(main())
