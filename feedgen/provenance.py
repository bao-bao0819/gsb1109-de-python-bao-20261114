# -*- coding: utf-8 -*-
'''
    feedgen.provenance
    ~~~~~~~~~~~~~~~~~~

    Build a provenance index for manuscripts that may have been republished
    ("转供") one or more times.

    A manuscript keeps the authors it declares itself; those declarations are
    never overwritten with an upstream site's authors.  The index only follows
    *provable* source relationships:

        * the ``source`` a manuscript declares on its own record, and
        * repost statements explicitly supplied by integrating parties.

    Titles are never used to connect manuscripts: two manuscripts sharing a
    title are different works unless an explicit relationship between their
    identifiers exists.

    When the source chain forks, every branch is reported as an origin
    candidate.  When two sites republish each other (a cycle, including the
    two-site mutual repost), the index records the segment from which an
    earlier origin can no longer be determined instead of guessing one.
'''

from collections import deque

# Relationship kinds
DECLARED = 'declared'   # the reposting manuscript declared this source itself
EXTERNAL = 'external'   # an integrating party supplied the relationship

CYCLE = 'cycle'
MUTUAL_REPOST = 'mutual_repost'
SELF_LOOP = 'self_loop'


class ArticleRecord(object):
    '''A single manuscript with its own, self-declared metadata.

    :param article_id: Unique identifier of the manuscript (required).
    :param title: Title of the manuscript. Never used to build relationships.
    :param authors: Authors as declared by the manuscript itself. Kept as
                    given (strings or dictionaries), never rewritten.
    :param source: Identifier/reference of the source this manuscript itself
                   declares. ``None`` if it declares none.
    :param publisher: Optional site or outlet publishing this manuscript.
    :param extra: Additional metadata retained on the record.
    '''

    def __init__(self, article_id, title=None, authors=None, source=None,
                 publisher=None, **extra):
        if not article_id:
            raise ValueError('article_id is required')
        self.article_id = article_id
        self.title = title
        self.authors = list(authors) if authors else []
        self.source = source
        self.publisher = publisher
        self.extra = dict(extra)

    @classmethod
    def from_dict(cls, data):
        '''Create an :class:`ArticleRecord` from a plain dictionary.'''
        if not isinstance(data, dict):
            raise ValueError('Record data must be a dictionary')
        data = dict(data)
        article_id = data.pop('article_id', data.pop('id', None))
        return cls(article_id, **data)


class RepostStatement(object):
    '''A repost relationship supplied by an integrating party.

    States that ``reposter`` republished the manuscript identified by
    ``source``. This is evidence *about* a manuscript rather than a
    declaration made by the manuscript itself.

    :param reposter: Identifier of the reposting manuscript.
    :param source: Identifier of the manuscript it was republished from.
    :param declared_by: Party asserting this relationship (optional).
    :param note: Optional free-text note.
    '''

    def __init__(self, reposter, source, declared_by=None, note=None):
        if not reposter or not source:
            raise ValueError('reposter and source are required')
        if reposter == source:
            raise ValueError('A manuscript cannot repost itself')
        self.reposter = reposter
        self.source = source
        self.declared_by = declared_by
        self.note = note

    @classmethod
    def from_dict(cls, data):
        '''Create a :class:`RepostStatement` from a plain dictionary.'''
        if not isinstance(data, dict):
            raise ValueError('Statement data must be a dictionary')
        return cls(data['reposter'], data['source'],
                   data.get('declared_by'), data.get('note'))


