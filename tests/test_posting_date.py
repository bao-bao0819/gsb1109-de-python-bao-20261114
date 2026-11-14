from __future__ import absolute_import

import datetime
from decimal import Decimal
from unittest import TestCase

import six

from ofxparse import OfxParser, Statement
from ofxparse.ofxparse import OfxDate

STMTTRN = '''
 <TRNTYPE>POS
 <DTPOSTED>{posted}
 <TRNAMT>-6.60
 <FITID>{fitid}
 <NAME>TEST
'''


def parse_transaction(posted, fitid='1', **parse_kwargs):
    sample = '''OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD
<BANKACCTFROM><BANKID>1<ACCTID>1<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><STMTTRN>''' + \
        STMTTRN.format(posted=posted, fitid=fitid) + \
        '''</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>'''
    ofx = OfxParser.parse(six.BytesIO(six.b(sample)), **parse_kwargs)
    return ofx.account.statement.transactions[0]


class TestOfxDatePrecision(TestCase):
    def test_date_only_keeps_date_precision(self):
        parsed = OfxParser.parseOfxDate('20260115')
        self.assertTrue(parsed.date_only)
        self.assertEqual('date', parsed.date_precision)
        self.assertEqual(datetime.date(2026, 1, 15), parsed.date)
        self.assertIsNone(parsed.local_datetime)
        self.assertIsNone(parsed.wall_datetime)
        self.assertIsNone(parsed.utc_datetime)
        self.assertEqual('20260115', parsed.raw)

    def test_complete_timestamp_keeps_fraction_and_offset(self):
        parsed = OfxParser.parseOfxDate('20260115123045.12345[-5:EST]')
        self.assertFalse(parsed.date_only)
        self.assertEqual('12345', parsed.fraction)
        self.assertEqual(Decimal('0.12345'), parsed.fraction_value)
        self.assertEqual(123450, parsed.microsecond)
        self.assertEqual(
            datetime.datetime(2026, 1, 15, 12, 30, 45, 123450),
            parsed.local_datetime)
        self.assertEqual(datetime.timedelta(hours=-5), parsed.reported_offset)
        self.assertEqual('-5', parsed.reported_offset_text)
        self.assertEqual('EST', parsed.timezone_name)
        self.assertEqual('-05:00', parsed.reported_offset_string)

    def test_legacy_datetime_is_unchanged(self):
        self.assertEqual(
            datetime.datetime(2009, 4, 1, 17, 20, 17),
            OfxParser.parseOfxDateTime('20090401122017.000[-5:EST]'))

    def test_parse_populates_structured_transaction_date(self):
        transaction = parse_transaction('20260115123045.12345[-5:EST]')
        self.assertIsInstance(transaction.dtposted, OfxDate)
        self.assertEqual('20260115123045.12345[-5:EST]',
                         transaction.raw_date)
        self.assertEqual('12345', transaction.fraction)
        self.assertEqual('-05:00', transaction.reported_offset_string)
        self.assertEqual(datetime.timedelta(hours=-5),
                         transaction.reported_offset)

    def test_date_only_transaction_date_stays_a_date(self):
        transaction = parse_transaction('20260115')
        self.assertEqual(datetime.date(2026, 1, 15), transaction.date)
        self.assertFalse(isinstance(transaction.date, datetime.datetime))
        self.assertEqual(datetime.datetime(2026, 1, 15),
                         transaction.legacy_date)

    def test_datetime_without_offset_is_not_aligned_to_utc(self):
        transaction = parse_transaction('20260115123045.12345')
        self.assertTrue(transaction.has_time)
        self.assertFalse(transaction.has_offset)
        self.assertIsNone(transaction.utc_datetime)
        self.assertEqual(datetime.datetime(2026, 1, 15, 12, 30, 45, 123450),
                         transaction.local_datetime)


