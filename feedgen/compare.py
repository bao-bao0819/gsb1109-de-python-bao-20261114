# -*- coding: utf-8 -*-
'''
    feedgen.compare
~~~~~~~~~~~~~~~

Compatibility entry point for the ATOM/RSS comparison (parity) view.
All functionality lives in :mod:`feedgen.parity`.
'''

from feedgen.diff import *  # noqa: F401,F403
from feedgen.diff import __all__  # noqa: F401


def main(argv=None):
    """Command line entry point for ``python -m feedgen.compare``."""
    from feedgen.parity import main as _main

    return _main(argv)


if __name__ == '__main__':
    import sys

    sys.exit(main())
