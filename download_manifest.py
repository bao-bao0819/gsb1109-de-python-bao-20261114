#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Build a download manifest for one issue (刊) of media entries for a
mountain relay site with a per-sync byte limit.

Input is a JSON description of the issue built from the entries' media
information (e.g. collected from ``entry.media.content()``)::

    {
      "byte_limit": 1048576,
      "required_languages": ["de", "en"],
      "entries": [
        {"id": "article-1",
         "groups": [
           {"id": "trailer", "required": true,
            "versions": [
              {"url": "http://ex/lo.mp4", "lang": "de",
               "fileSize": 400000, "quality": 1},
              {"url": "http://ex/hi.mp4", "lang": "de",
               "fileSize": 900000, "quality": 5}
            ]}
         ]}
      ]
    }

Rules implemented:

- every mandatory group gets at least one version, optional groups
  (``"required": false``) may stay empty;
- the required languages are covered first, then the total quality score
  of the issue is maximised (ties are broken by lower byte usage);
- the same confirmed asset referenced by two articles is only downloaded
  and billed once (assets are identified by URL);
- if two sources report conflicting sizes for the same asset, the larger
  size is used for planning -- the smaller one must not be used to sneak
  the asset past the limit;
- if no feasible selection exists, the report lists the mandatory groups
  whose smallest available version already exceeds the byte limit.

Usage::

    python download_manifest.py issue.json > manifest.json

