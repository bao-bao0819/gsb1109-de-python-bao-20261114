"""
Referential-integrity analysis for deletion-marked DBF rows.

The analysis works on an in-memory snapshot read from the .dbf files
and never writes to them.  Every physical row is represented by a
:class:`RowId`; foreign-key relations declared in :mod:`cleanup.schema`
become directed edges from a child row to the candidate parent row(s)
sharing the key.

Rules implemented:

* a row that is *not* deletion-marked always survives;
* a deletion-marked row survives as long as at least one surviving row
  references it -- references are resolved to a fixed point, so chains
  of marked rows ending in an active row are retained together;
* a reference whose key matches several parent rows (duplicate parent
  key, target ambiguous) holds *every* matching parent, conservatively;
* a group of marked rows that only reference each other (a cycle with
  no reference coming in from a surviving row) is released as a whole;
* blank / null foreign keys are not references.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

import dbf


# --------------------------------------------------------------------------- #
# configuration objects
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Ref:
    """One declared foreign-key relation.

    A row in ``child_table`` carrying ``child_fields`` values references
    the row(s) in ``parent_table`` carrying the same values in
    ``parent_fields``.
    """

    name: str
    child_table: str
    child_fields: tuple
    parent_table: str
    parent_fields: tuple


@dataclass(frozen=True)
class TableSpec:
    name: str
    filename: str
    key_fields: tuple


@dataclass(frozen=True)
class Schema:
    tables: tuple
    refs: tuple

    def table(self, name):
        for spec in self.tables:
            if spec.name == name:
                return spec
        raise KeyError("unknown table %r" % name)

    def field_groups(self, table_name):
        """All field tuples we need to read from a table."""

        groups = set()
        groups.add(tuple(self.table(table_name).key_fields))
        for ref in self.refs:
            if ref.child_table == table_name:
                groups.add(tuple(ref.child_fields))
            if ref.parent_table == table_name:
                groups.add(tuple(ref.parent_fields))
        return groups


def make_schema(tables, refs):
    """Build a :class:`Schema`, normalizing dBase field names to upper case."""

    normalized_tables = tuple(
        TableSpec(
            name=spec.name,
            filename=spec.filename,
            key_fields=tuple(field_name.upper() for field_name in spec.key_fields),
        )
        for spec in tables
    )
    normalized_refs = tuple(
        Ref(
            name=ref.name,
            child_table=ref.child_table,
            child_fields=tuple(field_name.upper() for field_name in ref.child_fields),
            parent_table=ref.parent_table,
            parent_fields=tuple(field_name.upper() for field_name in ref.parent_fields),
        )
        for ref in refs
    )
    schema = Schema(tables=normalized_tables, refs=normalized_refs)
    _validate(schema)
    return schema


def _validate(schema):
    names = {spec.name for spec in schema.tables}
    for ref in schema.refs:
        if ref.child_table not in names or ref.parent_table not in names:
            raise ValueError("ref %r points at an unknown table" % ref.name)
        if len(ref.child_fields) != len(ref.parent_fields):
            raise ValueError("ref %r joins fields of different arity" % ref.name)
    for spec in schema.tables:
        if not spec.key_fields:
            raise ValueError("table %r needs at least one key field" % spec.name)


# --------------------------------------------------------------------------- #
# analysis objects
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, order=True)
class RowId:
    table: str
    recno: int  # 0-based physical record number, dbf.recno()


@dataclass(frozen=True)
class RowInfo:
    rid: RowId
    deleted: bool
    key: tuple            # normalized values of the table's key_fields
    label: str            # human readable "table#recno(key=...)"


@dataclass(frozen=True)
class HoldReason:
    holder: RowId
    holder_label: str
    holder_deleted: bool
    ref: Ref
    foreign_key: tuple


@dataclass(frozen=True)
class DanglingRef:
    holder: RowId
    holder_label: str
    holder_deleted: bool
    ref: Ref
    foreign_key: tuple


@dataclass
class Snapshot:
    schema: Schema
    rows: dict
    field_values: dict       # RowId -> {field tuple: normalized tuple}
    order: dict              # table name -> [RowId] in physical order


@dataclass(frozen=True)
class AmbiguousRef:
    holder: RowId
    holder_label: str
    ref: Ref
    foreign_key: tuple
    targets: tuple          # every parent row sharing the key


@dataclass
class AnalysisResult:
    schema: Schema
    rows: dict
    removable: set
    retained: dict           # marked-but-kept RowId -> [HoldReason]
    dangling: list
    ambiguous: list          # refs matching more than one parent row
    released_groups: list    # removable SCCs that reference each other
    edges: dict = field(default_factory=dict)

    def table_rows(self, table_name):
        return sorted(
            (info for info in self.rows.values() if info.rid.table == table_name),
            key=lambda info: info.rid.recno,
        )

    def marked(self, table_name):
        return [info for info in self.table_rows(table_name) if info.deleted]


# --------------------------------------------------------------------------- #
# value normalization / file identity helpers
# --------------------------------------------------------------------------- #

def normalize(value):
    """Normalize a field value for key comparison.

    Character fields come back as auto-trimming ``dbf.Char``; whitespace
    only and uninitialized (None) values become the blank key, which
    means "no foreign key".
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def is_blank_key(key):
    return all(part is None or part == "" for part in key)


