"""Reconcile security transfers reported by several OFX statements.

Example::

    python utils/ofxmatch.py statement-a.ofx statement-b.ofx --window 3d

The command prints the confirmed transfer relationships and the remaining
quantity of every record that could not be fully matched.  Use ``--json`` for
machine-readable output.
"""

from __future__ import absolute_import

import argparse
import json
import sys

from ofxparse import OfxParser, match_transfers


def load(paths, window, aliases, distinct_accounts):
    import io

    sources = []
    for path in paths:
        with io.open(path, 'rb') as handle:
            sources.append(OfxParser.parse(handle))
    return match_transfers(
        sources,
        settlement_window=window,
        security_aliases=aliases,
        require_distinct_accounts=distinct_accounts,
    )


def parse_aliases(values):
    aliases = {}
    for value in values or []:
        if '=' not in value:
            raise argparse.ArgumentTypeError(
                "alias must look like TICKER=UNIQUEID: %r" % value)
        key, target = value.split('=', 1)
        aliases[key.strip()] = target.strip()
    return aliases


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Match security transfers across OFX statements.')
    parser.add_argument('files', metavar='statement.ofx', nargs='+')
    parser.add_argument(
        '--window', default='3d',
        help='allowed settlement date difference (default: 3d); '
             'use "off" to disable the date check')
    parser.add_argument(
        '--alias', action='append', metavar='TICKER=UNIQUEID',
        help='treat TICKER as UNIQUEID when comparing securities')
    parser.add_argument(
        '--allow-same-account', action='store_true',
        help='also pair records that belong to the same account')
    parser.add_argument('--json', action='store_true', dest='as_json')
    arguments = parser.parse_args(argv)

    window = None if arguments.window.lower() in ('off', 'none', '') \
        else arguments.window
    result = load(arguments.files, window, parse_aliases(arguments.alias),
                  not arguments.allow_same_account)

    if arguments.as_json:
        json.dump(result.as_dict(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write('\n')
    else:
        sys.stdout.write(result.render())
        sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
