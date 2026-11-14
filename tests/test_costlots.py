from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase

import six

from ofxparse import OfxParser
from ofxparse.costlots import (
    Lot, Sale, Snapshot, InfeasibleAllocationError, analyze,
    analyze_statement, format_report, lots_and_sales_from_statement,
)

D = Decimal


def lot(lot_id, units, cost, day):
    return Lot(lot_id, 'XYZ', date(2026, 1, day), D(units), D(cost))


def sale(sale_id, units, day):
    return Sale(sale_id, 'XYZ', date(2026, 2, day), D(units))


class TestCostRange(TestCase):
    def test_unconstrained_sales_span_every_lot_combination(self):
        lots = [lot('L1', 100, 10, 1), lot('L2', 100, 20, 2)]
        sales = [sale('S1', 60, 1), sale('S2', 60, 2)]

        report = analyze(lots, sales)['XYZ']

        self.assertEqual(2, len(report.ranges))
        for result in report.ranges:
            self.assertEqual(D(600), result.min_cost)
            self.assertEqual(D(1200), result.max_cost)
            self.assertEqual(D(10), result.min_unit_cost)
            self.assertEqual(D(20), result.max_unit_cost)
            self.assertFalse(result.cost_is_exact)
            self.assertEqual([], result.forced_allocations)
            # Each bound is a real allocation of the full sale quantity.
            self.assertEqual(D(60), sum(result.min_cost_allocation.values()))
            self.assertEqual(D(60), sum(result.max_cost_allocation.values()))

    def test_lot_shares_cannot_be_spent_by_two_sales(self):
        # One lot of 100 shares cannot cover two sales of 60 each.
        lots = [lot('L1', 100, 10, 1)]
        sales = [sale('S1', 60, 1), sale('S2', 60, 2)]

        with self.assertRaises(InfeasibleAllocationError):
            analyze(lots, sales)

    def test_forced_allocation_when_one_lot_is_too_small(self):
        lots = [lot('L1', 40, 10, 1), lot('L2', 100, 20, 2)]
        sales = [sale('S1', 60, 5)]

        result = analyze(lots, sales)['XYZ'].ranges[0]

        # Cheapest uses all 40 of L1 then 20 of L2; priciest is all L2.
        self.assertEqual(D(800), result.min_cost)
        self.assertEqual(D(1200), result.max_cost)
        forced = dict((f.lot_id, f.units)
                      for f in result.forced_allocations)
        self.assertEqual({'L2': D(20)}, forced)
        forced_l2 = next(f for f in result.forced_allocations
                         if f.lot_id == 'L2')
        self.assertEqual(D(400), forced_l2.cost)

    def test_lot_bought_after_a_sale_cannot_fund_it(self):
        lots = [
            Lot('L1', 'XYZ', date(2026, 1, 1), D(100), D(10)),
            Lot('L2', 'XYZ', date(2026, 2, 15), D(100), D(1)),
        ]
        sales = [Sale('S1', 'XYZ', date(2026, 2, 1), D(60))]

        result = analyze(lots, sales)['XYZ'].ranges[0]

        # L2 does not exist yet on the sale date, so the cost is pinned
        # to L1 even though L2 is far cheaper.
        self.assertEqual(D(600), result.min_cost)
        self.assertEqual(D(600), result.max_cost)
        self.assertTrue(result.cost_is_exact)
        forced = dict((f.lot_id, f.units)
                      for f in result.forced_allocations)
        self.assertEqual({'L1': D(60)}, forced)

    def test_not_enough_held_shares_is_infeasible(self):
        with self.assertRaises(InfeasibleAllocationError):
            analyze([lot('L1', 10, 10, 1)], [sale('S1', 60, 1)])

    def test_fractional_shares_are_supported(self):
        lots = [Lot('L1', 'XYZ', date(2026, 1, 1), D('10.5'), D(100))]
        sales = [Sale('S1', 'XYZ', date(2026, 2, 1), D('0.25'))]

        result = analyze(lots, sales)['XYZ'].ranges[0]

        self.assertEqual(D('25.00'), result.min_cost)
        self.assertEqual(D('25.00'), result.max_cost)


