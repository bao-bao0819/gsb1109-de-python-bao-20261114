# -------------------------------------------------------------------------------
# elftools example: elf_size_diff.py
#
# Compare the size footprint of two ELF files.
#
# The report separates on-disk bytes (NOBITS sections take no file space and
# overlapping section ranges are merged) from expanded, logical sizes
# (compressed sections counted at their decompressed size, NOBITS included).
# Per-section deltas are given for uniquely matched sections; sections that
# cannot be paired one-to-one are reported as grouped deltas.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
# -------------------------------------------------------------------------------
import argparse
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.sizediff import PairDiff, SizeReport, compare_files


def process_files(old_filename: str, new_filename: str) -> None:
    with open(old_filename, 'rb') as old_stream, open(new_filename, 'rb') as new_stream:
        report = compare_files(
            ELFFile(old_stream), ELFFile(new_stream), old_filename, new_filename
        )
    print_report(report)


def print_report(report: SizeReport) -> None:
    old = report.old
    new = report.new

    print(f'Old file: {old.path} ({old.file_size} bytes)')
    print(f'New file: {new.path} ({new.file_size} bytes)')
    print()
    print('Totals (new - old)')
    _print_delta('  file size              ', report.file_size_delta)
    _print_delta('  section disk declared  ', report.disk_declared_delta)
    _print_delta('  overlapping claims     ', report.overlap_delta)
    _print_delta('  section disk (unique)  ', report.disk_delta)
    _print_delta('  gaps/headers           ', report.gap_delta)
    _print_delta('  expanded size          ', report.expanded_delta)
    print()
    print('Sections')
    print(f'  {"name":<24} {"disk":>12} {"expanded":>12}  pairing')
    for diff in report.pair_diffs:
        print(_format_diff(diff))

    missing = [
        diff for diff in report.pair_diffs if diff.old_count == 0 or diff.new_count == 0
    ]
    if missing:
        print()
        print('Unmatched sections:')
        for diff in missing:
            if diff.old_count == 0:
                print(f'  only in new: {diff.name} (x{diff.new_count})')
            else:
                print(f'  only in old: {diff.name} (x{diff.old_count})')


def _format_diff(diff: PairDiff) -> str:
    if diff.paired:
        pairing = 'paired 1:1'
    else:
        pairing = f'group {diff.old_count} -> {diff.new_count}'
    return (
        f'  {diff.name:<24} '
        f'{_signed(diff.disk_delta):>12} '
        f'{_signed(diff.expanded_delta):>12}  {pairing}'
    )


def _print_delta(label: str, delta: int) -> None:
    print(f'{label}{_signed(delta)}')


def _signed(value: int) -> str:
    return f'{value:+d}'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Compare the size footprint of two ELF files.'
    )
    parser.add_argument(
        'elf_files',
        nargs='+',
        help='ELF files; with --test each is compared to '
        'itself, otherwise exactly two are expected',
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='test mode: compare every given file to itself',
    )
    args = parser.parse_args(argv)

    if args.test:
        for filename in args.elf_files:
            process_files(filename, filename)
    else:
        if len(args.elf_files) != 2:
            parser.error('exactly two ELF files are required')
        process_files(args.elf_files[0], args.elf_files[1])
    return 0


if __name__ == '__main__':
    sys.exit(main())
