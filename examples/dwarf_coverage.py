#-------------------------------------------------------------------------------
# elftools example: dwarf_coverage.py
#
# Build code-coverage statistics from DWARF range lists. A compilation unit can
# be split across several distant address regions, so coverage is computed as
# source-attributed intervals rather than min(low_pc)..max(high_pc).
#
# Run from the repository root, supplying one or more ELF files:
#     PYTHONPATH=. python examples/dwarf_coverage.py examples/sample_exe64.elf
#
# Relative range entries are resolved against the base address in force at
# their position in the list (CU DW_AT_low_pc/DW_AT_entry_pc, replaced by each
# base-address selection entry). Adjacent intervals of one source are merged,
# while the raw range-list entries stay reachable. Bytes covered by two units
# are counted once in the total but remain attributed to both units.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
import sys

from elftools.dwarf.coverage import CoverageReport, all_coverage_intervals
from elftools.elf.elffile import ELFFile


def process_file(filename):
    """Print per-unit and overall coverage for one ELF file.

    Opens and closes filename, writes to stdout, and returns None.
    """
    print('Processing file:', filename)
    with open(filename, 'rb') as f:
        elffile = ELFFile(f)
        if not elffile.has_dwarf_info(strict=True):
            print('  file has no DWARF info')
            return

        dwarfinfo = elffile.get_dwarf_info()
        intervals = all_coverage_intervals(dwarfinfo)
        if not intervals:
            print('  no code coverage information')
            return

        report = CoverageReport(intervals)
        for unit, spans in sorted(
                report.by_unit().items(), key=lambda item: item[0].cu_offset):
            name = unit.cu_name if unit.cu_name is not None else '<no name>'
            size = sum(end - begin for begin, end in spans)
            print(f'  CU {name} (.debug_info offset {unit.cu_offset:#x}): '
                  f'{len(spans)} span(s), {size} byte(s)')
            for begin, end in spans:
                print(f'    [{begin:#x}, {end:#x})')

        per_unit_total = sum(report.per_unit_size().values())
        overall = report.total_size()
        print(f'  Total covered (overlapping bytes counted once): '
              f'{overall} byte(s) in {len(report.union_spans())} span(s)')
        print(f'  Sum of per-unit attributions: {per_unit_total} byte(s)')
        print(f'  Bytes attributed to more than one unit: '
              f'{per_unit_total - overall}')


if __name__ == '__main__':
    filenames = sys.argv[1:]
    if filenames[:1] == ['--test']:
        filenames = filenames[1:]
    if not filenames:
        sys.exit(f'Usage: {sys.argv[0]} <elf-file> [...]')
    for filename in filenames:
        process_file(filename)