def row_key(record, fields):
    return tuple(normalize(record[name]) for name in fields)


def _label(spec, recno, key):
    rendered = ", ".join(
        "%s=%r" % (field_name, value)
        for field_name, value in zip(spec.key_fields, key)
    )
    return "%s#%d(%s)" % (spec.name, recno, rendered)


def file_companions(path):
    """The .dbf plus its memo file(s), if any -- every file we guard."""

    companions = [path]
    stem = os.path.splitext(path)[0]
    for extension in (".dbt", ".fpt", ".DBT", ".FPT"):
        candidate = stem + extension
        if os.path.exists(candidate) and candidate not in companions:
            companions.append(candidate)
    return companions


def schema_files(data_dir, schema):
    paths = []
    for spec in schema.tables:
        paths.extend(file_companions(_resolve_filename(data_dir, spec)))
    return paths


def hash_files(paths):
    digests = {}
    for path in paths:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
        digests[path] = digest.hexdigest()
    return digests


def _resolve_filename(data_dir, spec):
    for candidate in (spec.filename, spec.filename.upper(), spec.filename.lower()):
        path = os.path.join(data_dir, candidate)
        if os.path.exists(path):
            return path
    raise FileNotFoundError("table file not found for %s" % spec.name)


# --------------------------------------------------------------------------- #
# read-only snapshot
# --------------------------------------------------------------------------- #

def load_snapshot(data_dir, schema):
    """Open every table READ_ONLY and snapshot its physical rows."""

    rows = {}
    field_values = {}
    order = {}

    for spec in schema.tables:
        path = _resolve_filename(data_dir, spec)
        groups = schema.field_groups(spec.name)
        table = dbf.Table(path, codepage="utf8", on_disk=True)
        table.open(dbf.READ_ONLY)
        physical_order = []
        try:
            for record in table:  # iteration includes deletion-marked rows
                recno = dbf.recno(record)
                rid = RowId(spec.name, recno)
                cache = {groups: row_key(record, groups) for groups in groups}
                field_values[rid] = cache
                key = cache[tuple(spec.key_fields)]
                rows[rid] = RowInfo(
                    rid=rid,
                    deleted=dbf.is_deleted(record),
                    key=key,
                    label=_label(spec, recno, key),
                )
                physical_order.append(rid)
        finally:
            table.close()
        order[spec.name] = physical_order

    return Snapshot(schema=schema, rows=rows, field_values=field_values, order=order)


# --------------------------------------------------------------------------- #
# core analysis
# --------------------------------------------------------------------------- #

