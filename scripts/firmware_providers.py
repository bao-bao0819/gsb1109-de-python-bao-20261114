#!/usr/bin/env python
#-------------------------------------------------------------------------------
# scripts/firmware_providers.py
#
# Pick the smallest archive of library versions that lets every firmware
# target be assembled from its offline acquisition modules.
#
# This code is in the public domain.
#-------------------------------------------------------------------------------
from __future__ import annotations

import argparse
import json
import sys

# For running from development directory. It should take precedence over the
# installed pyelftools.
sys.path.insert(0, '.')

from elftools.elf.firmware import (
    load_catalog,
    load_config,
    select_providers,
)


def _provider_dict(provider) -> dict:
    return {
        'soname': provider.soname,
        'version': provider.version,
        'archive_size': provider.archive_size,
        'satisfies': sorted(provider.satisfies),
        'path': provider.path,
    }


def _report(result) -> dict:
    if not result.feasible:
        return {
            'feasible': False,
            'conflicts': [
                {
                    'firmware': conflict.firmware,
                    'requirements': sorted(conflict.requirements),
                    'detail': conflict.detail,
                }
                for conflict in result.conflicts
            ],
        }

    return {
        'feasible': True,
        'total_archive_size': result.total_size,
        'archive': [_provider_dict(provider) for provider in result.archive],
        'firmwares': [
            {
                'firmware': selection.firmware,
                'requirements': {
                    name: {
                        'soname': provider.soname,
                        'version': provider.version,
                    }
                    for name, provider in sorted(
                        selection.assignment.items())
                },
            }
            for selection in result.firmwares
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Select provider library versions that satisfy every firmware '
            'target with the smallest total archive size.'))
    parser.add_argument(
        '--catalog', required=True,
        help='JSON catalog of available library versions')
    parser.add_argument(
        '--config', required=True,
        help='JSON firmware/module configuration')
    parser.add_argument(
        '--json', action='store_true',
        help='print the full result as JSON instead of a summary')
    args = parser.parse_args(argv)

    try:
        providers = load_catalog(args.catalog)
        targets = load_config(args.config)
        result = select_providers(targets, providers)
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2

    report = _report(result)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif not report['feasible']:
        print('No feasible provider selection:')
        for conflict in report['conflicts']:
            print(f"  firmware {conflict['firmware']}: "
                  f"mutually exclusive {conflict['requirements']}")
            print(f"    {conflict['detail']}")
    else:
        print(f"Total archive size: {report['total_archive_size']}")
        print('Archive:')
        for provider in report['archive']:
            print(f"  {provider['soname']} ({provider['version']}), "
                  f"{provider['archive_size']} bytes")
        for firmware in report['firmwares']:
            used = sorted(
                {(item['soname'], item['version'])
                 for item in firmware['requirements'].values()})
            print(f"  {firmware['firmware']}: " + ', '.join(
                f'{soname} ({version})' for soname, version in used))

    return 0 if report['feasible'] else 1


if __name__ == '__main__':
    sys.exit(main())
