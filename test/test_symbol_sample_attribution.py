#-------------------------------------------------------------------------------
# elftools test: test_symbol_sample_attribution.py
#
# Unit tests for the symbol_sample_attribution example: alias grouping,
# zero-length range inference, disputed overlaps and per-section
# reconciliation of sample counts.
#
# This code is in the public domain
#-------------------------------------------------------------------------------
import importlib.util
import os
import unittest


def load_example_module():
    path = os.path.join(os.path.dirname(__file__), os.pardir, 'examples',
                        'symbol_sample_attribution.py')
    spec = importlib.util.spec_from_file_location(
        'symbol_sample_attribution', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ssa = load_example_module()


def make_section(groups, name='.text', start=0x1000, end=0x2000):
    return ssa.SectionInfo(name, start, end, groups)


def definite_counts(result):
    return {tuple(g.names): c for g, c in result['definite'].items()}


class TestBuildRanges(unittest.TestCase):
    def test_explicit_and_inferred(self):
        groups = ssa.build_ranges(
            [('a', 0x100, 0x20), ('b', 0x120, 0), ('c', 0x140, 0)], 0x180)
        by_name = {g.names[0]: g for g in groups}
        self.assertEqual((by_name['a'].start, by_name['a'].end),
                         (0x100, 0x120))
        self.assertFalse(by_name['a'].inferred)
        # Zero-length b is inferred up to the next start.
        self.assertEqual((by_name['b'].start, by_name['b'].end),
                         (0x120, 0x140))
        self.assertTrue(by_name['b'].inferred)
        # Zero-length c is the last symbol: inferred up to the section end.
        self.assertEqual((by_name['c'].start, by_name['c'].end),
                         (0x140, 0x180))
        self.assertTrue(by_name['c'].inferred)

    def test_aliases_share_one_group(self):
        groups = ssa.build_ranges(
            [('alias1', 0x100, 0x10), ('alias2', 0x100, 0x10)], 0x200)
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0].names), ['alias1', 'alias2'])

    def test_zero_length_alias_of_explicit_range(self):
        # A zero-length symbol whose inferred end matches an explicit range
        # joins that range as an alias, and the group stays explicit.
        groups = ssa.build_ranges(
            [('sized', 0x100, 0x20), ('unsized', 0x100, 0)], 0x200)
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0].names), ['sized', 'unsized'])
        self.assertFalse(groups[0].inferred)

    def test_same_name_locals_stay_separate(self):
        groups = ssa.build_ranges(
            [('foo', 0x100, 0x10), ('foo', 0x200, 0x10)], 0x300)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].start, 0x100)
        self.assertEqual(groups[1].start, 0x200)


class TestAttributeSamples(unittest.TestCase):
    def test_alias_group_gets_one_count(self):
        section = make_section(
            ssa.build_ranges([('a1', 0x1000, 0x40), ('a2', 0x1000, 0x40)],
                             0x2000))
        per_section, outside = ssa.attribute_samples([(0x1010, 7)],
                                                     [section])
        self.assertEqual(outside, 0)
        self.assertEqual(definite_counts(per_section[0]),
                         {('a1', 'a2'): 7})
        self.assertEqual(per_section[0]['unowned'], 0)

    def test_explicit_beats_inferred(self):
        # Zero-length 'loose' infers over [0x1000, 0x1080); explicit 'tight'
        # covers [0x1040, 0x1060). Hits in 'tight' are definite for it.
        section = make_section(
            ssa.build_ranges([('loose', 0x1000, 0), ('tight', 0x1040, 0x20)],
                             0x1080))
        per_section, _ = ssa.attribute_samples(
            [(0x1010, 3), (0x1050, 5)], [section])
        counts = definite_counts(per_section[0])
        self.assertEqual(counts[('loose',)], 3)
        self.assertEqual(counts[('tight',)], 5)
        self.assertEqual(per_section[0]['disputed'], {})

    def test_partial_overlap_is_disputed(self):
        section = make_section(
            ssa.build_ranges([('a', 0x1000, 0x80), ('b', 0x1040, 0x80)],
                             0x2000))
        per_section, _ = ssa.attribute_samples(
            [(0x1010, 2), (0x1050, 6), (0x1090, 3)], [section])
        result = per_section[0]
        # Exclusive parts stay definite...
        counts = definite_counts(result)
        self.assertEqual(counts[('a',)], 2)
        self.assertEqual(counts[('b',)], 3)
        # ...the overlap is disputed between the two groups.
        self.assertEqual(sum(result['disputed'].values()), 6)
        candidates, = result['disputed']
        self.assertEqual(sorted(n for g in candidates for n in g.names),
                         ['a', 'b'])
        # Reconciliation: definite + disputed + unowned == input.
        self.assertEqual(
            sum(counts.values()) + 6 + result['unowned'], result['input'])

    def test_unowned_and_outside(self):
        section = make_section(
            ssa.build_ranges([('a', 0x1000, 0x10)], 0x2000),
            start=0x1000, end=0x2000)
        per_section, outside = ssa.attribute_samples(
            [(0x1500, 4), (0x9000, 9)], [section])
        self.assertEqual(per_section[0]['unowned'], 4)
        self.assertEqual(outside, 9)

    def test_reconciliation_sums_to_input(self):
        section = make_section(
            ssa.build_ranges(
                [('a', 0x1000, 0x80), ('b', 0x1040, 0x80), ('c', 0x1200, 0)],
                0x2000))
        samples = [(0x1004, 1), (0x1050, 2), (0x1080, 3), (0x1200, 4),
                   (0x1f00, 5), (0x3000, 6)]
        per_section, outside = ssa.attribute_samples(samples, [section])
        result = per_section[0]
        accounted = (sum(result['definite'].values()) +
                     sum(result['disputed'].values()) + result['unowned'])
        self.assertEqual(accounted, result['input'])
        self.assertEqual(accounted + outside,
                         sum(count for _, count in samples))


class TestReport(unittest.TestCase):
    def test_report_checks_are_ok(self):
        section = make_section(
            ssa.build_ranges([('a', 0x1000, 0x80), ('b', 0x1040, 0x80)],
                             0x2000))
        samples = [(0x1010, 2), (0x1050, 6), (0x1f00, 1), (0x9000, 3)]
        per_section, outside = ssa.attribute_samples(samples, [section])
        report = ssa.format_report(per_section, outside)
        self.assertIn('disputed', report)
        self.assertIn('a vs b: 6', report)
        self.assertNotIn('MISMATCH', report)
        self.assertIn('Unowned outside any section: 3', report)


if __name__ == '__main__':
    unittest.main()