class TestSnapshots(TestCase):
    def setUp(self):
        self.lots = [lot('L1', 100, 10, 1), lot('L2', 100, 20, 2)]
        self.sales = [sale('S1', 60, 1), sale('S2', 60, 2)]

    def test_snapshot_excludes_previously_feasible_allocations(self):
        without = analyze(self.lots, self.sales)['XYZ'].ranges[0]
        self.assertEqual(D(600), without.min_cost)

        # End of S1's day: 70 L1 and 70 L2 remain (140 of the original
        # 200 shares), so S1 consumed exactly 30 from each lot.  The
        # all-L1 minimum (cost 600) is no longer reachable.
        snapshot = Snapshot(date(2026, 2, 1),
                            {'L1': D(70), 'L2': D(70)})
        first = analyze(self.lots, self.sales, [snapshot])['XYZ'].ranges[0]

        self.assertEqual(D(900), first.min_cost)
        self.assertEqual(D(900), first.max_cost)
        self.assertTrue(first.cost_is_exact)
        forced = dict((f.lot_id, f.units)
                      for f in first.forced_allocations)
        self.assertEqual({'L1': D(30), 'L2': D(30)}, forced)

    def test_snapshot_between_sales_only_constrains_earlier_sale(self):
        snapshot = Snapshot(date(2026, 2, 1),
                            {'L1': D(70), 'L2': D(70)})
        ranges = analyze(self.lots, self.sales, [snapshot])['XYZ'].ranges

        second = ranges[1]
        self.assertEqual(D(600), second.min_cost)
        self.assertEqual(D(1200), second.max_cost)
        self.assertEqual([], second.forced_allocations)

    def test_snapshot_forces_a_specific_lot_into_a_later_sale(self):
        # After S1 all of L1 is gone, so S2 must be settled from L2.
        snapshot = Snapshot(date(2026, 2, 1),
                            {'L1': D(40), 'L2': D(100)})
        ranges = analyze(self.lots, self.sales, [snapshot])['XYZ'].ranges

        first, second = ranges
        self.assertEqual(D(600), first.min_cost)
        self.assertEqual(D(600), first.max_cost)
        self.assertEqual(D(800), second.min_cost)
        self.assertEqual(D(1200), second.max_cost)
        forced = dict((f.lot_id, f.units)
                      for f in second.forced_allocations)
        self.assertEqual({'L2': D(20)}, forced)

    def test_final_snapshot_must_match_total_sold_quantity(self):
        # 120 shares were sold but the snapshot claims both lots remain.
        snapshot = Snapshot(date(2026, 2, 2),
                            {'L1': D(0), 'L2': D(0)})
        with self.assertRaises(InfeasibleAllocationError):
            analyze(self.lots, self.sales, [snapshot])

    def test_snapshot_reading_larger_than_the_lot_is_infeasible(self):
        snapshot = Snapshot(date(2026, 2, 1),
                            {'L1': D(101), 'L2': D(100)})
        with self.assertRaises(InfeasibleAllocationError):
            analyze(self.lots, self.sales, [snapshot])

    def test_snapshot_referencing_unknown_lot_is_rejected(self):
        snapshot = Snapshot(date(2026, 2, 1), {'GHOST': D(10)})
        with self.assertRaises(ValueError):
            analyze(self.lots, self.sales, [snapshot])

    def test_snapshot_before_a_lot_exists_ignores_that_lot(self):
        lots = [lot('L1', 100, 10, 10), lot('L2', 100, 20, 20)]
        snapshot = Snapshot(date(2026, 1, 5), {'L1': D(100), 'L2': D(100)})
        # Must not raise: L2 had not been bought when the snapshot was
        # taken, so its reading is simply irrelevant.
        report = analyze(lots, self.sales, [snapshot])['XYZ']
        self.assertEqual(2, len(report.ranges))

    def test_snapshot_before_any_sale_pins_the_source_edge(self):
        lots = [lot('L1', 100, 10, 1)]
        sales = [sale('S1', 60, 10)]

        # No sale has happened yet, so the whole lot must still be there.
        ok = Snapshot(date(2026, 1, 20), {'L1': D(100)})
        self.assertEqual(1, len(analyze(lots, sales, [ok])['XYZ'].ranges))

        # Shares cannot vanish before the first recorded sale.
        bad = Snapshot(date(2026, 1, 20), {'L1': D(90)})
        with self.assertRaises(InfeasibleAllocationError):
            analyze(lots, sales, [bad])

    def test_two_snapshots_on_different_dates_both_apply(self):
        # After S1: 70/70 remain; after S2: 40/40 remain.
        first = Snapshot(date(2026, 2, 1), {'L1': D(70), 'L2': D(70)})
        second = Snapshot(date(2026, 2, 2), {'L1': D(40), 'L2': D(40)})
        ranges = analyze(self.lots, self.sales, [first, second])['XYZ'].ranges

        for result in ranges:
            self.assertTrue(result.cost_is_exact)
        # S1 is forced to 30+30; S2 must then take the remaining 30+30.
        self.assertEqual(
            {'L1': D(30), 'L2': D(30)},
            dict((f.lot_id, f.units)
                 for f in ranges[0].forced_allocations))
        self.assertEqual(
            {'L1': D(30), 'L2': D(30)},
            dict((f.lot_id, f.units)
                 for f in ranges[1].forced_allocations))

    def test_snapshot_may_cover_only_some_lots(self):
        # Three lots, but the snapshot reports L1 only; L2 and L3 stay
        # unconstrained by it.
        lots = [lot('L1', 100, 10, 1), lot('L2', 100, 20, 2),
                lot('L3', 100, 30, 3)]
        sales = [sale('S1', 60, 5)]
        snapshot = Snapshot(date(2026, 2, 5), {'L1': D(70)})

        first = analyze(lots, sales, [snapshot])['XYZ'].ranges[0]

        # 30 L1 shares are forced; the other 30 may come from L2 or L3.
        forced = dict((f.lot_id, f.units)
                      for f in first.forced_allocations)
        self.assertEqual({'L1': D(30)}, forced)
        self.assertEqual(D(900), first.min_cost)
        self.assertEqual(D(1200), first.max_cost)

    def test_combined_allocations_never_exceed_lot_capacities(self):
        snapshot = Snapshot(date(2026, 2, 1),
                            {'L1': D(70), 'L2': D(70)})
        report = analyze(self.lots, self.sales, [snapshot])['XYZ']
        for bound in ('min_cost_allocation', 'max_cost_allocation'):
            used = {}
            for result in report.ranges:
                for lot_id, units in getattr(result, bound).items():
                    used[lot_id] = used.get(lot_id, D(0)) + units
            for lot_id, units in used.items():
                self.assertLessEqual(units, D(100))


