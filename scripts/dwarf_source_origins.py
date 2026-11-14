#-------------------------------------------------------------------------------
# scripts/dwarf_source_origins.py
#
# Attribute the file entries of DWARF line tables to relocated source
# trees.  Prints one line per compilation-unit file entry, with every
# candidate source directory when the evidence cannot distinguish them.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elftools.elf.elffile import ELFFile
from elftools.dwarf.sourceroots import ChecksumStore, SourceRootResolver


def parse_args():
    parser = argparse.ArgumentParser(
        description='Attribute DWARF line-table files to source trees.')
    parser.add_argument('elf', nargs='+', help='ELF file(s) with debug info')
    parser.add_argument(
        '-r', '--source-root', action='append', default=[],
        help='relocated source tree root (repeatable)')
    parser.add_argument(
        '-c', '--checksums',
        help='checksum manifest with "<digest>  <path>" lines')
    parser.add_argument(
        '--only', choices=('unique', 'ambiguous', 'unmatched'),
        help='show only entries with this status')
    return parser.parse_args()


def main():
    args = parse_args()
    checksums = None
    if args.checksums:
        with open(args.checksums, encoding='utf-8') as manifest:
            checksums = ChecksumStore.from_manifest(manifest)
    resolver = SourceRootResolver(args.source_root, checksums)

    exit_code = 0
    for elf_path in args.elf:
        with open(elf_path, 'rb') as stream:
            elf = ELFFile(stream)
            if not elf.has_dwarf_info():
                print(f'{elf_path}: no DWARF info', file=sys.stderr)
                continue
            for resolution in resolver.resolve_dwarf_info(
                    elf.get_dwarf_info()):
                cu_name = resolution.cu_name or '<unknown>'
                for origin in resolution.origins:
                    if args.only and origin.status != args.only:
                        continue
                    if origin.status == 'unmatched':
                        exit_code = 1
                        print(f'{elf_path}\t{cu_name}\t{origin.status}\t'
                              f'{origin.build_path}')
                        continue
                    for source in origin.sources:
                        print(f'{elf_path}\t{cu_name}\t{origin.status}\t'
                              f'{origin.build_path}\t'
                              f'{source.build_prefix or "."}\t'
                              f'{source.filesystem_path}')
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
