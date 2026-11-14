# -*- coding: utf-8 -*-
'''
    feedgen.diff
    ~~~~~~~~~~~~

Compatibility entry point for the ATOM/RSS difference (parity) view.
All functionality lives in :mod:`feedgen.parity`.
'''

from feedgen.parity import (  # noqa: F401
    EntryList,
    EntryParity,
    FieldParity,
    ParityReport,
    atom_rss_difference,
    atom_rss_diff,
    atom_rss_parity,
    atom_rss_view,
    build_diff_view,
    build_parity_report,
    build_parity_view,
    compare,
    compare_atom_rss,
    compare_feed_outputs,
    compare_feeds,
    compare_outputs,
    compare_representations,
    diff,
    diff_atom_rss,
    diff_view,
    difference_view,
    feed_diff,
    feed_parity,
    make_diff_view,
    make_parity_view,
    output_diff,
    parity,
    parity_view,
)

__all__ = [
    'ParityReport',
    'EntryParity',
    'FieldParity',
    'EntryList',
    'build_parity_view',
    'parity_view',
    'diff_view',
    'compare_feeds',
    'compare_atom_rss',
    'atom_rss_diff',
    'build_parity_report',
    'build_diff_view',
    'compare',
    'parity',
    'diff',
    'diff_atom_rss',
    'atom_rss_difference',
    'feed_parity',
    'compare_representations',
    'atom_rss_parity',
    'atom_rss_view',
    'make_diff_view',
    'compare_outputs',
    'compare_feed_outputs',
    'output_diff',
    'feed_diff',
    'difference_view',
    'make_parity_view',
]


def main(argv=None):
    """Command line entry point for ``python -m feedgen.diff``."""
    from feedgen.parity import main as _main

    return _main(argv)


if __name__ == '__main__':
    import sys

    sys.exit(main())
