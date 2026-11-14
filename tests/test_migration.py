# -*- coding: utf-8 -*-

"""
Tests for migrating RSS guids to Atom ids.
"""

import os
import unittest

from feedgen.feed import FeedGenerator
from feedgen.migration import GuidMapping, MappingError, resolve_mappings

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def feed_with_entries(*entries):
    fg = FeedGenerator()
    fg.id('http://example.com/feed')
    fg.title('Migration feed')
    fg.link(href='http://example.com', rel='alternate')
    fg.description('...')
    for entry in entries:
        fe = fg.add_entry(order='append')
        fe.title(entry['title'])
        fe.content(u'...')
        if 'id' in entry:
            fe.id(entry['id'])
        if 'guid' in entry:
            fe.guid(entry['guid'], entry.get('permalink', False))
        if entry.get('link'):
            fe.link(href=entry['link'])
    return fg


class TestEntryIdentity(unittest.TestCase):

    def test_id_then_guid_keeps_both_identifiers(self):
        fg = feed_with_entries({'title': 'one', 'id': 'atom:1'})
        fe = fg.entry()[0]
        fe.guid('rss:1')
        self.assertEqual(fe.id(), 'atom:1')
        self.assertEqual(fe.guid()['guid'], 'rss:1')

    def test_guid_then_id_keeps_both_identifiers(self):
        fg = feed_with_entries({'title': 'one'})
        fe = fg.entry()[0]
        fe.guid('rss:1')
        fe.id('atom:1')
        self.assertEqual(fe.id(), 'atom:1')
        self.assertEqual(fe.guid()['guid'], 'rss:1')

    def test_explicit_remap_sets_both_at_once(self):
        fe = feed_with_entries({'title': 'one'}).entry()[0]
        fe.remap_id('atom:1', 'rss:1')
        self.assertEqual(fe.id(), 'atom:1')
        self.assertEqual(fe.guid(), {'guid': 'rss:1', 'permalink': False})

    def test_id_accepts_both_identifiers_in_one_call(self):
        fe = feed_with_entries({'title': 'one'}).entry()[0]
        fe.id('atom:1', guid='rss:1')
        self.assertEqual(fe.id(), 'atom:1')
        self.assertEqual(fe.legacy_guid(), 'rss:1')

    def test_guid_accepts_both_identifiers_in_one_call(self):
        fe = feed_with_entries({'title': 'one'}).entry()[0]
        fe.guid('rss:1', id='atom:1')
        self.assertEqual(fe.id(), 'atom:1')
        self.assertEqual(fe.legacy_guid(), 'rss:1')

    def test_combined_calls_are_order_independent(self):
        first = feed_with_entries({'title': 'one'}).entry()[0]
        first.id('atom:1', guid='rss:1')
        second = feed_with_entries({'title': 'one'}).entry()[0]
        second.guid('rss:1', id='atom:1')
        self.assertEqual((first.id(), first.legacy_guid()),
                         (second.id(), second.legacy_guid()))
        self.assertEqual(first.guid(), second.guid())

    def test_rss_and_atom_use_their_own_identifier(self):
        fg = feed_with_entries({'title': 'one', 'guid': 'rss:1'})
        fg.entry()[0].id('atom:1')
        rss = fg.rss_str()
        atom = fg.atom_str()
        self.assertIn(b'<guid isPermaLink="false">rss:1</guid>', rss)
        self.assertNotIn(b'<guid isPermaLink="false">atom:1</guid>', rss)
        self.assertIn(b'<id>atom:1</id>', atom)
        self.assertNotIn(b'<id>rss:1</id>', atom)


