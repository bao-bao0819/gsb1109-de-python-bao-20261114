# -*- coding: utf-8 -*-

'''
Rebuild historical RSS snapshots from timestamped announcement records.

Two time axes are tracked separately:

* ``collected_at`` -- system/transaction time: when a record was received
  (采集时间).  This alone decides what a client could have seen at any
  given moment.
* ``business_time`` -- the item's own business timestamp (稿件发布日期).
  A first publication may carry a business date earlier than its first
  receipt, and a correction may backfill an even earlier business time.
  Neither affects *visibility*; they only change the timestamps shown in
  a reconstructed feed.

Semantics of the record kinds:

* ``PUBLISH`` -- the item becomes visible (again).  A re-publication after
  a retraction ends the retraction window; the gap in between stays
  invisible in every historical slice.
* ``CORRECT`` -- updates content and/or business time.  It never changes
  visibility: a retracted item stays retracted until it is published
  again.  A correction is also accepted as the first sighting of an item.
* ``RETRACT`` -- the item becomes invisible from the record's collection
  time onward.

Guarantees:

* Idempotent ingest: records are deduplicated by ``event_id``, so
  replaying the same message any number of times changes no historical
  slice.
* Conflicts: records for the same item collected at the same instant
  whose effects disagree cannot be ordered.  Instead of picking a winner
  arbitrarily, every candidate state is kept and the item is reported as
  a conflict.
'''

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from feedgen.feed import FeedGenerator


class Kind(Enum):
    PUBLISH = 'publish'
    CORRECT = 'correct'
    RETRACT = 'retract'


@dataclass(frozen=True)
class Record:
    '''One received message (publication, correction or retraction).'''
    event_id: str
    item_id: str
    kind: Kind
    collected_at: datetime
    business_time: datetime = None
    title: str = None
    content: str = None


@dataclass(frozen=True)
class _State:
    '''One possible state of an item at some point in collection time.'''
    seen: bool = False
    visible: bool = False
    title: str = None
    content: str = None
    business_time: datetime = None


def _apply(state, record):
    '''Apply a single record to a state, returning the successor state.'''
    if record.kind is Kind.RETRACT:
        return _State(seen=True, visible=False, title=state.title,
                      content=state.content,
                      business_time=state.business_time)
    if record.kind is Kind.PUBLISH or not state.seen:
        # A publication (re)opens visibility; a correction as first
        # sighting is treated like a publication.
        return _State(seen=True, visible=True,
                      title=record.title if record.title is not None
                      else state.title,
                      content=record.content if record.content is not None
                      else state.content,
                      business_time=record.business_time
                      if record.business_time is not None
                      else state.business_time)
    # CORRECT on a known item: update fields, keep visibility as is.
    return _State(seen=True, visible=state.visible,
                  title=record.title if record.title is not None
                  else state.title,
                  content=record.content if record.content is not None
                  else state.content,
                  business_time=record.business_time
                  if record.business_time is not None
                  else state.business_time)


def _effect_key(record):
    return (record.kind, record.title, record.content, record.business_time)


@dataclass(frozen=True)
class ItemView:
    '''Resolved state of one item in a snapshot.'''
    item_id: str
    visible: bool
    title: str = None
    content: str = None
    business_time: datetime = None
    first_collected_at: datetime = None


@dataclass(frozen=True)
class Conflict:
    '''An item whose records cannot be ordered unambiguously.'''
    item_id: str
    since: datetime
    candidates: tuple


class Snapshot:
    '''The reconstructed feed as visible at one collection-time instant.'''

    def __init__(self, as_of, items, conflicts):
        self.as_of = as_of
        self.items = items          # dict: item_id -> ItemView
        self.conflicts = conflicts  # list of Conflict

    def visible_items(self):
        '''ItemViews the client could see at ``as_of``, conflicts excluded.'''
        return [view for view in self.items.values() if view.visible]

    def visible_ids(self):
        return sorted(view.item_id for view in self.visible_items())

    def to_rss(self, feed_id='announcements', title='Announcements',
               link='http://example.org/'):
        '''Render the visible items as an RSS 2.0 document (bytes).'''
        fg = FeedGenerator()
        fg.id(feed_id)
        fg.title(title)
        fg.link(href=link, rel='alternate')
        fg.description(title)

        def aware(dt):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        views = sorted(
            self.visible_items(),
            key=lambda v: (v.business_time or v.first_collected_at),
            reverse=True)
        for view in views:
            fe = fg.add_entry()
            fe.id(view.item_id)
            fe.title(view.title or view.item_id)
            if view.content is not None:
                fe.description(view.content)
            fe.pubDate(aware(view.business_time or view.first_collected_at))
        return fg.rss_str(pretty=True)


class FeedHistory:
    '''Append-only store of records with point-in-time reconstruction.'''

    def __init__(self):
        self._events = {}

    def ingest(self, record):
        '''Store a record.  Returns False for duplicates (replays).'''
        if record.event_id in self._events:
            return False
        self._events[record.event_id] = record
        return True

    def ingest_all(self, records):
        return sum(1 for record in records if self.ingest(record))

    def snapshot(self, as_of):
        '''Reconstruct the feed as a client could have seen it at ``as_of``.'''
        by_item = {}
        for record in self._events.values():
            if record.collected_at <= as_of:
                by_item.setdefault(record.item_id, []).append(record)

        items = {}
        conflicts = []
        for item_id, records in by_item.items():
            records.sort(key=lambda r: (r.collected_at, r.event_id))
            first_collected = records[0].collected_at

            branches = {_State()}
            conflict_since = None
            cursor = 0
            while cursor < len(records):
                collected = records[cursor].collected_at
                group = []
                while cursor < len(records) and \
                        records[cursor].collected_at == collected:
                    group.append(records[cursor])
                    cursor += 1
                # Records with identical effects are redundant replays;
                # only genuinely different effects fork the state.
                effects = {}
                for record in group:
                    effects.setdefault(_effect_key(record), record)
                if len(effects) > 1 and conflict_since is None:
                    conflict_since = collected
                branches = {_apply(branch, record)
                            for branch in branches
                            for record in effects.values()}

            if len(branches) > 1:
                candidates = tuple(sorted(
                    (ItemView(item_id=item_id, visible=b.visible,
                              title=b.title, content=b.content,
                              business_time=b.business_time,
                              first_collected_at=first_collected)
                     for b in branches),
                    key=lambda v: (not v.visible, v.title or '',
                                   v.content or '')))
                conflicts.append(Conflict(item_id=item_id,
                                          since=conflict_since,
                                          candidates=candidates))
            else:
                state = branches.pop()
                if state.seen:
                    items[item_id] = ItemView(
                        item_id=item_id, visible=state.visible,
                        title=state.title, content=state.content,
                        business_time=state.business_time,
                        first_collected_at=first_collected)
        return Snapshot(as_of, items, conflicts)

    def visible_at(self, as_of):
        '''Ids of the announcements visible at ``as_of`` (the client view).'''
        return self.snapshot(as_of).visible_ids()
