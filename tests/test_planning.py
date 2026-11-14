# -*- coding: utf-8 -*-
'''
    Tests for the feed planning module (feedgen.planning).
'''
import random
import unittest
from datetime import date

from feedgen.planning import (
    Article,
    Issue,
    PlanResult,
    Rejection,
    IssuePlan,
    ScheduledEntry,
    plan_feeds,
    _Optimizer,
    _brute_force_optimum,
    NO_ELIGIBLE_ISSUE,
    CORRECTION_TARGET_UNPUBLISHED,
    STRUCTURALLY_INFEASIBLE,
    PRIORITY_TRADEOFF,
    OPTIMAL_TIE_BREAK,
)


def _plan_is_feasible(articles, issues, result, correction_pairs):
    by_id = {article.id: article for article in articles}
    issue_index = {issue.id: index for index, issue in enumerate(issues)}
    placement = result.scheduled
    for issue_plan in result.issues:
        if len(issue_plan.entries) > issue_plan.capacity:
            return False
    for article_id, placed_issue in placement.items():
        article = by_id[article_id]
        if article.eligible_issues \
                and placed_issue not in article.eligible_issues:
            return False
    for correction_id, target_id in correction_pairs:
        if correction_id not in placement:
            continue
        if target_id not in placement:
            return False
        delta = (issue_index[placement[correction_id]]
                 - issue_index[placement[target_id]])
        if delta not in (0, 1):
            return False
    return True


