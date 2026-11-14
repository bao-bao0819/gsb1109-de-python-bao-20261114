# -*- coding: utf-8 -*-
"""
Compatibility tests for running two entry extensions side by side.

The editorial department enables the media extension and an in-house
entry extension at the same time.  The partners consume the two output
formats (Atom and RSS), so the extension registration and generation
entry points have to keep working regardless of how the namespace
prefixes and the registration order are arranged.

The scenarios below assert on the parsed, round-tripped data instead of
a successful serialization alone:

* Two prefixes aliasing one namespace are equivalent.
* Elements sharing a local name but living in different namespaces
  stay readable independently.
* Adding an entry before or after loading the extensions leads to the
  same result.
* Losing a required extension field in exactly one format is reported
  with the failing format, the entry and the extension field.
"""

import unittest

from lxml import etree

from feedgen.ext.base import BaseEntryExtension, BaseExtension
from feedgen.feed import FeedGenerator
from feedgen.util import xml_elem

# The media partner extension uses the established MRSS namespace.
MEDIA_NS = 'http://search.yahoo.com/mrss/'
# The in-house editorial extension lives in a separate namespace even
# though it reuses the same local element name ``content``.
HOUSE_NS = 'http://example.com/house-editorial/1.0'

ENTRY_TITLE = 'compatibility-entry'
ENTRY_ID = 'urn:uuid:compatibility-entry'
MEDIA_URL = 'https://example.com/media/clip.mp4'
HOUSE_URL = 'https://example.com/house/note.xml'


class MediaPartnerEntryExtension(BaseEntryExtension):
    """Entry extension writing an MRSS ``content`` element."""

    def __init__(self):
        self._content_url = None

    def content(self, url=None):
        if url is not None:
            self._content_url = url
        return self._content_url

    def extend_atom(self, entry):
        return self._write(entry)

    def extend_rss(self, item):
        return self._write(item)

    def _write(self, parent):
        if self._content_url is None:
            return parent
        node = xml_elem('{%s}content' % MEDIA_NS, parent)
        node.set('url', self._content_url)
        return parent


class MediaFeedExtension(BaseExtension):
    """Advertises MRSS under the ``media`` prefix."""

    def extend_ns(self):
        return {'media': MEDIA_NS}


class PartnerMediaFeedExtension(BaseExtension):
    """Advertises the same MRSS namespace under the ``partner`` prefix."""

    def extend_ns(self):
        return {'partner': MEDIA_NS}


class HouseEntryExtension(BaseEntryExtension):
    """In-house extension whose ``content`` element is not MRSS."""

    def __init__(self):
        self._content_url = None

    def content(self, url=None):
        if url is not None:
            self._content_url = url
        return self._content_url

    def extend_atom(self, entry):
        return self._write(entry)

    def extend_rss(self, item):
        return self._write(item)

    def _write(self, parent):
        if self._content_url is None:
            return parent
        node = xml_elem('{%s}content' % HOUSE_NS, parent)
        node.set('url', self._content_url)
        return parent


class HouseFeedExtension(BaseExtension):
    """Advertises the in-house namespace under the ``house`` prefix."""

    def extend_ns(self):
        return {'house': HOUSE_NS}


class BrokenRssEntryExtension(HouseEntryExtension):
    """Silently drops the required field when an RSS item is built."""

    def extend_rss(self, item):
        return item


class BrokenRssFeedExtension(HouseFeedExtension):
    def extend_ns(self):
        return {'housebroken': HOUSE_NS}


def _new_base_feed():
    fg = FeedGenerator()
    fg.id('urn:uuid:compatibility-feed')
    fg.title('Compatibility feed')
    fg.link(href='http://example.com/feed', rel='self')
    fg.link(href='http://example.com/feed.alt', rel='alternate')
    fg.description('Compatibility test feed')
    return fg


def _add_base_entry(fg):
    entry = fg.add_entry()
    entry.id(ENTRY_ID)
    entry.title(ENTRY_TITLE)
    entry.content('entry body')
    return entry


