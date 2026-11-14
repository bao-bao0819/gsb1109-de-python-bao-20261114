#!/usr/bin/env python
#-------------------------------------------------------------------------------
# scripts/find_debug_info.py
#
# Match ELF files against external debug files listed in an offline manifest.
#
# This code is in the public domain.
#-------------------------------------------------------------------------------
from __future__ import annotations

import argparse
import json
import sys

# For running from the development directory. It should take precedence over
# an installed pyelftools.
sys.path.insert(0, '.')

from elftools import __version__
from elftools.elf.debuglink import (
    STATUS_MATCHED,
    CandidateManifest,
    match_debug_files,
    match_manifest,
    report_to_dict,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Match ELF files with external debug files from an offline '
            'directory manifest. Every decision records the identifiers used, '
            'the CRC32 values, and each candidate path; ambiguous matches are '
            'reported rather than guessed.'))
    parser.add_argument('manifest',
                        help='JSON or line-oriented manifest of candidate '
                             'directories/files and optionally the ELF files')
    parser.add_argument('--elf', action='append', default=[],
                        metavar='PATH',
                        help='ELF file to match (repeatable). Overrides the '
                             'manifest ELF list')
    parser.add_argument('-o', '--output', metavar='PATH',
                        help='write the JSON report to PATH instead of stdout')
    parser.add_argument('--indent', type=int, default=2,
                        help='JSON indentation (default: 2)')
    parser.add_argument('--quiet', action='store_true',
                        help='do not print the summary to stderr')
    parser.add_argument('--version', action='version',
                        version=f'%(prog)s {__version__}')
    args = parser.parse_args()

    try:
        manifest = CandidateManifest.from_file(args.manifest)
    except (OSError, ValueError) as error:
        print(f'{parser.prog}: {error}', file=sys.stderr)
        return 1

    elf_paths = args.elf or list(manifest.elfs)
    if not elf_paths:
        print(f'{parser.prog}: no ELF files to match; pass --elf or add '
              f'"elfs" to the manifest', file=sys.stderr)
        return 1

    if args.elf:
        results = match_debug_files(elf_paths, manifest,
                                    recursive=manifest.recursive)
    else:
        results = match_manifest(manifest)

    if args.output:
        write_report(results, args.output, manifest=manifest, indent=args.indent)
    else:
        json.dump(report_to_dict(results, manifest), sys.stdout,
                  ensure_ascii=False, indent=args.indent)
        sys.stdout.write('\n')

    matched = sum(1 for result in results if result.status == STATUS_MATCHED)
    if not args.quiet:
        print(f'{matched}/{len(results)} ELF files matched', file=sys.stderr)
        for result in results:
            if result.status == STATUS_MATCHED:
                continue
            print(f'  {result.status}: {result.elf}', file=sys.stderr)
    return 0 if matched == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