class TestMappingResolution(unittest.TestCase):

    def test_conflict_group_only_drops_contended_guid(self):
        plan = resolve_mappings(
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-2'},
            [
                {'legacy_guid': 'old-1', 'atom_id': 'new-9',
                 'source': 'second-export'},
            ])
        self.assertEqual([conflict.legacy_guid
                          for conflict in plan.conflict_groups], ['old-1'])
        self.assertEqual(str(plan.conflict_groups[0]),
                         'legacy guid conflict: old-1 -> new-1, old-1 -> '
                         'new-9 (second-export)')
        self.assertEqual(sorted(plan.mappings), ['old-2'])
        self.assertEqual(plan.new_ids, ('new-2',))
        self.assertEqual(plan.article_count, 1)

    def test_order_of_materials_does_not_change_article_count(self):
        first = [
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-3', 'atom_id': 'new-3'},
        ]
        second = [
            {'legacy_guid': 'old-3', 'atom_id': 'new-4'},
            {'legacy_guid': 'old-4', 'atom_id': 'new-4'},
        ]
        forward = resolve_mappings(first, second)
        reverse = resolve_mappings(second, first)
        self.assertEqual(forward.article_count, reverse.article_count)
        self.assertEqual(forward.new_ids, reverse.new_ids)
        self.assertEqual(
            [conflict.legacy_guid for conflict in forward.conflicts],
            [conflict.legacy_guid for conflict in reverse.conflicts])

    def test_reversed_record_order_is_stable(self):
        records = [
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-3', 'atom_id': 'new-3'},
            {'legacy_guid': 'old-1', 'atom_id': 'new-9'},
        ]
        forward = resolve_mappings(records)
        reverse = resolve_mappings(list(reversed(records)))
        self.assertEqual(forward.as_dict()['conflicts'],
                         reverse.as_dict()['conflicts'])
        self.assertEqual(forward.as_dict()['articles'],
                         reverse.as_dict()['articles'])

    def test_same_link_does_not_merge_distinct_guids(self):
        plan = resolve_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'new-1',
             'link': 'http://example.com/repost'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-2',
             'link': 'http://example.com/repost'},
        ])
        self.assertEqual(plan.article_count, 2)
        self.assertFalse(plan.conflicts)

    def test_same_link_does_not_create_conflict(self):
        plan = resolve_mappings(
            {'legacy_guid': 'old-1', 'atom_id': 'new-1',
             'link': 'http://example.com/repost'},
            {'legacy_guid': 'old-1', 'atom_id': 'new-1',
             'link': 'http://example.com/repost'})
        self.assertFalse(plan.conflicts)
        self.assertEqual(plan.mappings['old-1'].atom_id, 'new-1')

    def test_alias_guids_for_same_article_count_once(self):
        plan = resolve_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-1'},
        ])
        self.assertEqual(plan.mapping_count, 2)
        self.assertEqual(plan.article_count, 1)
        articles = plan.as_dict()['articles']
        self.assertEqual(articles[0]['legacy_guids'], ['old-1', 'old-2'])

    def test_simple_old_to_new_dictionary(self):
        plan = resolve_mappings({'old-1': 'new-1', 'old-2': 'new-2'})
        self.assertEqual(plan.new_ids, ('new-1', 'new-2'))

    def test_sources_are_tracked_in_conflicts(self):
        plan = resolve_mappings(
            {'sources': [
                {'name': 'first.json',
                 'mappings': [{'old_guid': 'old-1', 'new_id': 'new-1'}]},
                {'name': 'second.json',
                 'mappings': [{'old_guid': 'old-1', 'new_id': 'new-2'}]},
            ]})
        conflict = plan.conflicts[0]
        self.assertEqual(conflict.sources, ('first.json', 'second.json'))

    def test_invalid_record_is_rejected(self):
        with self.assertRaises(MappingError):
            resolve_mappings([{'legacy_guid': 'old-1'}])

    def test_explicit_mapping_object(self):
        plan = resolve_mappings(GuidMapping('old-1', 'new-1'))
        self.assertEqual(plan.mappings['old-1'].atom_id, 'new-1')

    def test_two_tuple_mapping(self):
        plan = resolve_mappings([('old-1', 'new-1'), ('old-2', 'new-2')])
        self.assertEqual(plan.new_ids, ('new-1', 'new-2'))

    def test_duplicate_identical_records_are_deduplicated(self):
        plan = resolve_mappings([{'legacy_guid': 'old-1',
                                  'atom_id': 'new-1'}] * 4)
        self.assertFalse(plan.conflicts)
        self.assertEqual(plan.mapping_count, 1)

    def test_duplicate_link_points_to_two_distinct_articles(self):
        # A repost and its original: same link, different identity. This must
        # not reduce the article count or be treated as a conflict.
        plan = resolve_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'new-1',
             'link': 'http://example.com/shared'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-2',
             'link': 'http://example.com/shared'},
        ])
        self.assertEqual(plan.article_count, 2)
        self.assertEqual(plan.mappings['old-1'].link,
                         plan.mappings['old-2'].link)

    def test_conflict_str_uses_sorted_candidates(self):
        plan = resolve_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'z', 'source': 'b'},
            {'legacy_guid': 'old-1', 'atom_id': 'a', 'source': 'a'},
        ])
        self.assertEqual(str(plan.conflicts[0]),
                         'legacy guid conflict: old-1 -> a (a), '
                         'old-1 -> z (b)')

    def test_conflict_does_not_remove_other_guid_of_same_article(self):
        plan = resolve_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-1', 'atom_id': 'new-9'},
        ])
        self.assertEqual(list(plan.mappings), ['old-2'])
        self.assertEqual(plan.new_ids, ('new-1',))

    def test_conflicts_are_deterministic_across_material_order(self):
        a = [{'legacy_guid': 'g%02d' % i, 'atom_id': 'a%02d' % i}
             for i in range(10)]
        b = [{'legacy_guid': 'g%02d' % i, 'atom_id': 'b%02d' % i}
             for i in range(0, 10, 2)]
        forward = resolve_mappings(a, b)
        reverse = resolve_mappings(b, a)
        self.assertEqual(forward.as_dict(), reverse.as_dict())

    def test_plan_reports_unmatched_articles(self):
        fg = feed_with_entries({'title': 'one', 'id': 'new-1'})
        plan = fg.apply_guid_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-2'},
        ])
        self.assertEqual(plan.unmatched, ('new-2',))


