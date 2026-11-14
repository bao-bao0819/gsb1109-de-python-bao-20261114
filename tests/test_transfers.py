from __future__ import absolute_import

import datetime
import json
from decimal import Decimal
from unittest import TestCase

from ofxparse import OfxParser, match_transfers, TransferMatcher
from ofxparse.transfers import TransferRecord, _FlowNetwork

from .support import open_file


class FakeTransaction(object):
    def __init__(self, fitid, units, security='SEC-AAA', action=None,
                 day=0, settle=None, trade=None, type='transfer', memo=''):
        self.id = fitid
        self.units = Decimal(units)
        self.security = security
        self.tferaction = action
        self.type = type
        self.memo = memo
        base = datetime.datetime(2024, 1, 1, 12)
        self.tradeDate = base + datetime.timedelta(days=day) \
            if trade is None else trade
        self.settleDate = base + datetime.timedelta(days=day) \
            if settle is None else settle


class FakeAccount(object):
    def __init__(self, account_id, transactions, brokerid='example.org'):
        self.account_id = account_id
        self.brokerid = brokerid
        self.institution = None
        self.type = 'investment'
        self.statement = type('Statement', (), {
            'transactions': transactions,
            'positions': [],
        })()


def entry(account_id, fitid, units, action, **kwargs):
    transaction = FakeTransaction(fitid, units, action=action, **kwargs)
    return (account_id, transaction)


class TestFlowRange(TestCase):
    def test_max_flow_respects_capacities(self):
        network = _FlowNetwork(4)
        network.add_edge(0, 1, Decimal(5))
        network.add_edge(0, 2, Decimal(5))
        network.add_edge(1, 3, Decimal(3))
        network.add_edge(2, 3, Decimal(4))
        self.assertEqual(network.max_flow(0, 3), Decimal(7))


