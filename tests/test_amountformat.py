import locale
from decimal import Decimal
from unittest import TestCase

from ofxparse import OfxParser, OfxParserException
from ofxparse.amountformat import (AmbiguousAmountError, AmountFormatError,
                                   COMMA_DECIMAL, DOT_DECIMAL,
                                   StatementAmountResolver, candidate_values)

from .support import open_file


class TestAmountFormat(TestCase):
    def test_dot_decimal_parse(self):
        self.assertEqual(DOT_DECIMAL.parse('1,234.56'), Decimal('1234.56'))
        self.assertEqual(DOT_DECIMAL.parse('-45.00'), Decimal('-45.00'))
        self.assertEqual(DOT_DECIMAL.parse('100'), Decimal('100'))

    def test_comma_decimal_parse(self):
        self.assertEqual(COMMA_DECIMAL.parse('1.234,56'), Decimal('1234.56'))
        self.assertEqual(COMMA_DECIMAL.parse('-45,00'), Decimal('-45.00'))
        self.assertEqual(COMMA_DECIMAL.parse('1 025,53'), Decimal('1025.53'))

    def test_invalid_under_format(self):
        # '1,234.56' cannot be a comma-decimal amount and vice versa.
        self.assertEqual(candidate_values('1,234.56'),
                         [(DOT_DECIMAL, Decimal('1234.56'))])
        self.assertEqual(candidate_values('1.234,56'),
                         [(COMMA_DECIMAL, Decimal('1234.56'))])

    def test_lone_ambiguous_amount(self):
        # A single 1,234 is not enough to tell the conventions apart.
        self.assertEqual(candidate_values('1,234'),
                         [(DOT_DECIMAL, Decimal('1234')),
                          (COMMA_DECIMAL, Decimal('1.234'))])


class TestStatementAmountResolver(TestCase):
    def test_unambiguous_amount_pins_format(self):
        resolver = StatementAmountResolver()
        resolver.add_amount('1,234')      # ambiguous on its own
        resolver.add_amount('1.234,56')   # only valid as comma-decimal
        resolution = resolver.resolve()
        self.assertEqual(resolution.format, COMMA_DECIMAL)
        self.assertEqual(resolution.value('1,234'), Decimal('1.234'))

    def test_balance_difference_disambiguates(self):
        resolver = StatementAmountResolver()
        resolver.add_balance_difference('0', '1,734', ['1,234', '500'])
        resolution = resolver.resolve()
        # 1234 + 500 == 1734 - 0 only holds for the dot-decimal reading.
        self.assertEqual(resolution.format, DOT_DECIMAL)
        self.assertEqual(resolution.value('1,234'), Decimal('1234'))

    def test_subtotal_constraint(self):
        resolver = StatementAmountResolver()
        resolver.add_subtotal(['1,234', '2,000'], '3,234')
        resolution = resolver.resolve()
        # 1234 + 2000 == 3234 and 1.234 + 2.000 == 3.234: both
        # conventions reconcile, so the candidates are handed back.
        self.assertTrue(resolution.ambiguous)
        self.assertEqual(resolution.candidates('1,234'),
                         [(DOT_DECIMAL, Decimal('1234')),
                          (COMMA_DECIMAL, Decimal('1.234'))])
        self.assertRaises(AmbiguousAmountError, resolution.value, '1,234')

    def test_product_constraint_disambiguates(self):
        resolver = StatementAmountResolver()
        # 2 * 3 == 6 (comma-decimal); 2000 * 3000 != 6000 (dot-decimal).
        resolver.add_product(['2,000', '3,000'], '6,000')
        resolution = resolver.resolve()
        self.assertEqual(resolution.format, COMMA_DECIMAL)
        self.assertEqual(resolution.value('2,000'), Decimal('2'))

    def test_inconsistent_statement_raises(self):
        resolver = StatementAmountResolver()
        resolver.add_amount('1,234.56')
        resolver.add_amount('1.234,56')
        self.assertRaises(AmountFormatError, resolver.resolve)

    def test_identical_values_are_not_ambiguous(self):
        resolver = StatementAmountResolver()
        resolver.add_amount('100')
        resolution = resolver.resolve()
        # Both conventions survive, but they agree on the value.
        self.assertEqual(resolution.value('100'), Decimal('100'))

    def test_locale_is_not_consulted(self):
        for loc in ('de_DE.UTF-8', 'en_US.UTF-8', ''):
            try:
                locale.setlocale(locale.LC_NUMERIC, loc)
            except locale.Error:
                continue
            resolver = StatementAmountResolver()
            resolver.add_amount('1,234')
            resolution = resolver.resolve()
            self.assertTrue(resolution.ambiguous)
            self.assertEqual(resolution.candidates('1,234'),
                             [(DOT_DECIMAL, Decimal('1234')),
                              (COMMA_DECIMAL, Decimal('1.234'))])
        locale.setlocale(locale.LC_NUMERIC, '')