class TestFeedApplication(unittest.TestCase):

    def test_apply_preserves_old_guid_and_new_atom_id(self):
        fg = feed_with_entries({'title': 'one', 'id': 'old-1'},
                               {'title': 'two', 'id': 'old-2'})
        plan = fg.apply_guid_mappings({
            'old-1': 'new-1',
            'old-2': 'new-2',
        })
        self.assertEqual(len(plan.applied), 2)
        rss = fg.rss_str()
        atom = fg.atom_str()
        self.assertIn(b'<guid isPermaLink="false">old-1</guid>', rss)
        self.assertIn(b'<guid isPermaLink="false">old-2</guid>', rss)
        self.assertIn(b'<id>new-1</id>', atom)
        self.assertIn(b'<id>new-2</id>', atom)
        self.assertNotIn(b'<id>old-1</id>', atom)

    def test_conflicting_guid_is_not_applied_but_others_are(self):
        fg = feed_with_entries({'title': 'one', 'id': 'old-1'},
                               {'title': 'two', 'id': 'old-2'})
        plan = fg.apply_guid_mappings(
            [{'legacy_guid': 'old-1', 'atom_id': 'new-1'},
             {'legacy_guid': 'old-2', 'atom_id': 'new-2'}],
            [{'legacy_guid': 'old-1', 'atom_id': 'new-9'}])
        self.assertEqual(
            [conflict.legacy_guid for conflict in plan.conflicts], ['old-1'])
        self.assertEqual(
            [mapping.atom_id for mapping in plan.mappings.values()],
            ['new-2'])
        atom = fg.atom_str()
        self.assertIn(b'<id>old-1</id>', atom)
        self.assertIn(b'<id>new-2</id>', atom)

    def test_link_hint_does_not_merge_two_entries(self):
        fg = feed_with_entries(
            {'title': 'one', 'id': 'old-1',
             'link': 'http://example.com/repost'},
            {'title': 'two', 'id': 'old-2',
             'link': 'http://example.com/repost'})
        plan = fg.apply_guid_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'new-1',
             'link': 'http://example.com/repost'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-2',
             'link': 'http://example.com/repost'},
        ], link_hint=True)
        self.assertEqual(len(plan.applied), 2)
        self.assertEqual(fg.entry()[0].id(), 'new-1')
        self.assertEqual(fg.entry()[1].id(), 'new-2')

    def test_link_hint_matches_only_an_unidentified_entry(self):
        fg = feed_with_entries(
            {'title': 'one', 'link': 'http://example.com/one'})
        plan = fg.apply_guid_mappings(
            {'legacy_guid': 'old-9', 'atom_id': 'new-9',
             'link': 'http://example.com/one'}, link_hint=True)
        self.assertEqual(len(plan.applied), 1)
        self.assertEqual(fg.entry()[0].id(), 'new-9')
        self.assertEqual(fg.entry()[0].guid()['guid'], 'old-9')

    def test_link_hint_does_not_override_explicit_identifier(self):
        fg = feed_with_entries(
            {'title': 'one', 'id': 'unknown-id',
             'link': 'http://example.com/one'})
        plan = fg.apply_guid_mappings(
            {'legacy_guid': 'old-9', 'atom_id': 'new-9',
             'link': 'http://example.com/one'}, link_hint=True)
        self.assertEqual(plan.applied, ())
        self.assertEqual(fg.entry()[0].id(), 'unknown-id')

    def test_apply_twice_is_stable(self):
        fg = feed_with_entries({'title': 'one', 'id': 'old-1'})
        mappings = {'old-1': 'new-1'}
        fg.apply_guid_mappings(mappings)
        first = fg.atom_str()
        fg.apply_guid_mappings(mappings)
        self.assertEqual(first, fg.atom_str())

    def test_conflicted_entry_is_reported_as_rejected(self):
        fg = feed_with_entries({'title': 'one', 'id': 'old-1'})
        plan = fg.apply_guid_mappings(
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-1', 'atom_id': 'new-2'})
        self.assertEqual([item.legacy_guid for item in plan.rejected],
                         ['old-1'])
        self.assertEqual(plan.rejected[0].atom_ids, ('new-1', 'new-2'))
        self.assertEqual(fg.entry()[0].id(), 'old-1')

    def test_no_link_hint_means_links_never_match(self):
        fg = feed_with_entries(
            {'title': 'one', 'id': 'unmapped',
             'link': 'http://example.com/one'})
        plan = fg.apply_guid_mappings(
            {'legacy_guid': 'old-9', 'atom_id': 'new-9',
             'link': 'http://example.com/one'})
        self.assertEqual(plan.applied, ())
        self.assertEqual(fg.entry()[0].id(), 'unmapped')

    def test_apply_returns_article_count_from_feed_work(self):
        fg = feed_with_entries(
            {'title': 'one', 'id': 'old-1'},
            {'title': 'two', 'id': 'old-2'},
            {'title': 'three', 'id': 'old-3'})
        before = fg.apply_guid_mappings([
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-3', 'atom_id': 'new-3'},
        ])
        self.assertEqual(before.article_count, 2)
        self.assertEqual(len(before.applied), 3)
        # Reversing the materials gives the same article count.
        after = fg.apply_guid_mappings([
            {'legacy_guid': 'old-3', 'atom_id': 'new-3'},
            {'legacy_guid': 'old-2', 'atom_id': 'new-1'},
            {'legacy_guid': 'old-1', 'atom_id': 'new-1'},
        ])
        self.assertEqual(before.article_count, after.article_count)


class TestMigrationMaterials(unittest.TestCase):

    def test_two_exports_keep_agreed_articles_and_report_conflict(self):
        first = os.path.join(FIXTURES, 'first-export.json')
        second = os.path.join(FIXTURES, 'second-export.json')
        plan = resolve_mappings(first, second)
        self.assertEqual(plan.article_count, 3)
        self.assertEqual([conflict.legacy_guid
                          for conflict in plan.conflict_groups], ['legacy-2'])
        self.assertEqual(plan.conflict_groups[0].atom_ids,
                         ('https://example.com/posts/1',
                          'https://example.com/posts/conflict'))

    def test_reversing_material_order_keeps_article_count(self):
        first = os.path.join(FIXTURES, 'first-export.json')
        second = os.path.join(FIXTURES, 'second-export.json')
        forward = resolve_mappings(first, second)
        reverse = resolve_mappings(second, first)
        self.assertEqual(forward.article_count, reverse.article_count)
        self.assertEqual(forward.article_count, 3)
        self.assertEqual(forward.new_ids, reverse.new_ids)

    def test_shared_link_is_not_identity_in_materials(self):
        first = os.path.join(FIXTURES, 'first-export.json')
        plan = resolve_mappings(first)
        self.assertEqual(plan.article_count, 2)
        self.assertIn('https://example.com/shared',
                      plan.articles['https://example.com/posts/2'].links)


if __name__ == '__main__':
    unittest.main()