def analyze_snapshot(snapshot):
    schema = snapshot.schema
    rows = snapshot.rows

    # parent lookup: (parent table, key) -> every row carrying that key.
    # Lists matter: duplicate parent keys must retain every possible
    # parent row.
    key_index = {}
    for info in rows.values():
        if is_blank_key(info.key):
            continue
        key_index.setdefault((info.rid.table, info.key), []).append(info.rid)

    edges = {}
    incoming = {}
    dangling = []
    ambiguous = []

    for ref in schema.refs:
        child_fields = tuple(ref.child_fields)
        for info in rows.values():
            if info.rid.table != ref.child_table:
                continue
            foreign_key = snapshot.field_values[info.rid][child_fields]
            if is_blank_key(foreign_key):
                continue
            targets = key_index.get((ref.parent_table, foreign_key))
            if not targets:
                dangling.append(
                    DanglingRef(
                        holder=info.rid,
                        holder_label=info.label,
                        holder_deleted=info.deleted,
                        ref=ref,
                        foreign_key=foreign_key,
                    )
                )
                continue
            if len(targets) > 1:
                ambiguous.append(
                    AmbiguousRef(
                        holder=info.rid,
                        holder_label=info.label,
                        ref=ref,
                        foreign_key=foreign_key,
                        targets=tuple(targets),
                    )
                )
            bucket = edges.setdefault(info.rid, [])
            for target in targets:
                if target not in bucket:
                    bucket.append(target)
                incoming.setdefault(target, []).append(
                    (info.rid, ref, foreign_key)
                )

    # Fixed point: every active row survives; a marked row survives once
    # any surviving row points at it.  Cycles of purely marked rows never
    # light up unless an outside survivor reaches them.
    survives = {rid for rid, info in rows.items() if not info.deleted}
    frontier = list(survives)
    while frontier:
        holder = frontier.pop()
        for target in edges.get(holder, ()):
            if target not in survives:
                survives.add(target)
                frontier.append(target)

    removable = {
        rid for rid, info in rows.items()
        if info.deleted and rid not in survives
    }

    retained = {}
    for rid in sorted(survives):
        info = rows[rid]
        if not info.deleted:
            continue
        reasons = []
        seen = set()
        for holder, ref, foreign_key in incoming.get(rid, ()):
            if holder not in survives:
                continue
            signature = (holder, ref.name, foreign_key)
            if signature in seen:
                continue
            seen.add(signature)
            holder_info = rows[holder]
            reasons.append(
                HoldReason(
                    holder=holder,
                    holder_label=holder_info.label,
                    holder_deleted=holder_info.deleted,
                    ref=ref,
                    foreign_key=foreign_key,
                )
            )
        retained[rid] = reasons

    released_groups = _cyclic_components(removable, edges)

    return AnalysisResult(
        schema=schema,
        rows=rows,
        removable=removable,
        retained=retained,
        dangling=dangling,
        ambiguous=ambiguous,
        released_groups=released_groups,
        edges=edges,
    )


def _cyclic_components(removable, edges):
    """Strongly connected components of removable rows that contain a cycle.

    A singleton counts when the row references itself; larger components
    are the "rows referencing each other, no longer used from outside"
    groups that may be released together.
    """

    nodes = sorted(removable, key=lambda rid: (rid.table, rid.recno))
    node_set = set(nodes)
    neighbors = {
        rid: [target for target in edges.get(rid, ()) if target in node_set]
        for rid in nodes
    }

    index = 0
    stack = []
    on_stack = set()
    indices = {}
    lowlink = {}
    components = []

    def strongconnect(start):
        nonlocal index
        work = [(start, iter(neighbors[start]))]
        indices[start] = lowlink[start] = index
        index += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, iterator = work[-1]
            advanced = False
            for nxt in iterator:
                if nxt not in indices:
                    indices[nxt] = lowlink[nxt] = index
                    index += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(neighbors[nxt])))
                    advanced = True
                    break
                if nxt in on_stack:
                    lowlink[node] = min(lowlink[node], indices[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == indices[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)

    for rid in nodes:
        if rid not in indices:
            strongconnect(rid)

    cyclic = []
    for component in components:
        if len(component) > 1:
            cyclic.append(sorted(component, key=lambda rid: (rid.table, rid.recno)))
        elif component[0] in neighbors.get(component[0], ()):
            cyclic.append(component)
    cyclic.sort(key=lambda group: [(rid.table, rid.recno) for rid in group])
    return cyclic


def analyze(data_dir, schema):
    return analyze_snapshot(load_snapshot(data_dir, schema))
