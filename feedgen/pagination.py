# -*- coding: utf-8 -*-
'''
    feedgen.pagination
    ~~~~~~~~~~~~~~~~~~

    Snapshot based pagination for feeds.

    Paginating a live feed by offset breaks as soon as entries are added or
    re-ordered between requests: entries are skipped or delivered twice.
    This module lets callers pin the feed to one determined published
    version (a snapshot) and page through that version consistently.
    Entries added or modified afterwards are not injected into an ongoing
    pagination run but are left for the next incremental round.

    :copyright: 2013-2020, Lars Kiesow <lkiesow@uos.de>

    :license: FreeBSD and LGPL, see license.* for more details.

'''


class SnapshotExpiredError(Exception):
    '''Raised when a page is requested from a published version which the
    feed generator no longer retains. The caller must restart with a full
    read from a fresh snapshot instead of silently receiving pages mixed
    from different versions.
    '''

    def __init__(self, version):
        super(SnapshotExpiredError, self).__init__(
            'Published version %s has expired. Restart pagination with a '
            'full read from a new snapshot.' % (version,))
        self.version = version


def _sort_key(entry):
    '''Sort key for entries: most recently updated first, ties broken by
    entry id so that entries sharing the same updated timestamp still get
    a deterministic, stable position and each appear exactly once.
    '''
    return (entry.updated(), entry.id() or '')


def _sorted_entries(entries):
    return sorted(entries, key=_sort_key, reverse=True)


class FeedSnapshot(object):
    '''A pinned, immutable view of the entries of a feed at one determined
    published version.

    Instances are created via :meth:`feedgen.feed.FeedGenerator.snapshot`.
    Pages sliced from a snapshot always belong to the same version, no
    matter how the feed changes while paginating.
    '''

    def __init__(self, generator, version, entries):
        self.__generator = generator
        #: Version identifier of the pinned published version.
        self.version = version
        # Pinned, stably sorted list of entries. Sort keys are captured
        # now so that later modifications of an entry (e.g. an editor
        # bumping an old entry to the top) cannot shift positions while
        # a caller is paginating.
        self.__entries = _sorted_entries(entries)
        # State per entry used to compute the next incremental round.
        self.__entry_states = dict(
            (id(entry), entry.updated()) for entry in entries)

    def _check_retained(self):
        if self.version not in self.__generator._retained_versions():
            raise SnapshotExpiredError(self.version)

    @property
    def expired(self):
        '''Whether this version is no longer retained by the generator.'''
        return self.version not in self.__generator._retained_versions()

    def __len__(self):
        return len(self.__entries)

    def entries(self):
        '''All entries of this version in pinned order.

        :returns: List of FeedEntry objects.
        '''
        self._check_retained()
        return list(self.__entries)

    def page(self, number, size):
        '''One page of this version.

        :param number: Zero based page number.
        :param size: Maximum number of entries per page.
        :returns: List of FeedEntry objects.
        :raises SnapshotExpiredError: If this version expired. Restart
            with a full read from a new snapshot.
        '''
        self._check_retained()
        if size < 1:
            raise ValueError('Page size must be at least 1')
        if number < 0:
            raise ValueError('Page number must not be negative')
        start = number * size
        return list(self.__entries[start:start + size])

    def pages(self, size):
        '''Iterate over all pages of this version.

        Every page is checked against the retention list, so a caller is
        explicitly stopped with a SnapshotExpiredError instead of being
        handed pages of a version the generator already dropped.

        :param size: Maximum number of entries per page.
        :returns: Generator of FeedEntry lists.
        '''
        number = 0
        while True:
            page = self.page(number, size)
            if not page:
                return
            yield page
            number += 1

    def _entry_states(self):
        '''Mapping of pinned entries to their updated value at snapshot
        time. Used by FeedGenerator.changes_since.
        '''
        return self.__entry_states
