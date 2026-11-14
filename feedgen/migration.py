# -*- coding: utf-8 -*-
'''
    feedgen.migration
    ~~~~~~~~~~~~~~~~~

    Helpers for migrating an existing RSS feed to Atom without losing the
    identity of historical articles.

    The important rule is that identity must come from an explicit
    ``legacy guid -> atom id`` mapping.  A shared link is only a hint: two
    genuinely different articles (for example a repost) may use the same link,
    so links are never used to decide whether two records describe the same
    article.

    :license: FreeBSD and LGPL, see license.* for more details.
'''

import argparse
from collections import OrderedDict
try:
    from collections.abc import Mapping
except ImportError:  # pragma: no cover - Python 2 compatibility
    from collections import Mapping
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import sys


_LEGACY_KEYS = ('legacy_guid', 'old_guid', 'rss_guid', 'previous_guid',
                'legacy_id', 'old_id', 'previous_id', 'oldGuid', 'oldGUID',
                'legacyGuid', 'rssGuid', 'oldID', 'previousId',
                'legacy', 'previous', 'old', 'guid')
_ATOM_KEYS = ('atom_id', 'new_id', 'atom_uuid', 'new_guid', 'current_id',
              'canonical_id', 'atomId', 'atomID', 'newId', 'newID',
              'canonicalId', 'newGuid', 'atom', 'current', 'canonical',
              'new', 'id')
_LINK_KEYS = ('link', 'url', 'href', 'permalink_url')


class MappingError(ValueError):
    '''Raised when a mapping record cannot be interpreted.'''


class MappingConflictError(MappingError):
    '''Raised when conflicting mappings are found in strict mode.

    The :attr:`conflicts` attribute contains the affected conflict groups so a
    caller can report them without reparsing the input.
    '''

    def __init__(self, conflicts):
        self.conflicts = tuple(conflicts)
        super().__init__(str(self))

    def __str__(self):
        return '\n'.join(str(conflict) for conflict in self.conflicts)


def _clean_identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise MappingError('%s must be a non-empty string' % name)
    return value.strip()


@dataclass(frozen=True)
class GuidMapping:
    '''One explicit mapping between a historical RSS guid and a new Atom id.

    :param legacy_guid: The guid used by the historical RSS feed.
    :param atom_id: The id used by the new Atom feed.
    :param link: Optional article link. This is only a hint and is never used
        as identity.
    :param source: Optional name of the migration material this record came
        from. It is only used for reporting.
    '''

    legacy_guid: str
    atom_id: str
    link: str = None
    source: str = None

    def __post_init__(self):
        object.__setattr__(self, 'legacy_guid',
                           _clean_identifier(self.legacy_guid, 'legacy_guid'))
        object.__setattr__(self, 'atom_id',
                           _clean_identifier(self.atom_id, 'atom_id'))
        if self.link is not None and not isinstance(self.link, str):
            raise MappingError('link must be a string or None')
        if self.source is not None and not isinstance(self.source, str):
            raise MappingError('source must be a string or None')

    @property
    def old_guid(self):
        '''Alias for :attr:`legacy_guid`.'''
        return self.legacy_guid

    @property
    def new_id(self):
        '''Alias for :attr:`atom_id`.'''
        return self.atom_id

    @property
    def guid(self):
        '''Alias for :attr:`legacy_guid`.'''
        return self.legacy_guid

    @property
    def id(self):
        '''Alias for :attr:`atom_id`.'''
        return self.atom_id

    @classmethod
    def from_value(cls, value, source=None):
        '''Create a mapping from a dictionary.

        The accepted key names are intentionally flexible so that exports
        from different migration tools can be consumed without rewriting:

        - legacy guid: ``legacy_guid``, ``old_guid``, ``rss_guid``, ``old``,
          ``guid``
        - Atom id: ``atom_id``, ``new_id``, ``new``, ``id``, ``atom``
        - optional: ``link``, ``source``
        '''
        if isinstance(value, cls):
            if source and not value.source:
                return replace(value, source=source)
            return value
        if not isinstance(value, Mapping):
            raise MappingError('mapping record must be a dictionary')
        legacy_key = next((key for key in _LEGACY_KEYS if key in value), None)
        atom_key = next((key for key in _ATOM_KEYS if key in value), None)
        if legacy_key is None or atom_key is None:
            raise MappingError(
                'mapping record needs an old guid and a new atom id')
        link = value.get('link')
        if link is None:
            link = next((value[key] for key in _LINK_KEYS
                         if value.get(key)), None)
        if link is not None and not isinstance(link, str):
            raise MappingError('link must be a string or None')
        return cls(legacy_guid=value[legacy_key],
                   atom_id=value[atom_key],
                   link=link,
                   source=value.get('source') or source)

    @classmethod
    def from_csv_row(cls, row, source=None):
        '''Create a mapping from a CSV row (a dictionary of strings).

        Like :meth:`from_value`, the column names are flexible.
        '''
        return cls.from_value(row, source)

    def as_dict(self):
        '''Return a JSON-serializable representation.'''
        result = OrderedDict()
        result['legacy_guid'] = self.legacy_guid
        result['atom_id'] = self.atom_id
        if self.link is not None:
            result['link'] = self.link
        if self.source is not None:
            result['source'] = self.source
        return dict(result)

    to_dict = as_dict

    def __getitem__(self, key):
        if key in ('legacy_guid', 'old_guid', 'guid', 'old', 'legacy'):
            return self.legacy_guid
        if key in ('atom_id', 'new_id', 'id', 'new', 'atom'):
            return self.atom_id
        if key in ('link', 'url', 'href'):
            return self.link
        if key == 'source':
            return self.source
        raise KeyError(key)

    def _sort_key(self):
        return (self.atom_id, self.source or '', self.link or '')


