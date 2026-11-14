#!/usr/bin/env python3
"""column_match -- 季度档案 DBF 列映射核验工具。

场景:导出工具更换后,同一长业务名被截成不同的 DBF 短名,不同列也可能撞前缀,
因此不能按列名配对。本工具根据:
  1. 两张 DBF 表(旧表 -> 新表);
  2. 人工确认的业务键列(--keys);
  3. 少量人工确认的字段对照(--seed);
给出可核验的列映射。

配对约束:
  * 类型可表示范围:源列的值域(声明类型 + 实际样本)必须能被目标列类型表示;
  * 样本一致:在相同业务键下,候选两列的样本值必须逐行一致;
  * 一一对应:一次匹配中任一目标列至多被一个源列占用(二分图匹配)。

输出:
  * 必然映射(种子 + 在所有最大匹配中都成立的边)-- 只有这部分被发布;
  * 等价歧义分量,以及区分它们所需核对的最少候选列对(精确最小测试集)。
"""
import argparse
import datetime
import json
import math
import sys
from decimal import Decimal

import dbf


# ---------------------------------------------------------------- 模式读取

class Column(object):
    def __init__(self, table, name):
        info = table.field_info(name)
        self.name = name
        ftype = info[0]
        self.type = (chr(ftype) if isinstance(ftype, int) else ftype).upper()
        self.length = info[1]
        self.decimals = info[2]

    def spec(self):
        if self.type in ('C', 'M', 'G', 'P'):
            return '%s(%d)' % (self.type, self.length)
        if self.type in ('N', 'F', 'B'):
            return '%s(%d,%d)' % (self.type, self.length, self.decimals)
        return self.type


def load_table(path):
    table = dbf.Table(path)
    table.open(dbf.READ_ONLY)
    return table


def table_columns(table):
    return [Column(table, name) for name in table.field_names]


# ---------------------------------------------------------------- 值规范化