class TestPlanningBasics(unittest.TestCase):

    def test_capacity_and_eligibility(self):
        issues = [
            Issue(id='i1', capacity=2, available_date=date(2026, 1, 1)),
            Issue(id='i2', capacity=1, available_date=date(2026, 2, 1)),
        ]
        articles = [
            Article(id='a', priority=10, eligible_issues=('i1',)),
            Article(id='b', priority=9, eligible_issues=('i1',)),
            Article(id='c', priority=8, eligible_issues=('i1',)),
            Article(id='d', priority=1, eligible_issues=('i2',)),
        ]
        result = plan_feeds(articles, issues)
        self.assertIsInstance(result, PlanResult)
        self.assertEqual(result.total_priority, 20)
        self.assertEqual(result.issue('i1').article_ids, ['a', 'b'])
        self.assertEqual(result.issue('i2').article_ids, ['d'])
        rejected = {item.article_id: item for item in result.rejected}
        self.assertEqual(set(rejected), {'c'})
        self.assertEqual(rejected['c'].reason_code, PRIORITY_TRADEOFF)
        included_value = (
            rejected['c'].details.get('best_total_if_included')
            if 'best_total_if_included' in rejected['c'].details
            else rejected['c'].details['relaxed_upper_bound_if_included'])
        self.assertEqual(included_value, 19)
        self.assertTrue(_plan_is_feasible(
            articles, issues, result, []))

    def test_availability_dates_derive_windows(self):
        issues = [
            Issue(id='i1', capacity=5, available_date=date(2026, 1, 10)),
            Issue(id='i2', capacity=5, available_date=date(2026, 2, 10)),
            Issue(id='i3', capacity=5, available_date=date(2026, 3, 10)),
        ]
        articles = [
            Article(id='late', priority=5,
                    available_from=date(2026, 2, 20)),
            Article(id='early', priority=5,
                    available_from=date(2026, 1, 1)),
        ]
        result = plan_feeds(articles, issues)
        placement = result.scheduled
        self.assertEqual(placement['early'], 'i1')
        self.assertEqual(placement['late'], 'i3')

    def test_no_eligible_issue_reason(self):
        issues = [Issue(id='i1', capacity=2,
                        available_date=date(2026, 1, 1))]
        articles = [
            Article(id='a', priority=3,
                    available_from=date(2026, 6, 1)),
        ]
        result = plan_feeds(articles, issues)
        self.assertEqual(result.total_priority, 0)
        self.assertEqual(len(result.rejected), 1)
        rejection = result.rejected[0]
        self.assertEqual(rejection.reason_code, NO_ELIGIBLE_ISSUE)
        self.assertEqual(rejection.details['eligible_issues'], [])

    def test_structurally_infeasible_correction(self):
        # The correction's only eligible issue has no capacity left, so it
        # can never be published even though its target runs in the previous
        # issue.
        issues = [
            Issue(id='i1', capacity=1, available_date=date(2026, 1, 1)),
            Issue(id='i2', capacity=0, available_date=date(2026, 2, 1)),
        ]
        articles = [
            Article(id='target', priority=1, eligible_issues=('i1',)),
            Article(id='fix', priority=10, eligible_issues=('i2',),
                    corrects=('target',)),
        ]
        result = plan_feeds(articles, issues)
        self.assertEqual(result.scheduled, {'target': 'i1'})
        rejection = result.rejection_of('fix')
        self.assertEqual(rejection.reason_code, STRUCTURALLY_INFEASIBLE)

    def test_structurally_infeasible_when_window_gap_too_large(self):
        issues = [
            Issue(id='i1', capacity=1, available_date=date(2026, 1, 1)),
            Issue(id='i2', capacity=1, available_date=date(2026, 2, 1)),
            Issue(id='i3', capacity=1, available_date=date(2026, 3, 1)),
        ]
        articles = [
            Article(id='target', priority=5, eligible_issues=('i1',)),
            Article(id='fix', priority=5, eligible_issues=('i3',),
                    corrects=('target',)),
        ]
        result = plan_feeds(articles, issues)
        rejection = result.rejection_of('fix')
        self.assertEqual(rejection.reason_code, STRUCTURALLY_INFEASIBLE)

    def test_correction_runs_same_or_next_issue(self):
        issues = [
            Issue(id='i1', capacity=2, available_date=date(2026, 1, 1)),
            Issue(id='i2', capacity=2, available_date=date(2026, 2, 1)),
        ]
        articles = [
            Article(id='target', priority=10, eligible_issues=('i1',)),
            Article(id='fix', priority=10,
                    eligible_issues=('i1', 'i2'),
                    corrects=('target',)),
        ]
        result = plan_feeds(articles, issues)
        self.assertEqual(result.scheduled['target'], 'i1')
        self.assertIn(result.scheduled['fix'], ('i1', 'i2'))
        self.assertEqual(result.total_priority, 20)

    def test_correction_without_published_target(self):
        issues = [
            Issue(id='i1', capacity=1, available_date=date(2026, 1, 1)),
        ]
        articles = [
            Article(id='target', priority=1, eligible_issues=('i1',)),
            Article(id='blocker', priority=9, eligible_issues=('i1',)),
            Article(id='fix', priority=9, eligible_issues=('i1',),
                    corrects=('target',)),
        ]
        result = plan_feeds(articles, issues)
        self.assertEqual(result.scheduled, {'blocker': 'i1'})
        reasons = {item.article_id: item.reason_code
                   for item in result.rejected}
        self.assertEqual(reasons['target'], PRIORITY_TRADEOFF)
        self.assertEqual(reasons['fix'], CORRECTION_TARGET_UNPUBLISHED)

    def test_final_issue_slots_protected_for_last_issue_only_pair(self):
        # Two articles can only appear in the final issue (a correction
        # pair).  A flexible article must not take their slot by being
        # scheduled early... it must be moved to an earlier issue instead.
        issues = [
            Issue(id='i1', capacity=1, available_date=date(2026, 1, 1)),
            Issue(id='i2', capacity=2, available_date=date(2026, 2, 1)),
        ]
        articles = [
            Article(id='pair-target', priority=4,
                    eligible_issues=('i2',)),
            Article(id='pair-fix', priority=4, eligible_issues=('i2',),
                    corrects=('pair-target',)),
            Article(id='flex', priority=3,
                    eligible_issues=('i1', 'i2')),
        ]
        result = plan_feeds(articles, issues)
        self.assertEqual(result.total_priority, 11)
        self.assertEqual(result.issue('i1').article_ids, ['flex'])
        self.assertEqual(
            sorted(result.issue('i2').article_ids),
            ['pair-fix', 'pair-target'])

    def test_final_issue_pair_loses_only_on_priority_grounds(self):
        # A single slot means the heavy article and the correction pair
        # cannot coexist; the pair loses on priority and both members get
        # checkable reasons.
        issues = [Issue(id='i1', capacity=1,
                        available_date=date(2026, 1, 1))]
        articles = [
            Article(id='pair-target', priority=2,
                    eligible_issues=('i1',)),
            Article(id='pair-fix', priority=2, eligible_issues=('i1',),
                    corrects=('pair-target',)),
            Article(id='heavy', priority=9, eligible_issues=('i1',)),
        ]
        result = plan_feeds(articles, issues)
        self.assertEqual(result.total_priority, 9)
        self.assertEqual(result.issue('i1').article_ids, ['heavy'])
        reasons = {item.article_id: item.reason_code
                   for item in result.rejected}
        self.assertEqual(reasons['pair-target'], PRIORITY_TRADEOFF)
        self.assertEqual(reasons['pair-fix'],
                         CORRECTION_TARGET_UNPUBLISHED)

    def test_tie_break_reason(self):
        issues = [Issue(id='i1', capacity=1,
                        available_date=date(2026, 1, 1))]
        articles = [
            Article(id='a', priority=5, eligible_issues=('i1',)),
            Article(id='b', priority=5, eligible_issues=('i1',)),
        ]
        result = plan_feeds(articles, issues)
        self.assertEqual(result.total_priority, 5)
        self.assertEqual(len(result.rejected), 1)
        rejection = result.rejected[0]
        self.assertEqual(rejection.reason_code, OPTIMAL_TIE_BREAK)
        self.assertEqual(
            rejection.details['best_total_if_included'], 5)

    def test_mapping_inputs_and_result_types(self):
        result = plan_feeds(
            [
                {'id': 'a', 'priority': 2, 'eligible_issues': ['i1']},
            ],
            [
                {'id': 'i1', 'capacity': 3,
                 'available_date': '2026-01-01'},
            ],
        )
        self.assertIsInstance(result.issues[0], IssuePlan)
        self.assertIsInstance(result.issues[0].entries[0], ScheduledEntry)
        self.assertEqual(result.issues[0].used_capacity, 1)
        self.assertEqual(result.issues[0].free_capacity, 2)
        self.assertIsInstance(result.rejected, list)

    def test_validation_errors(self):
        issues = [Issue(id='i1', capacity=1,
                        available_date=date(2026, 1, 1))]
        with self.assertRaises(ValueError):
            plan_feeds(
                [Article(id='a', priority=1),
                 Article(id='a', priority=1)], issues)
        with self.assertRaises(ValueError):
            plan_feeds(
                [Article(id='a', priority=1,
                         eligible_issues=('nope',))], issues)
        with self.assertRaises(ValueError):
            plan_feeds(
                [Article(id='a', priority=1, corrects=('ghost',))], issues)
        with self.assertRaises(ValueError):
            plan_feeds(
                [Article(id='a', priority=1, corrects=('a',))], issues)
        with self.assertRaises(ValueError):
            Issue(id='i', capacity=-1)