class TestBusinessDayCutoff(TestCase):
    def test_cutoff_is_configurable_on_parse(self):
        transaction = parse_transaction(
            '20260115180000.000[-5:EST]', business_day_cutoff='06:00')
        self.assertEqual(datetime.time(6, 0),
                         transaction.business_day_cutoff)
        self.assertEqual(datetime.date(2026, 1, 16),
                         transaction.business_date)

    def test_statement_records_the_cutoff(self):
        sample = '''OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD
<BANKACCTFROM><BANKID>1<ACCTID>1<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><STMTTRN>''' + \
            STMTTRN.format(posted='20260115', fitid='1') + \
            '''</STMTTRN></BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>'''
        ofx = OfxParser.parse(six.BytesIO(six.b(sample)),
                              business_day_cutoff='12:00')
        statement = ofx.account.statement
        self.assertEqual(datetime.time(12, 0), ofx.business_day_cutoff)
        self.assertEqual(datetime.time(12, 0), statement.business_day_cutoff)
        statement.business_day_cutoff = '06:00'
        self.assertEqual(datetime.time(6, 0),
                         statement.transactions[0].business_day_cutoff)
        grouped = statement.transactions_by_business_date()
        self.assertEqual([None], list(grouped.keys()))
        possible = statement.transactions_by_possible_business_date()
        self.assertEqual(
            [datetime.date(2026, 1, 15), datetime.date(2026, 1, 16)],
            list(possible.keys()))
        self.assertEqual(1, len(possible[datetime.date(2026, 1, 16)]))

    def test_before_cutoff_stays_on_the_named_day(self):
        transaction = parse_transaction(
            '20260115050000.000[-5:EST]', business_day_cutoff='06:00')
        self.assertEqual(datetime.date(2026, 1, 15),
                         transaction.business_date)

    def test_business_day_cutoff_override(self):
        transaction = parse_transaction(
            '20260115050000.000[-5:EST]', business_day_cutoff='06:00')
        self.assertEqual(datetime.date(2026, 1, 15),
                         transaction.business_date)
        self.assertEqual(datetime.date(2026, 1, 16),
                         transaction.business_day(datetime.time(4, 0)))

    def test_date_only_with_noon_cutoff_lists_both_candidates(self):
        transaction = parse_transaction(
            '20260115', business_day_cutoff='12:00')
        self.assertTrue(transaction.date_only)
        self.assertIsNone(transaction.business_date)
        self.assertEqual(
            [datetime.date(2026, 1, 15), datetime.date(2026, 1, 16)],
            transaction.possible_business_dates)

    def test_date_only_does_not_become_midnight(self):
        transaction = parse_transaction('20260115')
        self.assertIsNone(transaction.local_datetime)
        self.assertIsNone(transaction.wall_datetime)
        self.assertIsNone(transaction.utc_datetime)
        self.assertEqual(datetime.date(2026, 1, 15), transaction.date)
        self.assertNotIsInstance(transaction.date, datetime.datetime)
        self.assertEqual(datetime.datetime(2026, 1, 15),
                         transaction.legacy_date)
        self.assertEqual(datetime.date(2026, 1, 15),
                         transaction.business_date)

    def test_date_only_with_midnight_cutoff_is_unambiguous(self):
        transaction = parse_transaction(
            '20260115', business_day_cutoff='00:00')
        self.assertEqual(datetime.date(2026, 1, 15),
                         transaction.business_date)
        self.assertEqual([datetime.date(2026, 1, 15)],
                         transaction.possible_business_dates)


class TestUtcAlignmentAndOrdering(TestCase):
    def test_utc_alignment_requires_reported_offset(self):
        aligned = parse_transaction('20260115120000.000[-5:EST]')
        unaligned = parse_transaction('20260115120000')
        self.assertEqual(
            datetime.datetime(2026, 1, 15, 17, 0, tzinfo=datetime.timezone.utc),
            aligned.utc_datetime)
        self.assertIsNone(unaligned.utc_datetime)

    def test_same_wall_time_different_offsets_remain_distinct(self):
        earlier = parse_transaction(
            '20260115120000.000[-5:EST]', fitid='earlier')
        later = parse_transaction(
            '20260115120000.000[-6:CST]', fitid='later')
        self.assertEqual(earlier.wall_datetime.replace(tzinfo=None),
                         later.wall_datetime.replace(tzinfo=None))
        self.assertNotEqual(earlier.utc_datetime, later.utc_datetime)
        self.assertNotEqual(earlier.utc_sort_key, later.utc_sort_key)
        ordered = sorted([later, earlier], key=lambda t: t.utc_sort_key)
        self.assertEqual(['earlier', 'later'], [t.id for t in ordered])

    def test_statement_sorts_by_utc_not_by_wall_time(self):
        # Wall order: 12:00 (-5), 12:00 (-6), 13:00 (-6).
        # UTC order:  17:00, 18:00, 19:00.
        transaction = parse_transaction('20260115120000.000[-5:EST]')
        transaction.id = 'a'
        first = parse_transaction('20260115120000.000[-6:CST]')
        first.id = 'b'
        transaction2 = parse_transaction('20260115130000.000[-6:CST]')
        transaction2.id = 'c'

        statement = Statement()
        statement.transactions = [transaction2, first, transaction]

        ordered = statement.sort_transactions_by_utc()
        self.assertEqual(['a', 'b', 'c'], [t.id for t in ordered])
