# -*- coding: utf-8 -*-

'''
Tests for the repost provenance index (feedgen.provenance).
'''

import json
import unittest

from feedgen.feed import FeedGenerator
from feedgen.provenance import (ArticleRecord, DECLARED, EXTERNAL,
                                MUTUAL_REPOST, CYCLE, ProvenanceIndex,
                                RepostStatement)


def make_index(records, statements=None):
    index = ProvenanceIndex()
    for record in records:
        index.add_record(record)
    for statement in statements or []:
        index.add_repost(statement)
    return index


class TestProvenanceIndex(unittest.TestCase):

    def test_two_hop_chain_keeps_each_manuscripts_authors(self):
        index = make_index([
            ArticleRecord('O', title='Origin', authors=['Origin Author']),
            ArticleRecord('M', title='Middle', authors=['Editor M'],
                          source='O'),
            ArticleRecord('D', title='Reposted twice',
                          authors=['Reporter D'], source='M'),
        ])
        result = index.index_for('D')

        # The manuscript keeps its own declared authors.
        self.assertEqual(result['declared_authors'], ['Reporter D'])

        direct = result['direct_contributors']
        self.assertEqual([c['article_id'] for c in direct], ['M'])
        self.assertEqual(direct[0]['relationship'], DECLARED)
        self.assertEqual(direct[0]['authors'], ['Editor M'])
        self.assertTrue(direct[0]['resolved'])

        # Both intermediate statements appear in the chain, nearest first.
        self.assertEqual([(hop['from'], hop['to']) for hop in result['chain']],
                         [('M', 'D'), ('O', 'M')])

        self.assertEqual(len(result['origin_candidates']), 1)
        origin = result['origin_candidates'][0]
        self.assertEqual(origin['article_id'], 'O')
        self.assertEqual(origin['authors'], ['Origin Author'])
        self.assertTrue(origin['resolved'])
        self.assertEqual(origin['path'], ['D', 'M', 'O'])
        self.assertIsNone(result['indeterminate'])

    def test_external_repost_statement_extends_the_chain(self):
        index = make_index([
            ArticleRecord('O', authors=['Origin Author']),
            ArticleRecord('M', authors=['Editor M']),
            ArticleRecord('D', authors=['Reporter D']),
        ], [
            RepostStatement('M', 'O', declared_by='aggregator'),
            RepostStatement('D', 'M', declared_by='aggregator'),
        ])
        result = index.index_for('D')
        self.assertEqual(result['direct_contributors'][0]['relationship'],
                         EXTERNAL)
        self.assertEqual(
                result['direct_contributors'][0]['declared_by'],
                ['aggregator'])
        self.assertEqual(result['origin_candidates'][0]['article_id'], 'O')

    def test_unresolved_source_is_flagged_not_invented(self):
        index = make_index([
            ArticleRecord('D', authors=['Reporter D'],
                          source='unknown://O'),
        ])
        result = index.index_for('D')
        origin = result['origin_candidates'][0]
        self.assertEqual(origin['article_id'], 'unknown://O')
        self.assertFalse(origin['resolved'])
        self.assertFalse(result['direct_contributors'][0]['resolved'])

    def test_same_title_manuscripts_are_not_connected(self):
        index = make_index([
            ArticleRecord('X', title='Same Headline', authors=['A']),
            ArticleRecord('Y', title='Same Headline', authors=['B']),
        ])
        result = index.index_for('X')
        origins = [o['article_id']
                   for o in result['origin_candidates']]
        self.assertEqual(origins, ['X'])
        self.assertEqual(result['direct_contributors'], [])

    def test_fork_reports_multiple_origin_candidates(self):
        index = make_index([
            ArticleRecord('P', title='Wire P', authors=['P Author']),
            ArticleRecord('Q', title='Wire Q', authors=['Q Author']),
            ArticleRecord('D', title='Forked', authors=['D Author'],
                          source='P'),
        ], [
            RepostStatement('D', 'Q', declared_by='aggregator'),
        ])
        result = index.index_for('D')
        self.assertEqual(
                sorted(c['article_id']
                       for c in result['direct_contributors']),
                ['P', 'Q'])
        self.assertEqual(
                sorted(o['article_id'] for o in result['origin_candidates']),
                ['P', 'Q'])
        authors = {o['article_id']: o['authors']
                   for o in result['origin_candidates']}
        self.assertEqual(authors['P'], ['P Author'])
        self.assertEqual(authors['Q'], ['Q Author'])
        self.assertIsNone(result['indeterminate'])

    def test_mutual_repost_marks_indeterminate_segment(self):
        index = make_index([
            ArticleRecord('A', title='Site A', authors=['A Author'],
                          source='B'),
            ArticleRecord('B', title='Site B', authors=['B Author'],
                          source='A'),
        ])
        result = index.index_for('A')
        self.assertEqual(result['origin_candidates'], [])
        ambiguity = result['indeterminate']
        self.assertEqual(ambiguity['reason'], MUTUAL_REPOST)
        self.assertEqual(ambiguity['segment'], ['A', 'B', 'A'])
        self.assertEqual(ambiguity['from_article_id'], 'A')

    def test_mutual_repost_seen_from_downstream(self):
        index = make_index([
            ArticleRecord('A', source='B'),
            ArticleRecord('B', source='A'),
            ArticleRecord('C'),
        ], [RepostStatement('C', 'A', declared_by='aggregator')])
        result = index.index_for('C')
        ambiguity = result['indeterminate']
        self.assertEqual(ambiguity['reason'], MUTUAL_REPOST)
        self.assertEqual(ambiguity['segment'], ['A', 'B', 'A'])

    def test_longer_cycle_is_reported_as_cycle(self):
        index = make_index([
            ArticleRecord('A', source='C'),
            ArticleRecord('B', source='A'),
            ArticleRecord('C', source='B'),
        ])
        result = index.index_for('A')
        ambiguity = result['indeterminate']
        self.assertEqual(ambiguity['reason'], CYCLE)
        self.assertEqual(ambiguity['segment'], ['A', 'C', 'B', 'A'])

    def test_fork_with_one_cyclic_branch_keeps_clean_origin(self):
        index = make_index([
            ArticleRecord('A', source='B'),
            ArticleRecord('B', source='A'),
            ArticleRecord('P', authors=['P Author']),
            ArticleRecord('D', source='A'),
        ], [RepostStatement('D', 'P', declared_by='aggregator')])
        result = index.index_for('D')
        # The clean branch still yields its confirmable origin ...
        self.assertEqual(
                [o['article_id'] for o in result['origin_candidates']],
                ['P'])
        # ... while the cyclic branch is flagged as indeterminate.
        self.assertEqual(result['indeterminate']['reason'], MUTUAL_REPOST)
        self.assertEqual(result['indeterminate']['segment'],
                         ['A', 'B', 'A'])

    def test_declared_self_loop_is_flagged(self):
        index = make_index([ArticleRecord('A', source='A')])
        result = index.index_for('A')
        self.assertEqual(result['origin_candidates'], [])
        self.assertEqual(result['indeterminate']['reason'], 'self_loop')
        self.assertEqual(result['indeterminate']['segment'], ['A', 'A'])

    def test_declared_and_external_evidence_merge_on_one_edge(self):
        index = make_index([
            ArticleRecord('O', authors=['O Author']),
            ArticleRecord('D', authors=['D Author'], source='O'),
        ], [RepostStatement('D', 'O', declared_by='aggregator')])
        result = index.index_for('D')
        hop = result['chain'][0]
        relationships = sorted(
                ev['relationship'] for ev in hop['evidence'])
        self.assertEqual(relationships, [DECLARED, EXTERNAL])
        # External corroboration does not erase the manuscript's own claim.
        self.assertEqual(result['declared_authors'], ['D Author'])

    def test_build_index_covers_every_manuscript(self):
        index = make_index([
            ArticleRecord('O'),
            ArticleRecord('D', source='O'),
        ])
        entries = index.build_index()
        self.assertEqual([e['article_id'] for e in entries], ['D', 'O'])
        json.dumps(entries)  # the whole index is JSON serializable

    def test_dict_inputs_and_duplicate_rejection(self):
        index = ProvenanceIndex()
        index.add_record({'article_id': 'O', 'authors': ['X']})
        index.add_repost({'reposter': 'D', 'source': 'O',
                          'declared_by': 'aggregator'})
        with self.assertRaises(ValueError):
            index.add_record({'article_id': 'O'})
        with self.assertRaises(ValueError):
            index.add_repost(RepostStatement('X', 'X'))

    def test_from_feed_uses_entry_ids_authors_and_sources(self):
        fg = FeedGenerator()
        fg.id('http://example.com/feed')
        fg.title('Testfeed')
        fg.link(href='http://example.com', rel='alternate')

        origin = fg.add_entry()
        origin.id('http://example.com/O')
        origin.title('Origin')
        origin.content('...')
        origin.author(name='Origin Author')

        repost = fg.add_entry()
        repost.id('http://example.com/D')
        repost.title('Repost')
        repost.content('...')
        repost.author(name='Reporter D')
        repost.source(url='http://example.com/O', title='Origin')

        index = ProvenanceIndex.from_feed(fg)
        result = index.index_for('http://example.com/D')
        self.assertEqual(
                result['declared_authors'],
                [{'name': 'Reporter D'}])
        self.assertEqual(
                result['origin_candidates'][0]['article_id'],
                'http://example.com/O')


if __name__ == '__main__':
    unittest.main()
