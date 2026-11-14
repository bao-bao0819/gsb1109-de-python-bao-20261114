"""
Statement-level number format resolution for OFX amounts.

Supplier-exported OFX files are not consistent about decimal and
thousands separators: some use ``1,234.56`` (dot decimal) and others
``1.234,56`` (comma decimal).  A single value such as ``1,234`` is
ambiguous on its own, so this module resolves the format at the
*statement* level instead of per value, using as evidence:

* unambiguous amounts (values that only parse under one convention),
* subtotals of same-currency transactions,
* explicit balance differences (e.g. ledger vs. available balance),
* investment subtotals (``units * unit_price`` vs. ``total``).

The raw strings are always kept next to the parsed values by the
parser.  When more than one convention is consistent with all the
evidence, the resolution stays ambiguous and candidate values are
handed back to the caller -- the machine locale is never consulted.
"""
from __future__ import absolute_import

import decimal
import re


class AmountFormatError(ValueError):
    """No known amount format is consistent with a statement."""


class AmbiguousAmountError(ValueError):
    """More than one amount format is consistent with a statement."""


class AmountFormat(object):
    """
    A decimal/thousands separator convention.

    ``parse`` converts a raw string to a :class:`decimal.Decimal`
    following the convention, raising :class:`decimal.InvalidOperation`
    if the string is not valid under it.
    """

    def __init__(self, name, decimal_sep, thousands_sep):
        self.name = name
        self.decimal_sep = decimal_sep
        self.thousands_sep = thousands_sep
        # Integer part: either plain digits, or 1-3 digits followed by
        # groups of exactly 3 digits separated by the thousands sep.
        # Spaces are accepted as an additional thousands separator.
        grouped = r'\d{1,3}(?:[ \t%s]\d{3})+' % re.escape(thousands_sep)
        self._pattern = re.compile(
            r'^(?P<int>\d+|%s)(?:%s(?P<frac>\d+))?$'
            % (grouped, re.escape(decimal_sep)))

    def parse(self, raw):
        text = raw.strip() if raw else ''
        negative = False
        if text.startswith(('+', '-')):
            negative = text.startswith('-')
            text = text[1:]
        match = self._pattern.match(text)
        if match is None:
            raise decimal.InvalidOperation(
                "%r is not a valid %s amount" % (raw, self.name))
        int_part = match.group('int')
        for sep in (self.thousands_sep, ' ', '\t'):
            int_part = int_part.replace(sep, '')
        frac_part = match.group('frac') or ''
        if frac_part:
            value = decimal.Decimal('%s.%s' % (int_part, frac_part))
        else:
            value = decimal.Decimal(int_part)
        return -value if negative else value

    def __repr__(self):
        return 'AmountFormat(%r)' % (self.name,)


#: ``1,234.56`` -- dot decimal separator, comma thousands separator.
DOT_DECIMAL = AmountFormat('dot-decimal', '.', ',')

#: ``1.234,56`` -- comma decimal separator, dot thousands separator.
COMMA_DECIMAL = AmountFormat('comma-decimal', ',', '.')

#: All conventions considered, in stable order.
FORMATS = (DOT_DECIMAL, COMMA_DECIMAL)


def candidate_values(raw):
    """
    Return a list of ``(format, Decimal)`` pairs under which ``raw``
    parses.  A lone ``1,234`` yields both conventions; ``1,234.56``
    only yields :data:`DOT_DECIMAL`.
    """
    pairs = []
    for fmt in FORMATS:
        try:
            pairs.append((fmt, fmt.parse(raw)))
        except decimal.InvalidOperation:
            pass
    return pairs


