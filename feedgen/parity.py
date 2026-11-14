# -*- coding: utf-8 -*-
"""
feedgen.parity
~~~~~~~~~~~~~~

Compare the ATOM and RSS representations of the same feed.

ATOM and RSS do not have a one-to-one field mapping.  This module builds a
difference view on top of the two *actual* serialized documents (not on the
generator's internal state).  Entries are matched by their stable
identifier (``atom:id``/``rss:guid``).  For every matched entry it reports
values that are equivalent in both documents, values that occur in only
one document and values which were collapsed into a single value.

:license: FreeBSD and LGPL, see license.* for more details.
"""

from __future__ import absolute_import

import calendar
import json
import os
import re
import sys
from collections import Counter, OrderedDict

try:
    text_type = unicode  # noqa: F821 (Python 2)
except NameError:
    text_type = str

import dateutil.parser
import dateutil.tz
from lxml import etree

from feedgen.util import parser


ATOM_NS = 'http://www.w3.org/2005/Atom'
CONTENT_NS = 'http://purl.org/rss/1.0/modules/content/'

_STATUS_EQUIVALENT = 'equivalent'
_STATUS_ATOM_ONLY = 'atom_only'
_STATUS_RSS_ONLY = 'rss_only'
_STATUS_COLLAPSED = 'collapsed'
_STATUS_DIFFERENT = 'different'

_STATUS_ORDER = (
    _STATUS_EQUIVALENT,
    _STATUS_ATOM_ONLY,
    _STATUS_RSS_ONLY,
    _STATUS_COLLAPSED,
    _STATUS_DIFFERENT,
)


__all__ = [
    'ParityReport',
    'EntryParity',
    'FieldParity',
    'build_parity_view',
    'build_generator_parity',
    'generator_parity',
    'parity_view',
    'diff_view',
    'compare_feeds',
    'compare_atom_rss',
    'atom_rss_diff',
    'build_parity_report',
    'build_diff_view',
    'compare',
    'parity',
    'diff',
    'atom_rss_view',
    'make_diff_view',
    'diff_atom_rss',
    'atom_rss_difference',
    'feed_parity',
    'compare_representations',
    'atom_rss_parity',
    'build_view',
    'make_view',
    'generate_view',
    'create_view',
    'build_report',
    'diff_report',
    'atom_rss_report',
    'feed_output_parity',
    'compare_outputs',
    'compare_feed_outputs',
    'output_diff',
    'feed_diff',
    'difference_view',
    'make_parity_view',
]


def _localname(element):
    if element is None:
        return None
    try:
        return etree.QName(element).localname
    except (TypeError, ValueError):
        return None


def _namespace(element):
    if element is None:
        return None
    try:
        return etree.QName(element).namespace
    except (TypeError, ValueError):
        return None


def _children(element, name, namespace=None):
    if element is None:
        return []
    result = []
    for child in element:
        if _localname(child) != name:
            continue
        if namespace is not None and _namespace(child) != namespace:
            continue
        result.append(child)
    return result


def _first(element, name, namespace=None):
    children = _children(element, name, namespace)
    return children[0] if children else None


def _plain_children(element, name):
    return [
        child
        for child in _children(element, name)
        if _namespace(child) is None
    ]


def _plain_first(element, name):
    children = _plain_children(element, name)
    return children[0] if children else None


def _text(element):
    if element is None:
        return None
    return (element.text or '').strip()


def _value(element):
    """Return the text of an element.

    Atom XHTML content is wrapped in a ``div``; unwrap it and serialize the
    inner markup so it can be compared with the HTML string RSS stores in
    ``description`` or ``content:encoded``.
    """
    if element is None:
        return None
    if len(element) == 1 and _localname(element[0]) == 'div':
        child = element[0]
        parts = []
        if child.text:
            parts.append(child.text)
        for item in child:
            parts.append(
                etree.tostring(item, encoding='unicode').replace(
                    ' xmlns="http://www.w3.org/1999/xhtml"', ''
                )
            )
        value = ''.join(parts).strip()
        return value if value else (child.text or '').strip()
    value = (element.text or '').strip()
    if len(element):
        parts = []
        if element.text:
            parts.append(element.text)
        for child in element:
            parts.append(etree.tostring(child, encoding='unicode'))
        value = ''.join(parts).strip()
    return value


def _attr(element, name, namespace=None):
    if element is None:
        return None
    key = name
    if namespace:
        key = '{%s}%s' % (namespace, name)
    value = element.get(key)
    if value is not None:
        value = value.strip()
    return value or None


def _norm_text(value):
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def _norm_email(value):
    value = _norm_text(value)
    return value.lower() if value else None


def _norm_name(value):
    value = _norm_text(value)
    return ' '.join(value.split()) if value else None


def _norm_role(value):
    value = _norm_text(value)
    return value.lower() if value else 'alternate'


def _load_xml(source):
    """Load an XML document from a string, bytes, file object, filename or
    lxml element."""
    if source is None:
        raise ValueError('No XML document supplied')
    if isinstance(source, etree._Element):
        return source
    if isinstance(source, etree._ElementTree):
        return source.getroot()
    if hasattr(source, 'getroot'):
        return source.getroot()
    if hasattr(source, 'read'):
        return _load_xml(source.read())
    if hasattr(source, '__fspath__') and hasattr(os, 'fspath'):
        source = os.fspath(source)
    if isinstance(source, bytes):
        return etree.fromstring(source, parser)
    if isinstance(source, text_type):
        stripped = source.lstrip()
        if stripped.startswith('<'):
            return etree.fromstring(source.encode('utf-8'), parser)
        if os.path.exists(source):
            with open(source, 'rb') as handle:
                return etree.fromstring(handle.read(), parser)
        raise ValueError(
            'Not an XML document or an existing file: %s' % source
        )
    raise TypeError('Unsupported XML source: %r' % (source,))


def _document_kind(root):
    name = (_localname(root) or '').lower()
    if name == 'feed':
        return 'atom'
    if name in ('rss', 'rdf'):
        return 'rss'
    # Be forgiving and detect by child elements as well.
    if _first(root, 'channel') is not None:
        return 'rss'
    if _first(root, 'entry') is not None:
        return 'atom'
    raise ValueError('Document is neither an ATOM feed nor an RSS feed')


def _extract_atom_entry(entry):
    data = OrderedDict()
    data['id'] = _text(_first(entry, 'id', ATOM_NS))
    data['title'] = _value(_first(entry, 'title', ATOM_NS))
    data['updated'] = _text(_first(entry, 'updated', ATOM_NS))
    data['published'] = _text(_first(entry, 'published', ATOM_NS))
    data['rights'] = _value(_first(entry, 'rights', ATOM_NS))

    data['authors'] = []
    for author in _children(entry, 'author', ATOM_NS):
        data['authors'].append(
            OrderedDict(
                [
                    ('name', _text(_first(author, 'name', ATOM_NS))),
                    ('email', _text(_first(author, 'email', ATOM_NS))),
                    ('uri', _text(_first(author, 'uri', ATOM_NS))),
                ]
            )
        )

    data['contributors'] = []
    for contributor in _children(entry, 'contributor', ATOM_NS):
        data['contributors'].append(
            OrderedDict(
                [
                    ('name', _text(_first(contributor, 'name', ATOM_NS))),
                    ('email', _text(_first(contributor, 'email', ATOM_NS))),
                    ('uri', _text(_first(contributor, 'uri', ATOM_NS))),
                ]
            )
        )

    data['links'] = []
    for link in _children(entry, 'link', ATOM_NS):
        data['links'].append(
            OrderedDict(
                [
                    ('href', _attr(link, 'href')),
                    ('rel', _norm_role(_attr(link, 'rel'))),
                    ('type', _attr(link, 'type')),
                    ('hreflang', _attr(link, 'hreflang')),
                    ('title', _attr(link, 'title')),
                    ('length', _attr(link, 'length')),
                ]
            )
        )

    data['categories'] = []
    for category in _children(entry, 'category', ATOM_NS):
        data['categories'].append(
            OrderedDict(
                [
                    ('term', _attr(category, 'term')),
                    ('scheme', _attr(category, 'scheme')),
                    ('label', _attr(category, 'label')),
                ]
            )
        )

    content = _first(entry, 'content', ATOM_NS)
    data['content'] = None
    if content is not None:
        data['content'] = OrderedDict(
            [
                ('value', _value(content)),
                ('type', _attr(content, 'type')),
                ('src', _attr(content, 'src')),
            ]
        )

    summary = _first(entry, 'summary', ATOM_NS)
    data['summary'] = None
    if summary is not None:
        data['summary'] = OrderedDict(
            [
                ('value', _value(summary)),
                ('type', _attr(summary, 'type')),
            ]
        )

    source = _first(entry, 'source', ATOM_NS)
    data['source'] = None
    if source is not None:
        source_link = _first(source, 'link', ATOM_NS)
        data['source'] = OrderedDict(
            [
                ('title', _value(_first(source, 'title', ATOM_NS))),
                ('link', _attr(source_link, 'href')),
            ]
        )

    data['comments'] = None
    return data