@dataclass(frozen=True)
class MappingSource:
    '''A named migration material.

    :param name: Name used in conflict reports (typically a file name).
    :param mappings: Mapping records, a simple dictionary, or a record.
    '''

    name: str
    mappings: object

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise MappingError('source name must be a non-empty string')


@dataclass(frozen=True, eq=False)
class MappingConflict:
    '''A group of records that disagree about one historical guid.'''

    legacy_guid: str
    candidates: tuple

    def __post_init__(self):
        object.__setattr__(self, 'candidates', tuple(self.candidates))

    def __eq__(self, other):
        if isinstance(other, MappingConflict):
            return (self.legacy_guid == other.legacy_guid and
                    self.candidates == other.candidates)
        if isinstance(other, Mapping):
            # Allow comparisons against a plain expected dictionary using
            # only the keys the caller supplied.
            try:
                return all(self[key] == value for key, value in other.items())
            except KeyError:
                return False
        return NotImplemented

    __hash__ = None

    @property
    def guid(self):
        '''Alias for :attr:`legacy_guid`.'''
        return self.legacy_guid

    @property
    def atom_ids(self):
        '''All distinct new ids claimed for this guid, sorted.'''
        return tuple(sorted({candidate.atom_id
                             for candidate in self.candidates}))

    @property
    def new_ids(self):
        '''Alias for :attr:`atom_ids`.'''
        return self.atom_ids

    @property
    def sources(self):
        '''Source names participating in this conflict, sorted.'''
        return tuple(sorted({candidate.source
                             for candidate in self.candidates
                             if candidate.source}))

    def as_dict(self):
        '''Return a JSON-serializable conflict group.'''
        return {
            'legacy_guid': self.legacy_guid,
            'atom_ids': list(self.atom_ids),
            'sources': list(self.sources),
            'candidates': [candidate.as_dict()
                           for candidate in self.candidates],
        }

    to_dict = as_dict

    def __getitem__(self, key):
        if key in ('legacy_guid', 'guid', 'old_guid', 'old'):
            return self.legacy_guid
        if key in ('atom_ids', 'new_ids', 'ids'):
            return self.atom_ids
        if key == 'sources':
            return self.sources
        if key in ('candidates', 'records', 'mappings'):
            return self.candidates
        raise KeyError(key)

    def __iter__(self):
        return iter(self.candidates)

    def __len__(self):
        return len(self.candidates)

    def __str__(self):
        claims = ', '.join(
            '%s -> %s%s' % (
                candidate.legacy_guid,
                candidate.atom_id,
                ' (%s)' % candidate.source if candidate.source else '')
            for candidate in self.candidates)
        return 'legacy guid conflict: %s' % claims


@dataclass(frozen=True)
class ResolvedArticle:
    '''One article after migration, with all of its known identifiers.'''

    atom_id: str
    legacy_guids: tuple
    links: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'legacy_guids',
                           tuple(sorted(set(self.legacy_guids))))
        object.__setattr__(self, 'links',
                           tuple(sorted(set(link for link in self.links
                                            if link))))

    def canonical_guid(self, preferred_links=()):
        '''Return the guid to use in the RSS representation.

        When several historical guids resolve to the same Atom article, an
        explicit link can be used as a tie-breaker.  If no link matches, the
        lexicographically first guid is used so the result is deterministic.
        '''
        preferred_links = tuple(preferred_links or ())
        for link in preferred_links:
            for candidate in self._candidates:
                if candidate.link == link:
                    return candidate.legacy_guid
        return self.legacy_guids[0] if self.legacy_guids else None

    @property
    def _candidates(self):
        return tuple(getattr(self, '_mapping_candidates', ()))

    def as_dict(self):
        '''Return a JSON-serializable article record.'''
        return {
            'atom_id': self.atom_id,
            'legacy_guids': list(self.legacy_guids),
            'links': list(self.links),
        }


