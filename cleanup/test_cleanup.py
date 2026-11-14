"""
Tests for the referential-integrity cleanup.

The fixture (cleanup/fixture.py) is a hand-built oracle: every cleanup
rule from the handoff brief has at least one dedicated scenario row, and
EXPECTED_* constants encode the correct answer derived independently.
"""

import hashlib
import os

import dbf
import pytest

from cleanup.apply import apply_cleanup, expected_survivors
from cleanup.fixture import (
    EXPECTED_AMBIGUOUS_TARGETS,
    EXPECTED_CYCLE,
    EXPECTED_DANGLING,
    EXPECTED_REMOVABLE,
    EXPECTED_RETAINED,
    SCENARIO,
)
from cleanup.model import (
    RowId,
    analyze,
    hash_files,
    is_blank_key,
    load_snapshot,
    normalize,
    schema_files,
)
from cleanup.report import render_report
from cleanup.schema import SCHEMA


def _ids(table, recnos):
    return {RowId(table, recno) for recno in recnos}


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# loading / snapshot
# --------------------------------------------------------------------------- #

def test_snapshot_sees_every_physical_row_including_marked(fixture_dir):
    snapshot = load_snapshot(fixture_dir, SCHEMA)
    for spec in SCHEMA.tables:
        expected_rows = len(SCENARIO[spec.name])
        actual_rows = len(snapshot.order[spec.name])
        assert actual_rows == expected_rows
        deleted_flags = [
            snapshot.rows[rid].deleted for rid in snapshot.order[spec.name]
        ]
        assert deleted_flags == [flag for _, flag in SCENARIO[spec.name]]


def test_blank_and_whitespace_keys_normalize_to_blank():
    assert is_blank_key(("",))
    assert is_blank_key((normalize("   "),))
    assert normalize(None) == ""
    assert not is_blank_key(("C00001",))


# --------------------------------------------------------------------------- #
# core rule: maximum safe removal set
# --------------------------------------------------------------------------- #

