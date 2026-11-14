# -*- coding: utf-8 -*-

'''
Tests for feedgen.schedule
'''

import unittest
from datetime import datetime, timedelta

from dateutil.tz import gettz, UTC

from feedgen.feed import FeedGenerator
from feedgen.schedule import (BlockedWindow, PollSource, next_poll_time,
                              poll_schedule)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)  # a Saturday


class TestPollSource(unittest.TestCase):

    def test_defaults(self):
        source = PollSource()
        self.assertEqual(source.ttl, None)
        self.assertEqual(source.skip_hours, set())
        self.assertEqual(source.skip_days, set())
        self.assertIsNotNone(source.timezone)

    def test_invalid_values(self):
        self.assertRaises(ValueError, PollSource, ttl=-1)
        self.assertRaises(ValueError, PollSource, skip_hours=[24])
        self.assertRaises(ValueError, PollSource, skip_days=['noday'])
        self.assertRaises(ValueError, PollSource, timezone='No/Such_Zone')

    def test_from_feed(self):
        fg = FeedGenerator()
        fg.ttl(60)
        fg.skipHours([1, 2])
        fg.skipDays('Sunday')
        source = PollSource.from_feed(fg, timezone='Europe/Berlin')
        self.assertEqual(source.ttl, 60)
        self.assertEqual(source.skip_hours, {1, 2})
        self.assertEqual(source.skip_days, {'Sunday'})
        self.assertEqual(source.timezone, gettz('Europe/Berlin'))


