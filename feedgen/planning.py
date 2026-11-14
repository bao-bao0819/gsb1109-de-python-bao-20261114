# -*- coding: utf-8 -*-
'''
    feedgen.planning
    ~~~~~~~~~~~~~~~~

    Selection of inventory articles into per-issue regional feeds.

    Each article has a priority and a list of issues it may be published in.
    Each issue has a fixed capacity. A correction may only be published in the
    same issue as the article it corrects or in the immediately following
    issue.

    The selection is solved to global optimality: the sum of priorities of all
    scheduled articles is maximized, so an article that could be moved to an
    earlier issue can never occupy a slot needed by articles (for example a
    correction pair) that can only appear in the final issue.

    Every article that is not scheduled receives a verifiable rejection
    reason.  For a priority trade-off the reason carries either the exact
    best total achievable when the article is forced in, or a provable
    relaxation upper bound (always below the optimum), so the trade-off can
    be checked independently.

    :copyright: 2026
    :license: FreeBSD and LGPL, see license.* for more details.
'''
from dataclasses import dataclass, field
from datetime import date, datetime
from fractions import Fraction
from itertools import product
import math
from typing import Any

__all__ = [
    'Article', 'Issue', 'ScheduledEntry', 'IssuePlan', 'Rejection',
    'PlanResult', 'plan_feeds',
    'NO_ELIGIBLE_ISSUE', 'CORRECTION_TARGET_UNPUBLISHED',
    'STRUCTURALLY_INFEASIBLE', 'PRIORITY_TRADEOFF', 'OPTIMAL_TIE_BREAK',
]

# --- Reason codes ---------------------------------------------------------

NO_ELIGIBLE_ISSUE = 'no_eligible_issue'
CORRECTION_TARGET_UNPUBLISHED = 'correction_target_unpublished'
STRUCTURALLY_INFEASIBLE = 'structurally_infeasible'
PRIORITY_TRADEOFF = 'priority_tradeoff'
OPTIMAL_TIE_BREAK = 'optimal_tie_break'

_REASON_MESSAGES = {
    NO_ELIGIBLE_ISSUE:
        '稿件没有任何可投期次（可用日期晚于所有期次或可投期次为空）。',
    CORRECTION_TARGET_UNPUBLISHED:
        '更正稿所更正的稿件未纳入任何期次，更正稿不能单独发布。',
    STRUCTURALLY_INFEASIBLE:
        '即使清空其他稿件，容量、可投期次与更正同/次期约束仍无法同时'
        '满足，稿件在结构上不可发布。',
    PRIORITY_TRADEOFF:
        '纳入该稿件会挤占更高优先级稿件的位置，强制纳入可达到的优先级'
        '总和低于当前最优方案。',
    OPTIMAL_TIE_BREAK:
        '该稿件可出现在另一个同等最优的方案中，按确定性平局规则未纳入。',
}


# --- Input / output data model --------------------------------------------

def _as_date(value):
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    raise TypeError('unsupported date value: %r' % (value,))


def _as_id_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


@dataclass(frozen=True)
class Article(object):
    '''A candidate article from the inventory.

    :param id: Unique article identifier.
    :param priority: Priority weight; the optimizer maximizes the sum.
    :param available_from: Earliest availability (date or ISO string).
    :param eligible_issues: Explicit list of issue ids the article may run in.
        When omitted, eligibility is derived from ``available_from`` and the
        issue availability dates.
    :param corrects: Identifier (or list of identifiers) of the article(s)
        corrected by this one.  A correction must run in the same issue as its
        target or in the immediately following issue.
    '''

    id: Any = None
    priority: int = 0
    available_from: Any = None
    eligible_issues: tuple = ()
    corrects: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'available_from',
                           _as_date(self.available_from))
        object.__setattr__(self, 'eligible_issues',
                           tuple(self.eligible_issues))
        object.__setattr__(self, 'corrects', tuple(_as_id_list(self.corrects)))

    @classmethod
    def from_obj(cls, obj):
        if isinstance(obj, Article):
            return obj
        if not isinstance(obj, dict):
            raise TypeError('article must be an Article or mapping, got %r'
                            % type(obj))
        return cls(
            id=obj['id'],
            priority=obj.get('priority', 0),
            available_from=obj.get('available_from'),
            eligible_issues=tuple(obj.get('eligible_issues', ())),
            corrects=tuple(_as_id_list(obj.get('corrects'))),
        )