class ProvenanceIndex(object):
    '''Holds manuscripts and provable repost relationships and builds the
    provenance index for each manuscript.
    '''

    def __init__(self):
        self._records = {}
        # downstream id -> list of edges, each edge:
        # {'source': upstream id, 'evidence': [{'relationship',
        #                                      'declared_by', 'note'}]}
        self._incoming = {}

    # -- registration -----------------------------------------------------

    def add_record(self, record):
        '''Add an :class:`ArticleRecord` (or a plain dict).

        The manuscript's own ``source`` field is registered as a *declared*
        relationship. Duplicate identifiers are rejected.
        '''
        if isinstance(record, dict):
            record = ArticleRecord.from_dict(record)
        if not isinstance(record, ArticleRecord):
            raise ValueError('Expected an ArticleRecord')
        if record.article_id in self._records:
            raise ValueError(
                    'Duplicate article_id: %s' % record.article_id)
        self._records[record.article_id] = record
        self._incoming.setdefault(record.article_id, [])
        if record.source:
            self._add_edge(record.source, record.article_id,
                           DECLARED, record.article_id, None)
        return record

    def add_repost(self, statement):
        '''Add an integrator-supplied :class:`RepostStatement` (or dict).

        Relationships may reference manuscripts that were never registered;
        they are kept and surfaced as unresolved source references.
        '''
        if isinstance(statement, dict):
            statement = RepostStatement.from_dict(statement)
        if not isinstance(statement, RepostStatement):
            raise ValueError('Expected a RepostStatement')
        self._incoming.setdefault(statement.reposter, [])
        self._add_edge(statement.source, statement.reposter,
                       EXTERNAL, statement.declared_by, statement.note)
        return statement

    def _add_edge(self, upstream, downstream, relationship, declared_by, note):
        edges = self._incoming.setdefault(downstream, [])
        for edge in edges:
            if edge['source'] == upstream:
                edge['evidence'].append({
                        'relationship': relationship,
                        'declared_by': declared_by,
                        'note': note})
                return
        edges.append({'source': upstream, 'evidence': [{
                'relationship': relationship,
                'declared_by': declared_by,
                'note': note}]})

    @classmethod
    def from_entries(cls, entries, repost_statements=None):
        '''Build an index from :class:`feedgen.entry.FeedEntry` objects.

        Each entry's atom id is used as the manuscript identifier, the entry's
        own authors are preserved verbatim and its declared ``source`` becomes
        a declared relationship. A source reference matching no entry id is
        retained as an unresolved reference.
        '''
        index = cls()
        for entry in entries:
            source_ref = None
            src = entry.source()
            if src:
                source_ref = src.get('url')
            index.add_record(ArticleRecord(
                    article_id=entry.id(),
                    title=entry.title(),
                    authors=[dict(a) for a in (entry.author() or [])],
                    source=source_ref))
        for statement in repost_statements or []:
            index.add_repost(statement)
        return index

    @classmethod
    def from_feed(cls, feed, repost_statements=None):
        '''Build an index from a :class:`feedgen.feed.FeedGenerator`.'''
        return cls.from_entries(feed.entry(), repost_statements)

    # -- queries ----------------------------------------------------------

    def records(self):
        '''Return all registered records, sorted by identifier.'''
        return [self._records[rid] for rid in sorted(self._records)]

    def index_for(self, article_id):
        '''Build the provenance index entry for one manuscript.

        The returned structure is plain JSON-serializable data and contains:

        * ``article_id`` / ``title`` / ``publisher`` of the manuscript;
        * ``declared_authors``: the manuscript's own author declarations;
        * ``direct_contributors``: every manuscript (or unresolved reference)
          feeding it directly, with the relationship kind and asserting party;
        * ``chain``: every intermediate repost statement reachable upstream,
          ordered from the manuscript backwards;
        * ``origin_candidates``: earliest confirmable sources — more than one
          when the chain forks, flagged ``resolved`` when the referenced
          manuscript is known;
        * ``indeterminate``: ``None`` or a description of the cycle segment
          past which the origin cannot be determined.
        '''
        if article_id not in self._records:
            raise KeyError('Unknown article_id: %s' % article_id)

        hops = {}                 # (upstream, downstream) -> hop dict
        origins = []              # deduped candidate dicts
        origin_ids = set()
        ambiguities = []
        ambiguity_keys = set()

        queue = deque([(article_id, [article_id])])
        while queue:
            node, path = queue.popleft()
            edges = self._incoming.get(node, [])
            if not edges:
                # No provable upstream: the manuscript itself is the earliest
                # confirmable source.
                self._add_origin(origins, origin_ids, node, path)
                continue
            for edge in edges:
                upstream = edge['source']
                self._record_hop(hops, upstream, node, edge)
                if upstream in path:
                    self._record_cycle(ambiguities, ambiguity_keys,
                                       path, node, upstream)
                    continue
                if upstream not in self._records:
                    self._add_unresolved_origin(
                            origins, origin_ids, upstream,
                            path + [upstream])
                    continue
                queue.append((upstream, path + [upstream]))

        record = self._records[article_id]
        return {
                'article_id': article_id,
                'title': record.title,
                'publisher': record.publisher,
                'declared_authors': list(record.authors),
                'direct_contributors': self._direct_contributors(
                        article_id),
                'chain': [hops[key] for key in sorted(
                        hops, key=lambda pair: (hops[pair]['order'],
                                                pair[0], pair[1]))],
                'origin_candidates': origins,
                'indeterminate': self._build_indeterminate(ambiguities),
        }

    def build_index(self):
        '''Build index entries for every registered manuscript.'''
        return [self.index_for(rid) for rid in sorted(self._records)]

    # -- internals --------------------------------------------------------

    def _record_hop(self, hops, upstream, downstream, edge):
        key = (upstream, downstream)
        if key in hops:
            return
        evidence = list(edge['evidence'])
        kinds = set(ev['relationship'] for ev in evidence)
        relationship = EXTERNAL if EXTERNAL in kinds else DECLARED
        hops[key] = {
                'order': len(hops),
                'from': upstream,
                'to': downstream,
                'relationship': relationship,
                'resolved': upstream in self._records,
                'declared_by': [ev['declared_by'] for ev in evidence
                                if ev['declared_by'] is not None],
                'evidence': evidence,
        }

    def _record_cycle(self, ambiguities, ambiguity_keys, path, node,
                      upstream):
        # The closing edge runs from the current node back to `upstream`,
        # which already appears in the traversal path.
        start = path.index(upstream)
        segment = path[start:] + [upstream]
        key = tuple(segment)
        if key in ambiguity_keys:
            return
        ambiguity_keys.add(key)
        distinct = set(segment)
        if len(segment) == 2:
            reason = SELF_LOOP
        elif len(distinct) == 2:
            reason = MUTUAL_REPOST
        else:
            reason = CYCLE
        ambiguities.append({
                'reason': reason,
                'segment': segment,
                'from_article_id': upstream,
                'message': (
                        'Repost cycle %s detected; the origin cannot be '
                        'determined beyond the segment starting at %r.'
                        % (' -> '.join(segment), upstream)),
        })

    def _add_origin(self, origins, origin_ids, article_id, path):
        if article_id in origin_ids:
            return
        origin_ids.add(article_id)
        record = self._records[article_id]
        origins.append({
                'article_id': article_id,
                'resolved': True,
                'title': record.title,
                'publisher': record.publisher,
                'authors': list(record.authors),
                'path': list(path),
        })

    def _add_unresolved_origin(self, origins, origin_ids, reference, path):
        if reference in origin_ids:
            return
        origin_ids.add(reference)
        origins.append({
                'article_id': reference,
                'resolved': False,
                'title': None,
                'publisher': None,
                'authors': [],
                'path': list(path),
        })

    def _direct_contributors(self, article_id):
        result = []
        for edge in self._incoming.get(article_id, []):
            upstream = edge['source']
            kinds = set(ev['relationship'] for ev in edge['evidence'])
            relationship = EXTERNAL if EXTERNAL in kinds else DECLARED
            record = self._records.get(upstream)
            result.append({
                    'article_id': upstream,
                    'resolved': record is not None,
                    'relationship': relationship,
                    'declared_by': [
                            ev['declared_by'] for ev in edge['evidence']
                            if ev['declared_by'] is not None],
                    'publisher': record.publisher if record else None,
                    'authors': list(record.authors) if record else [],
            })
        return result

    def _build_indeterminate(self, ambiguities):
        if not ambiguities:
            return None
        if len(ambiguities) == 1:
            return ambiguities[0]
        return {
                'reason': CYCLE,
                'segments': [amb['segment'] for amb in ambiguities],
                'from_article_id': ambiguities[0]['from_article_id'],
                'messages': [amb['message'] for amb in ambiguities],
        }