def _content_values(root, namespace):
    """Read ``content`` URLs in a namespace aware (prefix agnostic) way."""
    return [node.get('url')
            for node in root.findall('.//{%s}content' % namespace)]


def assert_extension_data_preserved(fg, expected):
    """Round-trip both formats and fail with precise diagnostics.

    :param fg: configured :class:`FeedGenerator`
    :param expected: mapping of ``(namespace, local_name)`` to the value
        that must survive in every output format
    :raises AssertionError: naming the format, entry and extension field
        for each piece of information that did not survive
    """
    serializers = (('atom', fg.atom_str), ('rss', fg.rss_str))
    failures = []
    for fmt, serialize in serializers:
        root = etree.fromstring(serialize())
        for (namespace, local_name), value in expected.items():
            nodes = root.findall('.//{%s}%s' % (namespace, local_name))
            values = [node.get('url') for node in nodes]
            if value not in values:
                failures.append(
                    'format=%s entry=%s extension=%s field=%s '
                    'expected=%r found=%r' % (
                        fmt, ENTRY_TITLE, namespace, local_name,
                        value, values))
    if failures:
        raise AssertionError(
            'Required extension information lost:\n' + '\n'.join(failures))


class TestPrefixAliasing(unittest.TestCase):
    """Two prefixes pointing at one namespace must be equivalent."""

    def _build(self, write_accessor):
        fg = _new_base_feed()
        fg.register_extension('media', MediaFeedExtension,
                              MediaPartnerEntryExtension)
        fg.register_extension('partner', PartnerMediaFeedExtension,
                              MediaPartnerEntryExtension)
        entry = _add_base_entry(fg)
        getattr(entry, write_accessor).content(MEDIA_URL)
        return fg

    def test_alias_equivalent_when_written_through_media(self):
        self._assert_alias_equivalent('media')

    def test_alias_equivalent_when_written_through_partner(self):
        self._assert_alias_equivalent('partner')

    def _assert_alias_equivalent(self, write_accessor):
        fg = self._build(write_accessor)
        for serialize in (fg.atom_str, fg.rss_str):
            root = etree.fromstring(serialize())
            # Both prefixes are advertised and resolve to one namespace.
            self.assertEqual(root.nsmap['media'], MEDIA_NS)
            self.assertEqual(root.nsmap['partner'], MEDIA_NS)

            via_media = root.xpath(
                '//media:content/@url',
                namespaces={'media': MEDIA_NS})
            via_partner = root.xpath(
                '//partner:content/@url',
                namespaces={'partner': MEDIA_NS})
            self.assertEqual(via_media, [MEDIA_URL])
            self.assertEqual(via_partner, [MEDIA_URL])

            # Clark notation ignores the prefix entirely and still sees
            # the single underlying element.
            self.assertEqual(_content_values(root, MEDIA_NS), [MEDIA_URL])


class TestSameLocalNameDifferentNamespace(unittest.TestCase):
    """Same local name, different namespaces must remain distinguishable."""

    def _build(self):
        fg = _new_base_feed()
        fg.register_extension('media', MediaFeedExtension,
                              MediaPartnerEntryExtension)
        fg.register_extension('house', HouseFeedExtension,
                              HouseEntryExtension)
        entry = _add_base_entry(fg)
        entry.media.content(MEDIA_URL)
        entry.house.content(HOUSE_URL)
        return fg

    def test_elements_are_distinct_in_both_formats(self):
        fg = self._build()
        for serialize in (fg.atom_str, fg.rss_str):
            root = etree.fromstring(serialize())
            self.assertEqual(root.nsmap['media'], MEDIA_NS)
            self.assertEqual(root.nsmap['house'], HOUSE_NS)

            media_values = _content_values(root, MEDIA_NS)
            house_values = _content_values(root, HOUSE_NS)
            self.assertEqual(media_values, [MEDIA_URL])
            self.assertEqual(house_values, [HOUSE_URL])
            # Neither value may leak into the other namespace.
            self.assertNotIn(HOUSE_URL, media_values)
            self.assertNotIn(MEDIA_URL, house_values)

    def test_local_name_only_query_is_ambiguous(self):
        # Documents why the assertions must be namespace aware: ignoring
        # the namespace conflates the two same-named elements, while the
        # Clark notation lookup separates them again.
        fg = self._build()
        for serialize in (fg.atom_str, fg.rss_str):
            root = etree.fromstring(serialize())
            ambiguous = root.xpath(
                '//*[local-name()="content" and @url]')
            urls = sorted(node.get('url') for node in ambiguous)
            self.assertEqual(urls, sorted([MEDIA_URL, HOUSE_URL]))

            self.assertEqual(
                len(root.findall('.//{%s}content' % MEDIA_NS)), 1)
            self.assertEqual(
                len(root.findall('.//{%s}content' % HOUSE_NS)), 1)