class TestTransferMatching(TestCase):
    def test_one_to_one(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '100', 'IN'),
        ])
        self.assertEqual(len(result.pairs), 1)
        pair = result.pairs[0]
        self.assertEqual(pair.outgoing.fitid, 'OUT-1')
        self.assertEqual(pair.incoming.fitid, 'IN-1')
        self.assertEqual(pair.quantity, Decimal('100'))
        self.assertEqual(pair.min_quantity, Decimal('100'))
        self.assertEqual(pair.max_quantity, Decimal('100'))
        self.assertFalse(result.unmatched)

    def test_one_outgoing_splits_across_incoming(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '60', 'IN'),
            entry('B', 'IN-2', '40', 'IN', day=1),
        ])
        self.assertEqual(len(result.pairs), 2)
        quantities = {
            pair.incoming.fitid: pair.quantity for pair in result.pairs
        }
        self.assertEqual(quantities, {'IN-1': Decimal('60'),
                                      'IN-2': Decimal('40')})
        outgoing = result.record_matches[0]
        self.assertEqual(outgoing.remaining_quantity, Decimal('0'))
        self.assertEqual(result.remaining_for(outgoing.record), Decimal('0'))

    def test_several_outgoing_merge_into_one_incoming(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-30', 'OUT'),
            entry('C', 'OUT-2', '-70', 'OUT'),
            entry('B', 'IN-1', '100', 'IN'),
        ])
        self.assertEqual(len(result.pairs), 2)
        self.assertEqual(sum(pair.quantity for pair in result.pairs),
                         Decimal('100'))
        for item in result.record_matches:
            self.assertEqual(item.remaining_quantity, Decimal('0'))

    def test_ambiguous_allocations_confirm_nothing(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('C', 'OUT-2', '-100', 'OUT'),
            entry('B', 'IN-1', '100', 'IN'),
            entry('D', 'IN-2', '100', 'IN'),
        ])
        self.assertEqual(result.pairs, [])
        self.assertEqual(len(result.ambiguities), 4)
        for item in result.record_matches:
            self.assertEqual(item.matched_min, Decimal('100'))
            self.assertEqual(item.matched_max, Decimal('100'))
            self.assertEqual(item.remaining_quantity, Decimal('0'))
        for ambiguity in result.ambiguities:
            self.assertEqual(ambiguity.min_quantity, Decimal('0'))
            self.assertEqual(ambiguity.max_quantity, Decimal('100'))

    def test_common_relation_survives_partially_ambiguous_allocations(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '100', 'IN'),
            entry('D', 'IN-2', '30', 'IN'),
        ])
        self.assertEqual(len(result.pairs), 1)
        pair = result.pairs[0]
        self.assertEqual(pair.incoming.fitid, 'IN-1')
        self.assertEqual(pair.min_quantity, Decimal('70'))
        self.assertEqual(pair.max_quantity, Decimal('100'))
        incoming = next(item for item in result.record_matches
                        if item.record.fitid == 'IN-1')
        self.assertEqual(incoming.remaining_min, Decimal('0'))
        self.assertEqual(incoming.remaining_max, Decimal('30'))
        self.assertEqual(incoming.reason, 'partially_ambiguous')

    def test_oversized_incoming_leaves_unmatched_units(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '140', 'IN'),
        ])
        self.assertEqual(result.pairs[0].quantity, Decimal('100'))
        incoming = next(item for item in result.record_matches
                        if item.record.fitid == 'IN-1')
        self.assertEqual(incoming.remaining_quantity, Decimal('40'))
        self.assertEqual(result.unmatched_quantity[incoming.record],
                         Decimal('40'))

    def test_one_record_never_participates_twice(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '70', 'IN'),
            entry('D', 'IN-2', '70', 'IN'),
        ])
        self.assertEqual(len(result.pairs), 2)
        outgoing = next(item for item in result.record_matches
                        if item.record.fitid == 'OUT-1')
        self.assertEqual(outgoing.matched_min, Decimal('100'))
        self.assertEqual(outgoing.matched_max, Decimal('100'))
        for incoming in (item for item in result.record_matches
                         if item.direction == 'IN'):
            self.assertEqual(incoming.matched_min, Decimal('30'))
            self.assertEqual(incoming.matched_max, Decimal('70'))
        for pair in result.pairs:
            self.assertEqual(pair.min_quantity, Decimal('30'))
            self.assertEqual(pair.max_quantity, Decimal('70'))

    def test_settlement_window_is_inclusive(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT', day=0),
            entry('B', 'IN-1', '100', 'IN', day=3),
        ], settlement_window=3)
        self.assertEqual(len(result.pairs), 1)
        result = match_transfers([
            entry('A', 'OUT-2', '-100', 'OUT', day=0),
            entry('B', 'IN-2', '100', 'IN', day=4),
        ], settlement_window=3)
        self.assertEqual(result.pairs, [])

    def test_outside_window_is_reported_for_both_records(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT', day=0),
            entry('B', 'IN-1', '100', 'IN', day=10),
        ])
        self.assertEqual(result.pairs, [])
        self.assertEqual(len(result.unmatched), 2)
        for item in result.unmatched:
            self.assertEqual(item.reason, 'outside_settlement_window')
            self.assertEqual(item.remaining_quantity, Decimal('100'))

    def test_window_accepts_iso_and_unit_strings(self):
        for window in ('P3D', '3d', '72h', datetime.timedelta(days=3), 3):
            result = match_transfers([
                entry('A', 'OUT-1', '-100', 'OUT', day=0),
                entry('B', 'IN-1', '100', 'IN', day=3),
            ], settlement_window=window)
            self.assertEqual(len(result.pairs), 1)

    def test_invalid_window_is_rejected(self):
        for window in ('', 'soon', -1, True):
            with self.assertRaises((ValueError, TypeError)):
                match_transfers([], settlement_window=window)

    def test_none_window_disables_date_check(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT', day=0),
            entry('B', 'IN-1', '100', 'IN', day=365),
        ], settlement_window=None)
        self.assertEqual(len(result.pairs), 1)

    def test_direction_is_inferred_from_sign_without_action(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', None),
            entry('B', 'IN-1', '100', None),
        ])
        self.assertEqual(len(result.pairs), 1)
        self.assertEqual(result.pairs[0].outgoing.direction, 'OUT')
        self.assertEqual(result.pairs[0].incoming.direction, 'IN')

    def test_same_account_records_do_not_pair_by_default(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('A', 'IN-1', '100', 'IN'),
        ])
        self.assertEqual(result.pairs, [])
        self.assertEqual(result.unmatched[0].reason, 'same_account_only')
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('A', 'IN-1', '100', 'IN'),
        ], require_distinct_accounts=False)
        self.assertEqual(len(result.pairs), 1)

    def test_non_transfer_transactions_are_ignored(self):
        result = match_transfers([
            entry('A', 'BUY-1', '5', None, type='buymf'),
            entry('A', 'SELL-1', '-5', None, type='sellmf'),
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '100', 'IN'),
        ])
        self.assertEqual(len(result.pairs), 1)
        self.assertEqual(len(result.record_matches), 2)

    def test_missing_security_or_units_is_reported(self):
        result = match_transfers([
            entry('A', 'OUT-BAD', '-100', 'OUT', security=''),
            entry('A', 'OUT-1', '-100', 'OUT', security='SEC-BBB'),
            entry('B', 'IN-1', '100', 'IN', security='SEC-BBB'),
            FakeTransaction('IN-2', '0', action='IN'),
        ])
        self.assertEqual(len(result.pairs), 1)
        self.assertIn('has no security', ' '.join(result.issues))
        self.assertIn('zero units', ' '.join(result.issues))
        self.assertEqual(len(result.record_matches), 2)

    def test_security_alias_from_seclist(self):
        with open_file('transfers_multi_account.ofx') as handle:
            ofx = OfxParser.parse(handle)
        result = match_transfers(ofx, security_aliases={'AAA': 'SEC-AAA'})
        self.assertEqual(len(result.pairs), 2)

    def test_json_output_is_serializable_and_exposes_remaining(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '40', 'IN'),
        ])
        data = result.as_dict()
        self.assertEqual(data['pairs'][0]['quantity'], '40')
        self.assertEqual(data['unmatched'][0]['remaining_quantity'], '60')
        json.dumps(data)

    def test_record_remaining_distinguishes_definite_from_possible(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('C', 'OUT-2', '-50', 'OUT'),
            entry('B', 'IN-1', '60', 'IN'),
            entry('D', 'IN-2', '60', 'IN'),
        ])
        outgoing = {item.record.fitid: item
                    for item in result.record_matches}.get('OUT-1')
        self.assertEqual(outgoing.remaining_quantity, Decimal('30'))
        self.assertEqual(outgoing.definitely_unmatched_quantity,
                         Decimal('0'))
        self.assertEqual(result.total_matched, Decimal('120'))
        self.assertEqual(result.total_unmatched_outgoing, Decimal('30'))

    def test_result_totals_aggregate_remaining_units(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT'),
            entry('B', 'IN-1', '40', 'IN'),
            entry('C', 'OUT-2', '-70', 'OUT'),
        ])
        self.assertEqual(result.total_matched, Decimal('40'))
        self.assertEqual(result.total_unmatched, Decimal('130'))
        self.assertEqual(result.total_unmatched_outgoing, Decimal('130'))
        self.assertEqual(result.total_unmatched_incoming, Decimal('0'))
        self.assertEqual(result.total_remaining_min, Decimal('90'))
        self.assertEqual(result.total_remaining_max, Decimal('170'))

    def test_identical_statement_reported_twice_is_deduplicated(self):
        with open_file('transfers_multi_account.ofx') as handle:
            ofx = OfxParser.parse(handle)
        with open_file('transfers_multi_account.ofx') as handle:
            duplicate = OfxParser.parse(handle)
        result = match_transfers([ofx, duplicate])
        self.assertEqual(len(result.record_matches), 9)
        self.assertEqual(len(result.pairs), 2)
        self.assertTrue(any('more than once' in issue
                            for issue in result.issues))

    def test_duplicate_fitid_with_conflicting_data_is_kept_separate(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT', day=0),
            entry('A', 'OUT-1', '-100', 'OUT', day=5),
            entry('B', 'IN-1', '100', 'IN'),
        ], settlement_window=None)
        self.assertEqual(len(result.record_matches), 3)

    def test_render_reports_remaining(self):
        result = match_transfers([
            entry('A', 'OUT-1', '-100', 'OUT', day=0),
            entry('B', 'IN-1', '100', 'IN', day=10),
        ])
        text = result.render()
        self.assertIn('Unmatched remaining quantities', text)
        self.assertIn('OUT-1', text)
        self.assertIn('outside_settlement_window', text)

    def test_matcher_object_reuses_settings(self):
        matcher = TransferMatcher(settlement_window='7d')
        result = matcher.match([
            entry('A', 'OUT-1', '-100', 'OUT', day=0),
            entry('B', 'IN-1', '100', 'IN', day=6),
        ])
        self.assertEqual(len(result.pairs), 1)

    def test_ofx_result_shortcut(self):
        with open_file('transfers_multi_account.ofx') as handle:
            ofx = OfxParser.parse(handle)
        result = ofx.match_transfers()
        self.assertEqual(len(result.pairs), 2)
        self.assertEqual(len(ofx.accounts), 4)