@dataclass(frozen=True)
class AppliedGuidMapping:
    '''A mapping that was applied to one entry.'''

    atom_id: str
    legacy_guid: str
    entry_index: int
    previous_guid: str = None

    def as_dict(self):
        result = {
            'atom_id': self.atom_id,
            'legacy_guid': self.legacy_guid,
            'entry_index': self.entry_index,
        }
        if self.previous_guid is not None:
            result['previous_guid'] = self.previous_guid
        return result


@dataclass(frozen=True)
class RejectedGuidMapping:
    '''A mapping that was not applied because its guid is ambiguous.'''

    legacy_guid: str
    atom_ids: tuple
    entry_index: int = None

    def as_dict(self):
        result = {
            'legacy_guid': self.legacy_guid,
            'atom_ids': list(self.atom_ids),
        }
        if self.entry_index is not None:
            result['entry_index'] = self.entry_index
        return result


@dataclass
class MappingPlan:
    '''Result of resolving one or more migration materials.

    :param mappings: Accepted mappings keyed by historical guid.
    :param conflicts: Conflicting groups keyed by historical guid.
    :param articles: Accepted articles keyed by new Atom id.
    :param applied: Mappings applied to feed entries (filled by
        :meth:`FeedGenerator.apply_guid_mappings`).
    :param unmatched: Atom ids known from the migration data but absent from
        the feed.
    '''

    mappings: dict = field(default_factory=dict)
    conflicts: tuple = ()
    articles: dict = field(default_factory=dict)
    applied: tuple = ()
    rejected: tuple = ()
    unmatched: tuple = ()
    strict: bool = False

    def raise_on_conflict(self):
        '''Raise :class:`MappingConflictError` if conflicts were found.

        :returns: The plan itself, for chaining.
        '''
        if self.conflicts:
            raise MappingConflictError(self.conflicts)
        return self

    @property
    def conflict_groups(self):
        '''Alias for :attr:`conflicts`.'''
        return self.conflicts

    @property
    def mapping_count(self):
        '''Number of accepted historical-guid mappings.'''
        return len(self.mappings)

    @property
    def article_count(self):
        '''Number of distinct Atom articles after conflict resolution.'''
        return len(self.articles)

    @property
    def new_ids(self):
        '''Accepted Atom ids, sorted.'''
        return tuple(sorted(self.articles))

    @property
    def legacy_guids(self):
        '''Accepted historical guids, sorted.'''
        return tuple(sorted(self.mappings))

    def for_legacy_guid(self, legacy_guid):
        '''Return the accepted mapping for a historical guid, if any.'''
        return self.mappings.get(legacy_guid)

    def for_atom_id(self, atom_id):
        '''Return the accepted article for an Atom id, if any.'''
        return self.articles.get(atom_id)

    def for_new_id(self, new_id):
        '''Alias for :meth:`for_atom_id`.'''
        return self.for_atom_id(new_id)

    def as_dict(self):
        '''Return a JSON-serializable plan, including conflict groups.'''
        return {
            'mappings': [self.mappings[guid].as_dict()
                         for guid in sorted(self.mappings)],
            'conflicts': [conflict.as_dict()
                          for conflict in self.conflicts],
            'conflict_groups': [conflict.as_dict()
                                for conflict in self.conflicts],
            'articles': [self.articles[atom_id].as_dict()
                         for atom_id in sorted(self.articles)],
            'article_count': self.article_count,
            'mapping_count': self.mapping_count,
            'new_ids': list(self.new_ids),
            'legacy_guids': list(self.legacy_guids),
            'applied': [item.as_dict() for item in self.applied],
            'rejected': [item.as_dict() for item in self.rejected],
            'unmatched': list(self.unmatched),
        }

    to_dict = as_dict

    def __getitem__(self, key):
        return self.as_dict()[key]

    def __contains__(self, legacy_guid):
        return legacy_guid in self.mappings

    def conflict_report(self):
        '''Return a short human-readable report of every conflict group.'''
        return '\n'.join(str(conflict) for conflict in self.conflicts)