@dataclass(frozen=True)
class Issue(object):
    '''A feed issue with a fixed capacity and an availability date.'''

    id: Any = None
    capacity: int = 0
    available_date: Any = None

    def __post_init__(self):
        if not isinstance(self.capacity, int) or self.capacity < 0:
            raise ValueError('capacity of issue %r must be a non-negative '
                             'integer' % (self.id,))
        object.__setattr__(self, 'available_date',
                           _as_date(self.available_date))

    @classmethod
    def from_obj(cls, obj):
        if isinstance(obj, Issue):
            return obj
        if not isinstance(obj, dict):
            raise TypeError('issue must be an Issue or mapping, got %r'
                            % type(obj))
        return cls(
            id=obj['id'],
            capacity=obj.get('capacity', 0),
            available_date=obj.get('available_date'),
        )


@dataclass(frozen=True)
class ScheduledEntry(object):
    '''One article placed into one issue.'''

    issue_id: Any
    article_id: Any


@dataclass
class IssuePlan(object):
    '''The entries selected for a single issue.'''

    issue_id: Any
    capacity: int
    entries: list = field(default_factory=list)

    @property
    def article_ids(self):
        return [entry.article_id for entry in self.entries]

    @property
    def used_capacity(self):
        return len(self.entries)

    @property
    def free_capacity(self):
        return self.capacity - len(self.entries)


@dataclass(frozen=True)
class Rejection(object):
    '''Verifiable reason why an article was not scheduled.'''

    article_id: Any
    reason_code: str
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class PlanResult(object):
    '''The full planning result.'''

    issues: list
    rejected: list
    total_priority: int
    _issue_by_id: dict = field(default_factory=dict, repr=False)
    _article_by_id: dict = field(default_factory=dict, repr=False)

    @property
    def scheduled(self):
        '''Mapping of article id to the issue id it appears in.'''
        placement = {}
        for issue_plan in self.issues:
            for entry in issue_plan.entries:
                placement[entry.article_id] = issue_plan.issue_id
        return placement

    def issue(self, issue_id):
        return self._issue_by_id[issue_id]

    def article(self, article_id):
        return self._article_by_id[article_id]
    def rejection_of(self, article_id):
        for rejection in self.rejected:
            if rejection.article_id == article_id:
                return rejection
        return None


# --- Exact linear-programming relaxation ----------------------------------

_FLOAT_EPS = 1e-10
# Safety margin used before trusting a floating-point bound to prune an exact
# search.  Tableau entries stay well below 1e5 in magnitude, so double
# precision errors are orders of magnitude smaller than this margin.
_FLOAT_MARGIN = 1e-6


