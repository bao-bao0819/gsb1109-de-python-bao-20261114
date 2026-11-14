# -*- coding: utf-8 -*-
'''
    feedgen.schedule
    ~~~~~~~~~~~~~~~~

    Compute shared polling times from the TTL, skipHours and skipDays hints
    of one or more feeds.  Aggregators and publishers can share the same
    subscription configuration and derive the times at which a feed may be
    polled from it.

    :copyright: 2013, Lars Kiesow <lkiesow@uos.de>
    :license: FreeBSD and LGPL, see license.* for more details.
'''

from datetime import datetime, time, timedelta

from dateutil.tz import gettz, UTC

DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
             'Saturday', 'Sunday')

#: How far into the future a polling time is searched by default.
DEFAULT_HORIZON = timedelta(days=7)


def _as_utc(value):
    '''Return an aware datetime in UTC.  Naive datetimes are assumed to be
    in UTC already.
    '''
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _resolve_timezone(timezone):
    '''Resolve a timezone given as name or tzinfo instance.'''
    if timezone is None:
        return UTC
    if isinstance(timezone, str):
        tz = gettz(timezone)
        if tz is None:
            raise ValueError('Unknown timezone %s' % timezone)
        return tz
    return timezone


class BlockedWindow(object):
    '''A half-open UTC interval ``[start, end)`` during which polling is
    not possible, together with the sources and reasons blocking it.
    '''

    def __init__(self, start, end, sources, reasons):
        self.start = start
        self.end = end
        self.sources = tuple(sources)
        self.reasons = tuple(reasons)

    def __repr__(self):
        return 'BlockedWindow(%s, %s, sources=%r, reasons=%r)' % (
                self.start.isoformat(), self.end.isoformat(),
                list(self.sources), list(self.reasons))

    def __eq__(self, other):
        return (isinstance(other, BlockedWindow)
                and (self.start, self.end, self.sources, self.reasons)
                == (other.start, other.end, other.sources, other.reasons))


class PollSchedule(object):
    '''Result of :func:`poll_schedule`.

    ``start`` is the earliest instant at which all sources may be polled
    or ``None`` if no such time exists within the horizon.  ``blocked``
    lists the merged windows (with sources and reasons) which prevent
    polling inside the horizon; it is especially relevant when ``start``
    is ``None`` since it tells exactly which periods block the poll
    instead of silently pushing the search further into the future.
    '''

    def __init__(self, start, horizon_end, blocked):
        self.start = start
        self.horizon_end = horizon_end
        self.blocked = blocked

    @property
    def ok(self):
        return self.start is not None

    def __repr__(self):
        return 'PollSchedule(start=%r, horizon_end=%r, blocked=%r)' % (
                self.start, self.horizon_end, self.blocked)


