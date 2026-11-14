# -*- coding: utf-8 -*-

"""
Tests for snapshot based pagination

These tests ensure that callers paginate one determined published
version, that concurrent edits are deferred to the next incremental
round, that entries with identical updated timestamps each appear
exactly once and that expired versions demand an explicit full re-read.
"""

import unittest
from datetime import datetime, timedelta

import dateutil.tz

from feedgen.feed import FeedGenerator
from feedgen.pagination import SnapshotExpiredError


BASE = datetime(2020, 1, 1, tzinfo=dateutil.tz.tzutc())


def make_feed(count):
    fg = FeedGenerator()
    fg.id('http://example.com/feed')
    fg.title('Testfeed')
    for i in range(count):
        fe = fg.add_entry()
        fe.id('http://example.com/entry/%d' % i)
        fe.title('Entry %d' % i)
        fe.updated(BASE + timedelta(minutes=i))
    return fg


class TestSnapshotPagination(unittest.TestCase):

    def test_pagination_is_pinned_to_one_version(self):
        fg = make_feed(5)
        snapshot = fg.snapshot()

        # Editor bumps an old entry to the top and a new entry arrives
        # while the reader is paginating.
        old = fg.entry()[-1]
        old.updated(BASE + timedelta(hours=1))
        fe = fg.add_entry()
        fe.id('http://example.com/entry/new')
        fe.title('New entry')
        fe.updated(BASE + timedelta(hours=2))

        pages = list(snapshot.pages(2))
        self.assertEqual([len(p) for p in pages], [2, 2, 1])
        seen = [e.id() for p in pages for e in p]
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)
        # Neither the new entry nor the bumped position leak into the run
        self.assertNotIn('http://example.com/entry/new', seen)
        self.assertEqual(seen[0], 'http://example.com/entry/4')
        self.assertEqual(seen[-1], 'http://example.com/entry/0')

    def test_same_updated_entries_each_appear_once(self):
        fg = FeedGenerator()
        fg.id('http://example.com/feed')
        fg.title('Testfeed')
        for i in range(4):
            fe = fg.add_entry()
            fe.id('http://example.com/entry/%d' % i)
            fe.title('Entry %d' % i)
            fe.updated(BASE)  # identical timestamps
        snapshot = fg.snapshot()
        seen = [e.id() for page in snapshot.pages(3) for e in page]
        self.assertEqual(sorted(seen), [
            'http://example.com/entry/%d' % i for i in range(4)])

    def test_expired_version_requires_full_reread(self):
        fg = make_feed(2)
        snapshot = fg.snapshot()
        self.assertEqual(snapshot.page(0, 1)[0].id(),
                         'http://example.com/entry/1')

        # Push enough new versions to evict the pinned one
        for i in range(fg.snapshot_retention()):
            fe = fg.add_entry()
            fe.id('http://example.com/entry/extra/%d' % i)
            fe.updated(BASE + timedelta(hours=2 + i))
            fg.snapshot()

        self.assertTrue(snapshot.expired)
        self.assertRaises(SnapshotExpiredError, snapshot.page, 1, 1)
        self.assertRaises(SnapshotExpiredError, snapshot.entries)
        self.assertRaises(SnapshotExpiredError,
                          lambda: list(snapshot.pages(1)))
        self.assertRaises(SnapshotExpiredError, fg.changes_since, snapshot)
        self.assertRaises(SnapshotExpiredError, fg.snapshot,
                          version=snapshot.version)

    def test_reattach_to_retained_version(self):
        fg = make_feed(3)
        snapshot = fg.snapshot()
        again = fg.snapshot(version=snapshot.version)
        self.assertIs(again, snapshot)

    def test_changes_since_returns_incremental_round(self):
        fg = make_feed(3)
        snapshot = fg.snapshot()

        modified = fg.entry()[0]
        modified.updated(BASE + timedelta(hours=1))
        fe = fg.add_entry()
        fe.id('http://example.com/entry/new')
        fe.title('New entry')
        fe.updated(BASE + timedelta(hours=2))

        changes = fg.changes_since(snapshot)
        self.assertEqual([e.id() for e in changes],
                         ['http://example.com/entry/new',
                          'http://example.com/entry/2'])
        # Unchanged entries are not part of the incremental round
        self.assertNotIn('http://example.com/entry/0',
                         [e.id() for e in changes])

    def test_empty_feed_pagination(self):
        fg = FeedGenerator()
        fg.id('http://example.com/feed')
        fg.title('Testfeed')
        snapshot = fg.snapshot()
        self.assertEqual(list(snapshot.pages(2)), [])
        self.assertEqual(snapshot.page(0, 2), [])


if __name__ == '__main__':
    unittest.main()
