"""
Physical cleanup -- the only stage that ever writes to disk.

Strategy per table:

1. undelete every deletion-marked row that the analysis decided to
   keep (``table.pack`` removes *all* marked rows, so kept rows must be
   unmarked first);
2. ``pack`` the table, physically removing the rows in the removable
   set and compacting the file;
3. re-apply the deletion marker to the kept rows at their new,
   deterministically computed record numbers.

A post-apply verification reopens every table read-only and checks the
surviving (key, marker) multiset against the analysis, so a botched
pack can never pass silently.
"""

from __future__ import annotations

from collections import Counter

import dbf

from .model import _resolve_filename, normalize


def expected_survivors(result):
    """Counter of (table, key tuple, deleted) for every surviving row."""

    expected = Counter()
    for rid, info in result.rows.items():
        if rid in result.removable:
            continue
        expected[(rid.table, info.key, info.deleted)] += 1
    return expected


def apply_cleanup(data_dir, schema, result):
    """Pack each table according to ``result``; return per-table counts."""

    counts = {}
    order = {}
    for spec in schema.tables:
        order[spec.name] = sorted(
            (rid for rid in result.rows if rid.table == spec.name),
            key=lambda rid: rid.recno,
        )

    for spec in schema.tables:
        table_order = order[spec.name]
        removable_here = [rid for rid in table_order if rid in result.removable]
        if not removable_here:
            counts[spec.name] = 0
            continue

        surviving = [rid for rid in table_order if rid not in result.removable]
        new_position = {rid: index for index, rid in enumerate(surviving)}
        re_mark = [rid for rid in surviving if result.rows[rid].deleted]

        path = _resolve_filename(data_dir, spec)
        table = dbf.Table(path, codepage="utf8", on_disk=True)
        table.open(dbf.READ_WRITE)
        try:
            # 1) unmark kept rows while old record numbers are valid
            for rid in re_mark:
                dbf.undelete(table[rid.recno])
            # 2) physically drop every still-marked row
            table.pack()
            if len(table) != len(surviving):
                raise RuntimeError(
                    "pack of %s left %d records, expected %d"
                    % (spec.name, len(table), len(surviving))
                )
            # 3) restore markers at the compacted positions
            for rid in re_mark:
                dbf.delete(table[new_position[rid]])
        finally:
            table.close()

        counts[spec.name] = len(removable_here)

    verify_apply(data_dir, schema, result)
    return counts


def verify_apply(data_dir, schema, result):
    """Reopen the files and assert the surviving rows match the plan."""

    expected = expected_survivors(result)
    actual = Counter()

    for spec in schema.tables:
        path = _resolve_filename(data_dir, spec)
        table = dbf.Table(path, codepage="utf8", on_disk=True)
        table.open(dbf.READ_ONLY)
        try:
            for record in table:
                key = tuple(normalize(record[name]) for name in spec.key_fields)
                actual[(spec.name, key, dbf.is_deleted(record))] += 1
        finally:
            table.close()

    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        raise RuntimeError(
            "post-apply verification failed; missing=%r extra=%r"
            % (dict(missing), dict(extra))
        )
