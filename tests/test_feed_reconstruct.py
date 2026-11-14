# -*- coding: utf-8 -*-

'''
Tests for feed_reconstruct: bitemporal RSS reconstruction.
'''

import unittest
from datetime import datetime, timedelta, timezone

from feed_reconstruct import FeedHistory, Kind, Record

TZ = timezone.utc
# A "Tuesday morning" for the client query.
MONDAY = datetime(2026, 11, 9, 9, 0, tzinfo=TZ)
TUESDAY_AM = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
WEDNESDAY = datetime(2026, 11, 11, 9, 0, tzinfo=TZ)


def rec(event_id, item_id, kind, collected_at, **kw):
    return Record(event_id=event_id, item_id=item_id, kind=kind,
                  collected_at=collected_at, **kw)


class TestBitemporalVisibility(unittest.TestCase):

    def test_business_date_may_precede_first_receipt(self):
        # The announcement is dated last week but only collected Monday.
        history = FeedHistory()
        history.ingest(rec('e1', 'a1', Kind.PUBLISH, MONDAY,
                           business_time=datetime(2026, 11, 2, 8, 0,
                                                  tzinfo=TZ),
                           title='Q3 report'))
        # Before the first receipt the client saw nothing, even though
        # the business date is much earlier.
        self.assertEqual(history.visible_at(datetime(2026, 11, 8, 9, 0,
                                                     tzinfo=TZ)), [])
        self.assertEqual(history.visible_at(TUESDAY_AM), ['a1'])
        # The reconstructed feed shows the (earlier) business date.
        view = history.snapshot(TUESDAY_AM).items['a1']
        self.assertEqual(view.business_time,
                         datetime(2026, 11, 2, 8, 0, tzinfo=TZ))

    def test_correction_backfills_earlier_business_time(self):
        history = FeedHistory()
        original = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
        backfilled = datetime(2026, 11, 5, 10, 0, tzinfo=TZ)
        history.ingest(rec('e1', 'a1', Kind.PUBLISH, MONDAY,
                           business_time=original, title='v1'))
        history.ingest(rec('e2', 'a1', Kind.CORRECT, TUESDAY_AM,
                           business_time=backfilled, title='v2'))
        # Monday's slice still shows the original business time.
        monday = history.snapshot(MONDAY + timedelta(hours=1))
        self.assertEqual(monday.items['a1'].business_time, original)
        self.assertEqual(monday.items['a1'].title, 'v1')
        # After the correction was collected, the slice is backfilled.
        tuesday = history.snapshot(TUESDAY_AM + timedelta(hours=1))
        self.assertEqual(tuesday.items['a1'].business_time, backfilled)
        self.assertEqual(tuesday.items['a1'].title, 'v2')

    def test_retract_and_republish_keeps_gap(self):
        history = FeedHistory()
        history.ingest(rec('e1', 'a1', Kind.PUBLISH, MONDAY, title='v1'))
        history.ingest(rec('e2', 'a1', Kind.RETRACT, TUESDAY_AM))
        history.ingest(rec('e3', 'a1', Kind.PUBLISH, WEDNESDAY, title='v2'))
        self.assertEqual(history.visible_at(MONDAY + timedelta(hours=1)),
                         ['a1'])
        # The retraction window stays empty.
        self.assertEqual(history.visible_at(TUESDAY_AM + timedelta(hours=1)),
                         [])
        self.assertEqual(history.visible_at(WEDNESDAY + timedelta(hours=1)),
                         ['a1'])

    def test_correction_does_not_resurrect_retracted_item(self):
        history = FeedHistory()
        history.ingest(rec('e1', 'a1', Kind.PUBLISH, MONDAY, title='v1'))
        history.ingest(rec('e2', 'a1', Kind.RETRACT, TUESDAY_AM))
        history.ingest(rec('e3', 'a1', Kind.CORRECT,
                           TUESDAY_AM + timedelta(hours=2), title='fixed'))
        self.assertEqual(history.visible_at(WEDNESDAY), [])


class TestIdempotency(unittest.TestCase):

    def test_replaying_same_message_changes_no_slice(self):
        records = [
            rec('e1', 'a1', Kind.PUBLISH, MONDAY, title='v1'),
            rec('e2', 'a1', Kind.CORRECT, TUESDAY_AM, title='v2'),
            rec('e3', 'a2', Kind.PUBLISH, TUESDAY_AM, title='other'),
            rec('e4', 'a1', Kind.RETRACT, WEDNESDAY),
        ]
        history = FeedHistory()
        history.ingest_all(records)
        baseline = {t: history.visible_at(t)
                    for t in (MONDAY, TUESDAY_AM, WEDNESDAY)}
        # Replay every message five times.
        for _ in range(4):
            for record in records:
                self.assertFalse(history.ingest(record))
        for moment, expected in baseline.items():
            self.assertEqual(history.visible_at(moment), expected)
        self.assertEqual(history.snapshot(WEDNESDAY).conflicts, [])


class TestConflicts(unittest.TestCase):

    def test_contradictory_unorderable_records_stay_conflicted(self):
        history = FeedHistory()
        history.ingest(rec('e1', 'a1', Kind.PUBLISH, MONDAY, title='yes'))
        history.ingest(rec('e2', 'a1', Kind.RETRACT, MONDAY))
        snapshot = history.snapshot(TUESDAY_AM)
        # Not silently resolved: the item is reported as a conflict and
        # both candidate states are kept.
        self.assertNotIn('a1', snapshot.items)
        self.assertEqual(len(snapshot.conflicts), 1)
        conflict = snapshot.conflicts[0]
        self.assertEqual(conflict.item_id, 'a1')
        self.assertEqual(sorted(c.visible for c in conflict.candidates),
                         [False, True])
        # Conflicted items are excluded from the visible list.
        self.assertEqual(snapshot.visible_ids(), [])

    def test_same_effect_same_instant_is_not_a_conflict(self):
        history = FeedHistory()
        history.ingest(rec('e1', 'a1', Kind.PUBLISH, MONDAY, title='same'))
        history.ingest(rec('e2', 'a1', Kind.PUBLISH, MONDAY, title='same'))
        snapshot = history.snapshot(TUESDAY_AM)
        self.assertEqual(snapshot.conflicts, [])
        self.assertEqual(snapshot.visible_ids(), ['a1'])


class TestClientQuestion(unittest.TestCase):

    def test_tuesday_morning_view(self):
        history = FeedHistory()
        history.ingest_all([
            # dated Sunday, collected Monday -> visible Tuesday
            rec('e1', 'a1', Kind.PUBLISH, MONDAY,
                business_time=datetime(2026, 11, 8, 12, 0, tzinfo=TZ),
                title='weekly outlook'),
            # collected only Wednesday -> not yet visible Tuesday
            rec('e2', 'a2', Kind.PUBLISH, WEDNESDAY, title='late one'),
            # published Monday, retracted Tuesday 08:00 -> gone by 09:00
            rec('e3', 'a3', Kind.PUBLISH, MONDAY, title='flash'),
            rec('e4', 'a3', Kind.RETRACT,
                datetime(2026, 11, 10, 8, 0, tzinfo=TZ)),
        ])
        self.assertEqual(history.visible_at(TUESDAY_AM), ['a1'])
        rss = history.snapshot(TUESDAY_AM).to_rss()
        self.assertIn(b'weekly outlook', rss)
        self.assertNotIn(b'late one', rss)
        self.assertNotIn(b'flash', rss)


if __name__ == '__main__':
    unittest.main()