class PollSource(object):
    '''Polling constraints of a single feed.

    :param name: Optional name used to identify the source in results.
    :param ttl: Time to live in minutes.  Minimum wait after the last
                successful fetch before the feed may be polled again.
    :param skip_hours: Hours (0-23, local time) which must not be polled.
    :param skip_days: Day names ('Monday' .. 'Sunday', local time) which
                      must not be polled.
    :param timezone: Timezone of the source as tzinfo or IANA name.  The
                     skip hours and days are evaluated in this timezone.
                     Defaults to UTC.
    :param last_fetched: Datetime of the last successful fetch.
    '''

    def __init__(self, name=None, ttl=None, skip_hours=None, skip_days=None,
                 timezone=None, last_fetched=None):
        self.name = name
        if ttl is not None:
            ttl = int(ttl)
            if ttl < 0:
                raise ValueError('Invalid ttl %s' % ttl)
        self.ttl = ttl
        self.skip_hours = set()
        for hour in skip_hours or ():
            if hour not in range(24):
                raise ValueError('Invalid hour %s' % hour)
            self.skip_hours.add(hour)
        self.skip_days = set()
        for day in skip_days or ():
            if day not in DAY_NAMES:
                raise ValueError('Invalid day %s' % day)
            self.skip_days.add(day)
        self.timezone = _resolve_timezone(timezone)
        self.last_fetched = (_as_utc(last_fetched)
                             if last_fetched is not None else None)

    @classmethod
    def from_feed(cls, feed, name=None, timezone=None, last_fetched=None):
        '''Create a source from a :class:`feedgen.feed.FeedGenerator`,
        reusing its ``ttl()``, ``skipHours()`` and ``skipDays()`` settings
        so that publisher and aggregator share one configuration.
        '''
        return cls(name=name, ttl=feed.ttl(), skip_hours=feed.skipHours(),
                   skip_days=feed.skipDays(), timezone=timezone,
                   last_fetched=last_fetched)

    def _hour_windows_utc(self, day, hour):
        '''UTC intervals covered by one local hour.  Ambiguous local hours
        (DST fall-back) occur twice and each occurrence is evaluated and
        returned separately.
        '''
        naive_start = datetime.combine(day, time(hour))
        naive_end = naive_start + timedelta(hours=1)
        windows = set()
        for fold in (0, 1):
            start = naive_start.replace(tzinfo=self.timezone, fold=fold)
            end = naive_end.replace(tzinfo=self.timezone, fold=fold)
            windows.add((start.astimezone(UTC), end.astimezone(UTC)))
        return sorted(windows)

    def blocked_windows(self, start, end):
        '''Yield BlockedWindow entries of this source within the half-open
        UTC interval ``[start, end)``.
        '''
        if self.ttl and self.last_fetched is not None:
            wait_until = self.last_fetched + timedelta(minutes=self.ttl)
            if wait_until > start:
                yield BlockedWindow(start, min(wait_until, end),
                                    (self.name,),
                                    ('ttl: last fetch %s + %dmin'
                                     % (self.last_fetched.isoformat(),
                                        self.ttl),))
        first_day = (start.astimezone(self.timezone)
                     - timedelta(days=1)).date()
        last_day = (end.astimezone(self.timezone)
                    + timedelta(days=1)).date()
        day = first_day
        while day <= last_day:
            day_name = DAY_NAMES[day.weekday()]
            if day_name in self.skip_days:
                hours = range(24)
                reason = 'skipDay %s' % day_name
            else:
                hours = sorted(self.skip_hours)
                reason = None
            for hour in hours:
                for win_start, win_end in self._hour_windows_utc(day, hour):
                    win_start = max(win_start, start)
                    win_end = min(win_end, end)
                    if win_start < win_end:
                        yield BlockedWindow(
                                win_start, win_end, (self.name,),
                                (reason or 'skipHour %d' % hour,))
            day += timedelta(days=1)


def _merge_windows(windows):
    '''Merge overlapping or adjacent blocked windows, collecting the
    sources and reasons of every contributing window.
    '''
    merged = []
    for window in sorted(windows, key=lambda w: (w.start, w.end)):
        if merged and window.start <= merged[-1].end:
            last = merged[-1]
            last.end = max(last.end, window.end)
            last.sources = tuple(sorted(set(last.sources)
                                        | set(window.sources)))
            last.reasons = tuple(sorted(set(last.reasons)
                                        | set(window.reasons)))
        else:
            merged.append(BlockedWindow(window.start, window.end,
                                        window.sources, window.reasons))
    return merged


def poll_schedule(sources, now=None, horizon=None):
    '''Compute when a group of sources sharing one collection run may be
    polled next.

    A time is only usable if it lies in the open periods of *every*
    source: no source may be in a skipped local hour or day and each
    source's TTL must have elapsed since its last successful fetch.

    :param sources: Iterable of :class:`PollSource` objects.
    :param now: Start of the search.  Defaults to the current time.
    :param horizon: How far to search.  Defaults to seven days.
    :returns: A :class:`PollSchedule`.  If no common open time exists
              within the horizon, its ``start`` is ``None`` and
              ``blocked`` contains the concrete periods preventing a
              poll; the search is never extended beyond the horizon.
    '''
    sources = list(sources)
    if not sources:
        raise ValueError('At least one source is required')
    now = _as_utc(now) if now is not None else datetime.now(UTC)
    horizon = horizon if horizon is not None else DEFAULT_HORIZON
    end = now + horizon

    for index, source in enumerate(sources):
        if source.name is None:
            source.name = 'source %d' % (index + 1)

    windows = []
    for source in sources:
        windows.extend(source.blocked_windows(now, end))
    blocked = _merge_windows(windows)

    start = now
    for window in blocked:
        if window.start > start:
            break
        start = max(start, window.end)
    if start >= end:
        start = None
    return PollSchedule(start, end, blocked)


def next_poll_time(sources, now=None, horizon=None):
    '''Return the earliest shared polling time or ``None`` if the sources
    have no common open period within the horizon.
    '''
    return poll_schedule(sources, now=now, horizon=horizon).start
