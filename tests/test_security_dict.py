from __future__ import absolute_import

import datetime
import json
from io import StringIO
from unittest import TestCase

import six

from ofxparse import OfxParser, SecurityDictionary, TradingCalendar
from ofxparse.ofxparse import Security
from .support import open_file


def date(value):
    return datetime.datetime.strptime(value, '%Y-%m-%d').date()


class TestTradingCalendar(TestCase):
    def setUp(self):
        self.calendar = TradingCalendar()

    def test_weekday_is_trading_day(self):
        self.assertTrue(self.calendar.is_trading_day(date('2025-01-06')))

    def test_weekend_is_not_trading_day(self):
        self.assertFalse(self.calendar.is_trading_day(date('2025-01-04')))
        self.assertFalse(self.calendar.is_trading_day(date('2025-01-05')))

    def test_fixed_holiday_is_not_trading_day(self):
        # New Year 2025 fell on a Wednesday.
        self.assertFalse(self.calendar.is_trading_day(date('2025-01-01')))

    def test_weekend_holiday_observed_on_weekday(self):
        # Christmas 2022 was a Sunday; Monday the 26th is the observed holiday.
        self.assertFalse(self.calendar.is_trading_day(date('2022-12-26')))
        self.assertTrue(self.calendar.is_trading_day(date('2022-12-23')))

    def test_nearest_trading_day_snaps_weekend_back(self):
        self.assertEqual(
            self.calendar.nearest_trading_day(date('2025-01-04')),
            date('2025-01-03'))

    def test_nearest_trading_day_snaps_holiday_forward(self):
        self.assertEqual(
            self.calendar.nearest_trading_day(
                date('2025-01-01'), preference='next'),
            date('2025-01-02'))

    def test_extra_holidays(self):
        calendar = TradingCalendar(holidays=[date('2025-01-06')])
        self.assertFalse(calendar.is_trading_day(date('2025-01-06')))


