"""Resolve every holding in OFX files against a versioned security dictionary.

Usage::

    python utils/resolve_positions.py statements/*.ofx \
        --mappings manual_mappings.csv \
        --output resolved_positions.csv

The mapping CSV columns are:
``source_id,target_id,effective_date,end_date,source,note``.

Only the OFX unique id (CUSIP) and the dated manual mappings define identity;
matching tickers or names never merge two securities.  When the mappings fork
to two different securities on the same trading day, every conflict chain is
written to the ``conflict_chains`` column and the row is flagged ambiguous.
"""

from __future__ import absolute_import, print_function

import argparse
import csv
import sys

from ofxparse import OfxParser, SecurityDictionary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ofx_files', metavar='*.ofx', nargs='+',
                        help='OFX/QFX investment statements')
    parser.add_argument('--mappings', metavar='mappings.csv',
                        help='Dated manual identity mappings')
    parser.add_argument('--output', metavar='resolved.csv',
                        help='Write resolved positions here (default: stdout)')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    dictionary = SecurityDictionary()
    holdings = []

    for filename in args.ofx_files:
        with open(filename, 'rb') as handle:
            ofx = OfxParser.parse(handle)
        statement = ofx.account.statement
        observed_on = statement.end_date.date() \
            if statement.end_date else None
        if ofx.security_list:
            dictionary.load_ofx_securities(
                ofx.security_list, observed_on=observed_on)
        for account in ofx.accounts:
            for position in account.statement.positions:
                holdings.append((filename, account.account_id, position))

    if args.mappings:
        dictionary.load_mappings_csv(args.mappings)

    rows = []
    for filename, account_id, position in holdings:
        result = dictionary.resolve_position(position)
        rows.append({
            'source_file': filename,
            'account_id': account_id,
            'as_of': result.as_of.isoformat(),
            'uniqueid': result.identity,
            'canonical_id': result.canonical_id,
            'ticker': result.ticker or '',
            'name': result.name or '',
            'units': str(position.units),
            'ambiguous': str(result.ambiguous).lower(),
            'conflict_chains': ' | '.join(
                result.conflict.chains()) if result.conflict else '',
        })

    fieldnames = ['source_file', 'account_id', 'as_of', 'uniqueid',
                  'canonical_id', 'ticker', 'name', 'units',
                  'ambiguous', 'conflict_chains']
    if args.output:
        output = open(args.output, 'w')
        close_after = True
    else:
        output = sys.stdout
        close_after = False
    try:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    finally:
        if close_after:
            output.close()

    ambiguous = [row for row in rows if row['ambiguous'] == 'true']
    if ambiguous:
        print('%d ambiguous position(s) kept with full conflict chains'
              % len(ambiguous), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