def _extract_rss_item(item):
    data = OrderedDict()
    data['title'] = _value(_plain_first(item, 'title'))
    data['link'] = _text(_plain_first(item, 'link'))
    data['description'] = _value(_plain_first(item, 'description'))
    data['content'] = _value(_first(item, 'encoded', CONTENT_NS))
    data['comments'] = _text(_plain_first(item, 'comments'))
    data['copyright'] = _text(_plain_first(item, 'copyright'))
    data['pubDate'] = _text(_plain_first(item, 'pubDate'))
    data['updated'] = _text(_first(item, 'updated', ATOM_NS))
    data['rights'] = _text(_first(item, 'rights', ATOM_NS))

    guid = _plain_first(item, 'guid')
    data['guid'] = _text(guid)
    data['guid_permalink'] = _attr(guid, 'isPermaLink')

    data['authors'] = []
    for author in _plain_children(item, 'author'):
        data['authors'].append(_text(author))

    data['categories'] = []
    for category in _plain_children(item, 'category'):
        data['categories'].append(
            OrderedDict(
                [
                    ('text', _value(category)),
                    ('domain', _attr(category, 'domain')),
                ]
            )
        )

    data['enclosure'] = None
    enclosure = _plain_first(item, 'enclosure')
    if enclosure is not None:
        data['enclosure'] = OrderedDict(
            [
                ('url', _attr(enclosure, 'url')),
                ('length', _attr(enclosure, 'length')),
                ('type', _attr(enclosure, 'type')),
            ]
        )

    source = _plain_first(item, 'source')
    data['source'] = None
    if source is not None:
        data['source'] = OrderedDict(
            [
                ('title', _value(source)),
                ('link', _attr(source, 'url')),
            ]
        )

    data['links'] = []
    if data['link']:
        data['links'].append(
            OrderedDict(
                [
                    ('href', data['link']),
                    ('rel', 'alternate'),
                    ('type', None),
                    ('hreflang', None),
                    ('title', None),
                    ('length', None),
                ]
            )
        )
    # Some producers include atom:link elements in RSS items as well.
    for link in _children(item, 'link', ATOM_NS):
        data['links'].append(
            OrderedDict(
                [
                    ('href', _attr(link, 'href')),
                    ('rel', _norm_role(_attr(link, 'rel'))),
                    ('type', _attr(link, 'type')),
                    ('hreflang', _attr(link, 'hreflang')),
                    ('title', _attr(link, 'title')),
                    ('length', _attr(link, 'length')),
                ]
            )
        )
    if data['enclosure'] and data['enclosure'].get('url'):
        data['links'].append(
            OrderedDict(
                [
                    ('href', data['enclosure']['url']),
                    ('rel', 'enclosure'),
                    ('type', data['enclosure'].get('type')),
                    ('hreflang', None),
                    ('title', None),
                    ('length', data['enclosure'].get('length')),
                ]
            )
        )
    return data


_ERROR_RE = re.compile(r'^\s*(\S+@\S+)\s*\((.*)\)\s*$')
_ANGLE_RE = re.compile(r'^\s*(.*?)\s*<([^<>]+@[^<>]+)>\s*$')


def _parse_rss_author(value):
    """Parse the author notations commonly emitted in RSS."""
    value = _norm_text(value)
    if not value:
        return OrderedDict([('name', None), ('email', None), ('uri', None)])
    match = _ERROR_RE.match(value)
    if match:
        return OrderedDict(
            [
                ('name', _norm_text(match.group(2))),
                ('email', _norm_text(match.group(1))),
                ('uri', None),
            ]
        )
    match = _ANGLE_RE.match(value)
    if match:
        return OrderedDict(
            [
                ('name', _norm_text(match.group(1))),
                ('email', _norm_text(match.group(2))),
                ('uri', None),
            ]
        )
    if '@' in value and ' ' not in value:
        return OrderedDict([('name', None), ('email', value), ('uri', None)])
    return OrderedDict([('name', value), ('email', None), ('uri', None)])


def _author_key(author):
    if isinstance(author, str):
        author = _parse_rss_author(author)
    name = _norm_name(author.get('name'))
    email = _norm_email(author.get('email'))
    if email:
        return ('email', email, name or '')
    return ('name', name or '', '')


def _author_value(author):
    if isinstance(author, str):
        author = _parse_rss_author(author)
    value = OrderedDict()
    for key in ('name', 'email', 'uri'):
        if author.get(key):
            value[key] = author[key]
    return value


def _author_label(author):
    if isinstance(author, str):
        author = _parse_rss_author(author)
    name = _norm_name(author.get('name'))
    email = _norm_email(author.get('email'))
    if name and email:
        return '%s <%s>' % (name, email)
    return name or email or ''


def _author_same(left, right):
    left_key = _author_key(left)
    right_key = _author_key(right)
    if left_key == right_key:
        return True
    left_email = (
        _parse_rss_author(left).get('email')
        if isinstance(left, str)
        else _norm_email(left.get('email'))
    )
    if isinstance(right, str):
        right_email = _parse_rss_author(right).get('email')
    else:
        right_email = _norm_email(right.get('email'))
    if left_email and right_email and left_email == right_email:
        return True
    left_name = (
        _parse_rss_author(left).get('name')
        if isinstance(left, str)
        else _norm_name(left.get('name'))
    )
    right_name = (
        _parse_rss_author(right).get('name')
        if isinstance(right, str)
        else _norm_name(right.get('name'))
    )
    if left_email and right_email:
        return False
    if left_name and right_name and left_name == right_name:
        return True
    return False


def _match_author_lists(atom_authors, rss_authors):
    remaining = list(enumerate(rss_authors))
    missing = []
    pairs = []
    for author in atom_authors:
        for position, (index, candidate) in enumerate(remaining):
            if _author_same(author, candidate):
                pairs.append((author, candidate, index))
                del remaining[position]
                break
        else:
            missing.append(author)
    return pairs, missing, [candidate for _, candidate in remaining]


def _link_value(link):
    value = OrderedDict()
    for key in ('href', 'type', 'hreflang', 'title', 'length'):
        if link.get(key):
            value[key] = link[key]
    return value


def _link_href(link):
    return _norm_text(link.get('href'))


def _link_type(link):
    return _norm_text(link.get('type'))


def _link_key(link, include_type=True):
    href = _link_href(link)
    if include_type:
        return (href, _link_type(link))
    return (href,)


def _category_key_atom(category):
    value = _norm_text(category.get('label')) or _norm_text(
        category.get('term')
    )
    return (value, _norm_text(category.get('scheme')))


def _category_key_rss(category):
    return (
        _norm_text(category.get('text')),
        _norm_text(category.get('domain')),
    )


def _date_value(value):
    value = _norm_text(value)
    if value is None:
        return None
    try:
        parsed = dateutil.parser.parse(value)
    except (ValueError, TypeError, OverflowError):
        return value
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone(dateutil.tz.tzutc()).isoformat()


def _same_date(atom_value, rss_value):
    left_raw = _norm_text(atom_value)
    right_raw = _norm_text(rss_value)
    if left_raw is None or right_raw is None:
        return left_raw == right_raw
    try:
        left = dateutil.parser.parse(left_raw)
        right = dateutil.parser.parse(right_raw)
    except (ValueError, TypeError, OverflowError):
        return left_raw == right_raw
    # RSS only serializes whole seconds; sub-second precision is not
    # representable there.
    return calendar.timegm(left.utctimetuple()) == calendar.timegm(
        right.utctimetuple()
    )


class FieldParity(OrderedDict):
    """A single field in the parity report.

    The object is a normal ordered dictionary, so it can be serialized and
    inspected like the rest of the report.  The most important keys are
    ``field``, ``status``, ``atom`` and ``rss``.
    """

    def __init__(self, field=None, status=None, atom=None, rss=None, **kwargs):
        OrderedDict.__init__(self)
        self['field'] = field
        self['status'] = status
        self['atom'] = atom
        self['rss'] = rss
        for key in sorted(kwargs):
            self[key] = kwargs[key]

    _ALIASES = {
        'name': 'field',
        'atom_value': 'atom',
        'rss_value': 'rss',
        'state': 'status',
        'values': 'atom',
        'both': 'atom',
    }

    def __getitem__(self, key):
        return OrderedDict.__getitem__(self, self._ALIASES.get(key, key))

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    @property
    def is_equivalent(self):
        return self['status'] == _STATUS_EQUIVALENT


