"""
cleanup
=======

Referential-integrity aware cleanup for deletion-marked records in a
small family of legacy .dbf tables.

The package never modifies data during a dry run: tables are opened
read-only and the on-disk bytes are hashed before and after the
analysis to prove the files are untouched.  Only an explicit apply
performs a physical removal (via ``undelete -> pack -> re-delete`` so
that deletion-marked rows which must be retained keep their marker).
"""

__version__ = "1.0.0"
