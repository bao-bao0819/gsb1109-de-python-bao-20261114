# -*- coding: utf-8 -*-
"""Tests for subtotalrecon: financial tables with city subtotals."""

from decimal import Decimal

import pytest
from odf.table import Table, TableRow
from odf.teletype import extractText

from subtotalrecon import Ledger, StructureError, parse_ods
from subtotalrecon.model import Detail, GrandTotal, Subtotal
from subtotalrecon.sample import build_document, write_sample


@pytest.fixture()
def sample_ledger(tmp_path):
    path = tmp_path / "financial.ods"
    write_sample(str(path))
    return parse_ods(str(path))


def test_ingest_splits_details_and_subtotal_members(sample_ledger):
    ledger = sample_ledger
    assert len(ledger.details) == 5
    assert [st.city for st in ledger.subtotals] == ["杭州", "南京", "苏州"]
    by_city = {st.city: st.member_ids for st in ledger.subtotals}
    assert by_city["杭州"] == ["r2", "r3"]
    assert by_city["南京"] == ["r5", "r6"]
    assert by_city["苏州"] == ["r8"]
    assert ledger.grand.row_id == "r10"


def test_recompute_counts_each_detail_once(sample_ledger):
    ledger = sample_ledger
    detail_sum = sum(d.amount for d in ledger.details.values())
    assert ledger.grand.computed == detail_sum == Decimal("700")
    # Not details + subtotals: the grand total must not double count.
    subtotal_sum = sum(st.computed for st in ledger.subtotals)
    assert subtotal_sum == detail_sum
    assert ledger.grand.computed != detail_sum + subtotal_sum


def test_manual_adjustment_is_preserved(sample_ledger):
    ledger = sample_ledger
    suzhou = ledger.subtotals[2]
    assert suzhou.stored == Decimal("230")
    assert suzhou.computed == Decimal("200")
    assert suzhou.adjustment == Decimal("30")
    assert ledger.grand.adjustment == Decimal("30")  # 730 stored - 700 computed


def test_adjacent_cities_with_equal_totals_stay_separate(sample_ledger):
    """Membership comes from row position, never from matching amounts."""
    ledger = sample_ledger
    hangzhou, nanjing = ledger.subtotals[0], ledger.subtotals[1]
    assert hangzhou.stored == nanjing.stored == Decimal("250")
    assert {ledger.details[m].store for m in hangzhou.member_ids} == {
        "杭州一店",
        "杭州二店",
    }
    assert {ledger.details[m].store for m in nanjing.member_ids} == {
        "南京一店",
        "南京二店",
    }
    assert set(hangzhou.member_ids).isdisjoint(nanjing.member_ids)


def test_declared_levels_match_row_groups(sample_ledger):
    assert [st.level for st in sample_ledger.subtotals] == [1, 1, 1]


def test_delete_detail_reports_affected_city_and_grand(sample_ledger):
    ledger = sample_ledger
    impact = ledger.delete_detail("r5")  # 南京一店, 90
    assert impact.city == "南京"
    assert impact.subtotal_row_id == "r7"
    assert impact.subtotal_computed_before == Decimal("250")
    assert impact.subtotal_computed_after == Decimal("160")
    assert impact.grand_computed_before == Decimal("700")
    assert impact.grand_computed_after == Decimal("610")
    # Stored values are untouched, so manual adjustments survive.
    nanjing = ledger.subtotals[1]
    assert nanjing.stored == Decimal("250")
    assert nanjing.adjustment == Decimal("90")
    assert ledger.grand.stored == Decimal("730")
    assert "r5" not in ledger.details


def test_delete_unknown_detail_raises(sample_ledger):
    with pytest.raises(KeyError):
        sample_ledger.delete_detail("r99")


def test_duplicate_membership_is_rejected():
    details = [Detail("r1", 1, "甲店", "杭州", Decimal("10"))]
    subtotals = [
        Subtotal("r2", 2, "杭州", 1, Decimal("10"), ["r1"]),
        Subtotal("r3", 3, "杭州", 1, Decimal("10"), ["r1"]),
    ]
    with pytest.raises(ValueError, match="roll up only once"):
        Ledger(details, subtotals, GrandTotal("r4", 4, Decimal("10")))


def test_details_without_subtotal_raise(tmp_path):
    doc = build_document()
    table = doc.spreadsheet.getElementsByType(Table)[0]
    # Remove every subtotal row, leaving orphan details before the total.
    for row in list(table.getElementsByType(TableRow)):
        if "小计" in extractText(row):
            table.removeChild(row)
    path = tmp_path / "broken.ods"
    doc.save(str(path))
    with pytest.raises(StructureError, match="never closed by a subtotal"):
        parse_ods(str(path))


def test_missing_grand_total_raises(tmp_path):
    doc = build_document()
    table = doc.spreadsheet.getElementsByType(Table)[0]
    for row in list(table.getElementsByType(TableRow)):
        if "总计" in extractText(row):
            table.removeChild(row)
    path = tmp_path / "nototal.ods"
    doc.save(str(path))
    with pytest.raises(StructureError, match="no grand total"):
        parse_ods(str(path))


def test_ungrouped_sheet_parses_positionally(tmp_path):
    """Without declared row groups, position alone assigns membership."""
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table

    from subtotalrecon.sample import _number_cell, _row, _text_cell

    doc = OpenDocumentSpreadsheet()
    table = Table(name="t")
    table.addElement(_row([_text_cell("甲店"), _number_cell("10")]))
    table.addElement(_row([_text_cell("乙店"), _number_cell("20")]))
    table.addElement(_row([_text_cell("苏州小计"), _number_cell("30")]))
    table.addElement(_row([_text_cell("总计"), _number_cell("30")]))
    doc.spreadsheet.addElement(table)
    path = tmp_path / "flat.ods"
    doc.save(str(path))

    ledger = parse_ods(str(path))
    assert len(ledger.details) == 2
    assert ledger.subtotals[0].member_ids == ["r1", "r2"]
    assert ledger.subtotals[0].city == "苏州"
    assert ledger.details["r1"].city == "苏州"  # inherited from subtotal
    assert ledger.grand.computed == Decimal("30")