class TestRegistrationOrder(unittest.TestCase):
    """Entry-first and extension-first registration must be equivalent."""

    @staticmethod
    def _build(entry_first):
        fg = _new_base_feed()
        if entry_first:
            entry = _add_base_entry(fg)
            fg.register_extension('media', MediaFeedExtension,
                                  MediaPartnerEntryExtension)
            fg.register_extension('house', HouseFeedExtension,
                                  HouseEntryExtension)
        else:
            fg.register_extension('media', MediaFeedExtension,
                                  MediaPartnerEntryExtension)
            fg.register_extension('house', HouseFeedExtension,
                                  HouseEntryExtension)
            entry = _add_base_entry(fg)
        entry.media.content(MEDIA_URL)
        entry.house.content(HOUSE_URL)
        return fg, entry

    def test_entry_added_before_extension(self):
        fg, entry = self._build(entry_first=True)
        # The late-registered extensions must reach the existing entry.
        self.assertTrue(hasattr(entry, 'media'))
        self.assertTrue(hasattr(entry, 'house'))
        self._assert_both_formats_complete(fg)

    def test_extension_loaded_before_entry(self):
        fg, entry = self._build(entry_first=False)
        self.assertTrue(hasattr(entry, 'media'))
        self.assertTrue(hasattr(entry, 'house'))
        self._assert_both_formats_complete(fg)

    def _assert_both_formats_complete(self, fg):
        assert_extension_data_preserved(
            fg,
            {(MEDIA_NS, 'content'): MEDIA_URL,
             (HOUSE_NS, 'content'): HOUSE_URL})


class TestSingleFormatDataLoss(unittest.TestCase):
    """A loss in one format must be reported instead of passing silently."""

    def _build(self):
        fg = _new_base_feed()
        fg.register_extension('housebroken', BrokenRssFeedExtension,
                              BrokenRssEntryExtension)
        entry = _add_base_entry(fg)
        entry.housebroken.content(HOUSE_URL)
        return fg

    def test_serialization_succeeds_but_data_check_fails(self):
        fg = self._build()
        # Serialization itself does not raise: that alone cannot prove
        # that the required information survived.
        self.assertTrue(fg.atom_str())
        self.assertTrue(fg.rss_str())

        with self.assertRaises(AssertionError) as ctx:
            assert_extension_data_preserved(
                fg, {(HOUSE_NS, 'content'): HOUSE_URL})
        message = str(ctx.exception)
        self.assertIn('format=rss', message)
        self.assertIn('entry=' + ENTRY_TITLE, message)
        self.assertIn(HOUSE_NS, message)
        self.assertIn('field=content', message)
        # The format that retained the data must not be flagged.
        self.assertNotIn('format=atom', message)

    def test_atom_keeps_the_field(self):
        fg = self._build()
        root = etree.fromstring(fg.atom_str())
        self.assertEqual(_content_values(root, HOUSE_NS), [HOUSE_URL])

    def test_rss_silently_dropped_the_field(self):
        fg = self._build()
        root = etree.fromstring(fg.rss_str())
        self.assertEqual(_content_values(root, HOUSE_NS), [])


if __name__ == '__main__':
    unittest.main()
