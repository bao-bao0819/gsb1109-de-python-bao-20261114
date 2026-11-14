# -*- coding: utf-8 -*-

"""
Tests for the ATOM/RSS parity (difference) view.
"""

import json
import unittest

from lxml import etree

from feedgen.feed import FeedGenerator
from feedgen.parity import (
    build_parity_view,
    compare_atom_rss,
    compare_feeds,
    diff_view,
    parity_view,
)


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>urn:feed:1</id>
  <title>Example feed</title>
  <subtitle>Example subtitle</subtitle>
  <updated>2020-01-01T00:00:00Z</updated>
  <link href="http://example.com/" rel="alternate"/>
  <link href="http://example.com/atom" rel="self" type="application/atom+xml"/>
  <author><name>Feed Author</name><email>feed@example.com</email></author>
  <entry>
    <id>urn:entry:1</id>
    <title>First entry</title>
    <updated>2020-01-02T00:00:00Z</updated>
    <published>2020-01-01T00:00:00Z</published>
    <author><name>Alice</name><email>alice@example.com</email></author>
    <author><name>Bob</name><email>bob@example.com</email></author>
    <author><name>Carol</name><email>carol@example.com</email></author>
    <link href="http://example.com/1" rel="alternate" type="text/html"/>
    <link href="http://example.com/1" rel="self" type="text/html"/>
    <link href="http://example.com/1" rel="related" type="text/html"/>
    <link href="http://example.com/audio-1.mp3" rel="enclosure"
          type="audio/mpeg" length="10"/>
    <link href="http://example.com/audio-2.mp3" rel="enclosure"
          type="audio/mpeg" length="20"/>
    <category term="news" scheme="http://example.com/categories"
              label="News"/>
    <category term="tech" scheme="http://example.com/categories"/>
    <summary>Short summary</summary>
    <content type="html">&lt;p&gt;Body&lt;/p&gt;</content>
    <rights>CC-BY</rights>
  </entry>
  <entry>
    <id>urn:entry:2</id>
    <title>Second entry</title>
    <updated>2020-01-03T00:00:00Z</updated>
    <content>Second body</content>
  </entry>
  <entry>
    <title>No identifier</title>
    <updated>2020-01-04T00:00:00Z</updated>
    <content>Anonymous</content>
  </entry>
</feed>
"""

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example feed</title>
    <link>http://example.com/</link>
    <description>Example subtitle</description>
    <lastBuildDate>Wed, 01 Jan 2020 00:00:00 +0000</lastBuildDate>
    <atom:link href="http://example.com/rss" rel="self"
               type="application/rss+xml"/>
    <item>
      <title>First entry</title>
      <link>http://example.com/1</link>
      <description>Short summary</description>
      <content:encoded>&lt;p&gt;Body&lt;/p&gt;</content:encoded>
      <author>alice@example.com (Alice)</author>
      <author>bob@example.com (Bob)</author>
      <author>carol@example.com (Carol)</author>
      <guid isPermaLink="false">urn:entry:1</guid>
      <category domain="http://example.com/categories">News</category>
      <category domain="http://example.com/categories">tech</category>
      <enclosure url="http://example.com/audio-2.mp3" length="20"
                 type="audio/mpeg"/>
      <pubDate>Wed, 01 Jan 2020 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Second entry</title>
      <guid isPermaLink="false">urn:entry:2</guid>
      <description>Second body</description>
      <pubDate>Fri, 03 Jan 2020 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>No identifier</title>
      <description>Anonymous</description>
    </item>
  </channel>
</rss>
"""