class TestPollSchedule(unittest.TestCase):

    def test_no_restrictions(self):
        schedule = poll_schedule([PollSource()], now=NOW)
        self.assertTrue(schedule.ok)
        self.assertEqual(schedule.start, NOW)
        self.assertEqual(schedule.blocked, [])

    def test_requires_source(self):
        self.assertRaises(ValueError, poll_schedule, [])

    def test_ttl_wait(self):
        source = PollSource(ttl=60, last_fetched=NOW - timedelta(minutes=30))
        schedule = poll_schedule([source], now=NOW)
        self.assertEqual(schedule.start, NOW + timedelta(minutes=30))
        self.assertEqual(len(schedule.blocked), 1)
        self.assertIn('ttl', schedule.blocked[0].reasons[0])

    def test_naive_datetimes_are_utc(self):
        source = PollSource(ttl=60,
                            last_fetched=datetime(2026, 11, 14, 11, 0))
        schedule = poll_schedule([source], now=datetime(2026, 11, 14, 11, 30))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 14, 12, 0, tzinfo=UTC))

    def test_skip_hour(self):
        # Saturday 2026-11-14, skip hour 13 UTC
        source = PollSource(skip_hours=[13])
        schedule = poll_schedule([source],
                                 now=datetime(2026, 11, 14, 12, 30,
                                              tzinfo=UTC))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 14, 12, 30, tzinfo=UTC))
        schedule = poll_schedule([source],
                                 now=datetime(2026, 11, 14, 13, 15,
                                              tzinfo=UTC))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 14, 14, 0, tzinfo=UTC))

    def test_skip_day(self):
        source = PollSource(skip_days=['Saturday'])
        schedule = poll_schedule([source], now=NOW)
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 15, 0, 0, tzinfo=UTC))

    def test_intersection_of_sources(self):
        # Both sources must be open: the shared slot is the later one.
        first = PollSource(skip_hours=[13])
        second = PollSource(ttl=120,
                            last_fetched=datetime(2026, 11, 14, 12, 30,
                                                  tzinfo=UTC))
        schedule = poll_schedule([first, second],
                                 now=datetime(2026, 11, 14, 13, 30,
                                              tzinfo=UTC))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 14, 14, 30, tzinfo=UTC))

    def test_timezone_skip_hour(self):
        # 13:00 in Europe/Berlin (CET, UTC+1) is 12:00 UTC
        source = PollSource(skip_hours=[13], timezone='Europe/Berlin')
        schedule = poll_schedule([source],
                                 now=datetime(2026, 11, 14, 12, 30,
                                              tzinfo=UTC))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 14, 13, 0, tzinfo=UTC))

    def test_dst_fall_back_ambiguous_hour(self):
        # 2026-11-01 02:00 EDT -> 01:00 EST: local hour 1 occurs twice,
        # at 05:00-06:00 UTC and again at 06:00-07:00 UTC.  Both
        # occurrences must be blocked separately.  05:30 UTC is 01:30
        # local time during the first occurrence of hour 1.
        source = PollSource(skip_hours=[1], timezone='America/New_York')
        schedule = poll_schedule([source],
                                 now=datetime(2026, 11, 1, 5, 30,
                                              tzinfo=UTC))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 1, 7, 0, tzinfo=UTC))
        blocked = schedule.blocked
        # The first blocked window is clipped to the search start and
        # spans both occurrences of the ambiguous local hour without a
        # gap at 06:00 UTC; the next one is hour 1 of the following day.
        self.assertEqual(blocked[0].start,
                         datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        self.assertEqual(blocked[0].end,
                         datetime(2026, 11, 1, 7, 0, tzinfo=UTC))
        self.assertEqual(blocked[1].start,
                         datetime(2026, 11, 2, 6, 0, tzinfo=UTC))

    def test_dst_fall_back_unskipped_hour_open_twice(self):
        # Hour 2 is not skipped, so polling is fine during both
        # occurrences of the repeated hour 1...2 transition day.
        source = PollSource(skip_hours=[3], timezone='America/New_York')
        schedule = poll_schedule([source],
                                 now=datetime(2026, 11, 1, 6, 30,
                                              tzinfo=UTC))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 1, 6, 30, tzinfo=UTC))

    def test_no_intersection_returns_blocking_periods(self):
        # Every day of the week is skipped by at least one source, so
        # there is no shared open time within the seven day horizon.
        first = PollSource(name='a', skip_days=['Monday', 'Tuesday',
                                                'Wednesday', 'Thursday',
                                                'Friday', 'Saturday'])
        second = PollSource(name='b', skip_days=['Sunday'])
        schedule = poll_schedule([first, second], now=NOW)
        self.assertFalse(schedule.ok)
        self.assertIsNone(schedule.start)
        self.assertTrue(schedule.blocked)
        # The blocking periods cover the whole horizon and name the
        # sources responsible.
        self.assertEqual(schedule.blocked[0].start, NOW)
        self.assertEqual(schedule.blocked[-1].end, schedule.horizon_end)
        self.assertEqual(schedule.horizon_end, NOW + timedelta(days=7))
        sources = set()
        for window in schedule.blocked:
            sources |= set(window.sources)
        self.assertEqual(sources, {'a', 'b'})

    def test_ttl_beyond_horizon(self):
        source = PollSource(ttl=60 * 24 * 30,
                            last_fetched=NOW - timedelta(minutes=1))
        schedule = poll_schedule([source], now=NOW)
        self.assertIsNone(schedule.start)
        self.assertEqual(len(schedule.blocked), 1)
        self.assertIn('ttl', schedule.blocked[0].reasons[0])

    def test_custom_horizon(self):
        source = PollSource(skip_days=['Saturday', 'Sunday'])
        schedule = poll_schedule([source], now=NOW,
                                 horizon=timedelta(days=1))
        self.assertIsNone(schedule.start)
        schedule = poll_schedule([source], now=NOW,
                                 horizon=timedelta(days=2))
        self.assertEqual(schedule.start,
                         datetime(2026, 11, 16, 0, 0, tzinfo=UTC))

    def test_next_poll_time(self):
        source = PollSource(skip_days=['Saturday'])
        self.assertEqual(next_poll_time([source], now=NOW),
                         datetime(2026, 11, 15, 0, 0, tzinfo=UTC))

    def test_blocked_window_eq(self):
        window = BlockedWindow(NOW, NOW, ('a',), ('r',))
        self.assertEqual(window, BlockedWindow(NOW, NOW, ('a',), ('r',)))
        self.assertNotEqual(window, BlockedWindow(NOW, NOW, ('b',), ('r',)))
        self.assertNotEqual(window, 'nope')
        self.assertIn("'a'", repr(window))


if __name__ == '__main__':
    unittest.main()