class TestMultipleSecurities(TestCase):
    def test_each_security_is_solved_independently(self):
        lots = [
            Lot('L1', 'XYZ', date(2026, 1, 1), D(100), D(10)),
            Lot('L2', 'ABC', date(2026, 1, 1), D(50), D(5)),
        ]
        sales = [
            Sale('SX', 'XYZ', date(2026, 2, 1), D(60)),
            Sale('SA', 'ABC', date(2026, 2, 1), D(50)),
        ]

        reports = analyze(lots, sales)

        self.assertEqual(set(['XYZ', 'ABC']), set(reports))
        self.assertEqual(D(600), reports['XYZ'].ranges[0].min_cost)
        self.assertEqual(D(250), reports['ABC'].ranges[0].min_cost)

    def test_snapshots_are_partitioned_by_security(self):
        lots = [
            Lot('L1', 'XYZ', date(2026, 1, 1), D(100), D(10)),
            Lot('L2', 'ABC', date(2026, 1, 1), D(50), D(5)),
        ]
        sales = [
            Sale('SX', 'XYZ', date(2026, 2, 1), D(60)),
            Sale('SA', 'ABC', date(2026, 2, 1), D(50)),
        ]
        snapshot = Snapshot(date(2026, 2, 1),
                            {'L1': D(40), 'L2': D(0)})

        reports = analyze(lots, sales, [snapshot])

        xyz_forced = dict(
            (f.lot_id, f.units)
            for f in reports['XYZ'].ranges[0].forced_allocations)
        abc_forced = dict(
            (f.lot_id, f.units)
            for f in reports['ABC'].ranges[0].forced_allocations)
        self.assertEqual({'L1': D(60)}, xyz_forced)
        self.assertEqual({'L2': D(50)}, abc_forced)


