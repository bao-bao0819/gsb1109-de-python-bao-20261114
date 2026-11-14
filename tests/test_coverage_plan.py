from __future__ import absolute_import

import importlib.util
import os
from datetime import date, timedelta
from unittest import TestCase


def load_module():
    path = os.path.join(os.path.dirname(__file__), os.pardir,
                        'utils', 'ofx_coverage_plan.py')
    spec = importlib.util.spec_from_file_location('ofx_coverage_plan', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ocp = load_module()


class TestMergeWindows(TestCase):
    def test_overlapping_and_touching_windows_merge(self):
        windows = [(date(2017, 1, 1), date(2017, 1, 31), 'a'),
                   (date(2017, 2, 1), date(2017, 2, 28), 'b'),
                   (date(2017, 2, 15), date(2017, 3, 15), 'c')]
        self.assertEqual(ocp.merge_windows(windows),
                         [(date(2017, 1, 1), date(2017, 3, 15))])

    def test_disjoint_windows_stay_separate(self):
        windows = [(date(2017, 1, 1), date(2017, 1, 31), 'a'),
                   (date(2017, 3, 1), date(2017, 3, 31), 'b')]
        self.assertEqual(len(ocp.merge_windows(windows)), 2)


class TestFindGaps(TestCase):
    def test_gap_between_windows(self):
        merged = [(date(2011, 6, 25), date(2011, 7, 27)),
                  (date(2012, 7, 10), date(2012, 9, 8))]
        self.assertEqual(ocp.find_gaps(merged),
                         [(date(2011, 7, 28), date(2012, 7, 9))])

    def test_single_window_has_no_gap(self):
        self.assertEqual(ocp.find_gaps([(date(2017, 1, 1), date(2017, 1, 31))]),
                         [])


class TestPlanBackfill(TestCase):
    def setUp(self):
        self.today = date(2026, 11, 14)

    def test_union_covers_gap_and_respects_span_and_overlap(self):
        gap = (date(2011, 7, 28), date(2012, 7, 9))
        chunks = ocp.plan_backfill(gap[0], gap[1], max_span_days=31,
                                   overlap_days=3, today=self.today,
                                   lower_bound=date(2011, 6, 25),
                                   upper_bound=date(2012, 9, 8))
        for start, end in chunks:
            self.assertLessEqual((end - start).days + 1, 31)
        for (prev_start, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
            self.assertEqual((prev_end - next_start).days + 1, 3)
        self.assertEqual(chunks[0][0], date(2011, 7, 25))
        self.assertGreaterEqual(chunks[-1][1], gap[1])
        self.assertLessEqual(chunks[-1][1], gap[1] + timedelta(days=3))
        merged = [(date(2011, 6, 25), date(2011, 7, 27)),
                  (date(2012, 7, 10), date(2012, 9, 8))]
        self.assertEqual(ocp.verify_plan([gap], chunks, merged, 31, 3), [])

    def test_small_gap_single_request(self):
        gap = (date(2020, 3, 10), date(2020, 3, 20))
        chunks = ocp.plan_backfill(gap[0], gap[1], max_span_days=31,
                                   overlap_days=3, today=self.today)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][0], date(2020, 3, 7))
        self.assertEqual(chunks[0][1], date(2020, 3, 23))

    def test_gap_beyond_today_rejected(self):
        with self.assertRaises(ValueError):
            ocp.plan_backfill(date(2020, 1, 1), self.today + timedelta(days=1),
                              today=self.today)

    def test_verify_flags_uncovered_gap(self):
        gap = (date(2020, 1, 1), date(2020, 1, 31))
        chunks = [(date(2020, 1, 1), date(2020, 1, 10))]
        problems = ocp.verify_plan([gap], chunks, [], 31, 3)
        self.assertTrue(problems)

    def test_verify_flags_full_month_repull(self):
        merged = [(date(2020, 1, 1), date(2020, 1, 31))]
        chunks = [(date(2020, 1, 1), date(2020, 1, 31))]
        problems = ocp.verify_plan([], chunks, merged, 31, 3)
        self.assertTrue(any('整月' in p or 'month' in p for p in problems))


class TestScanArchive(TestCase):
    def fixture(self, name):
        return os.path.join(os.path.dirname(__file__), 'fixtures', name)

    def test_zero_transaction_declared_window_counts(self):
        accounts, _ = ocp.scan_archive([self.fixture('td_ameritrade.ofx')])
        entry = accounts['121212121']
        self.assertEqual(len(entry['windows']), 1)
        self.assertEqual(entry['windows'][0][:2],
                         (date(2017, 11, 30), date(2017, 12, 3)))
        self.assertEqual(entry['unproven'], [])

    def test_undeclared_window_is_unproven(self):
        accounts, _ = ocp.scan_archive(
            [self.fixture('account_listing_aggregation.ofx')])
        for entry in accounts.values():
            self.assertEqual(entry['windows'], [])
            self.assertEqual(len(entry['unproven']), 1)