def _iter_records(value, default_source=None):
    '''Yield :class:`GuidMapping` objects from any supported input shape.'''
    if value is None:
        return
    if isinstance(value, MappingSource):
        yield from _iter_records(value.mappings, value.name)
        return
    if isinstance(value, GuidMapping):
        if default_source and not value.source:
            yield replace(value, source=default_source)
        else:
            yield value
        return
    if isinstance(value, Path):
        source = _load_source(value)
        yield from _iter_records(source.mappings, source.name)
        return
    if isinstance(value, tuple) and len(value) == 2 and \
            all(isinstance(part, str) for part in value):
        # A bare (old_guid, new_id) pair is accepted for convenience.
        yield GuidMapping(legacy_guid=value[0], atom_id=value[1],
                          source=default_source)
        return
    if isinstance(value, MappingPlan):
        for guid in sorted(value.mappings):
            yield value.mappings[guid]
        return
    if isinstance(value, Mapping):
        # A single record, possibly wrapped in {"mappings": ...}.
        has_legacy_key = any(key in value for key in _LEGACY_KEYS)
        has_atom_key = any(key in value for key in _ATOM_KEYS)
        if has_legacy_key and has_atom_key:
            legacy_key = next(key for key in _LEGACY_KEYS if key in value)
            legacy_value = value[legacy_key]
            if isinstance(legacy_value, (list, tuple, set)):
                # One new article can legitimately have several historical
                # guids (for example after a feed URL change).
                for legacy_guid in sorted(legacy_value):
                    record = dict(value)
                    record[legacy_key] = legacy_guid
                    yield GuidMapping.from_value(record, default_source)
                return
            yield GuidMapping.from_value(value, default_source)
            return
        if has_legacy_key or has_atom_key:
            raise MappingError(
                'mapping record needs an old guid and a new atom id')
        if 'sources' in value:
            for source in value['sources'] or ():
                if isinstance(source, Mapping):
                    name = source.get('name')
                    records = source.get('mappings', source.get('records'))
                    if name is None or records is None:
                        raise MappingError(
                            'source entry needs name and mappings')
                    yield from _iter_records(
                        MappingSource(name, records))
                else:
                    yield from _iter_records(source, default_source)
            return
        if 'mappings' in value or 'records' in value:
            records = value.get('mappings', value.get('records'))
            source = value.get('source', default_source)
            yield from _iter_records(records, source)
            return
        # A simple old-guid to new-id dictionary. A value may also be a list
        # of historical guids, or a record, for one new article.
        for legacy_guid, atom in sorted(value.items(),
                                        key=lambda item: str(item[0])):
            if isinstance(atom, Mapping):
                record = dict(atom)
                legacy_key = next(
                    (key for key in _LEGACY_KEYS if key in record), None)
                if legacy_key is None:
                    record['legacy_guid'] = legacy_guid
                yield GuidMapping.from_value(record, default_source)
            elif isinstance(atom, (list, tuple, set)):
                for new_id in atom:
                    yield GuidMapping(legacy_guid=legacy_guid, atom_id=new_id,
                                      source=default_source)
            else:
                yield GuidMapping(legacy_guid=legacy_guid, atom_id=atom,
                                  source=default_source)
        return
    if isinstance(value, (str, bytes)):
        text = value.decode('utf-8') if isinstance(value, bytes) else value
        candidate = Path(text)
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False
        if is_file:
            source = _load_source(candidate)
            yield from _iter_records(source.mappings, source.name)
            return
        if text.lstrip().startswith(('{', '[')):
            yield from _iter_records(json.loads(text), default_source)
            return
        raise MappingError('mapping data must not be a bare string')
    try:
        iterator = iter(value)
    except TypeError:
        raise MappingError('unsupported mapping data: %r' % (value,))
    for item in iterator:
        yield from _iter_records(item, default_source)


def normalize_mappings(*sources):
    '''Flatten one or more migration materials into mapping records.

    Each source may be a :class:`GuidMapping`, a dictionary, a list of
    records, a :class:`MappingSource`, or a :class:`MappingPlan`.  Records are
    returned in a deterministic order; the order of the source arguments does
    not affect the resolved result.
    '''
    records = []
    for source in sources:
        records.extend(_iter_records(source))
    return tuple(records)