class AmountResolution(object):
    """
    The outcome of resolving a statement's amount format.

    ``formats`` are the conventions under which every raw amount in
    the statement parses; ``reconciled`` is the subset that also
    satisfies every subtotal/balance-difference constraint.  When
    exactly one convention reconciles it wins, otherwise all
    syntactically valid conventions remain candidates.
    """

    def __init__(self, formats, reconciled):
        self.formats = list(formats)
        self.reconciled = list(reconciled)

    @property
    def effective(self):
        if len(self.reconciled) == 1:
            return list(self.reconciled)
        return list(self.formats)

    @property
    def format(self):
        """The single surviving convention, or None if ambiguous."""
        effective = self.effective
        return effective[0] if len(effective) == 1 else None

    @property
    def ambiguous(self):
        return self.format is None

    def value(self, raw):
        """
        Interpret ``raw`` under the surviving convention(s).  If every
        surviving convention yields the same number (e.g. a plain
        integer like ``100``), that number is returned.  Raises
        :class:`AmbiguousAmountError` when the conventions disagree --
        use :meth:`candidates` instead.
        """
        pairs = self.candidates(raw)
        values = set(value for _, value in pairs)
        if len(values) == 1:
            return values.pop()
        raise AmbiguousAmountError(
            'amount %r is consistent with more than one format; '
            'use candidates()' % (raw,))

    def candidates(self, raw):
        """
        Return ``(format, Decimal)`` pairs for ``raw`` under every
        surviving convention.
        """
        return [(fmt, fmt.parse(raw)) for fmt in self.effective]


class StatementAmountResolver(object):
    """
    Collects the raw amount strings of one statement (one currency)
    plus reconciliation constraints, and determines which amount
    format(s) the statement is written in.

    Constraints are soft: they only break ties between conventions
    that are all syntactically valid.  If every convention satisfies
    the constraints (or there are none), the resolution stays
    ambiguous and callers receive candidate values.
    """

    def __init__(self):
        self._raws = []
        self._constraints = []

    def add_amount(self, raw):
        """Register a raw amount string as syntactic evidence."""
        if raw is not None:
            self._raws.append(raw)

    def add_subtotal(self, part_raws, total_raw):
        """
        Constrain ``sum(part_raws) == total_raw`` (e.g. a subtotal of
        same-currency transactions stated in the file).
        """
        raws = list(part_raws) + [total_raw]
        for raw in raws:
            self.add_amount(raw)

        def check(fmt):
            total = fmt.parse(total_raw)
            return sum(fmt.parse(r) for r in part_raws) == total
        self._constraints.append(check)

    def add_balance_difference(self, start_raw, end_raw, change_raws,
                               either_direction=False):
        """
        Constrain ``sum(change_raws) == end_raw - start_raw`` -- an
        explicit balance difference explained by the listed amounts.
        """
        raws = [start_raw, end_raw] + list(change_raws)
        for raw in raws:
            self.add_amount(raw)

        def check(fmt):
            start = fmt.parse(start_raw)
            end = fmt.parse(end_raw)
            change = sum(fmt.parse(r) for r in change_raws)
            if change == end - start:
                return True
            return either_direction and change == start - end
        self._constraints.append(check)

    def add_product(self, factor_raws, total_raw,
                    rel_tolerance=decimal.Decimal('0.02')):
        """
        Constrain ``|product(factor_raws)| ~= |total_raw|`` within a
        relative tolerance (e.g. investment ``units * unit_price``
        versus the stated ``total``).
        """
        raws = list(factor_raws) + [total_raw]
        for raw in raws:
            self.add_amount(raw)

        def check(fmt):
            product = decimal.Decimal(1)
            for raw in factor_raws:
                product *= fmt.parse(raw)
            total = fmt.parse(total_raw)
            scale = max(abs(total), decimal.Decimal(1))
            return abs(abs(product) - abs(total)) <= rel_tolerance * scale
        self._constraints.append(check)

    def resolve(self):
        """
        Return an :class:`AmountResolution`.  Raises
        :class:`AmountFormatError` if no convention can parse every
        amount in the statement -- such a statement cannot be posted.
        """
        formats = []
        for fmt in FORMATS:
            try:
                for raw in self._raws:
                    fmt.parse(raw)
            except decimal.InvalidOperation:
                continue
            formats.append(fmt)
        if not formats:
            raise AmountFormatError(
                'no amount format is consistent with all of: %s'
                % (', '.join(repr(r) for r in self._raws),))
        reconciled = [
            fmt for fmt in formats
            if all(check(fmt) for check in self._constraints)
        ]
        return AmountResolution(formats, reconciled)
