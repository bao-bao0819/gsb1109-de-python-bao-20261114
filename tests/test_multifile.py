from __future__ import absolute_import

import os
from decimal import Decimal
from unittest import TestCase
import sys
sys.path.insert(0, os.path.abspath('..'))

import six

from .support import open_file
from ofxparse import OfxParser, OfxParserException, OfxVolume


class TestSplitVolumes(TestCase):
    def testSplitConcatenatedDocuments(self):
        with open_file('concat_mixed_encoding.ofx') as fh:
            data = fh.read()
        chunks = OfxParser.split_volumes(data)
        self.assertEqual(3, len(chunks))
        offsets = [offset for offset, chunk in chunks]
        self.assertEqual(sorted(offsets), offsets)
        # chunks tile the whole file with no gaps or overlaps
        self.assertEqual(0, offsets[0])
        self.assertEqual(
            data, six.b('').join(chunk for offset, chunk in chunks))
        for offset, chunk in chunks:
            self.assertEqual(data[offset:offset + len(chunk)], chunk)

    def testMemoMentioningOfxheaderDoesNotSplit(self):
        with open_file('recovered_volume.ofx') as fh:
            data = fh.read()
        self.assertTrue(six.b('OFXHEADER GUIDELINES') in data)
        chunks = OfxParser.split_volumes(data)
        self.assertEqual(1, len(chunks))
        self.assertEqual(0, chunks[0][0])

    def testSingleDocument(self):
        with open_file('bank_medium.ofx') as fh:
            data = fh.read()
        chunks = OfxParser.split_volumes(data)
        self.assertEqual(1, len(chunks))
        self.assertEqual((0, data), chunks[0])


class TestParseAll(TestCase):
    def testMixedEncodingsAndFormats(self):
        with open_file('concat_mixed_encoding.ofx') as fh:
            data = fh.read()
            fh.seek(0)
            volumes = OfxParser.parse_all(fh)

        self.assertEqual(3, len(volumes))
        for volume in volumes:
            self.assertTrue(isinstance(volume, OfxVolume))
            self.assertTrue(volume.success)
            self.assertTrue(volume.error is None)

        # byte offsets recorded against the original stream
        self.assertEqual(0, volumes[0].offset)
        self.assertEqual(data[volumes[1].offset:volumes[1].offset + 9],
                         six.b('OFXHEADER'))
        self.assertEqual(data[volumes[2].offset:volumes[2].offset + 5],
                         six.b('<?xml'))

        # formats
        self.assertEqual('sgml', volumes[0].format)
        self.assertEqual('sgml', volumes[1].format)
        self.assertEqual('xml', volumes[2].format)

        # each volume decoded with its own declared encoding
        self.assertEqual(six.u('USASCII'),
                         volumes[0].headers['ENCODING'])
        self.assertEqual(six.u('UNICODE'),
                         volumes[1].headers['ENCODING'])
        self.assertEqual(six.u('CAF\xc9 MORNINGS'),
                         volumes[0].account.statement.transactions[0].payee)
        self.assertEqual(six.u('CAF\xc9 REFUND'),
                         volumes[1].account.statement.transactions[0].payee)

        # accounts
        self.assertEqual('ACCT-CP1252', volumes[0].account.account_id)
        self.assertEqual('ACCT-UTF8', volumes[1].account.account_id)
        self.assertEqual('ACCT-XML', volumes[2].account.account_id)

        # transactions
        txn = volumes[0].account.statement.transactions[0]
        self.assertEqual('CP1252-1', txn.id)
        self.assertEqual(Decimal('-15.25'), txn.amount)
        txn = volumes[1].account.statement.transactions[0]
        self.assertEqual('UTF8-1', txn.id)
        self.assertEqual(Decimal('42.50'), txn.amount)
        txn = volumes[2].account.statement.transactions[0]
        self.assertEqual('XML-1', txn.id)
        self.assertEqual(Decimal('7.75'), txn.amount)

    def testBrokenVolumeDoesNotSwallowNextOne(self):
        with open_file('concat_broken_volume.ofx') as fh:
            data = fh.read()
            fh.seek(0)
            volumes = OfxParser.parse_all(fh, fail_fast=True)

        # the memo mentioning OFXHEADER must not add a phantom volume
        self.assertEqual(2, len(volumes))

        broken, good = volumes
        self.assertEqual(0, broken.offset)
        self.assertEqual('sgml', broken.format)
        self.assertFalse(broken.success)
        self.assertTrue(isinstance(broken.error, OfxParserException))
        self.assertTrue(broken.ofx is None)
        self.assertEqual([], broken.accounts)
        self.assertTrue(good.offset > 0)
        self.assertEqual(data[good.offset:good.offset + 9],
                         six.b('OFXHEADER'))
        self.assertEqual('sgml', good.format)
        self.assertTrue(good.success)
        self.assertEqual('ACCT-GOOD', good.account.account_id)

        # the recovered volume must match a standalone parse exactly
        with open_file('recovered_volume.ofx') as fh:
            standalone = OfxParser.parse(fh)
        recovered_txns = good.account.statement.transactions
        standalone_txns = standalone.account.statement.transactions
        self.assertEqual(len(standalone_txns), len(recovered_txns))
        self.assertEqual([t.id for t in standalone_txns],
                         [t.id for t in recovered_txns])
        self.assertEqual([t.amount for t in standalone_txns],
                         [t.amount for t in recovered_txns])
        self.assertEqual(['GOOD-1', 'GOOD-2'],
                         [t.id for t in recovered_txns])
        self.assertEqual([Decimal('1250.00'), Decimal('-19.95')],
                         [t.amount for t in recovered_txns])

    def testBrokenVolumeLenientMode(self):
        with open_file('concat_broken_volume.ofx') as fh:
            volumes = OfxParser.parse_all(fh, fail_fast=False)
        self.assertEqual(2, len(volumes))
        broken, good = volumes
        # lenient mode keeps the volume but discards the bad transaction
        self.assertTrue(broken.success)
        statement = broken.account.statement
        self.assertEqual(['BROKEN-2'],
                         [t.id for t in statement.transactions])
        self.assertEqual(1, len(statement.discarded_entries))
        self.assertTrue(good.success)
        self.assertEqual(2, len(good.account.statement.transactions))

    def testSingleDocumentParseAll(self):
        with open_file('bank_medium.ofx') as fh:
            volumes = OfxParser.parse_all(fh)
        self.assertEqual(1, len(volumes))
        self.assertTrue(volumes[0].success)
        self.assertEqual(0, volumes[0].offset)
        self.assertEqual('sgml', volumes[0].format)
        self.assertEqual('12300 000012345678',
                         volumes[0].account.account_id)