The exit status is 0 when a feasible manifest was produced and 1 when no
combination fits the limit.
'''

import argparse
import json
import sys


def _version_quality(version):
    '''Quality score of a version.

    Uses the explicit ``quality`` field when present, otherwise falls
    back to the pixel area and finally to the bitrate.
    '''
    if version.get('quality') is not None:
        return version['quality']
    width, height = version.get('width'), version.get('height')
    if width and height:
        return int(width) * int(height)
    return int(version.get('bitrate') or 0)


def _prepare(issue):
    '''Normalise media groups and resolve effective asset sizes.

    Conflicting sizes reported for the same URL are resolved to the
    largest reported value.
    '''
    raw_groups = []
    sizes = {}
    for entry in issue.get('entries', []):
        for group in entry.get('groups', []):
            versions = list(group.get('versions', []))
            raw_groups.append((entry.get('id'), group.get('id'),
                               bool(group.get('required', True)), versions))
            for version in versions:
                if version.get('fileSize') is None:
                    continue
                url = version['url']
                size = int(version['fileSize'])
                if size > sizes.get(url, -1):
                    sizes[url] = size
    groups = []
    for entry_id, group_id, required, versions in raw_groups:
        normalised = []
        for version in versions:
            url = version['url']
            normalised.append({
                'url': url,
                'lang': version.get('lang'),
                'quality': _version_quality(version),
                'fileSize': sizes.get(url, int(version.get('fileSize') or 0)),
            })
        groups.append({
            'entry': entry_id,
            'group': group_id,
            'required': required,
            'versions': normalised,
        })
    return groups, sizes


def _search(groups, byte_limit, required_langs):
    '''Find a feasible selection maximising (total quality, -total bytes).

    Returns the list of (group index, version index or None) or None when
    no feasible selection exists.
    '''
    lang_bit = {lang: 1 << pos for pos, lang in enumerate(required_langs)}
    full_mask = (1 << len(required_langs)) - 1

    choices = []
    for group in groups:
        options = list(range(len(group['versions'])))
        if not group['required']:
            options.append(None)
        choices.append(options)

    # Upper bound of the quality still reachable from each position,
    # used to prune branches that cannot beat the current best.
    suffix = [0] * (len(groups) + 1)
    for pos in range(len(groups) - 1, -1, -1):
        best_here = max([0] + [v['quality'] for v in groups[pos]['versions']])
        suffix[pos] = suffix[pos + 1] + best_here

    best = {'key': None, 'selection': None}

    def dfs(pos, used, mask, quality, selection, chosen):
        if used > byte_limit:
            return
        if best['key'] is not None and quality + suffix[pos] < best['key'][0]:
            return
        if pos == len(groups):
            if mask != full_mask:
                return
            key = (quality, -used)
            if best['key'] is None or key > best['key']:
                best['key'] = key
                best['selection'] = list(selection)
            return
        for option in choices[pos]:
            if option is None:
                dfs(pos + 1, used, mask, quality, selection, chosen)
                continue
            version = groups[pos]['versions'][option]
            already = version['url'] in chosen
            added = 0 if already else version['fileSize']
            if not already:
                chosen.add(version['url'])
            selection.append((pos, option))
            dfs(pos + 1, used + added, mask | lang_bit.get(version['lang'], 0),
                quality + version['quality'], selection, chosen)
            selection.pop()
            if not already:
                chosen.discard(version['url'])

    dfs(0, 0, 0, 0, [], set())
    return best['selection']


def _infeasible_report(groups, byte_limit, required_langs):
    '''Explain why no selection fits the byte limit.'''
    available = {v['lang'] for g in groups for v in g['versions']}
    missing = [lang for lang in required_langs if lang not in available]

    minimums = []
    seen_urls = set()
    min_bytes = 0
    for group in groups:
        if not group['required']:
            continue
        if group['versions']:
            smallest = min(group['versions'], key=lambda v: v['fileSize'])
            over = smallest['fileSize'] > byte_limit
            if smallest['url'] not in seen_urls:
                seen_urls.add(smallest['url'])
                min_bytes += smallest['fileSize']
        else:
            smallest = None
            over = True
        minimums.append({
            'entry': group['entry'],
            'group': group['group'],
            'min_version': smallest,
            'exceeds_limit_alone': over,
        })

    return {
        'feasible': False,
        'byte_limit': byte_limit,
        'missing_languages': missing,
        'min_required_bytes': min_bytes,
        'over_limit_groups': [m for m in minimums
                              if m['exceeds_limit_alone']],
        'group_minimums': minimums,
    }


def build_manifest(issue):
    '''Build the download manifest for one issue.

    :param issue: decoded JSON description of the issue.
    :returns: manifest dict with ``feasible`` set to True, or an
              infeasibility report.
    '''
    byte_limit = int(issue['byte_limit'])
    required_langs = list(issue.get('required_languages')
                          or issue.get('languages') or [])
    groups, sizes = _prepare(issue)

    selection = _search(groups, byte_limit, required_langs)
    if selection is None:
        return _infeasible_report(groups, byte_limit, required_langs)

    items = []
    asset_urls = []
    seen = set()
    for group_pos, version_pos in selection:
        if version_pos is None:
            continue
        group = groups[group_pos]
        version = group['versions'][version_pos]
        items.append({
            'entry': group['entry'],
            'group': group['group'],
            'url': version['url'],
            'lang': version['lang'],
            'quality': version['quality'],
            'fileSize': version['fileSize'],
        })
        if version['url'] not in seen:
            seen.add(version['url'])
            asset_urls.append(version['url'])

    assets = [{'url': url, 'fileSize': sizes.get(url, 0)}
              for url in asset_urls]
    return {
        'feasible': True,
        'byte_limit': byte_limit,
        'total_bytes': sum(asset['fileSize'] for asset in assets),
        'total_quality': sum(item['quality'] for item in items),
        'languages_covered': sorted({item['lang'] for item in items
                                     if item['lang']}),
        'items': items,
        'assets': assets,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Build a download manifest for one issue under a '
                    'byte limit.')
    parser.add_argument('issue',
                        help="issue description JSON file ('-' for stdin)")
    parser.add_argument('--indent', type=int, default=2,
                        help='JSON indent of the output (default: 2)')
    args = parser.parse_args(argv)

    if args.issue == '-':
        issue = json.load(sys.stdin)
    else:
        with open(args.issue, encoding='utf-8') as handle:
            issue = json.load(handle)

    sys.setrecursionlimit(max(1000, 4 * (len(issue.get('entries', [])) + 16)))
    manifest = build_manifest(issue)
    json.dump(manifest, sys.stdout, indent=args.indent, ensure_ascii=False)
    sys.stdout.write('\n')
    return 0 if manifest['feasible'] else 1


if __name__ == '__main__':
    sys.exit(main())
