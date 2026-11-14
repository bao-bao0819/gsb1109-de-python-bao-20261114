#-------------------------------------------------------------------------------
# elftools example: elf_note_ledger.py
#
# Build a reconciled ledger of the notes in an ELF file.
#
# A note is often reachable both through an SHT_NOTE section and through a
# PT_NOTE segment that covers the same file bytes. Iterating both tables
# therefore reports the same physical record twice (for example a build ID
# shows up twice in a weekly report). The ledger merges such entry points:
# each physical record is listed once, annotated with every section or
# segment that fully covers it. Records with identical contents at
# different file offsets are kept separate, and an entry point that only
# overlaps part of a record is never treated as a complete copy of it.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.notes import collect_note_ledger


def process_file(filename):
    print('Processing file:', filename)
    with open(filename, 'rb') as f:
        ledger = collect_note_ledger(ELFFile(f))

        print('  Note entry points:')
        for source in ledger.sources:
            print(
                f"    {source.label} covers "
                f"0x{source.offset:08x}-0x{source.end:08x} "
                f"({source.size} bytes)"
            )

        print(f'  Physical note records: {len(ledger)}')
        for index, record in enumerate(ledger):
            note = record.note
            entry_points = ', '.join(source.label for source in record.sources)
            status = 'duplicate entry' if record.is_duplicate_entry else 'single entry'
            print(
                f"    #{index} at 0x{record.offset:08x} "
                f"({record.size} bytes) name={note['n_name']!r} "
                f"type={note['n_type']} [{status}]"
            )
            print(f'        entry points: {entry_points}')


if __name__ == '__main__':
    if sys.argv[1] == '--test':
        for filename in sys.argv[2:]:
            process_file(filename)