class TestOptimalityAgainstBruteForce(unittest.TestCase):

    def _random_instance(self, rng):
        n_issues = rng.randint(1, 3)
        n_articles = rng.randint(1, 6)
        issues = [
            Issue(id='i%d' % index, capacity=rng.randint(0, 3),
                  available_date=date(2026, 1, index + 1))
            for index in range(n_issues)
        ]
        articles = []
        for index in range(n_articles):
            window = [
                issue.id for issue in issues
                if rng.random() > 0.4
            ] or [issues[-1].id]
            corrects = ()
            if index > 0 and rng.random() > 0.7:
                target = rng.randrange(index)
                corrects = ('a%d' % target,)
            articles.append(Article(
                id='a%d' % index,
                priority=rng.randint(0, 10),
                eligible_issues=tuple(window),
                corrects=corrects,
            ))
        return articles, issues

    def test_matches_brute_force(self):
        rng = random.Random(20261114)
        for trial in range(120):
            articles, issues = self._random_instance(rng)
            result = plan_feeds(articles, issues)

            issue_ids = [issue.id for issue in issues]
            windows = [
                tuple(issue_ids.index(issue_id)
                      for issue_id in article.eligible_issues)
                for article in articles
            ]
            correction_pairs = [
                (index, next(
                    other for other, candidate in enumerate(articles)
                    if candidate.id == article.corrects[0]))
                for index, article in enumerate(articles)
                if article.corrects
            ]
            expected = _brute_force_optimum(
                articles, windows, correction_pairs,
                [issue.capacity for issue in issues])
            self.assertEqual(
                result.total_priority, expected,
                msg='trial %d: %r' % (trial, result))
            self.assertTrue(_plan_is_feasible(
                articles, issues, result,
                [(articles[correction].id, articles[target].id)
                 for correction, target in correction_pairs]))

    def test_optimizer_directly(self):
        rng = random.Random(7)
        for _ in range(30):
            articles, issues = self._random_instance(rng)
            issue_ids = [issue.id for issue in issues]
            windows = [
                tuple(issue_ids.index(issue_id)
                      for issue_id in article.eligible_issues)
                for article in articles
            ]
            correction_pairs = [
                (index, next(
                    other for other, candidate in enumerate(articles)
                    if candidate.id == article.corrects[0]))
                for index, article in enumerate(articles)
                if article.corrects
            ]
            optimizer = _Optimizer(articles, issues, windows,
                                   correction_pairs)
            optimum, assignment = optimizer.best_assignment()
            expected = _brute_force_optimum(
                articles, windows, correction_pairs,
                [issue.capacity for issue in issues])
            self.assertEqual(optimum, expected)
            if assignment is not None:
                for article_index, issue_index in assignment.items():
                    self.assertIn(issue_index, windows[article_index])


