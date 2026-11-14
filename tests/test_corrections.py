from __future__ import absolute_import

import codecs
import itertools
from decimal import Decimal
from unittest import TestCase

import six

from ofxparse import (AccountKey, CorrectionLedger, OfxParser,
                      apply_corrections)
from ofxparse.corrections import DELETE, REPLACE


HEADER = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

"""


def ofx_document(transactions, org='BANK', fid='1001', bankid='111',
                 account_id='123', account_type='CHECKING'):
    return HEADER + """<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS>
<DTSERVER>20260101000000<LANGUAGE>ENG<FI><ORG>%s<FID>%s</FI></SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>%s<ACCTID>%s
<ACCTTYPE>%s</BANKACCTFROM><BANKTRANLIST><DTSTART>20260101<DTEND>20260131
%s
</BANKTRANLIST><LEDGERBAL><BALAMT>0<DTASOF>20260131</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>""" % (
        org, fid, bankid, account_id, account_type, transactions)


def stmttrn(fitid, amount, correct_fitid=None, correct_action=None,
            date='20260115', name='Transaction', trntype='DEBIT'):
    result = ['<STMTTRN><TRNTYPE>%s<DTPOSTED>%s<TRNAMT>%s<FITID>%s<NAME>%s'
              % (trntype, date, amount, fitid, name)]
    if correct_fitid:
        result.append('<CORRECTFITID>%s' % correct_fitid)
    if correct_action:
        result.append('<CORRECTACTION>%s' % correct_action)
    result.append('</STMTTRN>')
    return ''.join(result)


def stream(document):
    return six.BytesIO(six.b(document))


A = stmttrn('A', '-100.00', name='Original')
B = stmttrn('B', '-120.00', 'A', REPLACE, name='Replacement')
B_DELETE = stmttrn('C', '0.00', 'B', DELETE, name='Void replacement')


class TestParseCorrectionFields(TestCase):
    def testParsesCorrectionFields(self):
        document = ofx_document(B)
        ofx = OfxParser.parse(stream(document))
        transaction = ofx.account.statement.transactions[0]
        self.assertEqual('B', transaction.id)
        self.assertEqual('A', transaction.correct_fitid)
        self.assertEqual(REPLACE, transaction.correct_action)

    def testCorrectionFieldsDefaultToEmpty(self):
        document = ofx_document(A)
        ofx = OfxParser.parse(stream(document))
        transaction = ofx.account.statement.transactions[0]
        self.assertEqual('', transaction.correct_fitid)
        self.assertEqual('', transaction.correct_action)


class TestCorrectionChain(TestCase):
    def ledger(self, *documents):
        ledger = CorrectionLedger()
        for document in documents:
            ledger.add_file(stream(document))
        return ledger

    def testReplacementAndVoidInAnyOrder(self):
        files = [ofx_document(A), ofx_document(B), ofx_document(B_DELETE)]
        contributions = set()
        for order in itertools.permutations(files):
            ledger = self.ledger(*order)
            contributions.add(ledger.balance_contribution)
            self.assertEqual([], list(ledger.valid_transactions))
            self.assertEqual([], list(ledger.pending_transactions))
            chain = ledger.chains[0]
            self.assertEqual(('A', 'B', 'C'), chain.ordered_ids)
            self.assertEqual('deleted', chain.status)
        self.assertEqual({Decimal('0')}, contributions)

    def testReplacementOnlyKeepsLatestTransaction(self):
        files = [ofx_document(A), ofx_document(B)]
        for order in itertools.permutations(files):
            ledger = self.ledger(*order)
            self.assertEqual(['B'], ledger.valid_transactions.ids)
            self.assertEqual(Decimal('-120.00'), ledger.balance_contribution)
            self.assertEqual(('A', 'B'), ledger.chains[0].ordered_ids)
            self.assertEqual(('A', 'B'), ledger.chains[0].transition_ids)

    def testDuplicateStatementsAreIdempotent(self):
        ledger = self.ledger(ofx_document(A), ofx_document(A),
                             ofx_document(B), ofx_document(B))
        self.assertEqual(['B'], ledger.valid_transactions.ids)
        self.assertEqual(Decimal('-120.00'), ledger.balance_contribution)

    def testCorrectionBeforeOriginal(self):
        ledger = self.ledger(ofx_document(B), ofx_document(A))
        self.assertEqual(['B'], ledger.valid_transactions.ids)
        self.assertEqual(Decimal('-120.00'), ledger.balance_contribution)

    def testSelfDeleteRemovesTransaction(self):
        delete = stmttrn('A', '0.00', 'A', DELETE)
        ledger = self.ledger(ofx_document(A), ofx_document(delete))
        self.assertEqual([], list(ledger.valid_transactions))
        self.assertEqual(Decimal('0'), ledger.balance_contribution)
        self.assertEqual('deleted', ledger.chains[0].status)


class TestConflictingCorrections(TestCase):
    def ledger(self, *documents):
        ledger = CorrectionLedger()
        for document in documents:
            ledger.add_file(stream(document))
        return ledger

    def testReplaceAndDeleteOfSameIdMoveOnlyThatChainToPending(self):
        other = stmttrn('Z', '-50.00', name='Unrelated')
        third = stmttrn('D', '0.00', 'A', DELETE, name='Conflicting delete')
        ledger = self.ledger(ofx_document(A + B + third + other))
        self.assertEqual(['Z'], ledger.valid_transactions.ids)
        self.assertEqual(Decimal('-50.00'), ledger.balance_contribution)
        self.assertEqual(['A', 'B', 'D'],
                         sorted(ledger.pending_transactions.ids))
        pending = ledger.pending_chains
        self.assertEqual(1, len(pending))
        self.assertEqual(('A', 'B', 'D'), pending[0].node_ids)
        self.assertIn('conflicting_correction_actions', pending[0].reasons)

    def testTwoDifferentReplacementsOfSameIdArePending(self):
        c = stmttrn('C', '-90.00', 'A', REPLACE, name='Second replacement')
        ledger = self.ledger(ofx_document(A + B + c))
        self.assertEqual([], list(ledger.valid_transactions))
        self.assertEqual(1, len(ledger.pending_chains))
        self.assertIn('multiple_replacements', ledger.pending_chains[0].reasons)

    def testTransactionIdIsScopedByInstitution(self):
        other_bank = stmttrn('X', '-40.00', name='Other bank')
        account_here = stmttrn('X', '-10.00', name='This bank')
        ledger = self.ledger(
            ofx_document(account_here),
            ofx_document(other_bank, org='OTHER', fid='2002'),
        )
        self.assertEqual(2, len(ledger.accounts))
        self.assertEqual(Decimal('-50.00'), ledger.balance_contribution)
        self.assertEqual(['X', 'X'], sorted(
            transaction.id for transaction in ledger.valid_transactions))

    def testSameAccountNumberAtTwoInstitutionsIsNotMerged(self):
        ledger = self.ledger(
            ofx_document(stmttrn('X', '-10.00'), org='BANK', fid='1001',
                         account_id='123'),
            ofx_document(stmttrn('Y', '-20.00'), org='OTHER', fid='2002',
                         account_id='123'),
        )
        self.assertEqual(2, len(ledger.accounts))
        self.assertEqual(['X', 'Y'], sorted(
            transaction.id for transaction in ledger.valid_transactions))
        self.assertEqual(Decimal('-30.00'), ledger.balance_contribution)

    def testTransactionIdIsScopedByAccount(self):
        ledger = self.ledger(
            ofx_document(stmttrn('X', '-10.00'), account_id='123'),
            ofx_document(stmttrn('X', '-25.00'), account_id='456'),
        )
        self.assertEqual(2, len(ledger.accounts))
        self.assertEqual(Decimal('-35.00'), ledger.balance_contribution)

    def testReplacementInAnotherAccountDoesNotTouchOriginal(self):
        ledger = self.ledger(
            ofx_document(stmttrn('A', '-10.00'), account_id='123'),
            ofx_document(stmttrn('B', '-20.00', 'A', REPLACE),
                         account_id='456'),
        )
        self.assertEqual(Decimal('-30.00'), ledger.balance_contribution)
        self.assertEqual(2, len(ledger.correction_chains))

    def testAmbiguousRepeatedFitidWithDifferentAmountIsPending(self):
        ledger = self.ledger(ofx_document(stmttrn('A', '-10.00')))
        ledger.add_transactions([{'id': 'A', 'amount': Decimal('-11.00')}],
                                account=self.key)
        self.assertEqual([], list(ledger.valid_transactions))
        self.assertEqual(Decimal('0'), ledger.balance_contribution)
        self.assertEqual(1, len(ledger.pending_chains))

    @property
    def key(self):
        return AccountKey('BANK', '1001', '123')


class TestApplyCorrectionsHelper(TestCase):
    def testAcceptsFilesAndFileObjects(self):
        ledger = apply_corrections([stream(ofx_document(A)),
                                    stream(ofx_document(B))])
        self.assertEqual(['B'], ledger.valid_transactions.ids)
        self.assertEqual(Decimal('-120.00'), ledger.balance_contribution)

    def testLedgerComparisonUsesAccountKey(self):
        key = AccountKey('BANK', '1001', '123')
        ledger = CorrectionLedger()
        ledger.add_transactions([{'id': 'A', 'amount': Decimal('1')}],
                                account=key)
        self.assertEqual(Decimal('1'), ledger[key].balance_contribution)