def _lp_maximize_float(objective, rows, rhs):
    '''Floating-point twin of :func:`_lp_maximize`.

    Faster but approximate.  Callers must only use the result when it is
    unambiguous (a clear bound gap), falling back to the exact engine
    otherwise.
    '''
    n_vars = len(objective)
    n_rows = len(rows)
    flipped = [index for index, value in enumerate(rhs) if value < 0]
    n_art = len(flipped)
    art_column = {
        index: n_vars + n_rows + position
        for position, index in enumerate(flipped)
    }
    n_cols = n_vars + n_rows + n_art + 1
    big_m = 1.0 + sum(abs(value) for value in objective) + n_rows + 1
    tableau = [[0.0 for _ in range(n_cols)] for _ in range(n_rows + 1)]
    basis = []
    for i, row in enumerate(rows):
        if i in art_column:
            sign = -1
            tableau[i][art_column[i]] = 1.0
            basis.append(art_column[i])
        else:
            sign = 1
            basis.append(n_vars + i)
        for j, coefficient in enumerate(row):
            if coefficient:
                tableau[i][j] = float(sign * coefficient)
        tableau[i][n_vars + i] = float(sign)
        tableau[i][n_cols - 1] = float(sign * rhs[i])

    for j, coefficient in enumerate(objective):
        tableau[n_rows][j] = float(-coefficient)
    for index in flipped:
        tableau[n_rows][art_column[index]] = big_m
    for index in flipped:
        factor = tableau[n_rows][art_column[index]]
        tableau[n_rows] = [
            value - factor * pivot_value
            for value, pivot_value in zip(tableau[n_rows],
                                          tableau[index])
        ]

    real_column_limit = n_vars + n_rows
    while True:
        entering = None
        for column in range(real_column_limit):
            if tableau[n_rows][column] < -_FLOAT_EPS:
                entering = column
                break
        if entering is None:
            break
        pivot_row = None
        best_ratio = None
        for i in range(n_rows):
            coefficient = tableau[i][entering]
            if coefficient <= _FLOAT_EPS:
                continue
            ratio = tableau[i][n_cols - 1] / coefficient
            if best_ratio is None or ratio < best_ratio - _FLOAT_EPS or (
                    abs(ratio - best_ratio) <= _FLOAT_EPS
                    and basis[i] < basis[pivot_row]):
                best_ratio = ratio
                pivot_row = i
        if pivot_row is None:
            return None, None
        pivot_value = tableau[pivot_row][entering]
        tableau[pivot_row] = [value / pivot_value
                              for value in tableau[pivot_row]]
        for i in range(n_rows + 1):
            if i == pivot_row:
                continue
            factor = tableau[i][entering]
            if abs(factor) <= _FLOAT_EPS:
                continue
            tableau[i] = [
                value - factor * pivot_value
                for value, pivot_value in zip(tableau[i],
                                              tableau[pivot_row])
            ]
        basis[pivot_row] = entering

    for index in flipped:
        art = art_column[index]
        if art in basis \
                and tableau[basis.index(art)][n_cols - 1] > _FLOAT_EPS:
            return None, None

    solution = [0.0 for _ in range(n_vars)]
    for row_index, basic_column in enumerate(basis):
        if basic_column < n_vars:
            solution[basic_column] = tableau[row_index][n_cols - 1]
    return tableau[n_rows][n_cols - 1], solution


def _lp_maximize(objective, rows, rhs):
    '''Maximize objective*x subject to rows*x <= rhs.

    Right-hand sides may be negative because correction constraints contain
    negative coefficients after fixed variables are substituted.  A big-M
    simplex with artificial variables handles such rows; all arithmetic uses
    :class:`Fraction` so branch decisions are exact.

    Callers provide explicit ``x_j <= 1`` bound rows, so a feasible program
    is always bounded.

    :returns: ``(objective_value, x_values)`` or ``(None, None)`` when the
              relaxation is infeasible.
    '''
    n_vars = len(objective)
    n_rows = len(rows)
    flipped = [index for index, value in enumerate(rhs) if value < 0]
    n_art = len(flipped)
    art_column = {
        index: n_vars + n_rows + position
        for position, index in enumerate(flipped)
    }
    n_cols = n_vars + n_rows + n_art + 1
    # M only has to dominate the total real objective contribution so that
    # driving artificial variables to zero always takes precedence.
    big_m = 1 + sum(abs(value) for value in objective) + n_rows + 1
    tableau = [[Fraction(0) for _ in range(n_cols)]
               for _ in range(n_rows + 1)]
    basis = []
    for i, row in enumerate(rows):
        if i in art_column:
            sign = -1
            tableau[i][art_column[i]] = Fraction(1)
            basis.append(art_column[i])
        else:
            sign = 1
            basis.append(n_vars + i)
        for j, coefficient in enumerate(row):
            if coefficient:
                tableau[i][j] = Fraction(sign * coefficient)
        tableau[i][n_vars + i] = Fraction(sign)
        tableau[i][n_cols - 1] = Fraction(sign * rhs[i])

    # Objective row convention: coefficient -c_j, RHS equals current value.
    # An artificial variable has effective cost -M, hence row entry +M.
    for j, coefficient in enumerate(objective):
        tableau[n_rows][j] = Fraction(-coefficient)
    for index in flipped:
        tableau[n_rows][art_column[index]] = Fraction(big_m)
    # Eliminate non-zero entries on basic columns, including the penalty
    # terms: R <- R - M * (flipped row).
    for index in flipped:
        factor = tableau[n_rows][art_column[index]]
        tableau[n_rows] = [
            value - factor * pivot_value
            for value, pivot_value in zip(tableau[n_rows],
                                          tableau[index])
        ]

    real_column_limit = n_vars + n_rows
    while True:
        entering = None
        for column in range(real_column_limit):
            if tableau[n_rows][column] < 0:
                entering = column
                break
        if entering is None:
            break
        pivot_row = None
        best_ratio = None
        for i in range(n_rows):
            coefficient = tableau[i][entering]
            if coefficient <= 0:
                continue
            ratio = tableau[i][n_cols - 1] / coefficient
            if best_ratio is None or ratio < best_ratio or (
                    ratio == best_ratio
                    and basis[i] < basis[pivot_row]):
                best_ratio = ratio
                pivot_row = i
        if pivot_row is None:
            # Bounded callers should never reach this.
            return None, None
        pivot_value = tableau[pivot_row][entering]
        tableau[pivot_row] = [value / pivot_value
                              for value in tableau[pivot_row]]
        for i in range(n_rows + 1):
            if i == pivot_row:
                continue
            factor = tableau[i][entering]
            if factor == 0:
                continue
            tableau[i] = [
                value - factor * pivot_value
                for value, pivot_value in zip(tableau[i],
                                              tableau[pivot_row])
            ]
        basis[pivot_row] = entering

    for index in flipped:
        art = art_column[index]
        if art in basis and tableau[basis.index(art)][n_cols - 1] > 0:
            return None, None

    solution = [Fraction(0) for _ in range(n_vars)]
    for row_index, basic_column in enumerate(basis):
        if basic_column < n_vars:
            solution[basic_column] = tableau[row_index][n_cols - 1]
    return tableau[n_rows][n_cols - 1], solution