class TestSecurityDictionaryLoading(TestCase):
    def test_add_security_keeps_dated_snapshots(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('CUSIP1', 'Old Name', 'OLD',
                                observed_on=date('2009-01-05'))
        dictionary.add_security('CUSIP1', 'New Name', 'NEW',
                                observed_on=date('2012-05-01'))
        entry = dictionary.get('CUSIP1')
        self.assertEqual(len(entry.snapshots), 2)
        self.assertEqual(
            entry.snapshot_on(date('2010-01-01')).ticker, 'OLD')
        self.assertEqual(
            entry.snapshot_on(date('2012-05-01')).ticker, 'NEW')

    def test_load_ofx_security_list(self):
        securities = [
            Security('CUSIP1', 'First Brokerage Co', 'FOO', None),
            Security('CUSIP2', 'Second Brokerage Co', 'FOO', None),
        ]
        dictionary = SecurityDictionary()
        dictionary.load_ofx_securities(
            securities, observed_on=date('2012-09-07'))
        self.assertEqual(set(dictionary.to_dict()['entries'][0].keys()),
                         {'uniqueid', 'snapshots'})
        self.assertEqual(len(dictionary.revisions), 1)
        self.assertEqual(dictionary.get('CUSIP1').snapshots[0].ticker, 'FOO')

    def test_load_ofx_from_parsed_ofx_object(self):
        with open_file('fidelity.ofx') as handle:
            ofx = OfxParser.parse(handle)
        dictionary = SecurityDictionary()
        dictionary.load_ofx_securities(ofx)
        self.assertIsNotNone(dictionary.get('458140100'))

    def test_identical_ticker_or_name_never_merges(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('OLD1', 'Acme Inc', 'ACM',
                                observed_on=date('2000-01-03'))
        dictionary.add_security('NEW9', 'Acme Holdings', 'ACM',
                                observed_on=date('2015-06-01'))
        self.assertEqual(
            sorted(uid for uid, _ in dictionary.find_by_ticker('acm')),
            ['NEW9', 'OLD1'])
        self.assertEqual(dictionary.find_by_name('Acme Inc'), ['OLD1'])
        # The two identities still resolve to themselves independently.
        self.assertEqual(
            dictionary.resolve('OLD1', date('2016-01-04')).canonical_id,
            'OLD1')
        self.assertEqual(
            dictionary.resolve('NEW9', date('2016-01-04')).canonical_id,
            'NEW9')

    def test_find_by_ticker_respects_as_of(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('NEW9', 'Acme Holdings', 'ACM',
                                observed_on=date('2015-06-01'))
        self.assertEqual(dictionary.find_by_ticker('ACM', date('2014-01-01')),
                         [])
        self.assertEqual(
            len(dictionary.find_by_ticker('ACM', date('2016-01-01'))), 1)

    def test_blank_uniqueid_rejected(self):
        dictionary = SecurityDictionary()
        with self.assertRaises(ValueError):
            dictionary.add_security('')


class TestTemporalResolution(TestCase):
    def test_broker_recode_mapping_is_temporal(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('CUSIP1', 'Same Issuer', 'FOO',
                                observed_on=date('2009-01-02'))
        dictionary.add_security('CUSIP2', 'Same Issuer', 'FOO',
                                observed_on=date('2012-05-01'))
        dictionary.add_mapping('CUSIP1', 'CUSIP2',
                               valid_from=date('2012-05-01'))
        dictionary.commit('recode mapping')

        before = dictionary.resolve('CUSIP1', date('2012-04-30'))
        self.assertEqual(before.canonical_id, 'CUSIP1')
        self.assertFalse(before.ambiguous)

        on_day = dictionary.resolve('CUSIP1', date('2012-05-01'))
        self.assertEqual(on_day.canonical_id, 'CUSIP2')
        self.assertEqual(on_day.chosen_path, ['CUSIP1', 'CUSIP2'])

    def test_transitive_chain_resolves_by_day(self):
        dictionary = SecurityDictionary()
        for uniqueid in ('A', 'B', 'C'):
            dictionary.add_security(uniqueid, uniqueid, uniqueid)
        dictionary.add_mapping('A', 'B', valid_from=date('2010-01-04'))
        dictionary.add_mapping('B', 'C', valid_from=date('2015-06-01'))
        dictionary.commit('chains')

        self.assertEqual(
            dictionary.resolve('A', date('2009-12-31')).canonical_id, 'A')
        self.assertEqual(
            dictionary.resolve('A', date('2014-01-02')).canonical_id, 'B')
        result = dictionary.resolve('A', date('2016-01-04'))
        self.assertEqual(result.canonical_id, 'C')
        self.assertEqual(result.chosen_path, ['A', 'B', 'C'])

    def test_expired_mapping_does_not_apply(self):
        dictionary = SecurityDictionary()
        for uniqueid in ('A', 'B'):
            dictionary.add_security(uniqueid, uniqueid, uniqueid)
        dictionary.add_mapping(
            'A', 'B',
            valid_from=date('2020-01-01'), valid_to=date('2020-12-31'))
        self.assertEqual(
            dictionary.resolve('A', date('2019-12-31')).canonical_id, 'A')
        self.assertEqual(
            dictionary.resolve('A', date('2020-06-01')).canonical_id, 'B')
        self.assertEqual(
            dictionary.resolve('A', date('2021-01-04')).canonical_id, 'A')

    def test_delisted_ticker_reused_by_new_company(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('OLD1', 'Old Acme', 'ACM',
                                observed_on=date('2000-01-03'))
        dictionary.add_security('BUY9', 'Buyer Corp', 'BUYR',
                                observed_on=date('2010-03-01'))
        dictionary.add_security('NEW9', 'New Acme', 'ACM',
                                observed_on=date('2015-06-01'))
        # The old company is acquired and its identity retires into the buyer.
        dictionary.add_mapping('OLD1', 'BUY9',
                               valid_from=date('2010-03-01'))
        dictionary.commit('delisting')

        old_holding = dictionary.resolve('OLD1', date('2009-01-02'))
        self.assertEqual(old_holding.canonical_id, 'OLD1')
        acquired = dictionary.resolve('OLD1', date('2011-01-03'))
        self.assertEqual(acquired.canonical_id, 'BUY9')

        # The new company merely reused the ticker; it is its own identity and
        # must never be merged into the old company's chain.
        new_holding = dictionary.resolve('NEW9', date('2016-01-04'))
        self.assertEqual(new_holding.canonical_id, 'NEW9')
        self.assertEqual(new_holding.ticker, 'ACM')
        self.assertEqual(
            sorted(uid for uid, _ in dictionary.find_by_ticker('ACM')),
            ['NEW9', 'OLD1'])

    def test_resolve_snaps_holding_date_to_trading_day(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('A', 'A Co', 'AAA')
        dictionary.add_security('B', 'B Co', 'BBB')
        dictionary.add_mapping('A', 'B', valid_from=date('2025-01-03'))
        # Saturday Jan 4th snaps back to Friday Jan 3rd, the effective day.
        result = dictionary.resolve('A', date('2025-01-04'))
        self.assertEqual(result.as_of, date('2025-01-03'))
        self.assertEqual(result.canonical_id, 'B')

    def test_resolve_position_uses_position_date(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('A', 'A Co', 'AAA')

        class Position(object):
            security = 'A'
            date = datetime.datetime(2025, 1, 4, 16, 0, 0)

        result = dictionary.resolve_position(Position())
        self.assertEqual(result.as_of, date('2025-01-03'))
        self.assertEqual(result.ticker, 'AAA')

    def test_unknown_identity_raises(self):
        dictionary = SecurityDictionary()
        with self.assertRaises(KeyError):
            dictionary.resolve('NOPE', date('2020-01-02'))


class TestConflictChains(TestCase):
    def _dictionary(self):
        dictionary = SecurityDictionary()
        for uniqueid in ('A', 'B', 'C'):
            dictionary.add_security(uniqueid, uniqueid, uniqueid)
        dictionary.add_mapping('A', 'B', valid_from=date('2020-01-01'))
        dictionary.add_mapping(
            'A', 'C',
            valid_from=date('2020-06-01'), valid_to=date('2020-12-31'),
            note='second broker disagrees')
        dictionary.commit('conflicting mappings')
        return dictionary

    def test_conflict_retains_both_chains(self):
        dictionary = self._dictionary()
        result = dictionary.resolve('A', date('2020-06-02'))
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.conflict.candidates, ['B', 'C'])
        chains = result.conflict.chains()
        self.assertTrue(any(chain.endswith('B') for chain in chains))
        self.assertTrue(any(chain.endswith('C') for chain in chains))
        # Deterministic preferred pick: equal source rank, lower sequence wins.
        self.assertEqual(result.conflict.preferred, 'B')
        self.assertEqual(result.canonical_id, 'B')

    def test_conflict_does_not_pollute_other_dates(self):
        dictionary = self._dictionary()

        before_window = dictionary.resolve('A', date('2020-03-02'))
        self.assertFalse(before_window.ambiguous)
        self.assertEqual(before_window.canonical_id, 'B')

        after_window = dictionary.resolve('A', date('2021-01-04'))
        self.assertFalse(after_window.ambiguous)
        self.assertEqual(after_window.canonical_id, 'B')

        pre_mapping = dictionary.resolve('A', date('2019-12-31'))
        self.assertFalse(pre_mapping.ambiguous)
        self.assertEqual(pre_mapping.canonical_id, 'A')

    def test_detect_conflicts_scans_mapping_boundaries(self):
        dictionary = self._dictionary()
        conflicts = dictionary.detect_conflicts()
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict.identity, 'A')
        self.assertEqual(conflict.candidates, ['B', 'C'])

    def test_conflicts_on_lists_all_forks(self):
        dictionary = self._dictionary()
        conflicts = dictionary.conflicts_on(date('2020-07-01'))
        self.assertEqual([conflict.identity for conflict in conflicts], ['A'])
        self.assertEqual(dictionary.conflicts_on(date('2019-01-02')), [])

    def test_cycle_is_flagged_but_chain_kept(self):
        dictionary = SecurityDictionary()
        for uniqueid in ('A', 'B'):
            dictionary.add_security(uniqueid, uniqueid, uniqueid)
        dictionary.add_mapping('A', 'B', valid_from=date('2020-01-01'))
        dictionary.add_mapping('B', 'A', valid_from=date('2020-01-01'))
        result = dictionary.resolve('A', date('2020-06-01'))
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.chosen_path, ['A', 'B', 'A'])
        self.assertIsNotNone(result.conflict)

    def test_manual_edge_wins_over_ofx_edge(self):
        dictionary = SecurityDictionary()
        for uniqueid in ('A', 'B', 'C'):
            dictionary.add_security(uniqueid, uniqueid, uniqueid)
        # The OFX-sourced edge is registered first and would win on sequence,
        # but the manual edge must be preferred because its source ranks higher.
        dictionary.add_mapping('A', 'B', valid_from=date('2020-01-01'),
                               source='ofx')
        dictionary.add_mapping('A', 'C', valid_from=date('2020-01-01'),
                               source='manual')
        result = dictionary.resolve('A', date('2020-06-01'))
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.conflict.candidates, ['B', 'C'])
        self.assertEqual(result.canonical_id, 'C')
        self.assertEqual(result.conflict.preferred, 'C')

    def test_conflict_result_serializes_chains(self):
        dictionary = self._dictionary()
        result = dictionary.resolve('A', date('2020-06-02'))
        payload = result.as_dict()
        self.assertTrue(payload['ambiguous'])
        self.assertEqual(payload['canonical_id'], 'B')
        self.assertEqual(payload['conflict']['candidates'], ['B', 'C'])
        self.assertEqual(len(payload['conflict']['paths']), 2)
        json.dumps(payload)


class TestCsvAndValidation(TestCase):
    def test_load_mappings_csv(self):
        dictionary = SecurityDictionary()
        for uniqueid in ('A', 'B', 'C'):
            dictionary.add_security(uniqueid, uniqueid, uniqueid)
        stream = StringIO(six.u(
            'source_id,target_id,effective_date,end_date,note\n'
            'A,B,2020-01-01,,first recode\n'
            'B,C,2020-06-01,2020-12-31,temporary chain\n'))
        dictionary.load_mappings_csv(stream)
        self.assertEqual(
            dictionary.resolve('A', date('2020-03-02')).canonical_id, 'B')
        result = dictionary.resolve('A', date('2020-07-01'))
        self.assertEqual(result.canonical_id, 'C')
        self.assertEqual(
            dictionary.resolve('A', date('2021-01-04')).canonical_id, 'B')

    def test_duplicate_mapping_is_idempotent(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('A', 'A', 'A')
        dictionary.add_security('B', 'B', 'B')
        first = dictionary.add_mapping(
            'A', 'B', valid_from=date('2020-01-01'))
        second = dictionary.add_mapping(
            'A', 'B', valid_from=date('2020-01-01'))
        self.assertIs(first, second)
        self.assertEqual(len(dictionary.to_dict()['mappings']), 1)

    def test_self_referential_mapping_rejected(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('A', 'A', 'A')
        with self.assertRaises(ValueError):
            dictionary.add_mapping('A', 'A', valid_from=date('2020-01-01'))

    def test_inverted_date_range_rejected(self):
        dictionary = SecurityDictionary()
        for uniqueid in ('A', 'B'):
            dictionary.add_security(uniqueid, uniqueid, uniqueid)
        with self.assertRaises(ValueError):
            dictionary.add_mapping(
                'A', 'B',
                valid_from=date('2020-12-31'), valid_to=date('2020-01-01'))


class TestVersioning(TestCase):
    def test_revision_hash_chain(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('A', 'A Co', 'AAA')
        first = dictionary.commit('ofx list')
        dictionary.add_security('B', 'B Co', 'BBB')
        second = dictionary.commit('second list')
        self.assertEqual(second['previous_hash'], first['hash'])
        self.assertNotEqual(second['hash'], first['hash'])
        self.assertEqual(dictionary.version, second['hash'])
        self.assertEqual(len(dictionary.revisions), 2)

    def test_to_json_is_serializable(self):
        dictionary = SecurityDictionary()
        dictionary.add_security('A', 'A Co', 'AAA')
        dictionary.add_security('B', 'B Co', 'BBB')
        dictionary.add_mapping('A', 'B', valid_from=date('2020-01-01'))
        dictionary.commit('mapping')
        payload = json.loads(dictionary.to_json())
        self.assertEqual(len(payload['entries']), 2)
        self.assertEqual(payload['mappings'][0]['source_id'], 'A')
        self.assertEqual(payload['revisions'][0]['entries'], 2)


class TestParsedOfxIntegration(TestCase):
    def test_resolve_real_positions_on_trading_day(self):
        with open_file('fidelity.ofx') as handle:
            ofx = OfxParser.parse(handle)
        dictionary = SecurityDictionary()
        statement = ofx.account.statement
        dictionary.load_ofx_securities(
            ofx.security_list, observed_on=statement.end_date.date())

        position = statement.positions[0]
        self.assertEqual(position.security, 'G7945E105')
        result = dictionary.resolve_position(position)
        # DTPRICEASOF 2012-09-08 is a Saturday; it snaps to Friday Sep 7.
        self.assertEqual(result.as_of, date('2012-09-07'))
        self.assertFalse(result.ambiguous)
        self.assertEqual(result.canonical_id, 'G7945E105')
        self.assertEqual(result.ticker, 'SDRL')
        self.assertEqual(result.name, 'SEADRILL LTD USD2')