class TestStatementLevelParsing(TestCase):
    def test_eu_statement_resolves_ambiguous_amount(self):
        with open_file('amounts_eu.ofx') as f:
            ofx = OfxParser.parse(f)
        statement = ofx.account.statement
        self.assertEqual(statement.amount_format, COMMA_DECIMAL)
        amounts = [t.amount for t in statement.transactions]
        self.assertEqual(amounts, [Decimal('1234.56'), Decimal('-45.00'),
                                   Decimal('1.234')])
        # The ambiguous 1,234 was read as comma-decimal, and the raw
        # string travelled with the value.
        self.assertEqual(statement.transactions[2].amount_raw, '1,234')
        self.assertEqual(statement.transactions[2].amount_candidates, None)
        self.assertEqual(statement.balance, Decimal('1190.79'))
        self.assertEqual(statement.balance_raw, '1.190,79')

    def test_fully_ambiguous_statement_yields_candidates(self):
        with open_file('amounts_ambiguous.ofx') as f:
            ofx = OfxParser.parse(f)
        statement = ofx.account.statement
        # Both conventions reconcile (subtotal == balance difference),
        # so no single format is chosen and no value is guessed.
        self.assertEqual(statement.amount_format, None)
        self.assertTrue(statement.amounts_reconciled)
        transaction = statement.transactions[0]
        self.assertEqual(transaction.amount, None)
        self.assertEqual(transaction.amount_raw, '1,234')
        self.assertEqual(transaction.amount_candidates,
                         [(DOT_DECIMAL, Decimal('1234')),
                          (COMMA_DECIMAL, Decimal('1.234'))])
        self.assertEqual(statement.balance, None)
        self.assertEqual(statement.balance_candidates,
                         [(DOT_DECIMAL, Decimal('4234')),
                          (COMMA_DECIMAL, Decimal('4.234'))])

    def test_each_account_resolves_independently(self):
        with open_file('amounts_mixed_accounts.ofx') as f:
            ofx = OfxParser.parse(f)
        self.assertEqual(len(ofx.accounts), 2)
        us_statement = ofx.accounts[0].statement
        eu_statement = ofx.accounts[1].statement
        self.assertEqual(us_statement.amount_format, DOT_DECIMAL)
        self.assertEqual(eu_statement.amount_format, COMMA_DECIMAL)
        self.assertEqual(us_statement.transactions[0].amount,
                         Decimal('1234.56'))
        self.assertEqual(eu_statement.transactions[0].amount,
                         Decimal('1234.56'))
        self.assertEqual(us_statement.balance, Decimal('734.56'))
        self.assertEqual(eu_statement.balance, Decimal('734.56'))
        self.assertEqual(eu_statement.balance_raw, '734,56')

    def test_inconsistent_statement_fails_fast(self):
        with open_file('amounts_inconsistent.ofx') as f:
            self.assertRaises(OfxParserException, OfxParser.parse, f)

    def test_inconsistent_statement_not_postable_without_fail_fast(self):
        with open_file('amounts_inconsistent.ofx') as f:
            ofx = OfxParser.parse(f, fail_fast=False)
        statement = ofx.account.statement
        self.assertEqual(statement.amount_format, None)
        self.assertEqual(statement.amount_format_candidates, [])
        self.assertTrue(statement.warnings)

    def test_investment_subtotal_disambiguates(self):
        with open_file('amounts_invest_subtotal.ofx') as f:
            ofx = OfxParser.parse(f)
        statement = ofx.account.statement
        # units * unit_price ~= total only holds for the
        # comma-decimal reading (2 * 3 == 6).
        self.assertEqual(statement.amount_format, COMMA_DECIMAL)
        transaction = statement.transactions[0]
        self.assertEqual(transaction.units, Decimal('2'))
        self.assertEqual(transaction.unit_price, Decimal('3'))
        self.assertEqual(transaction.total, Decimal('-6'))
        self.assertEqual(transaction.units_raw, '2,000')
        self.assertEqual(statement.available_cash, Decimal('6'))