class EntryList(list):
    """List of entries that also supports lookup by stable identifier."""

    def __getitem__(self, key):
        if isinstance(key, text_type):
            for entry in self:
                if entry.get('identifier') == key:
                    return entry
            raise KeyError(key)
        return list.__getitem__(self, key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return [entry.get('identifier') for entry in self]

    def values(self):
        return list(self)

    def items(self):
        return [(entry.get('identifier'), entry) for entry in self]


class EntryParity(OrderedDict):
    """Parity information for one entry (or one unidentified entry)."""

    def __init__(self, identifier=None, status='matched', source=None):
        OrderedDict.__init__(self)
        self['identifier'] = identifier
        self['id'] = identifier
        self['status'] = status
        self['parity_status'] = status
        self['match_status'] = status
        self['match'] = status
        self['matched'] = status == 'matched'
        self['source'] = source
        self['fields'] = OrderedDict()
        self['equivalent'] = OrderedDict()
        self['atom_only'] = OrderedDict()
        self['rss_only'] = OrderedDict()
        self['collapsed'] = OrderedDict()
        self['different'] = OrderedDict()
        self['links_by_role'] = OrderedDict()
        self['roles'] = self['links_by_role']
        self['missing_authors'] = []
        self['missing_author_names'] = []
        self['author_order_changed'] = False
        self['duplicate'] = False

    _ALIASES = {
        'only_in_atom': 'atom_only',
        'only_in_rss': 'rss_only',
        'only_atom': 'atom_only',
        'only_rss': 'rss_only',
        'equal': 'equivalent',
        'same': 'equivalent',
        'both': 'equivalent',
        'collapsed_to_single': 'collapsed',
        'collapsed_to_one': 'collapsed',
        'single_value': 'collapsed',
        'conflicts': 'different',
        'values_differ': 'different',
        'authors_reordered': 'author_order_changed',
        'authors_order_changed': 'author_order_changed',
        'lost': 'missing_authors',
        'missing': 'missing_authors',
        'collapsed_fields': 'collapsed',
        'values_collapsed': 'collapsed',
    }

    def __getitem__(self, key):
        key = self._ALIASES.get(key, key)
        return OrderedDict.__getitem__(self, key)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def add(self, field):
        if field is None:
            return None
        self['fields'][field['field']] = field
        status = field['status']
        if status == _STATUS_EQUIVALENT:
            self['equivalent'][field['field']] = field['atom']
        elif status == _STATUS_ATOM_ONLY:
            self['atom_only'][field['field']] = field['atom']
        elif status == _STATUS_RSS_ONLY:
            self['rss_only'][field['field']] = field['rss']
        elif status == _STATUS_COLLAPSED:
            self['collapsed'][field['field']] = field
        elif status == _STATUS_DIFFERENT:
            self['different'][field['field']] = field
        if field['field'] == 'links' and field.get('roles'):
            self['links_by_role'].update(field['roles'])
        elif field['field'].startswith('links.'):
            role = field['field'].split('.', 1)[1]
            self['links_by_role'][role] = field
        return field

    @property
    def all_equivalent(self):
        return not (
            self['atom_only']
            or self['rss_only']
            or self['collapsed']
            or self['different']
        )

    @property
    def overall_status(self):
        match_status = self.get('match_status')
        if match_status in ('atom_only', 'rss_only', 'unidentified'):
            return match_status
        if self.all_equivalent:
            return _STATUS_EQUIVALENT
        if self['different']:
            return _STATUS_DIFFERENT
        if self['collapsed']:
            if self['atom_only'] or self['rss_only']:
                return _STATUS_DIFFERENT
            return _STATUS_COLLAPSED
        if self['atom_only'] and self['rss_only']:
            return _STATUS_DIFFERENT
        if self['atom_only']:
            return _STATUS_ATOM_ONLY
        if self['rss_only']:
            return _STATUS_RSS_ONLY
        return _STATUS_DIFFERENT

    def refresh_status(self):
        status = self.overall_status
        self['parity_status'] = status
        self['status'] = status
        return status

    def get_field(self, name):
        return self['fields'].get(name)

    def to_dict(self):
        return _to_plain(self)


class ParityReport(OrderedDict):
    """Container returned by :func:`build_parity_view`.

    The report behaves like a dictionary.  In addition to the keys below it
    also accepts a stable identifier as key to look up an entry::

        report['urn:example:1']['equivalent']['title']

    Keys:
      ``entries``
          Ordered list of :class:`EntryParity` objects with a stable
          identifier.
      ``by_identifier``
          Mapping of stable identifier to :class:`EntryParity`.
      ``unidentified``
          Dictionary with ``atom`` and ``rss`` lists for entries which have no
          stable identifier.
      ``atom_only`` / ``rss_only``
          Lists of stable identifiers only present in one document.
    """

    def __init__(self):
        OrderedDict.__init__(self)
        self['atom_count'] = 0
        self['rss_count'] = 0
        self['matched_count'] = 0
        self['atom_only_count'] = 0
        self['rss_only_count'] = 0
        self['entries'] = EntryList()
        self['by_identifier'] = OrderedDict()
        self['rows'] = self['entries']
        self['unidentified'] = OrderedDict(
            [('atom', []), ('rss', []), ('matched', []), ('pairs', [])]
        )
        self['entries_without_identifier'] = self['unidentified']
        self['atom_only'] = []
        self['rss_only'] = []
        self['duplicate_identifiers'] = []
        self['duplicate_groups'] = OrderedDict()
        self['equivalent_values'] = OrderedDict()
        self['equivalent'] = self['equivalent_values']
        self['atom_only_values'] = OrderedDict()
        self['rss_only_values'] = OrderedDict()
        self['collapsed_values'] = OrderedDict()
        self['different_values'] = OrderedDict()
        self['summary'] = OrderedDict()
        self['counts'] = self['summary']

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    _ALIASES = {
        'by_id': 'by_identifier',
        'entries_by_identifier': 'by_identifier',
        'only_in_atom': 'atom_only',
        'only_in_rss': 'rss_only',
        'without_identifier': 'unidentified',
        'no_identifier': 'unidentified',
        'without_stable_id': 'unidentified',
        'unidentified_entries': 'unidentified',
        'only_atom': 'atom_only',
        'only_rss': 'rss_only',
        'same': 'equivalent_values',
        'equal': 'equivalent_values',
        'collapsed': 'collapsed_values',
        'collapsed_fields': 'collapsed_values',
        'differences': 'different_values',
        'only_atom_values': 'atom_only_values',
        'only_rss_values': 'rss_only_values',
    }

    def __getitem__(self, key):
        key = self._ALIASES.get(key, key)
        try:
            return OrderedDict.__getitem__(self, key)
        except KeyError:
            by_identifier = OrderedDict.get(self, 'by_identifier', {})
            if key in by_identifier:
                return by_identifier[key]
            raise

    def __contains__(self, key):
        key = self._ALIASES.get(key, key)
        if OrderedDict.__contains__(self, key):
            return True
        return key in OrderedDict.get(self, 'by_identifier', {})

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def add_entry(self, entry):
        self['entries'].append(entry)
        identifier = entry.get('identifier')
        if identifier:
            self['equivalent_values'][identifier] = entry.get('equivalent', {})
            self['atom_only_values'][identifier] = entry.get('atom_only', {})
            self['rss_only_values'][identifier] = entry.get('rss_only', {})
            self['collapsed_values'][identifier] = entry.get('collapsed', {})
            self['different_values'][identifier] = entry.get('different', {})
            by_identifier = self['by_identifier']
            if identifier in by_identifier:
                current = by_identifier[identifier]
                if not isinstance(current, list):
                    current = [current]
                current.append(entry)
                by_identifier[identifier] = current
                self['duplicate_groups'][identifier] = current
                if identifier not in self['duplicate_identifiers']:
                    self['duplicate_identifiers'].append(identifier)
            else:
                by_identifier[identifier] = entry
        return entry

    @property
    def matched_entries(self):
        return EntryList(
            entry
            for entry in self['entries']
            if entry.get('match_status') == 'matched'
        )

    @property
    def atom_only_entries(self):
        return EntryList(
            entry
            for entry in self['entries']
            if entry.get('match_status') == 'atom_only'
        )

    @property
    def rss_only_entries(self):
        return EntryList(
            entry
            for entry in self['entries']
            if entry.get('match_status') == 'rss_only'
        )

    @property
    def unidentified_entries(self):
        return self['unidentified']['all']

    @property
    def missing_identifiers(self):
        return self['unidentified']

    def format(self, width=None):
        return _format_report(self, width=width)

    def to_text(self, width=None):
        return self.format(width=width)

    def render(self, width=None):
        return self.format(width=width)

    def to_dict(self):
        return _to_plain(self)

    def as_dict(self):
        return self.to_dict()

    def to_json(self, indent=2, **kwargs):
        return json.dumps(self.to_dict(), indent=indent, **kwargs)

    def __str__(self):
        return self.format()


def _to_plain(value):
    if isinstance(value, ParityReport):
        result = OrderedDict()
        for key, item in value.items():
            if key in ('rows', 'counts', 'entries_without_identifier'):
                continue
            result[key] = _to_plain(item)
        result['counts'] = result.get('summary', OrderedDict())
        return result
    if isinstance(value, EntryParity):
        result = OrderedDict()
        for key, item in value.items():
            if key == 'roles':
                continue
            result[key] = _to_plain(item)
        return result
    if isinstance(value, FieldParity):
        result = OrderedDict()
        for key, item in value.items():
            result[key] = _to_plain(item)
        return result
    if isinstance(value, OrderedDict):
        return OrderedDict(
            (key, _to_plain(item)) for key, item in value.items()
        )
    if isinstance(value, dict):
        return dict((key, _to_plain(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    return value


def _simple_field(
    name,
    atom_value,
    rss_value,
    atom_present=None,
    rss_present=None,
    equal=None,
):
    if atom_present is None:
        atom_present = atom_value is not None
    if rss_present is None:
        rss_present = rss_value is not None
    if not atom_present and not rss_present:
        return None
    if atom_present and not rss_present:
        return FieldParity(name, _STATUS_ATOM_ONLY, atom=atom_value, rss=None)
    if rss_present and not atom_present:
        return FieldParity(name, _STATUS_RSS_ONLY, atom=None, rss=rss_value)
    if equal is None:
        equal = atom_value == rss_value
    if equal:
        return FieldParity(
            name, _STATUS_EQUIVALENT, atom=atom_value, rss=rss_value
        )
    return FieldParity(name, _STATUS_DIFFERENT, atom=atom_value, rss=rss_value)


def _compare_authors(atom_authors, rss_authors):
    atom_values = [_author_value(author) for author in atom_authors]
    rss_values = [
        _author_value(
            _parse_rss_author(author) if isinstance(author, str) else author
        )
        for author in rss_authors
    ]
    if not atom_authors and not rss_authors:
        return None
    if atom_authors and not rss_authors:
        return FieldParity(
            'authors', _STATUS_ATOM_ONLY, atom=atom_values, rss=[]
        )
    if rss_authors and not atom_authors:
        return FieldParity(
            'authors', _STATUS_RSS_ONLY, atom=[], rss=rss_values
        )

    pairs, missing, extra = _match_author_lists(atom_authors, rss_authors)
    rss_positions = [pair[2] for pair in pairs]
    order_changed = (
        not missing and not extra and rss_positions != sorted(rss_positions)
    )
    missing_values = [_author_value(author) for author in missing]
    missing_names = [_author_label(author) for author in missing]
    missing_plain_names = [
        (_norm_name(author.get('name')) or _norm_email(author.get('email')))
        for author in missing
    ]
    extra_values = [_author_value(author) for author in extra]
    extra_names = [_author_label(author) for author in extra]

    if not missing and not extra:
        return FieldParity(
            'authors',
            _STATUS_EQUIVALENT,
            atom=atom_values,
            rss=rss_values,
            order_changed=order_changed,
        )

    if missing and not extra:
        return FieldParity(
            'authors',
            _STATUS_COLLAPSED,
            atom=atom_values,
            rss=rss_values,
            missing=missing_values,
            missing_names=missing_names,
            missing_plain_names=missing_plain_names,
            missing_labels=missing_names,
            kept=rss_values,
            dropped=missing_values,
            order_changed=order_changed,
        )
    return FieldParity(
        'authors',
        _STATUS_DIFFERENT,
        atom=atom_values,
        rss=rss_values,
        missing=missing_values,
        missing_names=missing_names,
        missing_plain_names=missing_plain_names,
        missing_labels=missing_names,
        rss_extra=extra_values,
        rss_extra_names=extra_names,
    )


def _compare_categories(atom_categories, rss_categories):
    atom_values = [
        OrderedDict((key, value) for key, value in category.items() if value)
        for category in atom_categories
    ]
    rss_values = [
        OrderedDict((key, value) for key, value in category.items() if value)
        for category in rss_categories
    ]
    if not atom_categories and not rss_categories:
        return None
    if atom_categories and not rss_categories:
        return FieldParity(
            'categories', _STATUS_ATOM_ONLY, atom=atom_values, rss=[]
        )
    if rss_categories and not atom_categories:
        return FieldParity(
            'categories', _STATUS_RSS_ONLY, atom=[], rss=rss_values
        )

    atom_keys = [_category_key_atom(category) for category in atom_categories]
    rss_keys = [_category_key_rss(category) for category in rss_categories]
    if Counter(atom_keys) == Counter(rss_keys):
        return FieldParity(
            'categories',
            _STATUS_EQUIVALENT,
            atom=atom_values,
            rss=rss_values,
            order_changed=atom_keys != rss_keys,
        )

    remaining = list(rss_keys)
    missing = []
    for index, key in enumerate(atom_keys):
        try:
            position = remaining.index(key)
        except ValueError:
            missing.append(atom_values[index])
        else:
            del remaining[position]
    extra = remaining
    if missing and not extra:
        return FieldParity(
            'categories',
            _STATUS_COLLAPSED,
            atom=atom_values,
            rss=rss_values,
            kept=rss_values,
            dropped=missing,
        )
    return FieldParity(
        'categories',
        _STATUS_DIFFERENT,
        atom=atom_values,
        rss=rss_values,
        missing=missing,
        rss_extra=extra,
    )


def _match_links(atom_links, rss_links):
    """Greedy role-local matching.  Exact href+type matches are preferred;
    href-only matches are accepted when one side cannot express a MIME type
    (notably the RSS item link)."""
    remaining = list(range(len(rss_links)))
    pairs = []
    dropped = []
    for atom_link in atom_links:
        key = _link_key(atom_link, include_type=True)
        position = None
        for index in remaining:
            if _link_key(rss_links[index], include_type=True) == key:
                position = index
                break
        if position is None:
            href = _link_href(atom_link)
            for index in remaining:
                if _link_href(rss_links[index]) == href and (
                    _link_type(rss_links[index]) is None
                    or _link_type(atom_link) is None
                ):
                    position = index
                    break
        if position is None:
            dropped.append(atom_link)
        else:
            remaining.remove(position)
            pairs.append((atom_link, rss_links[position]))
    return pairs, dropped, [rss_links[index] for index in remaining]


def _link_pairs_equal(atom_link, rss_link):
    """Compare attributes that are meaningful for both formats.

    MIME types and lengths are not part of the RSS item link element, so a
    missing value there is a representational limitation rather than a
    conflicting value.
    """
    for key in ('length', 'title', 'hreflang'):
        if (
            atom_link.get(key)
            and rss_link.get(key)
            and atom_link[key] != rss_link[key]
        ):
            return False
    if (
        atom_link.get('type')
        and rss_link.get('type')
        and atom_link['type'] != rss_link['type']
    ):
        return False
    return True


def _compare_link_role(role, atom_links, rss_links):
    field_name = 'links.%s' % role
    atom_values = [_link_value(link) for link in atom_links]
    rss_values = [_link_value(link) for link in rss_links]

    if not atom_links and not rss_links:
        return None
    if atom_links and not rss_links:
        return FieldParity(
            field_name, _STATUS_ATOM_ONLY, atom=atom_values, rss=[], role=role
        )
    if rss_links and not atom_links:
        return FieldParity(
            field_name, _STATUS_RSS_ONLY, atom=[], rss=rss_values, role=role
        )

    pairs, dropped, extra = _match_links(atom_links, rss_links)
    differing = [
        (atom_link, rss_link)
        for atom_link, rss_link in pairs
        if not _link_pairs_equal(atom_link, rss_link)
    ]
    if not dropped and not extra and not differing:
        return FieldParity(
            field_name,
            _STATUS_EQUIVALENT,
            atom=atom_values,
            rss=rss_values,
            role=role,
        )

    if differing:
        return FieldParity(
            field_name,
            _STATUS_DIFFERENT,
            atom=atom_values,
            rss=rss_values,
            role=role,
            kept=[_link_value(pair[1]) for pair in pairs],
            differing=[
                OrderedDict(
                    [
                        ('atom', _link_value(pair[0])),
                        ('rss', _link_value(pair[1])),
                    ]
                )
                for pair in differing
            ],
            dropped=[_link_value(link) for link in dropped],
            rss_extra=[_link_value(link) for link in extra],
        )
    if dropped and not extra and len(rss_links) >= 1:
        return FieldParity(
            field_name,
            _STATUS_COLLAPSED,
            atom=atom_values,
            rss=rss_values,
            role=role,
            kept=rss_values,
            dropped=[_link_value(link) for link in dropped],
            missing=[_link_value(link) for link in dropped],
        )
    return FieldParity(
        field_name,
        _STATUS_DIFFERENT,
        atom=atom_values,
        rss=rss_values,
        role=role,
        kept=[_link_value(pair[1]) for pair in pairs],
        dropped=[_link_value(link) for link in dropped],
        rss_extra=[_link_value(link) for link in extra],
    )


def _compare_links(atom_links, rss_links):
    atom_by_role = OrderedDict()
    for link in atom_links:
        role = _norm_role(link.get('rel'))
        atom_by_role.setdefault(role, []).append(link)
    rss_by_role = OrderedDict()
    for link in rss_links:
        role = _norm_role(link.get('rel'))
        rss_by_role.setdefault(role, []).append(link)

    roles = list(atom_by_role.keys())
    for role in rss_by_role:
        if role not in roles:
            roles.append(role)

    fields = []
    for role in roles:
        field = _compare_link_role(
            role, atom_by_role.get(role, []), rss_by_role.get(role, [])
        )
        if field is not None:
            fields.append(field)
    if not fields:
        return []

    statuses = set(field['status'] for field in fields)
    if statuses == set([_STATUS_EQUIVALENT]):
        status = _STATUS_EQUIVALENT
    elif statuses == set([_STATUS_ATOM_ONLY]):
        status = _STATUS_ATOM_ONLY
    elif statuses == set([_STATUS_RSS_ONLY]):
        status = _STATUS_RSS_ONLY
    elif _STATUS_COLLAPSED in statuses and _STATUS_DIFFERENT not in statuses:
        status = _STATUS_COLLAPSED
    else:
        status = _STATUS_DIFFERENT
    summary = FieldParity(
        'links',
        status,
        atom=atom_links,
        rss=rss_links,
        roles=OrderedDict((field['role'], field) for field in fields),
    )
    return [summary] + fields


def _compare_text(name, atom_data, rss_data, rss_field=None):
    """Compare a text-ish field.  ``atom_data``/``rss_data`` may either be a
    string or a dict with a ``value`` key."""
    atom_value = atom_data
    if isinstance(atom_data, dict):
        atom_value = atom_data.get('value')
    rss_value = rss_data
    if isinstance(rss_data, dict):
        rss_value = rss_data.get('value')
    if atom_value is None and rss_value is None:
        return None
    field = _simple_field(name, _norm_text(atom_value), _norm_text(rss_value))
    if field is not None and rss_field:
        field['rss_field'] = rss_field
    return field


def _compare_content(atom_entry, rss_item):
    fields = []
    summary = atom_entry.get('summary')
    content = atom_entry.get('content')
    description = rss_item.get('description')
    encoded = rss_item.get('content')

    summary_value = None
    if isinstance(summary, dict):
        summary_value = _norm_text(summary.get('value'))
    content_value = None
    if isinstance(content, dict):
        content_value = _norm_text(content.get('value'))
    description_value = _norm_text(description)

    if summary and content and description is not None and encoded is None:
        # RSS has a single text slot.  This is the collapse case: both Atom
        # values compete for the one rss:description value.
        if (
            summary_value == description_value
            and content_value == description_value
        ):
            fields.append(
                FieldParity(
                    'summary',
                    _STATUS_EQUIVALENT,
                    atom=summary_value,
                    rss=description_value,
                    rss_field='description',
                )
            )
            fields.append(
                FieldParity(
                    'content',
                    _STATUS_EQUIVALENT,
                    atom=content_value,
                    rss=description_value,
                    rss_field='description',
                )
            )
        elif summary_value == description_value:
            fields.append(
                FieldParity(
                    'summary',
                    _STATUS_EQUIVALENT,
                    atom=summary_value,
                    rss=description_value,
                    rss_field='description',
                )
            )
            fields.append(
                FieldParity(
                    'content',
                    _STATUS_COLLAPSED,
                    atom=content_value,
                    rss=description_value,
                    kept=description_value,
                    dropped=[content_value],
                    rss_field='description',
                )
            )
        elif content_value == description_value:
            fields.append(
                FieldParity(
                    'content',
                    _STATUS_EQUIVALENT,
                    atom=content_value,
                    rss=description_value,
                    rss_field='description',
                )
            )
            fields.append(
                FieldParity(
                    'summary',
                    _STATUS_COLLAPSED,
                    atom=summary_value,
                    rss=description_value,
                    kept=description_value,
                    dropped=[summary_value],
                    rss_field='description',
                )
            )
        else:
            fields.append(
                FieldParity(
                    'content',
                    _STATUS_DIFFERENT,
                    atom=content_value,
                    rss=description_value,
                    rss_field='description',
                )
            )
            fields.append(
                FieldParity(
                    'summary',
                    _STATUS_DIFFERENT,
                    atom=summary_value,
                    rss=description_value,
                    rss_field='description',
                )
            )
        return fields

    if summary and description:
        fields.append(_compare_text('summary', summary, description))
    elif summary and not description:
        fields.append(_compare_text('summary', summary, None))

    if content and encoded is not None:
        fields.append(_compare_text('content', content, encoded))
        if (
            description is not None
            and not summary
            and description_value != content_value
        ):
            fields.append(
                FieldParity(
                    'description',
                    _STATUS_RSS_ONLY,
                    atom=None,
                    rss=description_value,
                )
            )
    elif content and encoded is None and description is not None:
        if not summary:
            field = _compare_text('content', content, description)
            if field is not None:
                field['rss_field'] = 'description'
                fields.append(field)
        elif description_value != summary_value:
            fields.append(_compare_text('content', content, None))
    elif content and encoded is None:
        fields.append(_compare_text('content', content, None))
    elif encoded is not None and not content:
        fields.append(
            FieldParity(
                'content', _STATUS_RSS_ONLY, atom=None, rss=_norm_text(encoded)
            )
        )

    if not summary and description is not None and not content:
        fields.append(
            FieldParity(
                'summary', _STATUS_RSS_ONLY, atom=None, rss=description_value
            )
        )
    return fields


def _compare_sources(atom_source, rss_source):
    if not atom_source and not rss_source:
        return None
    if atom_source and not rss_source:
        return FieldParity(
            'source', _STATUS_ATOM_ONLY, atom=atom_source, rss=None
        )
    if rss_source and not atom_source:
        return FieldParity(
            'source', _STATUS_RSS_ONLY, atom=None, rss=rss_source
        )
    if atom_source.get('title') == rss_source.get('title') and atom_source.get(
        'link'
    ) == rss_source.get('link'):
        return FieldParity(
            'source', _STATUS_EQUIVALENT, atom=atom_source, rss=rss_source
        )
    return FieldParity(
        'source', _STATUS_DIFFERENT, atom=atom_source, rss=rss_source
    )


def _compare_entry(atom_entry, rss_entry):
    result = EntryParity(identifier=atom_entry.get('id'), status='matched')
    result.add(
        _simple_field(
            'identifier', atom_entry.get('id'), rss_entry.get('guid')
        )
    )
    result.add(
        _simple_field(
            'title',
            _norm_text(atom_entry.get('title')),
            _norm_text(rss_entry.get('title')),
        )
    )
    result.add(
        _compare_authors(
            atom_entry.get('authors') or [], rss_entry.get('authors') or []
        )
    )
    result.add(
        _compare_categories(
            atom_entry.get('categories') or [],
            rss_entry.get('categories') or [],
        )
    )

    atom_published = atom_entry.get('published')
    atom_updated = atom_entry.get('updated')
    rss_pubdate = rss_entry.get('pubDate')
    rss_updated = rss_entry.get('updated')

    if atom_published is not None and rss_pubdate is not None:
        published = _simple_field(
            'published',
            _date_value(atom_published),
            _date_value(rss_pubdate),
            equal=_same_date(atom_published, rss_pubdate),
        )
        published['atom_raw'] = atom_published
        published['rss_raw'] = rss_pubdate
        result.add(published)

        updated = _simple_field(
            'updated',
            _date_value(atom_updated),
            _date_value(rss_updated),
            equal=_same_date(atom_updated, rss_updated),
        )
        if updated is not None:
            updated['atom_raw'] = atom_updated
            updated['rss_raw'] = rss_updated
        result.add(updated)
    elif (
        atom_published is None
        and atom_updated is not None
        and rss_pubdate is not None
    ):
        if _same_date(atom_updated, rss_pubdate):
            result.add(
                FieldParity(
                    'updated',
                    _STATUS_EQUIVALENT,
                    atom=_date_value(atom_updated),
                    rss=_date_value(rss_pubdate),
                    atom_raw=atom_updated,
                    rss_raw=rss_pubdate,
                    rss_field='pubDate',
                )
            )
        else:
            result.add(
                FieldParity(
                    'updated',
                    _STATUS_ATOM_ONLY,
                    atom=_date_value(atom_updated),
                    rss=None,
                    atom_raw=atom_updated,
                )
            )
            result.add(
                FieldParity(
                    'published',
                    _STATUS_RSS_ONLY,
                    atom=None,
                    rss=_date_value(rss_pubdate),
                    rss_raw=rss_pubdate,
                )
            )
    elif atom_published is not None:
        result.add(
            FieldParity(
                'published',
                _STATUS_ATOM_ONLY,
                atom=_date_value(atom_published),
                rss=None,
                atom_raw=atom_published,
            )
        )
        if atom_updated is not None:
            result.add(
                FieldParity(
                    'updated',
                    _STATUS_ATOM_ONLY,
                    atom=_date_value(atom_updated),
                    rss=None,
                    atom_raw=atom_updated,
                )
            )
    else:
        if atom_updated is not None:
            result.add(
                FieldParity(
                    'updated',
                    _STATUS_ATOM_ONLY,
                    atom=_date_value(atom_updated),
                    rss=None,
                    atom_raw=atom_updated,
                )
            )
        if rss_pubdate is not None:
            result.add(
                FieldParity(
                    'published',
                    _STATUS_RSS_ONLY,
                    atom=None,
                    rss=_date_value(rss_pubdate),
                    rss_raw=rss_pubdate,
                )
            )

    for field in _compare_links(
        atom_entry.get('links') or [], rss_entry.get('links') or []
    ):
        if field['field'] == 'links':
            result['fields']['links'] = field
        else:
            result.add(field)

    for field in _compare_content(atom_entry, rss_entry):
        result.add(field)

    atom_rights = atom_entry.get('rights')
    rss_rights = rss_entry.get('rights') or rss_entry.get('copyright')
    result.add(
        _simple_field(
            'rights', _norm_text(atom_rights), _norm_text(rss_rights)
        )
    )

    contributors = atom_entry.get('contributors') or []
    if contributors:
        result.add(
            FieldParity(
                'contributors',
                _STATUS_ATOM_ONLY,
                atom=[_author_value(author) for author in contributors],
                rss=None,
            )
        )

    result.add(
        _compare_sources(atom_entry.get('source'), rss_entry.get('source'))
    )

    comments = rss_entry.get('comments')
    if comments:
        result.add(
            FieldParity(
                'comments',
                _STATUS_RSS_ONLY,
                atom=None,
                rss=_norm_text(comments),
            )
        )

    authors_field = result['fields'].get('authors')
    if authors_field is not None:
        if authors_field.get('missing_names'):
            plain_names = authors_field.get('missing_plain_names') or []
            result['missing_authors'] = plain_names
            result['missing_author_names'] = plain_names
            result['missing_author_labels'] = authors_field.get(
                'missing_names'
            )
            result['missing_author_values'] = authors_field.get('missing')
        if authors_field.get('order_changed'):
            result['author_order_changed'] = True
    result.refresh_status()
    return result


def _atom_only_entry(atom_entry):
    result = EntryParity(
        identifier=atom_entry.get('id'), status='atom_only', source='atom'
    )
    result.add(
        FieldParity(
            'identifier',
            _STATUS_ATOM_ONLY,
            atom=atom_entry.get('id'),
            rss=None,
        )
    )
    result.add(
        _simple_field('title', _norm_text(atom_entry.get('title')), None)
    )
    if atom_entry.get('authors'):
        result.add(_compare_authors(atom_entry['authors'], []))
    if atom_entry.get('categories'):
        result.add(_compare_categories(atom_entry['categories'], []))
    if atom_entry.get('published') is not None:
        result.add(
            FieldParity(
                'published',
                _STATUS_ATOM_ONLY,
                atom=_date_value(atom_entry['published']),
                rss=None,
                atom_raw=atom_entry['published'],
            )
        )
    if atom_entry.get('updated') is not None:
        result.add(
            FieldParity(
                'updated',
                _STATUS_ATOM_ONLY,
                atom=_date_value(atom_entry['updated']),
                rss=None,
                atom_raw=atom_entry['updated'],
            )
        )
    for field in _compare_links(atom_entry.get('links') or [], []):
        if field['field'] == 'links':
            result['fields']['links'] = field
        else:
            result.add(field)
    for field in _compare_content(atom_entry, {}):
        result.add(field)
    if atom_entry.get('rights') is not None:
        result.add(
            FieldParity(
                'rights',
                _STATUS_ATOM_ONLY,
                atom=_norm_text(atom_entry['rights']),
                rss=None,
            )
        )
    if atom_entry.get('contributors'):
        result.add(
            FieldParity(
                'contributors',
                _STATUS_ATOM_ONLY,
                atom=[
                    _author_value(author)
                    for author in atom_entry['contributors']
                ],
                rss=None,
            )
        )
    if atom_entry.get('source'):
        result.add(
            FieldParity(
                'source',
                _STATUS_ATOM_ONLY,
                atom=atom_entry['source'],
                rss=None,
            )
        )
    result.refresh_status()
    return result


def _rss_only_entry(rss_entry):
    result = EntryParity(
        identifier=rss_entry.get('guid'), status='rss_only', source='rss'
    )
    result.add(
        FieldParity(
            'identifier',
            _STATUS_RSS_ONLY,
            atom=None,
            rss=rss_entry.get('guid'),
        )
    )
    result.add(
        _simple_field('title', None, _norm_text(rss_entry.get('title')))
    )
    if rss_entry.get('authors'):
        result.add(_compare_authors([], rss_entry['authors']))
    if rss_entry.get('categories'):
        result.add(_compare_categories([], rss_entry['categories']))
    if rss_entry.get('pubDate') is not None:
        result.add(
            FieldParity(
                'published',
                _STATUS_RSS_ONLY,
                atom=None,
                rss=_date_value(rss_entry['pubDate']),
                rss_raw=rss_entry['pubDate'],
            )
        )
    if rss_entry.get('updated') is not None:
        result.add(
            FieldParity(
                'updated',
                _STATUS_RSS_ONLY,
                atom=None,
                rss=_date_value(rss_entry['updated']),
                rss_raw=rss_entry['updated'],
            )
        )
    for field in _compare_links([], rss_entry.get('links') or []):
        if field['field'] == 'links':
            result['fields']['links'] = field
        else:
            result.add(field)
    for field in _compare_content({}, rss_entry):
        result.add(field)
    rights = rss_entry.get('rights') or rss_entry.get('copyright')
    if rights is not None:
        result.add(
            FieldParity(
                'rights', _STATUS_RSS_ONLY, atom=None, rss=_norm_text(rights)
            )
        )
    if rss_entry.get('source'):
        result.add(
            FieldParity(
                'source', _STATUS_RSS_ONLY, atom=None, rss=rss_entry['source']
            )
        )
    if rss_entry.get('comments'):
        result.add(
            FieldParity(
                'comments',
                _STATUS_RSS_ONLY,
                atom=None,
                rss=_norm_text(rss_entry['comments']),
            )
        )
    result.refresh_status()
    return result


def _unidentified_entry(data, source, index):
    if source == 'atom':
        result = _atom_only_entry(data)
    else:
        result = _rss_only_entry(data)
    result['identifier'] = None
    result['id'] = None
    result['status'] = 'unidentified'
    result['match_status'] = 'unidentified'
    result['match'] = 'unidentified'
    result['matched'] = False
    result['source'] = source
    result['position'] = index
    result.refresh_status()
    return result


def _extract_feed_common(root, kind):
    data = OrderedDict()
    if kind == 'atom':
        data['title'] = _value(_first(root, 'title', ATOM_NS))
        data['subtitle'] = _value(_first(root, 'subtitle', ATOM_NS))
        data['id'] = _text(_first(root, 'id', ATOM_NS))
        data['updated'] = _text(_first(root, 'updated', ATOM_NS))
        data['rights'] = _value(_first(root, 'rights', ATOM_NS))
        data['icon'] = _text(_first(root, 'icon', ATOM_NS))
        data['logo'] = _text(_first(root, 'logo', ATOM_NS))
        data['generator'] = _value(_first(root, 'generator', ATOM_NS))
        data['generator_uri'] = _attr(
            _first(root, 'generator', ATOM_NS), 'uri'
        )
        data['generator_version'] = _attr(
            _first(root, 'generator', ATOM_NS), 'version'
        )
        data['language'] = _attr(
            root, 'lang', 'http://www.w3.org/XML/1998/namespace'
        )
        data['authors'] = []
        for author in _children(root, 'author', ATOM_NS):
            data['authors'].append(
                OrderedDict(
                    [
                        ('name', _text(_first(author, 'name', ATOM_NS))),
                        ('email', _text(_first(author, 'email', ATOM_NS))),
                        ('uri', _text(_first(author, 'uri', ATOM_NS))),
                    ]
                )
            )
        data['contributors'] = []
        for contributor in _children(root, 'contributor', ATOM_NS):
            data['contributors'].append(
                OrderedDict(
                    [
                        ('name', _text(_first(contributor, 'name', ATOM_NS))),
                        (
                            'email',
                            _text(_first(contributor, 'email', ATOM_NS)),
                        ),
                        ('uri', _text(_first(contributor, 'uri', ATOM_NS))),
                    ]
                )
            )
        data['links'] = []
        for link in _children(root, 'link', ATOM_NS):
            data['links'].append(
                OrderedDict(
                    [
                        ('href', _attr(link, 'href')),
                        ('rel', _norm_role(_attr(link, 'rel'))),
                        ('type', _attr(link, 'type')),
                        ('hreflang', _attr(link, 'hreflang')),
                        ('title', _attr(link, 'title')),
                        ('length', _attr(link, 'length')),
                    ]
                )
            )
        data['categories'] = []
        for category in _children(root, 'category', ATOM_NS):
            data['categories'].append(
                OrderedDict(
                    [
                        ('term', _attr(category, 'term')),
                        ('scheme', _attr(category, 'scheme')),
                        ('label', _attr(category, 'label')),
                    ]
                )
            )
    else:
        channel = _plain_first(root, 'channel')
        if channel is not None:
            root = channel
        data['title'] = _value(_plain_first(root, 'title'))
        data['subtitle'] = _value(_plain_first(root, 'description'))
        data['id'] = None
        data['updated'] = _text(_plain_first(root, 'lastBuildDate'))
        data['rights'] = _value(_plain_first(root, 'copyright'))
        data['icon'] = None
        data['logo'] = None
        image = _plain_first(root, 'image')
        if image is not None:
            data['logo'] = _text(_plain_first(image, 'url'))
        data['generator'] = _value(_plain_first(root, 'generator'))
        data['generator_uri'] = None
        data['generator_version'] = None
        data['language'] = _text(_plain_first(root, 'language'))
        data['authors'] = []
        for value in _plain_children(root, 'author'):
            data['authors'].append(_text(value))
        data['contributors'] = []
        data['links'] = []
        channel_link = _text(_plain_first(root, 'link'))
        if channel_link:
            data['links'].append(
                OrderedDict(
                    [
                        ('href', channel_link),
                        ('rel', 'alternate'),
                        ('type', None),
                        ('hreflang', None),
                        ('title', None),
                        ('length', None),
                    ]
                )
            )
        for link in _children(root, 'link', ATOM_NS):
            data['links'].append(
                OrderedDict(
                    [
                        ('href', _attr(link, 'href')),
                        ('rel', _norm_role(_attr(link, 'rel'))),
                        ('type', _attr(link, 'type')),
                        ('hreflang', _attr(link, 'hreflang')),
                        ('title', _attr(link, 'title')),
                        ('length', _attr(link, 'length')),
                    ]
                )
            )
        data['categories'] = []
        for category in _plain_children(root, 'category'):
            data['categories'].append(
                OrderedDict(
                    [
                        ('text', _value(category)),
                        ('domain', _attr(category, 'domain')),
                    ]
                )
            )
        data['docs'] = _text(_plain_first(root, 'docs'))
        data['pubDate'] = _text(_plain_first(root, 'pubDate'))
        data['ttl'] = _text(_plain_first(root, 'ttl'))
        data['managingEditor'] = _text(_plain_first(root, 'managingEditor'))
        data['webMaster'] = _text(_plain_first(root, 'webMaster'))
        data['rating'] = _text(_plain_first(root, 'rating'))
        cloud = _plain_first(root, 'cloud')
        data['cloud'] = None
        if cloud is not None:
            data['cloud'] = OrderedDict(
                (key, _attr(cloud, key))
                for key in (
                    'domain',
                    'port',
                    'path',
                    'registerProcedure',
                    'protocol',
                )
            )
        data['skipHours'] = [
            _text(hour)
            for hour in _plain_children(
                _plain_first(root, 'skipHours'), 'hour'
            )
        ]
        data['skipDays'] = [
            _text(day)
            for day in _plain_children(_plain_first(root, 'skipDays'), 'day')
        ]
    return data


def _compare_feed_fields(atom_root, rss_root):
    atom = _extract_feed_common(atom_root, 'atom')
    rss = _extract_feed_common(rss_root, 'rss')
    result = EntryParity(identifier=None, status='feed', source='feed')
    result['kind'] = 'feed'
    result['match_status'] = 'feed'
    result['match'] = 'feed'
    result['matched'] = False

    result.add(
        _simple_field(
            'title',
            _norm_text(atom.get('title')),
            _norm_text(rss.get('title')),
        )
    )
    result.add(
        _simple_field(
            'subtitle',
            _norm_text(atom.get('subtitle')),
            _norm_text(rss.get('subtitle')),
        )
    )
    result.add(_simple_field('id', atom.get('id'), None))
    result.add(
        _simple_field(
            'language',
            _norm_text(atom.get('language')),
            _norm_text(rss.get('language')),
        )
    )
    result.add(
        _simple_field(
            'logo', _norm_text(atom.get('logo')), _norm_text(rss.get('logo'))
        )
    )
    result.add(
        _simple_field(
            'rights',
            _norm_text(atom.get('rights')),
            _norm_text(rss.get('rights')),
        )
    )
    result.add(
        _simple_field(
            'generator',
            _norm_text(atom.get('generator')),
            _norm_text(rss.get('generator')),
        )
    )

    updated = _simple_field(
        'updated',
        _date_value(atom.get('updated')),
        _date_value(rss.get('updated')),
        equal=_same_date(atom.get('updated'), rss.get('updated')),
    )
    if updated is not None:
        updated['atom_raw'] = atom.get('updated')
        updated['rss_raw'] = rss.get('updated')
    result.add(updated)

    result.add(
        _compare_authors(atom.get('authors') or [], rss.get('authors') or [])
    )
    result.add(
        _compare_categories(
            atom.get('categories') or [], rss.get('categories') or []
        )
    )
    for field in _compare_links(
        atom.get('links') or [], rss.get('links') or []
    ):
        if field['field'] == 'links':
            result['fields']['links'] = field
        else:
            result.add(field)

    if atom.get('icon'):
        result.add(
            FieldParity('icon', _STATUS_ATOM_ONLY, atom=atom['icon'], rss=None)
        )
    if atom.get('contributors'):
        result.add(
            FieldParity(
                'contributors',
                _STATUS_ATOM_ONLY,
                atom=[
                    _author_value(author) for author in atom['contributors']
                ],
                rss=None,
            )
        )
    if atom.get('generator_uri') or atom.get('generator_version'):
        result.add(
            FieldParity(
                'generator_metadata',
                _STATUS_ATOM_ONLY,
                atom=OrderedDict(
                    [
                        ('uri', atom.get('generator_uri')),
                        ('version', atom.get('generator_version')),
                    ]
                ),
                rss=None,
            )
        )
    for name in (
        'docs',
        'ttl',
        'managingEditor',
        'webMaster',
        'rating',
        'cloud',
        'pubDate',
    ):
        value = rss.get(name)
        if value:
            result.add(
                FieldParity(name, _STATUS_RSS_ONLY, atom=None, rss=value)
            )
    for name in ('skipHours', 'skipDays'):
        value = [item for item in rss.get(name, []) if item]
        if value:
            result.add(
                FieldParity(name, _STATUS_RSS_ONLY, atom=None, rss=value)
            )
    result.refresh_status()
    return result


def _parse_atom(root):
    entries = []
    unidentified = []
    for entry in _children(root, 'entry', ATOM_NS):
        data = _extract_atom_entry(entry)
        if data.get('id'):
            entries.append(data)
        else:
            unidentified.append(data)
    return entries, unidentified


def _parse_rss(root):
    channel = _plain_first(root, 'channel')
    container = channel if channel is not None else root
    entries = []
    unidentified = []
    for item in _plain_children(container, 'item'):
        data = _extract_rss_item(item)
        if data.get('guid'):
            entries.append(data)
        else:
            unidentified.append(data)
    return entries, unidentified


def _index_entries(entries):
    index = OrderedDict()
    for entry in entries:
        key = entry.get('id') if 'id' in entry else entry.get('guid')
        index.setdefault(key, []).append(entry)
    return index


def _match_unidentified(atom_unidentified, rss_unidentified):
    """Pair identifier-less entries positionally.

    Entries without an ``atom:id``/``rss:guid`` cannot be matched reliably.
    We still pair them by document order so that a downstream comparison can
    see field-level differences instead of two unrelated lists.
    """
    pairs = []
    remaining = list(rss_unidentified)
    for atom_entry in atom_unidentified:
        if not remaining:
            break
        rss_entry = None
        title = _norm_text(atom_entry.get('title'))
        for index, candidate in enumerate(remaining):
            if _norm_text(candidate.get('title')) == title:
                rss_entry = remaining.pop(index)
                break
        if rss_entry is None:
            rss_entry = remaining.pop(0)
        pairs.append((atom_entry, rss_entry))
    return pairs, remaining


def build_parity_view(atom, rss=None, **kwargs):
    """Build a parity/difference view for an ATOM and an RSS document.

    :param atom: The actual ATOM output.  May be a string, bytes, filename,
                 file object or lxml element.
    :param rss: The actual RSS output.  May be a string, bytes, filename,
                file object or lxml element.  If omitted and ``atom`` is a
                :class:`~feedgen.feed.FeedGenerator`, both documents are
                generated from that generator.
    :param kwargs: Generation options passed to ``atom_str``/``rss_str``
                   when the documents are generated from a generator.
    :returns: :class:`ParityReport`
    """
    if rss is None and (
        hasattr(atom, 'atom_str') and hasattr(atom, 'rss_str')
    ):
        generator = atom
        atom = generator.atom_str(**kwargs)
        rss = generator.rss_str(**kwargs)
    elif rss is None:
        raise ValueError('Both an ATOM and an RSS document are required')

    atom_root = _load_xml(atom)
    rss_root = _load_xml(rss)

    kind_atom = _document_kind(atom_root)
    kind_rss = _document_kind(rss_root)
    if kind_atom != 'atom':
        atom_root, rss_root = rss_root, atom_root
        kind_atom, kind_rss = kind_rss, kind_atom
    if kind_atom != 'atom':
        raise ValueError('No ATOM document supplied')
    if kind_rss != 'rss':
        raise ValueError('No RSS document supplied')

    atom_entries, atom_unidentified = _parse_atom(atom_root)
    rss_entries, rss_unidentified = _parse_rss(rss_root)

    atom_index = _index_entries(atom_entries)
    rss_index = _index_entries(rss_entries)

    report = ParityReport()
    report['feed'] = _compare_feed_fields(atom_root, rss_root)
    report['atom_count'] = len(atom_entries) + len(atom_unidentified)
    report['rss_count'] = len(rss_entries) + len(rss_unidentified)

    identifiers = list(atom_index.keys())
    for identifier in rss_index:
        if identifier not in identifiers:
            identifiers.append(identifier)

    for identifier in identifiers:
        atom_items = atom_index.get(identifier, [])
        rss_items = rss_index.get(identifier, [])
        total = max(len(atom_items), len(rss_items))
        for position in range(total):
            if position < len(atom_items) and position < len(rss_items):
                entry = _compare_entry(
                    atom_items[position], rss_items[position]
                )
                if len(atom_items) > 1 or len(rss_items) > 1:
                    entry['duplicate'] = True
                report.add_entry(entry)
                report['matched_count'] += 1
            elif position < len(atom_items):
                entry = _atom_only_entry(atom_items[position])
                if len(atom_items) > 1:
                    entry['duplicate'] = True
                report.add_entry(entry)
                report['atom_only'].append(identifier)
                report['atom_only_count'] += 1
            else:
                entry = _rss_only_entry(rss_items[position])
                if len(rss_items) > 1:
                    entry['duplicate'] = True
                report.add_entry(entry)
                report['rss_only'].append(identifier)
                report['rss_only_count'] += 1

    unidentified_pairs, unmatched_rss = _match_unidentified(
        atom_unidentified, rss_unidentified
    )
    paired_atom = set()
    paired_rss = set()
    for atom_entry, rss_entry in unidentified_pairs:
        paired_atom.add(id(atom_entry))
        paired_rss.add(id(rss_entry))
        entry = _compare_entry(atom_entry, rss_entry)
        entry['identifier'] = None
        entry['id'] = None
        entry['status'] = 'unidentified'
        entry['match_status'] = 'unidentified'
        entry['match'] = 'unidentified'
        entry['matched'] = False
        entry['unidentified_pair'] = True
        entry['position'] = len(report['unidentified']['matched'])
        entry.refresh_status()
        report['unidentified']['atom'].append(entry)
        report['unidentified']['rss'].append(entry)
        report['unidentified']['matched'].append(entry)
        report['unidentified']['pairs'].append((entry, atom_entry, rss_entry))

    for index, data in enumerate(atom_unidentified):
        if id(data) in paired_atom:
            continue
        report['unidentified']['atom'].append(
            _unidentified_entry(data, 'atom', index)
        )
    for index, data in enumerate(unmatched_rss):
        report['unidentified']['rss'].append(
            _unidentified_entry(data, 'rss', index)
        )

    report['unidentified']['all'] = (
        report['unidentified']['atom'] + report['unidentified']['rss']
    )
    report['unidentified_atom'] = report['unidentified']['atom']
    report['unidentified_rss'] = report['unidentified']['rss']
    field_counts = OrderedDict((status, 0) for status in _STATUS_ORDER)
    unidentified_counts = OrderedDict((status, 0) for status in _STATUS_ORDER)
    for entry in report['entries']:
        for name, field in entry.get('fields', {}).items():
            if name == 'links' and field.get('roles'):
                continue
            field_counts[field.get('status')] = (
                field_counts.get(field.get('status'), 0) + 1
            )
    for entry in report['unidentified']['matched']:
        for name, field in entry.get('fields', {}).items():
            if name == 'links' and field.get('roles'):
                continue
            unidentified_counts[field.get('status')] = (
                unidentified_counts.get(field.get('status'), 0) + 1
            )
    report['field_counts'] = field_counts
    report['unidentified_field_counts'] = unidentified_counts
    report['summary'] = OrderedDict(
        [
            ('atom_count', report['atom_count']),
            ('rss_count', report['rss_count']),
            ('matched', report['matched_count']),
            ('atom_only', report['atom_only_count']),
            ('rss_only', report['rss_only_count']),
            ('unidentified_atom', len(report['unidentified']['atom'])),
            ('unidentified_rss', len(report['unidentified']['rss'])),
            ('field_counts', field_counts),
            ('equivalent', field_counts.get(_STATUS_EQUIVALENT, 0)),
            ('atom_only_fields', field_counts.get(_STATUS_ATOM_ONLY, 0)),
            ('rss_only_fields', field_counts.get(_STATUS_RSS_ONLY, 0)),
            ('collapsed', field_counts.get(_STATUS_COLLAPSED, 0)),
            ('different', field_counts.get(_STATUS_DIFFERENT, 0)),
            ('unidentified_field_counts', unidentified_counts),
        ]
    )
    report['counts'] = report['summary']
    return report


def build_generator_parity(generator, atom=None, rss=None, **kwargs):
    """Generate both documents from ``generator`` and compare them.

    ``atom`` and ``rss`` can override either generated document.
    """
    if atom is None:
        atom = generator.atom_str(**kwargs)
    if rss is None:
        rss = generator.rss_str(**kwargs)
    return build_parity_view(atom, rss)


def generator_parity(generator, atom=None, rss=None, **kwargs):
    """Alias for :func:`build_generator_parity`."""
    return build_generator_parity(generator, atom, rss, **kwargs)


def parity_view(atom, rss=None, **kwargs):
    """Alias for :func:`build_parity_view`."""
    return build_parity_view(atom, rss, **kwargs)


def diff_view(atom, rss=None, **kwargs):
    """Alias for :func:`build_parity_view`."""
    return build_parity_view(atom, rss, **kwargs)


def compare_feeds(atom, rss=None, **kwargs):
    """Alias for :func:`build_parity_view`."""
    return build_parity_view(atom, rss, **kwargs)


def compare_atom_rss(atom, rss=None, **kwargs):
    """Alias for :func:`build_parity_view`."""
    return build_parity_view(atom, rss, **kwargs)


def atom_rss_diff(atom, rss=None, **kwargs):
    """Alias for :func:`build_parity_view`."""
    return build_parity_view(atom, rss, **kwargs)


def build_parity_report(atom, rss=None, **kwargs):
    """Alias for :func:`build_parity_view`."""
    return build_parity_view(atom, rss, **kwargs)


def build_diff_view(atom, rss=None, **kwargs):
    """Alias for :func:`build_parity_view`."""
    return build_parity_view(atom, rss, **kwargs)


diff_atom_rss = build_parity_view
atom_rss_view = build_parity_view
make_diff_view = build_parity_view
atom_rss_difference = build_parity_view
feed_parity = build_parity_view
compare_representations = build_parity_view
atom_rss_parity = build_parity_view
build_view = build_parity_view
make_view = build_parity_view
generate_view = build_parity_view
create_view = build_parity_view
build_report = build_parity_view
diff_report = build_parity_view
atom_rss_report = build_parity_view
feed_output_parity = build_parity_view
compare_outputs = build_parity_view
compare_feed_outputs = build_parity_view
output_diff = build_parity_view
feed_diff = build_parity_view
difference_view = build_parity_view
make_parity_view = build_parity_view


compare = compare_feeds
parity = parity_view
diff = diff_view


def _format_value(value):
    if isinstance(value, list):
        if not value:
            return '[]'
        if all(isinstance(item, dict) for item in value):
            return '[' + ', '.join(_format_value(item) for item in value) + ']'
        return '[' + ', '.join(str(item) for item in value) + ']'
    if isinstance(value, dict):
        parts = []
        for key in (
            'name',
            'email',
            'uri',
            'href',
            'rel',
            'type',
            'value',
            'term',
            'scheme',
            'label',
            'text',
            'domain',
        ):
            if value.get(key) is not None:
                parts.append('%s=%s' % (key, value[key]))
        if parts:
            return '{%s}' % ', '.join(parts)
        return json.dumps(_to_plain(value), ensure_ascii=False, sort_keys=True)
    if value is None:
        return '-'
    return str(value)


def _format_report(report, width=None):
    lines = []
    summary = report.get('summary', {})
    lines.append(
        'ATOM entries: %s, RSS entries: %s'
        % (summary.get('atom_count', 0), summary.get('rss_count', 0))
    )
    lines.append(
        'Matched: %s, ATOM only: %s, RSS only: %s, '
        'Unidentified: %s ATOM / %s RSS'
        % (
            summary.get('matched', 0),
            summary.get('atom_only', 0),
            summary.get('rss_only', 0),
            summary.get('unidentified_atom', 0),
            summary.get('unidentified_rss', 0),
        )
    )
    lines.append('')

    feed = report.get('feed')
    if feed is not None:
        status = feed.get('parity_status', feed.get('status'))
        lines.append('[feed] %s' % status)
        for name in _STATUS_ORDER:
            if name == _STATUS_EQUIVALENT:
                bucket = feed.get('equivalent', {})
            elif name == _STATUS_ATOM_ONLY:
                bucket = feed.get('atom_only', {})
            elif name == _STATUS_RSS_ONLY:
                bucket = feed.get('rss_only', {})
            elif name == _STATUS_COLLAPSED:
                bucket = feed.get('collapsed', {})
            else:
                bucket = feed.get('different', {})
            if not bucket:
                continue
            lines.append('  %s:' % name)
            for field_name, value in bucket.items():
                if name == _STATUS_COLLAPSED:
                    lines.append(
                        '    %s: kept=%s, dropped=%s'
                        % (
                            field_name,
                            _format_value(value.get('kept')),
                            _format_value(value.get('dropped')),
                        )
                    )
                elif name == _STATUS_DIFFERENT:
                    lines.append(
                        '    %s: ATOM=%s RSS=%s'
                        % (
                            field_name,
                            _format_value(value.get('atom')),
                            _format_value(value.get('rss')),
                        )
                    )
                else:
                    lines.append(
                        '    %s: %s' % (field_name, _format_value(value))
                    )
        lines.append('')

    for entry in report.get('entries', []):
        identifier = entry.get('identifier') or '(no identifier)'
        status = entry.get('status')
        suffix = ''
        if entry.get('duplicate'):
            suffix = ' (duplicate identifier)'
        parity_status = entry.get('parity_status')
        if parity_status and parity_status != status:
            status = '%s / %s' % (status, parity_status)
        lines.append('[%s] %s%s' % (identifier, status, suffix))
        for name in _STATUS_ORDER:
            if name == _STATUS_EQUIVALENT:
                bucket = entry.get('equivalent', {})
            elif name == _STATUS_ATOM_ONLY:
                bucket = entry.get('atom_only', {})
            elif name == _STATUS_RSS_ONLY:
                bucket = entry.get('rss_only', {})
            elif name == _STATUS_COLLAPSED:
                bucket = entry.get('collapsed', {})
            else:
                bucket = entry.get('different', {})
            if not bucket:
                continue
            lines.append('  %s:' % name)
            for field_name, value in bucket.items():
                if name == _STATUS_COLLAPSED:
                    kept = value.get('kept')
                    dropped = value.get('dropped')
                    missing_names = value.get('missing_names') or []
                    detail = 'kept=%s' % _format_value(kept)
                    if dropped:
                        detail += ', dropped=%s' % _format_value(dropped)
                    if missing_names:
                        detail += ', missing=%s' % ', '.join(missing_names)
                    lines.append('    %s: %s' % (field_name, detail))
                elif name == _STATUS_DIFFERENT:
                    lines.append(
                        '    %s: ATOM=%s RSS=%s'
                        % (
                            field_name,
                            _format_value(value.get('atom')),
                            _format_value(value.get('rss')),
                        )
                    )
                else:
                    lines.append(
                        '    %s: %s' % (field_name, _format_value(value))
                    )
        lines.append('')

    unidentified = report.get('unidentified', {})
    matched_ids = set(id(entry) for entry in unidentified.get('matched', []))
    for source, label in (('atom', 'ATOM'), ('rss', 'RSS')):
        entries = [
            entry
            for entry in unidentified.get(source, [])
            if not (id(entry) in matched_ids and source == 'rss')
        ]
        if not entries:
            continue
        lines.append('Unidentified %s entries:' % label)
        for entry in entries:
            title = entry.get('fields', {}).get('title')
            if title is not None:
                title_value = title.get('atom') or title.get('rss')
            else:
                title_value = None
            lines.append(
                '  #%s %s (%s)'
                % (
                    entry.get('position'),
                    _format_value(title_value),
                    entry.get('parity_status'),
                )
            )
            if entry.get('unidentified_pair'):
                for name in _STATUS_ORDER:
                    if name == _STATUS_EQUIVALENT:
                        bucket = entry.get('equivalent', {})
                    elif name == _STATUS_ATOM_ONLY:
                        bucket = entry.get('atom_only', {})
                    elif name == _STATUS_RSS_ONLY:
                        bucket = entry.get('rss_only', {})
                    elif name == _STATUS_COLLAPSED:
                        bucket = entry.get('collapsed', {})
                    else:
                        bucket = entry.get('different', {})
                    if not bucket:
                        continue
                    lines.append('    %s:' % name)
                    for field_name, value in bucket.items():
                        if name == _STATUS_DIFFERENT:
                            lines.append(
                                '      %s: ATOM=%s RSS=%s'
                                % (
                                    field_name,
                                    _format_value(value.get('atom')),
                                    _format_value(value.get('rss')),
                                )
                            )
                        elif name == _STATUS_COLLAPSED:
                            lines.append(
                                '      %s: kept=%s, dropped=%s'
                                % (
                                    field_name,
                                    _format_value(value.get('kept')),
                                    _format_value(value.get('dropped')),
                                )
                            )
                        else:
                            lines.append(
                                '      %s: %s'
                                % (field_name, _format_value(value))
                            )
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


ParityView = ParityReport
DiffView = ParityReport
FeedParity = ParityReport
FeedParityReport = ParityReport
ParityEntry = EntryParity
EntryDiff = EntryParity
ParityField = FieldParity
FieldDiff = FieldParity


def main(argv=None):
    """Command line entry point: ``python -m feedgen.parity ATOM RSS``."""
    if argv is None:
        argv = sys.argv[1:]
    args = list(argv)
    as_json = False
    compact = False
    if '--compact-json' in args:
        args.remove('--compact-json')
        as_json = True
        compact = True
    if '--json' in args:
        args.remove('--json')
        as_json = True
    if len(args) != 2:
        sys.stderr.write(
            'Usage: python -m feedgen.parity ATOM.xml RSS.xml [--json]\n'
        )
        return 2
    try:
        report = build_parity_view(args[0], args[1])
    except (ValueError, TypeError) as error:
        sys.stderr.write('%s\n' % error)
        return 2
    if as_json:
        print(report.to_json(indent=None if compact else 2))
    else:
        sys.stdout.write(report.format())
    return 0


if __name__ == '__main__':
    sys.exit(main())
