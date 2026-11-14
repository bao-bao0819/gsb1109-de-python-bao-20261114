# -*- coding: utf-8 -*-
"""Data model for a financial table with subtotals.

A :class:`Ledger` keeps bottom-level details apart from subtotals.  Every
subtotal owns an explicit list of member detail ids, so recomputation sums
each detail exactly once.  The value stored in the sheet is preserved next
to the recomputed sum; their difference is the manual adjustment.
"""

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0")


@dataclass
class Detail:
    """One bottom-level row (a store)."""

    row_id: str
    row_index: int
    store: str
    city: str
    amount: Decimal


@dataclass
class Subtotal:
    """A city subtotal row and the details that roll up into it."""

    row_id: str
    row_index: int
    city: str
    level: int
    stored: Decimal
    member_ids: list = field(default_factory=list)
    computed: Decimal = ZERO

    @property
    def adjustment(self):
        """Manual adjustment: what the sheet shows minus what members sum to."""
        return self.stored - self.computed


@dataclass
class GrandTotal:
    """The closing total row of the table."""

    row_id: str
    row_index: int
    stored: Decimal
    computed: Decimal = ZERO

    @property
    def adjustment(self):
        return self.stored - self.computed


@dataclass
class Impact:
    """Which totals are affected by deleting one detail row."""

    detail_row_id: str
    store: str
    city: str
    subtotal_row_id: str
    subtotal_computed_before: Decimal
    subtotal_computed_after: Decimal
    grand_row_id: str
    grand_computed_before: Decimal
    grand_computed_after: Decimal


class Ledger:
    """Details + subtotals + grand total with single-count recomputation."""

    def __init__(self, details, subtotals, grand):
        self.details = {d.row_id: d for d in details}
        if len(self.details) != len(list(details)):
            raise ValueError("duplicate detail row_id")
        self.subtotals = list(subtotals)
        self.grand = grand
        self._owner = {}
        self._validate_membership()
        self.recompute()

    def _validate_membership(self):
        for st in self.subtotals:
            for member_id in st.member_ids:
                if member_id not in self.details:
                    raise KeyError(
                        "subtotal %r claims unknown detail %r"
                        % (st.row_id, member_id)
                    )
                if member_id in self._owner:
                    raise ValueError(
                        "detail %r claimed by both %r and %r; "
                        "a detail may roll up only once"
                        % (member_id, self._owner[member_id], st.row_id)
                    )
                self._owner[member_id] = st.row_id

    def subtotal_of(self, detail_row_id):
        """Return the subtotal that owns a detail, or None."""
        owner_id = self._owner.get(detail_row_id)
        for st in self.subtotals:
            if st.row_id == owner_id:
                return st
        return None

    def recompute(self):
        """Recompute every level; each detail is counted exactly once."""
        for st in self.subtotals:
            st.computed = sum(
                (self.details[m].amount for m in st.member_ids), ZERO
            )
        # The grand total sums the details directly, not details plus
        # subtotals, so nothing is double counted.
        self.grand.computed = sum(
            (d.amount for d in self.details.values()), ZERO
        )
        return self.grand.computed

    def delete_detail(self, row_id):
        """Remove a detail and report the affected city subtotal and total.

        Stored (sheet) values are left untouched, so manual adjustments
        remain visible as the difference between stored and computed.
        """
        if row_id not in self.details:
            raise KeyError("unknown detail %r" % (row_id,))
        detail = self.details[row_id]
        subtotal = self.subtotal_of(row_id)
        subtotal_before = subtotal.computed if subtotal else ZERO
        grand_before = self.grand.computed

        del self.details[row_id]
        self._owner.pop(row_id, None)
        if subtotal is not None:
            subtotal.member_ids.remove(row_id)
        self.recompute()

        return Impact(
            detail_row_id=row_id,
            store=detail.store,
            city=subtotal.city if subtotal else detail.city,
            subtotal_row_id=subtotal.row_id if subtotal else "",
            subtotal_computed_before=subtotal_before,
            subtotal_computed_after=subtotal.computed if subtotal else ZERO,
            grand_row_id=self.grand.row_id,
            grand_computed_before=grand_before,
            grand_computed_after=self.grand.computed,
        )

    def adjustments(self):
        """Manual adjustments per level: stored minus recomputed."""
        report = [
            (st.row_id, st.city, st.stored, st.computed, st.adjustment)
            for st in self.subtotals
        ]
        report.append(
            (
                self.grand.row_id,
                "TOTAL",
                self.grand.stored,
                self.grand.computed,
                self.grand.adjustment,
            )
        )
        return report