def normalize(value):
    """把 DBF 读出的值规范成可比较的形式。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value.rstrip()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return Decimal(repr(value)).normalize()
    if isinstance(value, datetime.datetime):
        return value.replace(microsecond=0)
    if isinstance(value, datetime.date):
        return value
    return value


def values_equal(a, b):
    na, nb = normalize(a), normalize(b)
    if na is None or nb is None:
        return na is None and nb is None
    if isinstance(na, Decimal) and isinstance(nb, Decimal):
        return na == nb
    if isinstance(na, Decimal) and isinstance(nb, str):
        try:
            return na == Decimal(nb.strip())
        except Exception:
            return False
    if isinstance(na, str) and isinstance(nb, Decimal):
        return values_equal(nb, na)
    if isinstance(na, datetime.date) and isinstance(nb, str):
        return na.isoformat() == nb.strip()
    if isinstance(na, str) and isinstance(nb, datetime.date):
        return values_equal(nb, na)
    if isinstance(na, bool) and isinstance(nb, str):
        return na == (nb.strip().upper() in ('Y', 'T', '1'))
    if isinstance(na, str) and isinstance(nb, bool):
        return values_equal(nb, na)
    return na == nb


# ---------------------------------------------------------------- 值域画像

class Domain(object):
    """源列声明类型 + 样本观察得到的值域画像。"""

    def __init__(self, column, samples):
        self.type = column.type
        self.length = column.length
        self.decimals = column.decimals
        self.max_str_len = 0
        self.max_int_digits = 0
        self.max_decimals = 0
        self.all_numeric_text = True
        self.all_date_text = True
        self.all_logical_text = True
        saw_value = False
        for value in samples:
            value = normalize(value)
            if value is None:
                continue
            saw_value = True
            if isinstance(value, str):
                self.max_str_len = max(self.max_str_len, len(value))
                try:
                    self._observe_decimal(Decimal(value.strip()))
                except Exception:
                    self.all_numeric_text = False
                try:
                    datetime.date.fromisoformat(value.strip())
                except Exception:
                    self.all_date_text = False
                if value.strip().upper() not in ('Y', 'N', 'T', 'F', '0', '1', ''):
                    self.all_logical_text = False
            elif isinstance(value, Decimal):
                self._observe_decimal(value)
                self.all_date_text = False
                self.all_logical_text = False
            elif isinstance(value, bool):
                self.all_numeric_text = False
                self.all_date_text = False
            elif isinstance(value, datetime.date):
                self.all_numeric_text = False
                self.all_logical_text = False
            else:
                self.all_numeric_text = False
                self.all_date_text = False
                self.all_logical_text = False
        self.empty = not saw_value

    def _observe_decimal(self, dec):
        sign, digits, exponent = dec.as_tuple()
        int_digits = max(len(digits) + min(exponent, 0), 0)
        dec_places = max(-exponent, 0)
        self.max_int_digits = max(self.max_int_digits, int_digits)
        self.max_decimals = max(self.max_decimals, dec_places)

    def declared_int_digits(self):
        if self.type in ('N', 'F', 'B'):
            return max(self.length - self.decimals -
                       (1 if self.decimals else 0), 0)
        return self.max_int_digits


def can_represent(src_domain, tgt_col):
    """目标列类型能否表示源列的值域(声明范围与样本范围同时满足)。"""
    t = tgt_col.type
    s = src_domain.type

    if t in ('C', 'M'):
        if s in ('C', 'M'):
            need = max(src_domain.max_str_len, 1)
        elif s in ('N', 'F', 'B'):
            need = src_domain.length      # 数字转文本,声明宽度足够
        elif s == 'D':
            need = 10                     # YYYY-MM-DD
        elif s == 'T':
            need = 19
        elif s == 'L':
            need = 1
        else:
            return False
        return tgt_col.length >= need

    if t in ('N', 'F', 'B'):
        if s in ('N', 'F', 'B'):
            pass
        elif s == 'C' and src_domain.all_numeric_text and not src_domain.empty:
            pass
        else:
            return False
        int_digits = max(src_domain.declared_int_digits(),
                         src_domain.max_int_digits)
        dec_places = max(src_domain.decimals if s in ('N', 'F', 'B') else 0,
                         src_domain.max_decimals)
        tgt_int = max(tgt_col.length - tgt_col.decimals -
                      (1 if tgt_col.decimals else 0), 0)
        return tgt_col.decimals >= dec_places and tgt_int >= int_digits

    if t == 'D':
        if s in ('D', 'T'):
            return True
        return s == 'C' and src_domain.all_date_text and not src_domain.empty

    if t == 'T':
        return s in ('D', 'T')

    if t == 'L':
        if s == 'L':
            return True
        return s == 'C' and src_domain.all_logical_text and not src_domain.empty

    # 其余目标类型(I/Y/G/P 等)只接受同类型
    return s == t


# ---------------------------------------------------------------- 样本比对

def build_key_index(table, key_cols):
    index = {}
    for record in table:
        key = tuple(normalize(record[name]) for name in key_cols)
        if key in index:
            raise ValueError('业务键 %r 在表 %s 中不唯一' % (key, table.filename))
        index[key] = record
    return index


def sample_pairs(src_table, tgt_table, src_keys, tgt_keys, limit):
    """按业务键对齐两表,产出 (源记录, 目标记录) 样本对。"""
    tgt_index = build_key_index(tgt_table, tgt_keys)
    pairs = []
    missing_keys = 0
    for record in src_table:
        key = tuple(normalize(record[name]) for name in src_keys)
        other = tgt_index.get(key)
        if other is None:
            missing_keys += 1
            continue
        pairs.append((record, other))
        if limit and len(pairs) >= limit:
            break
    return pairs, missing_keys


# ---------------------------------------------------------------- 二分匹配

def hopcroft_karp(adj, src_nodes):
    """adj: {src: set(tgt)}。返回 {src: tgt} 最大匹配。"""
    from collections import deque
    match_s = {}
    match_t = {}

    def bfs():
        dist = {}
        queue = deque()
        for s in src_nodes:
            if s not in match_s:
                dist[s] = 0
                queue.append(s)
        found = False
        while queue:
            s = queue.popleft()
            for t in adj.get(s, ()):
                s2 = match_t.get(t)
                if s2 is None:
                    found = True
                elif s2 not in dist:
                    dist[s2] = dist[s] + 1
                    queue.append(s2)
        return found, dist

    def dfs(s, dist):
        for t in adj.get(s, ()):
            s2 = match_t.get(t)
            if s2 is None or (dist.get(s2) == dist[s] + 1 and dfs(s2, dist)):
                match_s[s] = t
                match_t[t] = s
                return True
        dist[s] = -1
        return False

    while True:
        found, dist = bfs()
        if not found:
            break
        for s in src_nodes:
            if s not in match_s:
                dfs(s, dist)
    return match_s


def matching_size(adj, src_nodes):
    return len(hopcroft_karp(adj, src_nodes))


def without_edge(adj, edge):
    s0, t0 = edge
    return {s: ({t for t in ts if not (s == s0 and t == t0)})
            for s, ts in adj.items()}


def without_vertices(adj, s0, t0):
    return {s: ({t for t in ts if t != t0})
            for s, ts in adj.items() if s != s0}


def analyze_graph(adj, src_pool):
    """返回 (matching, forced, components)。

    forced: 删除后会使最大匹配变小的匹配边 -- 必然成立,可发布。
    components: 由"可行边"(出现在某个最大匹配中的边)连成的歧义分量,
                每个分量存在多种等价匹配。
    """
    matching = hopcroft_karp(adj, src_pool)
    base = len(matching)

    forced = {}
    flexible = {}
    for s, t in matching.items():
        if matching_size(without_edge(adj, (s, t)), src_pool) < base:
            forced[s] = t
        else:
            flexible[s] = t

    # 可行边:强制该边后仍能达到最大匹配数
    allowed = {s: set() for s in src_pool}
    for s in src_pool:
        for t in adj.get(s, ()):
            if matching_size(without_vertices(adj, s, t), src_pool) == base - 1:
                allowed[s].add(t)

    # 可行边图上的连通分量(只保留真正有多种选择的分量)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for s in src_pool:
        for t in allowed[s]:
            union(('S', s), ('T', t))

    comp_map = {}
    for node in parent:
        comp_map.setdefault(find(node), {'sources': set(), 'targets': set(),
                                         'edges': set()})
    for s in src_pool:
        for t in allowed[s]:
            comp = comp_map[find(('S', s))]
            comp['sources'].add(s)
            comp['targets'].add(t)
            comp['edges'].add((s, t))

    components = []
    for comp in comp_map.values():
        multi = any(sum(1 for e in comp['edges'] if e[0] == s) > 1
                    for s in comp['sources'])
        if multi:
            components.append(comp)
    return matching, forced, components


def enumerate_matchings(component, adj, size, cap=100000):
    """枚举分量内所有大小为 size 的匹配(超过 cap 返回 None)。"""
    sources = sorted(component['sources'])
    targets = component['targets']
    edges = component['edges']
    results = []

    def rec(i, used, current):
        if len(results) > cap:
            return
        remaining = len(sources) - i
        if len(current) + remaining < size:
            return
        if len(current) == size:
            results.append(dict(current))
            return
        if i == len(sources):
            return
        s = sources[i]
        for t in sorted(targets):
            if (s, t) in edges and t not in used:
                used.add(t)
                current[s] = t
                rec(i + 1, used, current)
                del current[s]
                used.discard(t)
        rec(i + 1, used, current)   # 该源列在本匹配中不占位

    rec(0, set(), {})
    if len(results) > cap:
        return None
    return results


# ---------------------------------------------------------------- 最小核对集

def min_distinguishing_checks(matchings, candidate_edges):
    """区分全部等价匹配所需核对的最少候选列对(精确最小测试集)。

    每次核对一条候选边(确认/否定),要求任意两个匹配在所选边上可区分。
    迭代加深搜索,按最大不可分组大小的 log2 下界剪枝。
    """
    varying = [e for e in candidate_edges
               if any(m.get(e[0]) == e[1] for m in matchings)
               and not all(m.get(e[0]) == e[1] for m in matchings)]
    if len(matchings) <= 1:
        return []
    edge_index = {e: i for i, e in enumerate(varying)}
    signatures = [frozenset(edge_index[e] for e in varying
                            if m.get(e[0]) == e[1]) for m in matchings]

    def max_group(chosen):
        groups = {}
        for sig in signatures:
            key = sig & chosen
            groups[key] = groups.get(key, 0) + 1
        return max(groups.values())

    all_ids = set(range(len(varying)))

    def search(chosen, budget):
        worst = max_group(chosen)
        if worst == 1:
            return set()
        if budget == 0 or math.ceil(math.log2(worst)) > budget:
            return None
        for e in sorted(all_ids - chosen):
            sub = search(chosen | {e}, budget - 1)
            if sub is not None:
                return sub | {e}
        return None

    depth = 1
    while depth <= len(varying):
        result = search(frozenset(), depth)
        if result is not None:
            return [varying[i] for i in sorted(result)]
        depth += 1
    return varying


# ---------------------------------------------------------------- 主流程

def match_tables(src_path, tgt_path, key_pairs, seed_map, sample_limit=0):
    src_table = load_table(src_path)
    tgt_table = load_table(tgt_path)
    try:
        src_cols = {c.name: c for c in table_columns(src_table)}
        tgt_cols = {c.name: c for c in table_columns(tgt_table)}
        src_keys = [p[0] for p in key_pairs]
        tgt_keys = [p[1] for p in key_pairs]
        for k in src_keys:
            if k not in src_cols:
                raise ValueError('源表无键列 %s' % k)
        for k in tgt_keys:
            if k not in tgt_cols:
                raise ValueError('目标表无键列 %s' % k)
        for s, t in seed_map.items():
            if s not in src_cols or t not in tgt_cols:
                raise ValueError('种子对照 %s->%s 含未知列' % (s, t))

        pairs, missing = sample_pairs(src_table, tgt_table,
                                      src_keys, tgt_keys, sample_limit)
        if not pairs:
            raise ValueError('两表在业务键下没有可比对的重叠样本')

        # 值域画像(基于对齐样本)
        domains = {}
        for name, col in src_cols.items():
            if name in src_keys:
                continue
            domains[name] = Domain(col, [r[name] for r, _ in pairs])

        # 候选边:类型可表示 + 样本逐行一致
        adj = {}
        evidence = {}
        seeded_targets = set(seed_map.values())
        for s_name, domain in domains.items():
            if s_name in seed_map:
                continue
            candidates = set()
            for t_name, t_col in tgt_cols.items():
                if t_name in tgt_keys or t_name in seeded_targets:
                    continue
                if not can_represent(domain, t_col):
                    continue
                mismatches = sum(
                    1 for r_src, r_tgt in pairs
                    if not values_equal(r_src[s_name], r_tgt[t_name]))
                if mismatches == 0:
                    candidates.add(t_name)
                    evidence[(s_name, t_name)] = len(pairs)
            adj[s_name] = candidates

        # 种子列同样接受核验,不通过则告警(仍按人工意见发布)
        seed_warnings = []
        for s_name, t_name in seed_map.items():
            if s_name in src_keys:
                continue
            domain = domains.get(s_name)
            t_col = tgt_cols[t_name]
            problems = []
            if domain is not None and not can_represent(domain, t_col):
                problems.append('类型不可表示')
            if domain is not None:
                bad = sum(1 for r_src, r_tgt in pairs
                          if not values_equal(r_src[s_name], r_tgt[t_name]))
                if bad:
                    problems.append('样本不一致 %d/%d 行' % (bad, len(pairs)))
            if problems:
                seed_warnings.append({'source': s_name, 'target': t_name,
                                      'problems': problems})

        src_pool = sorted(adj)
        matching, forced, components = analyze_graph(adj, src_pool)

        unmatched_src = sorted(set(src_pool) - set(matching))
        unmatched_tgt = sorted(set(tgt_cols) - set(tgt_keys) - seeded_targets -
                               set(matching.values()))

        report = {
            'rows_compared': len(pairs),
            'source_keys_missing_in_target': missing,
            'published': {},
            'seed_warnings': seed_warnings,
            'unmatched_source_columns': unmatched_src,
            'unmatched_target_columns': unmatched_tgt,
            'ambiguous': [],
            'checks_needed': [],
        }
        for s, t in sorted(seed_map.items()):
            if s not in src_keys:
                report['published'][s] = {'target': t, 'reason': '人工种子对照'}
        for s, t in sorted(forced.items()):
            report['published'][s] = {
                'target': t,
                'reason': '全部等价匹配中必然成立(样本 %d 行一致)' %
                          evidence[(s, t)],
            }

        for comp in components:
            comp_adj = {s: adj[s] & comp['targets'] for s in comp['sources']}
            comp_size = matching_size(comp_adj, sorted(comp['sources']))
            matchings = enumerate_matchings(comp, adj, comp_size)
            candidate_edges = sorted(comp['edges'])
            entry = {
                'sources': sorted(comp['sources']),
                'targets': sorted(comp['targets']),
                'candidate_edges': ['%s -> %s' % e for e in candidate_edges],
            }
            if matchings is None:
                entry['equivalent_matchings'] = '超过枚举上限,需人工处理'
            else:
                entry['equivalent_matchings'] = len(matchings)
                checks = min_distinguishing_checks(matchings, candidate_edges)
                entry['min_checks'] = ['%s -> %s' % e for e in checks]
                report['checks_needed'].extend(entry['min_checks'])
            report['ambiguous'].append(entry)
        return report
    finally:
        src_table.close()
        tgt_table.close()


def parse_keys(text):
    pairs = []
    for item in text.split(','):
        item = item.strip()
        if not item:
            continue
        if '=' in item:
            s, t = item.split('=', 1)
        else:
            s = t = item
        pairs.append((s.strip(), t.strip()))
    if not pairs:
        raise ValueError('至少需要一个人工确认的键列')
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(description='季度档案 DBF 列映射核验')
    parser.add_argument('source', help='旧表 DBF 路径')
    parser.add_argument('target', help='新表 DBF 路径')
    parser.add_argument('--keys', required=True,
                        help='人工确认的业务键,如 ACCT_NO=ACCTNO,BRANCH')
    parser.add_argument('--seed', help='人工确认的字段对照 JSON: {"旧列": "新列"}')
    parser.add_argument('--sample', type=int, default=0,
                        help='比对样本行数上限(0 为全部重叠行)')
    parser.add_argument('--out', help='报告输出 JSON 路径(默认打印到 stdout)')
    args = parser.parse_args(argv)

    key_pairs = parse_keys(args.keys)
    seed_map = {}
    if args.seed:
        with open(args.seed) as fh:
            seed_map = json.load(fh)

    report = match_tables(args.source, args.target, key_pairs, seed_map,
                          args.sample)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, 'w') as fh:
            fh.write(text + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