class TestStatementAnalysis(TestCase):
    OFX = '''<?OFX OFXHEADER="200" VERSION="200" SECURITY="NONE"
 OLDFILEUID="NONE" NEWFILEUID="NONE" ?>
<OFX>
 <SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS>
  <DTSERVER>20260310000000<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>
 <INVSTMTMSGSRSV1><INVSTMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
 <INVSTMTRS><CURDEF>USD<INVACCTFROM><BROKERID>b<ACCTID>a</INVACCTFROM>
 <INVTRANLIST><DTSTART>20260101<DTEND>20260310
 <BUYSTOCK><INVBUY><INVTRAN><FITID>buy-1<DTTRADE>20260105000000</INVTRAN>
  <SECID><UNIQUEID>XYZ<UNIQUEIDTYPE>CUSIP</SECID>
  <UNITS>100<UNITPRICE>10.00<COMMISSION>0<FEES>0<TOTAL>-1000.00</INVBUY>
  <BUYTYPE>BUY</BUYSTOCK>
 <BUYSTOCK><INVBUY><INVTRAN><FITID>buy-2<DTTRADE>20260205000000</INVTRAN>
  <SECID><UNIQUEID>XYZ<UNIQUEIDTYPE>CUSIP</SECID>
  <UNITS>100<UNITPRICE>20.00<COMMISSION>10.00<FEES>0<TOTAL>-2010.00</INVBUY>
  <BUYTYPE>BUY</BUYSTOCK>
 <REINVEST><INVTRAN><FITID>reinvest-1<DTTRADE>20260210000000</INVTRAN>
  <SECID><UNIQUEID>XYZ<UNIQUEIDTYPE>CUSIP</SECID>
  <UNITS>5<UNITPRICE>20.00<TOTAL>-100.00</INVTRAN></REINVEST>
 <SELLSTOCK><INVSELL><INVTRAN><FITID>sell-1<DTTRADE>2026022000000</INVTRAN>
  <SECID><UNIQUEID>XYZ<UNIQUEIDTYPE>CUSIP</SECID>
  <UNITS>-60<UNITPRICE>50.00<COMMISSION>5.00<FEES>0<TOTAL>2995.00</INVSELL>
  <SELLTYPE>SELL</SELLSTOCK>
 </INVTRANLIST></INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1>
</OFX>'''

    def _statement(self):
        ofx = OfxParser.parse(six.BytesIO(six.b(self.OFX)))
        return ofx.account.statement

    def test_statement_is_split_into_lots_and_sales(self):
        lots, sales = lots_and_sales_from_statement(self._statement())

        self.assertEqual(['buy-1', 'buy-2', 'reinvest-1'],
                         [lot.id for lot in lots])
        self.assertEqual(['sell-1'], [sale.id for sale in sales])
        self.assertEqual(D(60), sales[0].units)
        self.assertEqual(datetime(2026, 1, 5), lots[0].date)

    def test_cash_basis_loads_commission_into_lot_cost(self):
        lots, _ = lots_and_sales_from_statement(
            self._statement(), basis='cash')

        self.assertEqual(D(10), lots[0].unit_cost)
        self.assertEqual(D('20.10'), lots[1].unit_cost)

        report = analyze(lots,
                         [Sale('sell-1', 'XYZ',
                               datetime(2026, 2, 20), D(60))])['XYZ']
        result = report.ranges[0]
        self.assertEqual(D(600), result.min_cost)
        self.assertEqual(D('1206.00'), result.max_cost)

    def test_price_basis_ignores_commission(self):
        lots, _ = lots_and_sales_from_statement(
            self._statement(), basis='price')

        self.assertEqual(D(20), lots[1].unit_cost)

    def test_invalid_basis_is_rejected(self):
        with self.assertRaises(ValueError):
            lots_and_sales_from_statement(self._statement(), basis='fifo')

    def test_analyze_statement_applies_snapshot_by_fitid(self):
        # 205 shares were bought and 60 sold, so 145 remain: the
        # snapshot pins 30 consumed from each of the first two lots.
        snapshot = Snapshot(datetime(2026, 2, 20),
                            {'buy-1': D(70), 'buy-2': D(70),
                             'reinvest-1': D(5)})
        report = analyze_statement(
            self._statement(), snapshots=[snapshot])['XYZ']
        result = report.ranges[0]

        # 30 shares of the 10-dollar lot and 30 of the 20.10-dollar lot
        # are forced into this single sale.
        self.assertEqual(D('903.00'), result.min_cost)
        self.assertEqual(D('903.00'), result.max_cost)
        forced = dict((f.lot_id, f.units)
                      for f in result.forced_allocations)
        self.assertEqual({'buy-1': D(30), 'buy-2': D(30)}, forced)


class TestReportFormatting(TestCase):
    def test_report_lists_bounds_and_forced_shares(self):
        lots = [lot('L1', 100, 10, 1), lot('L2', 100, 20, 2)]
        sales = [sale('S1', 60, 1)]
        snapshot = Snapshot(date(2026, 2, 1),
                            {'L1': D(70), 'L2': D(70)})
        reports = analyze(lots, sales, [snapshot])

        text = format_report(reports)

        self.assertIn('sale S1', text)
        self.assertIn('900', text)
        self.assertIn('forced lot shares:', text)
        self.assertIn('L1: 30 units', text)
        self.assertIn('L2: 30 units', text)

    def test_report_states_when_assignment_is_not_determinable(self):
        lots = [lot('L1', 100, 10, 1), lot('L2', 100, 20, 2)]
        sales = [sale('S1', 60, 1)]

        text = format_report(analyze(lots, sales))

        self.assertIn('none (the lot assignment is not uniquely '
                      'determined)', text)
        self.assertNotIn('fifo', text.lower())


class TestPackageExports(TestCase):
    def test_public_api_is_exported_from_package(self):
        import ofxparse
        for name in ('Lot', 'Sale', 'Snapshot', 'SaleCostRange',
                     'ForcedAllocation', 'SecurityCostReport',
                     'InfeasibleAllocationError', 'analyze',
                     'analyze_statement',
                     'lots_and_sales_from_statement', 'format_report'):
            self.assertTrue(hasattr(ofxparse, name), name)