class TestParityView(unittest.TestCase):
    def setUp(self):
        self.report = build_parity_view(ATOM, RSS)

    def test_aliases(self):
        for function in (
            compare_atom_rss,
            compare_feeds,
            diff_view,
            parity_view,
        ):
            report = function(ATOM, RSS)
            self.assertEqual(report.summary['matched'], 2)
            self.assertEqual(report.summary['atom_count'], 3)
            self.assertEqual(report.summary['rss_count'], 3)

    def test_counts(self):
        summary = self.report.summary
        self.assertEqual(summary['atom_count'], 3)
        self.assertEqual(summary['rss_count'], 3)
        self.assertEqual(summary['matched'], 2)
        self.assertEqual(summary['atom_only'], 0)
        self.assertEqual(summary['rss_only'], 0)
        self.assertEqual(summary['unidentified_atom'], 1)
        self.assertEqual(summary['unidentified_rss'], 1)

    def test_equivalent_values(self):
        entry = self.report['urn:entry:1']
        self.assertEqual(entry['equivalent']['identifier'], 'urn:entry:1')
        self.assertEqual(entry['equivalent']['title'], 'First entry')
        self.assertEqual(
            entry['equivalent']['published'], '2020-01-01T00:00:00+00:00'
        )
        self.assertEqual(entry['equivalent']['summary'], 'Short summary')
        self.assertEqual(entry['fields']['content']['status'], 'equivalent')

    def test_links_keep_relationship_role(self):
        entry = self.report['urn:entry:1']
        links = entry['fields']['links']
        self.assertEqual(
            sorted(links['roles']),
            ['alternate', 'enclosure', 'related', 'self'],
        )
        self.assertEqual(links['roles']['alternate']['status'], 'equivalent')
        self.assertEqual(links['roles']['self']['status'], 'atom_only')
        self.assertEqual(links['roles']['related']['status'], 'atom_only')
        self.assertNotIn('links', entry['atom_only'])
        self.assertEqual(links['roles']['enclosure']['status'], 'collapsed')
        self.assertEqual(
            links['roles']['enclosure']['kept'][0]['href'],
            'http://example.com/audio-2.mp3',
        )

    def test_same_url_different_roles_are_not_merged(self):
        entry = self.report['urn:entry:1']
        alternates = entry['fields']['links']['roles']['alternate']
        self.assertEqual(len(alternates['atom']), 1)
        self.assertEqual(len(alternates['rss']), 1)
        self.assertEqual(alternates['atom'][0]['href'], 'http://example.com/1')
        self.assertEqual(alternates['rss'][0]['href'], 'http://example.com/1')

    def test_authors_in_different_order_are_not_missing(self):
        entry = self.report['urn:entry:1']
        authors = entry['fields']['authors']
        self.assertEqual(authors['status'], 'equivalent')
        self.assertFalse(authors['order_changed'])
        self.assertEqual(entry['missing_authors'], [])
        self.assertNotIn('authors', entry['atom_only'])
        self.assertFalse(entry['author_order_changed'])

    def test_only_first_author_reports_missing_names(self):
        rss = RSS.replace(
            '      <author>bob@example.com (Bob)</author>\n'
            '      <author>carol@example.com (Carol)</author>\n',
            '',
        )
        report = build_parity_view(ATOM, rss)
        entry = report['urn:entry:1']
        authors = entry['fields']['authors']
        self.assertEqual(authors['status'], 'collapsed')
        self.assertEqual(
            authors['missing_names'],
            ['Bob <bob@example.com>', 'Carol <carol@example.com>'],
        )
        self.assertEqual(entry['missing_authors'], ['Bob', 'Carol'])
        self.assertEqual(entry['missing_author_names'], ['Bob', 'Carol'])
        self.assertEqual(
            entry['missing_author_labels'],
            ['Bob <bob@example.com>', 'Carol <carol@example.com>'],
        )
        self.assertEqual(
            entry['collapsed']['authors']['kept'],
            [{'name': 'Alice', 'email': 'alice@example.com'}],
        )

    def test_reordered_authors_do_not_report_loss(self):
        rss = RSS.replace(
            '      <author>alice@example.com (Alice)</author>\n'
            '      <author>bob@example.com (Bob)</author>\n',
            '      <author>bob@example.com (Bob)</author>\n'
            '      <author>alice@example.com (Alice)</author>\n',
        )
        report = build_parity_view(ATOM, rss)
        entry = report['urn:entry:1']
        authors = entry['fields']['authors']
        self.assertEqual(authors['status'], 'equivalent')
        self.assertTrue(authors['order_changed'])
        self.assertEqual(entry['missing_authors'], [])
        self.assertTrue(entry['author_order_changed'])

    def test_unidentified_entries_are_listed_separately(self):
        unidentified = self.report.unidentified
        self.assertEqual(len(unidentified['atom']), 1)
        self.assertEqual(len(unidentified['rss']), 1)
        self.assertEqual(
            unidentified['atom'][0]['fields']['title']['atom'], 'No identifier'
        )
        self.assertEqual(
            unidentified['rss'][0]['fields']['title']['rss'], 'No identifier'
        )
        self.assertEqual(len(unidentified['all']), 2)

    def test_entry_lookup_by_identifier(self):
        self.assertIs(
            self.report['urn:entry:1'],
            self.report.by_identifier['urn:entry:1'],
        )
        self.assertEqual(self.report['urn:entry:1'].identifier, 'urn:entry:1')

    def test_unidentified_entries_can_be_paired(self):
        atom = ATOM.replace('    <id>urn:entry:1</id>\n', '')
        rss = RSS.replace(
            '      <guid isPermaLink="false">urn:entry:1</guid>\n', ''
        )
        report = build_parity_view(atom, rss)
        matched = report.unidentified['matched']
        self.assertEqual(len(matched), 2)
        self.assertEqual(matched[0]['fields']['title']['status'], 'equivalent')
        self.assertTrue(matched[0]['unidentified_pair'])

    def test_json_serializable(self):
        payload = json.loads(self.report.to_json())
        self.assertEqual(payload['summary']['matched'], 2)
        self.assertEqual(payload['feed']['kind'], 'feed')

    def test_feed_level_fields(self):
        feed = self.report.feed
        self.assertEqual(feed['equivalent']['title'], 'Example feed')
        self.assertEqual(feed['equivalent']['subtitle'], 'Example subtitle')

    def test_atom_and_rss_only_identifiers(self):
        atom = ATOM.replace('urn:entry:2', 'urn:entry:atom-only')
        rss = RSS.replace('urn:entry:2', 'urn:entry:rss-only')
        report = build_parity_view(atom, rss)
        self.assertEqual(report.atom_only, ['urn:entry:atom-only'])
        self.assertEqual(report.rss_only, ['urn:entry:rss-only'])
        self.assertEqual(report.summary['atom_only'], 1)
        self.assertEqual(report.summary['rss_only'], 1)

    def test_accepts_bytes_and_files(self):
        self.assertEqual(
            build_parity_view(
                ATOM.encode('utf-8'), RSS.encode('utf-8')
            ).summary['matched'],
            2,
        )
        self.assertEqual(
            build_parity_view(
                etree.fromstring(ATOM.encode('utf-8')),
                etree.fromstring(RSS.encode('utf-8')),
            ).summary['matched'],
            2,
        )

    def test_namespaced_atom_links_in_rss_items_keep_role(self):
        atom = ATOM
        rss = RSS.replace(
            '      <guid isPermaLink="false">urn:entry:1</guid>\n',
            '      <guid isPermaLink="false">urn:entry:1</guid>\n'
            '      <atom:link href="http://example.com/1" rel="self"'
            ' type="text/html"/>\n',
        )
        entry = build_parity_view(atom, rss)['urn:entry:1']
        self.assertEqual(
            entry['fields']['links']['roles']['self']['status'], 'equivalent'
        )

    def test_xhtml_content_matches_rss_html(self):
        atom = ATOM.replace(
            '<content type="html">&lt;p&gt;Body&lt;/p&gt;</content>',
            '<content type="xhtml">'
            '<div xmlns="http://www.w3.org/1999/xhtml">'
            '<p>Body</p></div></content>',
        )
        entry = build_parity_view(atom, RSS)['urn:entry:1']
        self.assertEqual(entry['fields']['content']['status'], 'equivalent')

    def test_summary_content_collapse_is_reported(self):
        atom = ATOM
        rss = RSS.replace(
            '      <description>Short summary</description>\n'
            '      <content:encoded>&lt;p&gt;Body&lt;/p&gt;'
            '</content:encoded>\n',
            '      <description>Short summary</description>\n',
        )
        entry = build_parity_view(atom, rss)['urn:entry:1']
        self.assertEqual(entry['fields']['summary']['status'], 'equivalent')
        self.assertEqual(entry['fields']['content']['status'], 'collapsed')
        self.assertEqual(entry['fields']['content']['kept'], 'Short summary')

    def test_text_report_is_readable(self):
        text = self.report.format()
        self.assertIn('Matched: 2', text)
        self.assertIn('[urn:entry:1]', text)
        self.assertIn('collapsed:', text)
        self.assertIn('Unidentified ATOM entries:', text)