class TestRejectionConsistency(unittest.TestCase):

    def test_every_unscheduled_article_has_rejection(self):
        rng = random.Random(99)
        for _ in range(40):
            n_issues = rng.randint(1, 3)
            issues = [
                Issue(id='i%d' % index, capacity=rng.randint(0, 2),
                      available_date=date(2026, 1, index + 1))
                for index in range(n_issues)
            ]
            articles = [
                Article(id='a%d' % index,
                        priority=rng.randint(0, 5),
                        eligible_issues=tuple(
                            issue.id for issue in issues
                            if rng.random() > 0.5) or (issues[-1].id,))
                for index in range(rng.randint(1, 7))
            ]
            result = plan_feeds(articles, issues)
            scheduled = set(result.scheduled)
            rejected_ids = {item.article_id for item in result.rejected}
            self.assertEqual(
                scheduled & rejected_ids, set())
            self.assertEqual(
                scheduled | rejected_ids,
                {article.id for article in articles})
            for rejection in result.rejected:
                self.assertIsInstance(rejection, Rejection)
                self.assertTrue(rejection.message)
                if rejection.reason_code == PRIORITY_TRADEOFF:
                    bound_value = rejection.details.get(
                        'best_total_if_included',
                        rejection.details.get(
                            'relaxed_upper_bound_if_included'))
                    self.assertLess(
                        bound_value,
                        rejection.details['optimal_total_priority'])
                if rejection.reason_code == OPTIMAL_TIE_BREAK:
                    self.assertEqual(
                        rejection.details['best_total_if_included'],
                        rejection.details['optimal_total_priority'])


if __name__ == '__main__':
    unittest.main()