def _unique_records(records):
    seen = set()
    result = []
    for record in sorted(records, key=lambda item: (
            item.legacy_guid, item.atom_id, item.link or '',
            item.source or '')):
        key = (record.legacy_guid, record.atom_id, record.link,
               record.source)
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def resolve_mappings(*sources, **kwargs):
    '''Resolve explicit historical-guid to Atom-id mappings.

    The resolver groups records by historical guid and accepts a mapping only
    when every record for that guid agrees on the new Atom id.  A group with
    two or more different Atom ids is reported as a conflict and is not
    published.  All other mappings are published normally.

    Records are never matched by ``link``.  If several historical guids
    resolve to the same Atom article, they are treated as aliases of one
    article, so :attr:`MappingPlan.article_count` counts distinct Atom ids.
    Because every grouping and representative choice is sorted, reversing the
    order of the input sources cannot change the accepted mappings or the
    article count.

    :param sources: One or more migration materials.
    :param strict: If true, raise :class:`MappingConflictError` when conflict
        groups are present. The default keeps publishing all agreed mappings.
    :returns: :class:`MappingPlan`
    '''
    strict = kwargs.pop('strict', False)
    if kwargs:
        raise TypeError('Unexpected keyword arguments: %s'
                        % ', '.join(sorted(kwargs)))
    records = _unique_records(normalize_mappings(*sources))

    by_guid = {}
    for record in records:
        by_guid.setdefault(record.legacy_guid, []).append(record)

    mappings = {}
    conflicts = []
    for legacy_guid in sorted(by_guid):
        candidates = sorted(by_guid[legacy_guid], key=GuidMapping._sort_key)
        atom_ids = {candidate.atom_id for candidate in candidates}
        if len(atom_ids) == 1:
            # All records agree. Keep a deterministic representative; the
            # accepted identity is the pair, not the link or source.
            mappings[legacy_guid] = candidates[0]
        else:
            # Report each distinct claim once per source; duplicate rows from
            # the same material must not inflate the conflict group.
            unique = []
            seen = set()
            for candidate in candidates:
                key = (candidate.atom_id, candidate.source, candidate.link)
                if key not in seen:
                    seen.add(key)
                    unique.append(candidate)
            conflicts.append(MappingConflict(
                legacy_guid=legacy_guid, candidates=tuple(unique)))

    by_atom = {}
    for legacy_guid in sorted(mappings):
        mapping = mappings[legacy_guid]
        by_atom.setdefault(mapping.atom_id, []).append(mapping)

    articles = {}
    for atom_id in sorted(by_atom):
        article_mappings = sorted(by_atom[atom_id], key=GuidMapping._sort_key)
        article = ResolvedArticle(
            atom_id=atom_id,
            legacy_guids=tuple(mapping.legacy_guid
                               for mapping in article_mappings),
            links=tuple(mapping.link for mapping in article_mappings
                        if mapping.link))
        # Keep the full candidates available for deterministic link hints.
        object.__setattr__(article, '_mapping_candidates',
                           tuple(article_mappings))
        articles[atom_id] = article

    plan = MappingPlan(mappings=mappings, conflicts=tuple(conflicts),
                       articles=articles, strict=strict)
    if strict and plan.conflicts:
        raise MappingConflictError(plan.conflicts)
    return plan


# Descriptive aliases for callers that use different terminology.
GuidMappingPlan = MappingPlan
ConflictGroup = MappingConflict
IdMapping = GuidMapping
resolve_guid_mappings = resolve_mappings
migrate_guid_mappings = resolve_mappings


def _load_source(path):
    if str(path) == '-':
        return MappingSource('stdin', _load_stream(sys.stdin, 'stdin'))
    path = Path(path)
    with path.open('r', encoding='utf-8') as stream:
        return MappingSource(path.name, _load_stream(stream, path.name))


def _load_stream(stream, name):
    text = stream.read()
    if not text.strip():
        raise MappingError('empty migration material: %s' % name)
    if name.lower().endswith('.csv'):
        import csv
        import io
        return [GuidMapping.from_csv_row(row, name)
                for row in csv.DictReader(io.StringIO(text))]
    return json.loads(text)


def main(argv=None):
    '''Print a resolved migration plan and its conflict groups as JSON.'''
    parser = argparse.ArgumentParser(
        description='Resolve legacy RSS guid to Atom id mappings.')
    parser.add_argument('sources', nargs='+',
                        help='JSON mapping files, or - for stdin')
    parser.add_argument('-o', '--output',
                        help='Write the plan to this file instead of stdout')
    parser.add_argument('--fail-on-conflict', action='store_true',
                        help='Exit with status 2 when conflicts are present')
    args = parser.parse_args(argv)

    plan = resolve_mappings(*[_load_source(path) for path in args.sources])
    text = json.dumps(plan.as_dict(), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        Path(args.output).write_text(text, encoding='utf-8')
    else:
        sys.stdout.write(text)
    return 2 if args.fail_on_conflict and plan.conflicts else 0


if __name__ == '__main__':
    raise SystemExit(main())
