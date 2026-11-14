"""
Command line entry point for :func:`dbf.audit_key`.

Usage::

    python -m dbf.audit customer.dbf --fields CUST_CODE BRANCH
"""

import sys

from ._audit import main

if __name__ == '__main__':
    sys.exit(main())