def test_removable_set_matches_oracle(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    expected = set()
    for table, recnos in EXPECTED_REMOVABLE.items():
        expected |= _ids(table, recnos)
    assert result.removable == expected


def test_active_rows_are_never_removable(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    for rid in result.removable:
        assert result.rows[rid].deleted is True


def test_removable_set_is_maximal(fixture_dir):
    """No surviving row references any removable row.

    If a surviving row pointed at a removable row, the fixed point would
    have kept that row; so an empty set of such edges proves the removal
    set cannot grow without breaking a live reference.
    """

    result = analyze(fixture_dir, SCHEMA)
    for holder, targets in result.edges.items():
        if holder in result.removable:
            continue
        for target in targets:
            assert target not in result.removable, (
                "surviving %s references removable %s" % (holder, target)
            )


def test_blank_foreign_key_is_not_a_reference(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    # O1005 (ORD#4) has a blank CUSTID: nothing holds it.
    assert RowId("ORD", 4) in result.removable
    # I005 (ITM#4) is active with blank FKs: active rows always survive.
    assert RowId("ITM", 4) not in result.removable
    assert result.rows[RowId("ITM", 4)].deleted is False


# --------------------------------------------------------------------------- #
# retention with explicit holder tracing
# --------------------------------------------------------------------------- #

def test_retained_marked_rows_match_oracle(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    expected = set()
    for table, recnos in EXPECTED_RETAINED.items():
        expected |= _ids(table, recnos)
    assert set(result.retained) == expected


def test_holder_reasons_identify_exact_surviving_records(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)

    c3 = result.retained[RowId("CUST", 2)]
    holders = {(reason.holder, reason.ref.name) for reason in c3}
    assert (RowId("ORD", 0), "ORD_CUST") in holders
    assert (RowId("ITM", 0), "ITM_CUST") in holders
    assert all(not reason.holder_deleted for reason in c3)

    # transitive case: O1003 is itself marked, but survives because the
    # active item I002 holds it; it then keeps customer C00004.
    o3 = result.retained[RowId("ORD", 2)]
    assert len(o3) == 1
    assert o3[0].holder == RowId("ITM", 1)
    assert o3[0].holder_deleted is False

    c4 = result.retained[RowId("CUST", 3)]
    chain = [reason for reason in c4 if reason.holder == RowId("ORD", 2)]
    assert chain and chain[0].holder_deleted is True
    direct = [reason for reason in c4 if reason.holder == RowId("ITM", 1)]
    assert direct and direct[0].holder_deleted is False


# --------------------------------------------------------------------------- #
# cyclic garbage released together
# --------------------------------------------------------------------------- #

def test_marked_cycle_with_no_outside_user_is_released(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    flattened = {rid for group in result.released_groups for rid in group}
    assert flattened == {RowId(table, recno) for table, recno in EXPECTED_CYCLE}
    # both cycle members are in the removable set as one group
    assert len(result.released_groups) == 1
    assert RowId("CUST", 4) in result.removable
    assert RowId("CUST", 5) in result.removable


def test_cycle_reached_from_active_row_would_be_kept(tmp_dir):
    """A marked cycle anchored by an active row must be retained."""

    from cleanup.fixture import build_fixture

    data = build_fixture(os.path.join(tmp_dir, "data"))
    path = os.path.join(data, "orders.dbf")
    table = dbf.Table(path, codepage="utf8")
    table.open(dbf.READ_WRITE)
    try:
        # an active order referencing C00005 anchors the whole cycle
        table.append(("O2000", "C00005"))
    finally:
        table.close()

    result = analyze(data, SCHEMA)
    assert RowId("CUST", 4) not in result.removable
    assert RowId("CUST", 5) not in result.removable
    assert result.retained[RowId("CUST", 5)][0].holder == RowId("CUST", 4)


# --------------------------------------------------------------------------- #
# ambiguous duplicate parent key
# --------------------------------------------------------------------------- #

def test_duplicate_parent_key_conservatively_retains_every_parent(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    for table, recno in EXPECTED_AMBIGUOUS_TARGETS:
        rid = RowId(table, recno)
        assert rid in result.retained
        reason = result.retained[rid][0]
        assert reason.holder == RowId("ORD", 5)
    assert len(result.ambiguous) == 1
    ambiguous = result.ambiguous[0]
    assert set(ambiguous.targets) == {RowId("CUST", 6), RowId("CUST", 7)}


def test_dangling_foreign_keys_are_reported_not_crash(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    found = {
        (item.holder.table, item.holder.recno, item.ref.name)
        for item in result.dangling
    }
    assert EXPECTED_DANGLING <= found


# --------------------------------------------------------------------------- #
# dry run must not modify files
# --------------------------------------------------------------------------- #

def test_dry_run_leaves_files_byte_identical(fixture_dir):
    paths = schema_files(fixture_dir, SCHEMA)
    before = hash_files(paths)
    analyze(fixture_dir, SCHEMA)
    analyze(fixture_dir, SCHEMA)  # repeat runs are deterministic
    after = hash_files(paths)
    assert before == after
    for path in paths:
        assert _hash(path) == before[path]


def test_report_renders_and_documents_integrity(fixture_dir):
    paths = schema_files(fixture_dir, SCHEMA)
    before = hash_files(paths)
    result = analyze(fixture_dir, SCHEMA)
    after = hash_files(paths)
    text = render_report(result, hashes_before=before, hashes_after=after)
    assert "Maximum safe removal set (10 rows)" in text
    assert "byte-identical" in text
    assert "C00005" in text and "C00009" in text
    assert "held by" in text


# --------------------------------------------------------------------------- #
# physical cleanup
# --------------------------------------------------------------------------- #

def test_apply_packs_removable_rows_and_preserves_kept_markers(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    counts = apply_cleanup(fixture_dir, SCHEMA, result)
    assert counts == {"CUST": 4, "ORD": 3, "ITM": 3}

    expected = expected_survivors(result)
    actual_rows = 0
    for spec in SCHEMA.tables:
        path = os.path.join(fixture_dir, spec.filename)
        table = dbf.Table(path, codepage="utf8")
        table.open(dbf.READ_ONLY)
        try:
            for record in table:
                key = tuple(normalize(record[name]) for name in spec.key_fields)
                bucket = (spec.name, key, dbf.is_deleted(record))
                assert expected[bucket] > 0
                expected[bucket] -= 1
                actual_rows += 1
        finally:
            table.close()
    assert all(count == 0 for count in expected.values())
    assert actual_rows == sum(len(v) for v in SCENARIO.values()) - 10


def test_retained_rows_keep_their_deletion_marker_after_pack(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    apply_cleanup(fixture_dir, SCHEMA, result)

    table = dbf.Table(os.path.join(fixture_dir, "customers.dbf"), codepage="utf8")
    table.open(dbf.READ_ONLY)
    try:
        markers = [
            (str(record.CUSTID).strip(), dbf.is_deleted(record)) for record in table
        ]
    finally:
        table.close()
    assert markers == [
        ("C00001", False),
        ("C00002", False),
        ("C00003", True),
        ("C00004", True),
        ("C00009", True),   # both duplicate-key physical rows survive
        ("C00009", True),
    ]
    assert sum(1 for _, flag in markers if flag) == 4


def test_apply_is_idempotent(fixture_dir):
    first = analyze(fixture_dir, SCHEMA)
    apply_cleanup(fixture_dir, SCHEMA, first)

    second = analyze(fixture_dir, SCHEMA)
    assert second.removable == set()
    paths = schema_files(fixture_dir, SCHEMA)
    before = hash_files(paths)
    counts = apply_cleanup(fixture_dir, SCHEMA, second)
    after = hash_files(paths)
    assert counts == {"CUST": 0, "ORD": 0, "ITM": 0}
    assert before == after


def test_post_apply_reanalysis_finds_nothing_removable(fixture_dir):
    result = analyze(fixture_dir, SCHEMA)
    apply_cleanup(fixture_dir, SCHEMA, result)
    post = analyze(fixture_dir, SCHEMA)
    assert post.removable == set()
    # retained marked rows are still reported as held after compaction
    assert set(post.retained) == {
        RowId("CUST", 2),
        RowId("CUST", 3),
        RowId("CUST", 4),
        RowId("CUST", 5),
        RowId("ORD", 2),
    }