# --- Mixed-integer optimizer ----------------------------------------------

class _Optimizer(object):
    '''Branch-and-bound solver for the article-to-issue assignment.'''

    def __init__(self, articles, issues, windows, correction_pairs):
        self.articles = articles
        self.issues = issues
        self.windows = windows
        self.correction_pairs = correction_pairs
        self.priorities = [article.priority for article in articles]
        # Lexicographic objective: first maximize the priority sum, then the
        # number of scheduled articles.  The count bonus can never outweigh a
        # single priority unit because it is below ``weight_scale``.
        self.weight_scale = len(articles) + 1
        self.weights = [
            article.priority * self.weight_scale + 1
            for article in articles
        ]

        self.var_of = {}
        self.var_article = []
        self.var_issue = []
        self.article_vars = [[] for _ in articles]
        for article_index, article in enumerate(articles):
            for issue_index in windows[article_index]:
                var_index = len(self.var_article)
                self.var_of[(article_index, issue_index)] = var_index
                self.var_article.append(article_index)
                self.var_issue.append(issue_index)
                self.article_vars[article_index].append(var_index)
        self.n_vars = len(self.var_article)
        self.base_rows, self.base_rhs = self._build_constraints()

    def _build_constraints(self):
        rows = []
        rhs = []
        n_issues = len(self.issues)

        # Capacity: one row per issue.
        for issue_index in range(n_issues):
            row = {var for var in range(self.n_vars)
                   if self.var_issue[var] == issue_index}
            rows.append(row)
            rhs.append(self.issues[issue_index].capacity)

        # At most one issue per article.
        for var_list in self.article_vars:
            if var_list:
                rows.append(set(var_list))
                rhs.append(1)

        # Correction links.
        for correction_index, target_index in self.correction_pairs:
            correction_vars = self.article_vars[correction_index]
            target_vars = self.article_vars[target_index]
            if correction_vars and target_vars:
                # The correction cannot appear without its target.
                rows.append(set(correction_vars) |
                            {('neg', var) for var in target_vars})
                rhs.append(0)
                # Same issue or the immediately following one.
                for var in correction_vars:
                    issue_index = self.var_issue[var]
                    allowed = {
                        self.var_of[(target_index, other)]
                        for other in (issue_index - 1, issue_index)
                        if (target_index, other) in self.var_of
                    }
                    rows.append({var} | {('neg', other)
                                         for other in allowed})
                    rhs.append(0)
        return rows, rhs

    def solve(self, include_article=None, stop_at_priority=None):
        '''Return the best achievable priority sum, or ``None`` if infeasible.

        When ``include_article`` is given, every explored schedule forces that
        article to be published via a single "at least one placement"
        constraint.  When ``stop_at_priority`` is given and a schedule
        reaching that priority is found, the search stops early.
        '''
        stop_weight = (None if stop_at_priority is None
                       else stop_at_priority * self.weight_scale)
        extra_rows = ()
        extra_rhs = ()
        if include_article is not None:
            if not self.article_vars[include_article]:
                return None
            # Sum of the article's placement variables >= 1, written as
            # -sum <= -1 so it fits the standard <= constraint form.
            extra_rows = (
                frozenset(('neg', var)
                          for var in self.article_vars[include_article]),
            )
            extra_rhs = (-1,)
        objective, _ = self._trace(
            {}, stop_weight, extra_rows=extra_rows, extra_rhs=extra_rhs,
            must_include=include_article)
        return objective

    def best_assignment(self):
        '''Return ``(objective, {article_index: issue_index})``.'''
        return self._trace({})

    def relaxed_priority_bound(self, include_article):
        '''Integer floor of the floating LP priority bound.

        Returns ``None`` when the result is inconclusive (infeasible or too
        close to an integer); callers then run the exact solver.  Any
        returned integer is a valid upper bound on the true relaxation.
        '''
        if not self.article_vars[include_article]:
            return None
        extra_rows = (
            frozenset(('neg', var)
                      for var in self.article_vars[include_article]),
        )
        extra_rhs = (-1,)
        active, rows, reduced_rhs = self._build_relaxation(
            {}, extra_rows, extra_rhs)
        objective = [self.priorities[self.var_article[var]]
                     for var in active]
        upper_bound, _ = _lp_maximize_float(objective, rows, reduced_rhs)
        if upper_bound is None:
            return None
        # A value close to an integer is inconclusive: rounding it down could
        # turn a true tie (bound equals the optimum) into a false trade-off.
        nearest_integer = round(upper_bound)
        if abs(upper_bound - nearest_integer) <= _FLOAT_MARGIN * max(
                1.0, abs(upper_bound)):
            return None
        return int(math.floor(upper_bound))

    def _fix_article_var(self, var, value):
        fixed = {var: value}
        if value == 1:
            article_index = self.var_article[var]
            for sibling in self.article_vars[article_index]:
                if sibling != var:
                    fixed[sibling] = 0
        return fixed

    def _solution_objective(self, solution):
        return sum(
            self.priorities[self.var_article[var]]
            for var, value in solution.items() if value == 1)

    def _initial_solution(self, fixed, must_include=None):
        '''Construct a feasible integer assignment honoring ``fixed``.

        Highest-priority articles are placed first in their earliest usable
        issue.  The result is validated against every constraint, so it is a
        safe lower bound for branch-and-bound.  Returns ``None`` if the fixed
        decisions are mutually inconsistent.
        '''
        capacities = [issue.capacity for issue in self.issues]
        load = [0] * len(self.issues)
        placement = {}
        for var, value in fixed.items():
            if value != 1:
                continue
            article_index = self.var_article[var]
            if article_index in placement:
                return None
            issue_index = self.var_issue[var]
            placement[article_index] = issue_index
            load[issue_index] += 1
        if any(load[index] > capacities[index]
               for index in range(len(capacities))):
            return None

        if must_include is not None and must_include not in placement:
            chosen_issue = None
            for issue_index in self.windows[must_include]:
                if load[issue_index] < capacities[issue_index]:
                    chosen_issue = issue_index
                    break
            if chosen_issue is None:
                return None
            placement[must_include] = chosen_issue
            load[chosen_issue] += 1

        targets_of = {}
        for correction_index, target_index in self.correction_pairs:
            targets_of.setdefault(correction_index, []).append(target_index)
        forbidden = {var for var, value in fixed.items() if value == 0}

        def can_place(article_index, issue_index):
            var = self.var_of.get((article_index, issue_index))
            if var is None or var in forbidden:
                return False
            if load[issue_index] >= capacities[issue_index]:
                return False
            for target_index in targets_of.get(article_index, ()):
                if target_index not in placement:
                    return False
                if issue_index not in (
                        placement[target_index],
                        placement[target_index] + 1):
                    return False
            return True

        remaining = [
            index for index in range(len(self.articles))
            if index not in placement
        ]
        remaining.sort(key=lambda index: (-self.priorities[index], index))
        deferred = []
        for article_index in remaining:
            if any(target not in placement
                   for target in targets_of.get(article_index, ())):
                deferred.append(article_index)
                continue
            for issue_index in self.windows[article_index]:
                if can_place(article_index, issue_index):
                    placement[article_index] = issue_index
                    load[issue_index] += 1
                    break
        for article_index in deferred:
            for issue_index in self.windows[article_index]:
                if can_place(article_index, issue_index):
                    placement[article_index] = issue_index
                    load[issue_index] += 1
                    break

        for correction_index, target_index in self.correction_pairs:
            if correction_index not in placement:
                continue
            if target_index not in placement:
                return None
            delta = (placement[correction_index]
                     - placement[target_index])
            if delta not in (0, 1):
                return None

        solution = {}
        for var in range(self.n_vars):
            article_index = self.var_article[var]
            is_placement = (
                article_index in placement
                and self.var_issue[var] == placement[article_index]
            )
            solution[var] = 1 if is_placement else 0
        for var, value in fixed.items():
            if solution[var] != value:
                return None
        return solution

    def _build_relaxation(self, current_fixed, extra_rows, extra_rhs):
        active = [var for var in range(self.n_vars)
                  if var not in current_fixed]
        active_index = {var: pos for pos, var in enumerate(active)}
        rows = []
        reduced_rhs = []
        all_rows = list(zip(self.base_rows, self.base_rhs))
        all_rows.extend(zip(extra_rows, extra_rhs))
        for base_row, base_rhs in all_rows:
            row = [0] * len(active)
            remainder = base_rhs
            for term in base_row:
                coefficient = 1
                var = term
                if isinstance(term, tuple):
                    coefficient, var = -1, term[1]
                if var in current_fixed:
                    remainder -= coefficient * current_fixed[var]
                else:
                    row[active_index[var]] += coefficient
            rows.append(row)
            reduced_rhs.append(remainder)
        for pos in range(len(active)):
            bound_row = [0] * len(active)
            bound_row[pos] = 1
            rows.append(bound_row)
            reduced_rhs.append(1)
        return active, rows, reduced_rhs

    def _node_relaxation(self, rows, reduced_rhs, objective,
                         has_incumbent, best_weight, fixed_weight):
        '''Evaluate one branch-and-bound node.

        Returns a ``(status, value, solution)`` triple where status is
        ``prune`` (bound cannot beat the incumbent), ``leaf`` (an integral
        feasible solution) or ``branch`` (a fractional solution requiring
        more branching).  Floating point handles clear-cut nodes; the exact
        Fraction engine resolves anything ambiguous.
        '''
        float_value, float_solution = _lp_maximize_float(
            objective, rows, reduced_rhs)
        if float_value is not None:
            rounded = [0 if value < 0.5 else 1
                       for value in float_solution]
            integral = all(
                abs(value - rounded_value) <= 1e-7
                for value, rounded_value in zip(float_solution, rounded))
            if integral and self._integer_solution_feasible(
                    rows, reduced_rhs, rounded):
                value = sum(coefficient * variable
                            for coefficient, variable in zip(objective,
                                                             rounded))
                return 'leaf', value, rounded
            if has_incumbent:
                scale = max(1.0, abs(float_value), abs(best_weight))
                error_band = _FLOAT_MARGIN * scale
                if fixed_weight + float_value + error_band \
                        <= best_weight:
                    return 'prune', None, None

        exact_value, exact_solution = _lp_maximize(
            objective, rows, reduced_rhs)
        if exact_value is None:
            return 'prune', None, None
        if has_incumbent \
                and fixed_weight + exact_value <= best_weight:
            return 'prune', None, None
        fractional = any(value.denominator != 1 for value in exact_solution)
        if not fractional:
            return 'leaf', exact_value, [int(value)
                                          for value in exact_solution]
        return 'branch', exact_value, exact_solution

    @staticmethod
    def _integer_solution_feasible(rows, rhs, solution):
        for row, limit in zip(rows, rhs):
            total = sum(coefficient * value
                        for coefficient, value in zip(row, solution)
                        if coefficient)
            # All inputs are integral, so any non-zero gap comfortably
            # exceeds floating-point round-off.
            if total > limit + 1e-7:
                return False
        return True

    def _trace(self, fixed, stop_at_weight=None, extra_rows=(),
               extra_rhs=(), must_include=None):
        if self.n_vars == 0:
            return 0, {}
        # Explicit stack of partial assignments; each entry is a mapping of
        # variable index to 0/1.  Depth-first search with LP upper bounds.
        stack = [dict(fixed)]
        best_solution = self._initial_solution(fixed, must_include)
        if best_solution is None:
            best_weight = 0
        else:
            best_weight = sum(
                self.weights[self.var_article[var]]
                for var, value in best_solution.items() if value == 1)
        if stop_at_weight is not None and best_solution is not None \
                and best_weight >= stop_at_weight:
            return self._solution_objective(best_solution), best_solution
        while stack:
            current_fixed = stack.pop()
            active, rows, reduced_rhs = self._build_relaxation(
                current_fixed, extra_rows, extra_rhs)

            objective = [self.weights[self.var_article[var]]
                         for var in active]
            fixed_weight = sum(
                self.weights[self.var_article[var]]
                for var, value in current_fixed.items() if value == 1)
            status, _, solution = self._node_relaxation(
                rows, reduced_rhs, objective,
                best_solution is not None, best_weight, fixed_weight)
            if status == 'prune':
                continue
            if status == 'leaf':
                candidate = dict(current_fixed)
                for var, value in zip(active, solution):
                    candidate[var] = int(value)
                candidate_weight = sum(
                    self.weights[self.var_article[var]]
                    for var, value in candidate.items() if value == 1)
                if best_solution is None \
                        or candidate_weight > best_weight:
                    best_weight = candidate_weight
                    best_solution = candidate
                    if stop_at_weight is not None \
                            and best_weight >= stop_at_weight:
                        break
                continue

            fractional = [
                (var, value)
                for var, value in zip(active, solution)
                if value.denominator != 1
            ]

            def branch_key(item):
                var, value = item
                return (-min(value, 1 - value), var)

            branch_var, _ = min(fractional, key=branch_key)
            # Explore the 1-branch first (depth-first, high objective).
            for value in (0, 1):
                child = dict(current_fixed)
                child.update(self._fix_article_var(branch_var, value))
                stack.append(child)

        if best_solution is None:
            return None, None
        assignment = {}
        for var, value in best_solution.items():
            if value == 1:
                assignment[self.var_article[var]] = self.var_issue[var]
        total_priority = sum(
            self.priorities[article_index]
            for article_index in assignment)
        return total_priority, assignment


