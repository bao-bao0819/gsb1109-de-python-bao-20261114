import unittest

from download_manifest import build_manifest
from feedgen.feed import FeedGenerator


def _issue(byte_limit, groups, languages=None):
    return {
        'byte_limit': byte_limit,
        'required_languages': languages or [],
        'entries': [{'id': 'article-1', 'groups': groups}],
    }


class TestDownloadManifest(unittest.TestCase):

    def test_mandatory_group_picks_best_quality_that_fits(self):
        issue = _issue(1000, [
            {'id': 'g1', 'required': True, 'versions': [
                {'url': 'u1-low', 'fileSize': 100, 'quality': 1},
                {'url': 'u1-hi', 'fileSize': 600, 'quality': 5},
            ]},
            {'id': 'g2', 'required': False, 'versions': [
                {'url': 'u2', 'fileSize': 300, 'quality': 4},
            ]},
        ])
        manifest = build_manifest(issue)
        self.assertTrue(manifest['feasible'])
        self.assertEqual(manifest['total_quality'], 9)
        self.assertEqual(manifest['total_bytes'], 900)
        self.assertEqual({i['url'] for i in manifest['items']},
                         {'u1-hi', 'u2'})

    def test_optional_group_may_stay_empty(self):
        issue = _issue(150, [
            {'id': 'g1', 'required': True, 'versions': [
                {'url': 'u1-low', 'fileSize': 100, 'quality': 1},
            ]},
            {'id': 'g2', 'required': False, 'versions': [
                {'url': 'u2', 'fileSize': 300, 'quality': 4},
            ]},
        ])
        manifest = build_manifest(issue)
        self.assertTrue(manifest['feasible'])
        self.assertEqual([i['url'] for i in manifest['items']], ['u1-low'])
        self.assertEqual(manifest['total_bytes'], 100)

    def test_language_requirement_beats_quality(self):
        issue = _issue(1000, [
            {'id': 'g1', 'required': True, 'versions': [
                {'url': 'u1-de', 'fileSize': 100, 'quality': 9, 'lang': 'de'},
                {'url': 'u1-fr', 'fileSize': 100, 'quality': 1, 'lang': 'fr'},
            ]},
        ], languages=['fr'])
        manifest = build_manifest(issue)
        self.assertTrue(manifest['feasible'])
        self.assertEqual([i['url'] for i in manifest['items']], ['u1-fr'])
        self.assertEqual(manifest['languages_covered'], ['fr'])

    def test_shared_asset_billed_once(self):
        issue = {
            'byte_limit': 450,
            'entries': [
                {'id': 'a1', 'groups': [
                    {'id': 'g1', 'required': True, 'versions': [
                        {'url': 'shared', 'fileSize': 400, 'quality': 2},
                    ]},
                ]},
                {'id': 'a2', 'groups': [
                    {'id': 'g2', 'required': True, 'versions': [
                        {'url': 'shared', 'fileSize': 400, 'quality': 3},
                    ]},
                ]},
            ],
        }
        manifest = build_manifest(issue)
        self.assertTrue(manifest['feasible'])
        self.assertEqual(manifest['total_bytes'], 400)
        self.assertEqual(len(manifest['assets']), 1)
        self.assertEqual(manifest['total_quality'], 5)

    def test_conflicting_sizes_use_the_larger_one(self):
        issue = {
            'byte_limit': 150,
            'entries': [
                {'id': 'a1', 'groups': [
                    {'id': 'g1', 'required': True, 'versions': [
                        {'url': 'shared', 'fileSize': 100, 'quality': 1},
                    ]},
                ]},
                {'id': 'a2', 'groups': [
                    {'id': 'g2', 'required': True, 'versions': [
                        {'url': 'shared', 'fileSize': 200, 'quality': 1},
                    ]},
                ]},
            ],
        }
        manifest = build_manifest(issue)
        self.assertFalse(manifest['feasible'])
        self.assertEqual(manifest['min_required_bytes'], 200)

    def test_infeasible_reports_groups_over_limit(self):
        issue = _issue(500, [
            {'id': 'g1', 'required': True, 'versions': [
                {'url': 'u1', 'fileSize': 800, 'quality': 1},
                {'url': 'u2', 'fileSize': 900, 'quality': 2},
            ]},
            {'id': 'g2', 'required': True, 'versions': [
                {'url': 'u3', 'fileSize': 100, 'quality': 1},
            ]},
        ])
        manifest = build_manifest(issue)
        self.assertFalse(manifest['feasible'])
        self.assertEqual([g['group'] for g in manifest['over_limit_groups']],
                         ['g1'])
        self.assertEqual(manifest['min_required_bytes'], 900)

    def test_uncoverable_language_is_reported(self):
        issue = _issue(1000, [
            {'id': 'g1', 'required': True, 'versions': [
                {'url': 'u1', 'fileSize': 100, 'quality': 1, 'lang': 'de'},
            ]},
        ], languages=['ja'])
        manifest = build_manifest(issue)
        self.assertFalse(manifest['feasible'])
        self.assertEqual(manifest['missing_languages'], ['ja'])

    def test_issue_from_feedgen_media_info(self):
        fg = FeedGenerator()
        fg.load_extension('media')
        fg.id('id')
        fg.title('title')
        fg.link(href='http://example.com', rel='self')
        fg.description('description')
        entry = fg.add_item()
        entry.id('article-1')
        entry.title('t')
        entry.media.content(url='http://ex/lo.mp4', fileSize='100',
                            height='360', width='640', lang='de',
                            group='trailer')
        entry.media.content(url='http://ex/hi.mp4', fileSize='300',
                            height='720', width='1280', lang='de',
                            group='trailer')

        groups = {}
        for content in entry.media.content():
            group = groups.setdefault(
                content['group'],
                {'id': content['group'], 'required': True, 'versions': []})
            group['versions'].append(content)
        issue = {
            'byte_limit': 250,
            'entries': [{'id': 'article-1', 'groups': list(groups.values())}],
        }
        manifest = build_manifest(issue)
        self.assertTrue(manifest['feasible'])
        self.assertEqual([i['url'] for i in manifest['items']],
                         ['http://ex/lo.mp4'])
        # quality derived from the pixel area when not given explicitly
        self.assertEqual(manifest['total_quality'], 360 * 640)


if __name__ == '__main__':
    unittest.main()
