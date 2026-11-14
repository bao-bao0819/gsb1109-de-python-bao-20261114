"""
Human-readable rendering of an :class:`~cleanup.model.AnalysisResult`.

The report answers, in order:

1. what is in each table and how many rows are marked;
2. the maximum set of marked rows that can be removed safely;
3. every marked row that must stay, and precisely which surviving
   record (and relation) holds it back;
4. self-referential groups released together, ambiguous duplicate-key
   references, and any dangling foreign keys.
"""

from __future__ import annotations


def _fmt_key(key):
    return "(" + ", ".join(repr(part) for part in key) + ")"


def render_report(result, hashes_before=None, hashes_after=None):
    lines = []
    schema = result.schema
    rows = result.rows

    lines.append("=" * 72)
    lines.append("DBF deletion-mark cleanup -- dry run analysis")
    lines.append("=" * 72)

    lines.append("")
    lines.append("Declared relations:")
    for ref in schema.refs:
        lines.append(
            "  %-9s %s(%s) -> %s(%s)"
            % (
                ref.name,
                ref.child_table,
                ",".join(ref.child_fields),
                ref.parent_table,
                ",".join(ref.parent_fields),
            )
        )

    lines.append("")
    lines.append("Per-table overview")
    lines.append("-" * 72)
    header = "  %-6s %8s %8s %10s %10s %10s" % (
        "table", "rows", "marked", "removable", "kept*", "active",
    )
    lines.append(header)
    for spec in schema.tables:
        table_rows = [i for i in rows.values() if i.rid.table == spec.name]
        marked = [i for i in table_rows if i.deleted]
        removable = [i for i in marked if i.rid in result.removable]
        kept = [i for i in marked if i.rid not in result.removable]
        active = [i for i in table_rows if not i.deleted]
        lines.append(
            "  %-6s %8d %8d %10d %10d %10d"
            % (
                spec.name,
                len(table_rows),
                len(marked),
                len(removable),
                len(kept),
                len(active),
            )
        )
    lines.append("  (* marked rows retained because a surviving row references them)")

    lines.append("")
    lines.append("Maximum safe removal set (%d rows)" % len(result.removable))
    lines.append("-" * 72)
    by_table = {}
    for rid in sorted(result.removable, key=lambda r: (r.table, r.recno)):
        by_table.setdefault(rid.table, []).append(rows[rid])
    if not result.removable:
        lines.append("  (none -- every marked row is still referenced)")
    for spec in schema.tables:
        infos = by_table.get(spec.name, [])
        if not infos:
            continue
        lines.append("  %s: %d row(s)" % (spec.name, len(infos)))
        for info in infos:
            lines.append("    - recno %d key=%s" % (info.rid.recno, _fmt_key(info.key)))

    lines.append("")
    lines.append("Marked rows that must be retained, and what holds them")
    lines.append("-" * 72)
    if not result.retained:
        lines.append("  (none)")
    for rid in sorted(result.retained, key=lambda r: (r.table, r.recno)):
        info = rows[rid]
        lines.append(
            "  %s recno %d key=%s" % (rid.table, rid.recno, _fmt_key(info.key))
        )
        reasons = result.retained[rid]
        if not reasons:
            lines.append(
                "      (retained: duplicate-key ambiguity among parent candidates)"
            )
        for reason in reasons:
            status = "marked" if reason.holder_deleted else "active"
            lines.append(
                "      held by %s [%s] via %s fk=%s"
                % (
                    reason.holder_label,
                    status,
                    reason.ref.name,
                    _fmt_key(reason.foreign_key),
                )
            )

    lines.append("")
    lines.append("Cyclic groups released together")
    lines.append("-" * 72)
    if not result.released_groups:
        lines.append("  (none)")
    for group in result.released_groups:
        labels = ", ".join(rows[rid].label for rid in group)
        lines.append("  group of %d: %s" % (len(group), labels))

    lines.append("")
    lines.append("Ambiguous references (duplicate parent key)")
    lines.append("-" * 72)
    if not result.ambiguous:
        lines.append("  (none)")
    for item in result.ambiguous:
        target_labels = ", ".join(rows[rid].label for rid in item.targets)
        lines.append(
            "  %s via %s fk=%s matches %d parents, all retained: %s"
            % (
                item.holder_label,
                item.ref.name,
                _fmt_key(item.foreign_key),
                len(item.targets),
                target_labels,
            )
        )

    lines.append("")
    lines.append("Dangling foreign keys (no matching parent, informational)")
    lines.append("-" * 72)
    if not result.dangling:
        lines.append("  (none)")
    for item in result.dangling:
        lines.append(
            "  %s via %s fk=%s has no parent in %s"
            % (
                item.holder_label,
                item.ref.name,
                _fmt_key(item.foreign_key),
                item.ref.parent_table,
            )
        )

    if hashes_before is not None and hashes_after is not None:
        lines.append("")
        lines.append("File integrity around the dry run")
        lines.append("-" * 72)
        identical = hashes_before == hashes_after
        for path in sorted(hashes_before):
            same = hashes_before[path] == hashes_after.get(path)
            lines.append(
                "  %s  %s" % ("IDENTICAL" if same else "CHANGED  ", path)
            )
            lines.append("    before %s" % hashes_before[path])
            lines.append("    after  %s" % hashes_after.get(path))
        lines.append("")
        lines.append(
            "RESULT: all files byte-identical before and after the dry run."
            if identical
            else "RESULT: WARNING -- a file changed during the dry run!"
        )

    lines.append("=" * 72)
    return "\n".join(lines)