class TestTransferFixture(TestCase):
    def test_accounts_and_records(self):
        with open_file('transfers_multi_account.ofx') as handle:
            ofx = OfxParser.parse(handle)
        self.assertEqual(
            [account.account_id for account in ofx.accounts],
            ['ACC-A-1', 'ACC-B-2', 'ACC-C-3', 'ACC-D-4'])
        transfers = [
            transaction
            for account in ofx.accounts
            for transaction in account.statement.transactions
            if transaction.type == 'transfer'
        ]
        self.assertEqual(len(transfers), 9)

    def test_split_and_ambiguous_relationships(self):
        with open_file('transfers_multi_account.ofx') as handle:
            ofx = OfxParser.parse(handle)
        result = match_transfers(ofx)
        pairs = {
            (pair.outgoing.fitid, pair.incoming.fitid): pair.quantity
            for pair in result.pairs
        }
        self.assertEqual(pairs, {
            ('A-OUT-AAA', 'B-IN-AAA-1'): Decimal('60'),
            ('A-OUT-AAA', 'B-IN-AAA-2'): Decimal('40'),
        })
        self.assertEqual(
            [item.record.fitid for item in result.unmatched],
            ['A-OUT-CCC', 'D-IN-CCC'])
        self.assertEqual(
            {item.reason for item in result.unmatched},
            {'outside_settlement_window'})
        self.assertEqual(
            result.remaining_for(result.unmatched[0].record), Decimal('100'))


class TestTransferRecord(TestCase):
    def test_signed_and_absolute_units(self):
        transaction = FakeTransaction('OUT-1', '-100', action='OUT')
        record = TransferRecord(
            transaction, account_id='A', broker_id='example.org',
            direction='OUT', security='SEC-AAA', quantity=Decimal('100'))
        self.assertEqual(record.units, Decimal('-100'))
        self.assertEqual(record.quantity, Decimal('100'))
        self.assertEqual(record.account_key, ('example.org', 'A'))

    def test_duplicate_fitids_are_independent_records(self):
        result = match_transfers([
            entry('A', 'SAME', '-50', 'OUT'),
            entry('C', 'SAME', '-50', 'OUT'),
            entry('B', 'IN-1', '100', 'IN'),
        ])
        self.assertEqual(sum(pair.quantity for pair in result.pairs),
                         Decimal('100'))
