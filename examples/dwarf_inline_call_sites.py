#-------------------------------------------------------------------------------
# elftools example: dwarf_inline_call_sites.py
#
# Print one row per concrete inlined-subroutine expansion.  Each row keeps its
# own DIE offset, call site and address ranges, while name and type are filled
# in from the abstract declaration.  Rows sharing one declaration are never
# merged.
#
# Run from the repository root, supplying one or more ELF files:
#     PYTHONPATH=. python examples/dwarf_inline_call_sites.py <elf-file>
#
# This code is in the public domain
#-------------------------------------------------------------------------------
import sys

from elftools.elf.elffile import ELFFile


def process_file(filename):
    print('Processing file:', filename)
    with open(filename, 'rb') as f:
        elffile = ELFFile(f)

        if not elffile.has_dwarf_info(strict=True):
            print('  file has no DWARF info')
            return

        dwarfinfo = elffile.get_dwarf_info()
        table = dwarfinfo.inline_call_sites()

        print(f'  {len(table)} concrete inline call sites')
        for row in table:
            ranges = ', '.join(
                f'[0x{r.begin:x}, 0x{r.end:x})' for r in row.ranges) or '-'
            print(f'    DIE 0x{row.offset:x} name={row.name!r} '
                  f'type={row.type!r}')
            print(f'      call={row.call_file_name}:{row.call_line} '
                  f'caller={row.caller_offset} depth={row.depth} '
                  f'ranges={ranges}')


if __name__ == '__main__':
    filenames = sys.argv[1:]
    if filenames[:1] == ['--test']:
        filenames = filenames[1:]
    if not filenames:
        sys.exit(f'Usage: {sys.argv[0]} <elf-file> [...]')
    for filename in filenames:
        process_file(filename)