class TestGeneratorParity(unittest.TestCase):
    def build_feed(self):
        fg = FeedGenerator()
        fg.id('urn:feed:generator')
        fg.title('Generator feed')
        fg.link(href='http://example.com/', rel='alternate')
        fg.description('Generator description')
        fg.author({'name': 'Feed Author', 'email': 'feed@example.com'})
        fg.language('en')
        fg.logo('http://example.com/logo.png')

        entry = fg.add_entry()
        entry.id('urn:entry:generator:1')
        entry.title('Generated entry')
        entry.content('Generated body')
        entry.summary('Generated summary')
        entry.author(
            [
                {'name': 'Alice', 'email': 'alice@example.com'},
                {'name': 'Bob', 'email': 'bob@example.com'},
            ]
        )
        entry.link(
            [
                {
                    'href': 'http://example.com/1',
                    'rel': 'alternate',
                    'type': 'text/html',
                },
                {
                    'href': 'http://example.com/1',
                    'rel': 'self',
                    'type': 'text/html',
                },
                {
                    'href': 'http://example.com/1',
                    'rel': 'related',
                    'type': 'text/html',
                },
                {
                    'href': 'http://example.com/a.mp3',
                    'rel': 'enclosure',
                    'type': 'audio/mpeg',
                    'length': '10',
                },
                {
                    'href': 'http://example.com/b.mp3',
                    'rel': 'enclosure',
                    'type': 'audio/mpeg',
                    'length': '20',
                },
            ]
        )
        entry.category(
            {
                'term': 'news',
                'scheme': 'http://example.com/cat',
                'label': 'News',
            }
        )
        entry.published('2020-01-01T00:00:00Z')
        entry.updated('2020-01-02T00:00:00Z')
        return fg

    def test_generator_parity(self):
        report = self.build_feed().parity()
        entry = report['urn:entry:generator:1']
        self.assertEqual(entry['equivalent']['title'], 'Generated entry')
        self.assertEqual(entry['equivalent']['authors'][0]['name'], 'Alice')
        self.assertEqual(entry['fields']['authors']['status'], 'equivalent')
        links = entry['fields']['links']
        self.assertEqual(links['roles']['alternate']['status'], 'equivalent')
        self.assertEqual(links['roles']['self']['status'], 'atom_only')
        self.assertEqual(links['roles']['related']['status'], 'atom_only')
        self.assertEqual(links['roles']['enclosure']['status'], 'collapsed')
        self.assertEqual(
            links['roles']['enclosure']['kept'][0]['href'],
            'http://example.com/b.mp3',
        )

    def test_generator_convenience_aliases(self):
        fg = self.build_feed()
        for method in (
            fg.parity_view,
            fg.diff_view,
            fg.compare_atom_rss,
            fg.parity_report,
            fg.output_diff,
            fg.atom_rss_diff,
            fg.difference_view,
        ):
            report = method()
            self.assertEqual(report.summary['matched'], 1)

    def test_entry_link_attributes_are_serialized(self):
        fg = self.build_feed()
        atom = fg.atom_str()
        root = etree.fromstring(atom)
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        links = root.findall('a:entry/a:link', ns)
        self.assertEqual(
            [link.get('rel') for link in links],
            ['alternate', 'self', 'related', 'enclosure', 'enclosure'],
        )
        self.assertEqual(links[0].get('type'), 'text/html')


if __name__ == '__main__':
    unittest.main()
