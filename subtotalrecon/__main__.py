# -*- coding: utf-8 -*-
"""Print a reconciliation report for a financial ODS table.

Usage: python -m subtotalrecon <file.ods> [sheet-name]
"""

import sys

from .parser import parse_ods


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__.strip())
        return 2
    ledger = parse_ods(argv[0], sheet_name=argv[1] if len(argv) > 1 else None)

    print("details: %d stores" % len(ledger.details))
    for st in ledger.subtotals:
        print(
            "  %-12s level=%d members=%-12s stored=%s computed=%s adjustment=%s"
            % (
                st.city,
                st.level,
                ",".join(st.member_ids),
                st.stored,
                st.computed,
                st.adjustment,
            )
        )
    print(
        "  %-12s stored=%s computed=%s adjustment=%s"
        % ("TOTAL", ledger.grand.stored, ledger.grand.computed, ledger.grand.adjustment)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