# --- Brute-force reference (used for validation and small instances) ------

def _brute_force_optimum(articles, windows, correction_pairs, capacities):
    '''Enumerate every assignment; only intended for tiny instances.'''
    choices = [[None] + list(windows[index])
               for index in range(len(articles))]
    best = 0
    for selection in product(*choices):
        load = [0] * len(capacities)
        feasible = True
        for article_index, issue_index in enumerate(selection):
            if issue_index is None:
                continue
            load[issue_index] += 1
            if load[issue_index] > capacities[issue_index]:
                feasible = False
                break
        if not feasible:
            continue
        for correction_index, target_index in correction_pairs:
            correction_issue = selection[correction_index]
            target_issue = selection[target_index]
            if correction_issue is None:
                continue
            if target_issue is None or correction_issue not in (
                    target_issue, target_issue + 1):
                feasible = False
                break
        if not feasible:
            continue
        total = sum(articles[index].priority
                    for index, issue_index in enumerate(selection)
                    if issue_index is not None)
        if total > best:
            best = total
    return best


# --- Public API -----------------------------------------------------------

def plan_feeds(articles, issues):
    '''Build an optimal per-issue feed plan.

    :param articles: Iterable of :class:`Article` objects or mappings with
        the same keys.
    :param issues: Iterable of :class:`Issue` objects or mappings.
    :returns: :class:`PlanResult` containing one :class:`IssuePlan` per issue
        and a verifiable :class:`Rejection` for every article not scheduled.
    '''
    articles = [Article.from_obj(item) for item in articles]
    issues = [Issue.from_obj(item) for item in issues]

    article_ids = [article.id for article in articles]
    issue_ids = [issue.id for issue in issues]
    if len(set(article_ids)) != len(article_ids):
        raise ValueError('duplicate article ids')
    if len(set(issue_ids)) != len(issue_ids):
        raise ValueError('duplicate issue ids')
    article_by_id = {article.id: article for article in articles}

    for article in articles:
        unknown = set(article.eligible_issues) - set(issue_ids)
        if unknown:
            raise ValueError('article %r references unknown issues %r'
                             % (article.id, sorted(unknown, key=str)))
        for target_id in article.corrects:
            if target_id not in article_by_id:
                raise ValueError('article %r corrects unknown article %r'
                                 % (article.id, target_id))
            if target_id == article.id:
                raise ValueError('article %r cannot correct itself'
                                 % article.id)

    windows = []
    for article in articles:
        if article.eligible_issues:
            window = [issue_ids.index(issue_id)
                      for issue_id in article.eligible_issues]
        elif article.available_from is not None and all(
                issue.available_date is not None for issue in issues):
            window = [
                index for index, issue in enumerate(issues)
                if issue.available_date >= article.available_from
            ]
        else:
            window = list(range(len(issues)))
        windows.append(tuple(sorted(set(window))))

    correction_pairs = []
    for article_index, article in enumerate(articles):
        for target_id in article.corrects:
            correction_pairs.append(
                (article_index, article_ids.index(target_id)))

    optimizer = _Optimizer(articles, issues, windows, correction_pairs)
    optimum, assignment = optimizer.best_assignment()

    issue_plans = [
        IssuePlan(issue_id=issue.id, capacity=issue.capacity)
        for issue in issues
    ]
    issue_plan_by_id = {plan.issue_id: plan for plan in issue_plans}
    scheduled_articles = set(assignment)
    for article_index in sorted(assignment,
                                key=lambda index: str(articles[index].id)):
        issue_index = assignment[article_index]
        issue_plans[issue_index].entries.append(
            ScheduledEntry(
                issue_id=issues[issue_index].id,
                article_id=articles[article_index].id,
            ))

    rejected = []
    rejected_indices = [index for index in range(len(articles))
                        if index not in scheduled_articles]
    for article_index in rejected_indices:
        article = articles[article_index]
        if not windows[article_index]:
            rejected.append(Rejection(
                article_id=article.id,
                reason_code=NO_ELIGIBLE_ISSUE,
                message=_REASON_MESSAGES[NO_ELIGIBLE_ISSUE],
                details={'eligible_issues': []},
            ))
            continue

        unpublished_targets = [
            target_id for target_id in article.corrects
            if article_ids.index(target_id) in rejected_indices
        ]
        if unpublished_targets:
            rejected.append(Rejection(
                article_id=article.id,
                reason_code=CORRECTION_TARGET_UNPUBLISHED,
                message=_REASON_MESSAGES[CORRECTION_TARGET_UNPUBLISHED],
                details={'corrected_article_ids': list(unpublished_targets)},
            ))
            continue

        # A single LP relaxation often proves the trade-off directly: if the
        # best fractional schedule that includes the article is still below
        # the global optimum, no integer schedule can do better.
        bound = optimizer.relaxed_priority_bound(article_index)
        if bound is not None and bound < optimum:
            rejected.append(Rejection(
                article_id=article.id,
                reason_code=PRIORITY_TRADEOFF,
                message=_REASON_MESSAGES[PRIORITY_TRADEOFF],
                details={
                    'optimal_total_priority': optimum,
                    'relaxed_upper_bound_if_included': bound,
                    'proven_lost_priority': optimum - bound,
                },
            ))
            continue

        # The relaxation cannot settle it (or float is inconclusive), so
        # solve the forced integer problem exactly, stopping as soon as the
        # optimum is matched.
        forced = optimizer.solve(include_article=article_index,
                                 stop_at_priority=optimum)
        if forced is None:
            rejected.append(Rejection(
                article_id=article.id,
                reason_code=STRUCTURALLY_INFEASIBLE,
                message=_REASON_MESSAGES[STRUCTURALLY_INFEASIBLE],
                details={'eligible_issues': [
                    issues[index].id for index in windows[article_index]
                ]},
            ))
        elif forced < optimum:
            rejected.append(Rejection(
                article_id=article.id,
                reason_code=PRIORITY_TRADEOFF,
                message=_REASON_MESSAGES[PRIORITY_TRADEOFF],
                details={
                    'optimal_total_priority': optimum,
                    'best_total_if_included': forced,
                    'lost_priority': optimum - forced,
                },
            ))
        else:
            rejected.append(Rejection(
                article_id=article.id,
                reason_code=OPTIMAL_TIE_BREAK,
                message=_REASON_MESSAGES[OPTIMAL_TIE_BREAK],
                details={
                    'optimal_total_priority': optimum,
                    'best_total_if_included': forced,
                },
            ))

    return PlanResult(
        issues=issue_plans,
        rejected=rejected,
        total_priority=optimum,
        _issue_by_id=issue_plan_by_id,
        _article_by_id=article_by_id,
    )
