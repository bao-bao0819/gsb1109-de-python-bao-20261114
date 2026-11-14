# -*- coding: utf-8 -*-
"""
ole_dir_audit.py
================

基于 OLE/CFB 复合文档原文件的目录项缺失清点工具。

背景:
    olefile.OleFileIO 在加载时只构建从根存储 (SID 0) 能走到的目录树,
    遇到重复引用 / 重名项还会直接报 defect, 且不会主动读取未被树引用的
    目录槽位 (见 olefile.py 中 "TODO: read ALL directory entries")。
    因此按目录导出时, 那些只存在于断开存储树、或被多处引用的对象会丢失,
    但它们在目录流里仍然占着一个目录项编号 (SID)。

本工具不依赖 olefile 的建树结果, 而是:
    1. 读取目录流的全部原始 128 字节槽位;
    2. 自行沿每个存储的红黑树指针 (child/left/right) 做有界遍历;
    3. 按 SID 把每个对象分进三类:
         - ROOT_REACHABLE       能从根走到的对象 (归属唯一)
         - DISCONNECTED_ONLY    只在某个断开的存储树里出现的对象
         - MULTIPLE_ATTRIBUTION 有多种可能归属 / 卷入环或重复引用的对象
    4. 重名只做提示, 一律按 SID 区分对象;
    5. 绕回自身的目录关系给出有限的 SID 证据路径。

用法:
    python tools/ole_dir_audit.py <ole 文件> [--md report.md] [--json report.json]
"""

from __future__ import print_function

import argparse
import json
import struct
import sys
from collections import defaultdict

import olefile

# 与 [MS-CFB] / olefile 保持一致的常量
NOSTREAM = 0xFFFFFFFF
STGTY_EMPTY = 0
STGTY_STORAGE = 1
STGTY_STREAM = 2
STGTY_ROOT = 5

STRUCT_DIRENTRY = '<64sHBBIII16sIQQIII'
DIRENTRY_SIZE = 128

# 分类结果
ROOT_REACHABLE = 'ROOT_REACHABLE'
DISCONNECTED_ONLY = 'DISCONNECTED_ONLY'
MULTIPLE_ATTRIBUTION = 'MULTIPLE_ATTRIBUTION'
SLOT_EMPTY = 'SLOT_EMPTY'
SLOT_INVALID = 'SLOT_INVALID'

CATEGORY_LABEL = {
    ROOT_REACHABLE: '根可达对象',
    DISCONNECTED_ONLY: '仅存于断开存储树',
    MULTIPLE_ATTRIBUTION: '多种可能归属',
    SLOT_EMPTY: '空槽位',
    SLOT_INVALID: '无效槽位',
}

TYPE_LABEL = {
    STGTY_EMPTY: 'empty',
    STGTY_STORAGE: 'storage',
    STGTY_STREAM: 'stream',
    STGTY_ROOT: 'root',
}


class RawEntry(object):
    """目录流中的一个原始 128 字节槽位, 未经任何建树裁剪。"""

    def __init__(self, sid, raw):
        self.sid = sid
        self.raw = raw
        (self.name_raw, self.namelength, self.entry_type, self.color,
         self.sid_left, self.sid_right, self.sid_child, self.clsid,
         self.dw_user_flags, self.create_time, self.modify_time,
         self.isect_start, self.size_low, self.size_high) = struct.unpack(
            STRUCT_DIRENTRY, raw)
        self.name = self._decode_name()
        self.valid_type = self.entry_type in (
            STGTY_EMPTY, STGTY_STORAGE, STGTY_STREAM, STGTY_ROOT)
        self.is_object = self.entry_type in (
            STGTY_STORAGE, STGTY_STREAM, STGTY_ROOT)

    def _decode_name(self):
        if self.namelength < 2 or self.namelength > 64:
            return u''
        return self.name_raw[:self.namelength - 2].decode(
            'utf-16-le', errors='replace')

    @property
    def type_label(self):
        return TYPE_LABEL.get(self.entry_type, 'type=%d' % self.entry_type)

    @property
    def is_storage(self):
        return self.entry_type in (STGTY_STORAGE, STGTY_ROOT)


def _ptr(value):
    """把 NOSTREAM 渲染成 '-'。"""
    return '-' if value == NOSTREAM else str(value)


def read_raw_entries(ole):
    """从已打开的 OleFileIO 中读出全部原始目录槽位 (含空槽)。"""
    ole.directory_fp.seek(0)
    data = ole.directory_fp.read()
    entries = []
    for sid in range(len(data) // DIRENTRY_SIZE):
        raw = data[sid * DIRENTRY_SIZE:(sid + 1) * DIRENTRY_SIZE]
        entries.append(RawEntry(sid, raw))
    return entries


def traverse_storage_tree(entries, storage):
    """
    沿一个存储的红黑树 (child -> left/right) 做有界迭代遍历。

    返回 (edges, anomalies):
      edges:    [(owner_sid, child_sid, role), ...] 去重后的父子关系
      anomalies: 字典, 含 'dangling' / 'empty_ptr' / 'cycle' / 'repeat' 列表,
                其中 cycle/repeat 附带有限的 SID 证据路径。
    """
    total = len(entries)
    edges = []
    seen_pairs = set()
    anomalies = {'dangling': [], 'empty_ptr': [], 'cycle': [], 'repeat': []}
    visited = set()
    # 栈元素: (待访问 SID, 从存储自身开始的路径, 指针角色)
    stack = [(storage.sid_child, [storage.sid], 'child')]
    while stack:
        sid, path, role = stack.pop()
        if sid == NOSTREAM:
            continue
        if sid < 0 or sid >= total:
            anomalies['dangling'].append({
                'owner': storage.sid, 'sid': sid, 'role': role,
                'path': list(path)})
            continue
        target = entries[sid]
        if target.entry_type == STGTY_EMPTY:
            anomalies['empty_ptr'].append({
                'owner': storage.sid, 'sid': sid, 'role': role,
                'path': list(path)})
            continue
        if sid in path:
            # 回到当前遍历栈上的节点 => 环 (含绕回自身), 路径到此为止即有限证据
            anomalies['cycle'].append({
                'owner': storage.sid, 'sid': sid,
                'evidence_path': path + [sid]})
            continue
        if sid in visited:
            # 同一存储子树里通过另一条路线再次到达 => 重复引用
            anomalies['repeat'].append({
                'owner': storage.sid, 'sid': sid,
                'evidence_path': path + [sid]})
            pair = (storage.sid, sid)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                edges.append((storage.sid, sid, role))
            continue
        visited.add(sid)
        pair = (storage.sid, sid)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            edges.append((storage.sid, sid, role))
        # 右子树先压栈, 保证左子树先处理 (仅影响展示顺序)
        stack.append((target.sid_right, path + [sid], 'right'))
        stack.append((target.sid_left, path + [sid], 'left'))
    return edges, anomalies


def shortest_path(adjacency, start, target):
    """在有向图上求 start -> target 的最短 SID 路径 (带 visited 界)。"""
    if start == target:
        return [start]
    queue = [(start, [start])]
    seen = {start}
    while queue:
        node, path = queue.pop(0)
        for nxt in adjacency.get(node, ()):
            if nxt in seen:
                continue
            new_path = path + [nxt]
            if nxt == target:
                return new_path
            seen.add(nxt)
            queue.append((nxt, new_path))
    return None


def detect_graph_cycles(adjacency, entries):
    """
    在全局父子有向图上做 DFS, 找出所有回边 (含自环与跨存储 child 环)。

    返回 [{'owner', 'sid', 'evidence_path'}], 证据路径为有限的 SID 序列,
    首尾相同 SID 即绕回点。
    """
    cycles = []
    color = {}  # 0=未访问, 1=在当前 DFS 栈中, 2=已完成
    for entry in entries:
        if entry.is_object:
            color[entry.sid] = 0

    def dfs(node, stack):
        color[node] = 1
        stack.append(node)
        for nxt in adjacency.get(node, ()):
            if color.get(nxt, 2) == 1:
                start = stack.index(nxt)
                cycles.append({
                    'owner': node, 'sid': nxt,
                    'evidence_path': stack[start:] + [nxt]})
            elif color.get(nxt) == 0:
                dfs(nxt, stack)
        stack.pop()
        color[node] = 2

    for sid in sorted(color):
        if color[sid] == 0:
            dfs(sid, [])
    return cycles


def audit_data(data):
    """
    对内存中的 OLE 文件字节做目录项清点, 返回结构化结果字典。

    olefile 以最高容忍级别打开: 重复引用 / 重名等 defect 只记录不抛异常,
    后续分类完全基于本模块自己读取的原始槽位, 不采信 olefile 的建树裁剪。
    """
    ole = olefile.OleFileIO(data, raise_defects=100)
    try:
        entries = read_raw_entries(ole)
        parsing_issues = list(ole.parsing_issues)
    finally:
        ole.close()

    total = len(entries)
    all_edges = []
    anomalies = {'dangling': [], 'empty_ptr': [], 'cycle': [], 'repeat': [],
                 'stream_with_child': [], 'invalid_type': []}

    for entry in entries:
        if not entry.valid_type:
            anomalies['invalid_type'].append(
                {'sid': entry.sid, 'entry_type': entry.entry_type})
            continue
        if entry.entry_type == STGTY_EMPTY:
            continue
        if entry.is_storage:
            edges, sub_anoms = traverse_storage_tree(entries, entry)
            all_edges.extend(edges)
            for key in anomalies:
                if key in sub_anoms:
                    anomalies[key].extend(sub_anoms[key])
        elif entry.sid_child != NOSTREAM:
            anomalies['stream_with_child'].append(
                {'sid': entry.sid, 'sid_child': entry.sid_child})

    # 去重边 + 邻接表 + 父项表
    unique_edges = sorted(set(all_edges))
    adjacency = defaultdict(list)
    parents = defaultdict(list)
    for owner, child, _role in unique_edges:
        adjacency[owner].append(child)
        if owner not in parents[child]:
            parents[child].append(owner)

    # 原始指针图: 不区分 child/left/right 角色, 保留槽位里的全部指针。
    # 语义邻接表只含 "存储 -> 其红黑树成员" 的边, 一旦根的 child 被切断,
    # 流之间的 left/right 指针仍能在原始图里把断开片段重新归组。
    raw_adjacency = defaultdict(set)
    raw_incoming = defaultdict(set)
    for entry in entries:
        if not entry.is_object:
            continue
        for pointer in (entry.sid_child, entry.sid_left, entry.sid_right):
            if pointer == NOSTREAM or not (0 <= pointer < total):
                continue
            target = entries[pointer]
            if not target.is_object:
                continue
            raw_adjacency[entry.sid].add(pointer)
            raw_incoming[pointer].add(entry.sid)

    # 全局有向图上的回边: 捕获跨存储 child 环与自环
    previous_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous_limit, total * 4 + 100))
    try:
        graph_cycles = detect_graph_cycles(adjacency, entries)
    finally:
        sys.setrecursionlimit(previous_limit)
    anomalies['cycle'].extend(graph_cycles)

    # 从根 SID 0 出发的有向可达集合
    root_reachable = set()
    if entries and entries[0].is_storage:
        stack = [0]
        while stack:
            node = stack.pop()
            if node in root_reachable:
                continue
            root_reachable.add(node)
            for nxt in adjacency.get(node, ()):
                if nxt not in root_reachable:
                    stack.append(nxt)

    # 卷入环 / 重复引用的对象
    cyclic_sids = set()
    cycle_evidence = defaultdict(list)
    for item in anomalies['cycle'] + anomalies['repeat']:
        cyclic_sids.add(item['sid'])
        cyclic_sids.add(item['owner'])
        cycle_evidence[item['sid']].append(item['evidence_path'])

    # 断开存储树: 在原始指针图上对非根可达对象做无向连通分量归组。
    # 每个分量选一个代表树头: 优先存储类型, 再取最小 SID。
    objects = [e for e in entries if e.is_object]
    disconnected_sids = sorted(
        entry.sid for entry in objects
        if entry.sid != 0 and entry.sid not in root_reachable)
    component_of = {}
    components = []
    for start in disconnected_sids:
        if start in component_of:
            continue
        component_id = len(components)
        members = []
        stack = [start]
        seen = {start}
        while stack:
            node = stack.pop()
            members.append(node)
            component_of[node] = component_id
            for nxt in raw_adjacency.get(node, ()):
                if nxt in root_reachable or nxt == 0:
                    continue
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
            for nxt in raw_incoming.get(node, ()):
                if nxt in root_reachable or nxt == 0:
                    continue
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(sorted(members))

    disconnected_heads = []
    for members in components:
        storages = [sid for sid in members
                    if entries[sid].entry_type == STGTY_STORAGE]
        head = min(storages) if storages else min(members)
        disconnected_heads.append(
            {'head': head, 'members': sorted(members)})

    # 重名分组 (CFB 名称比较不区分大小写), 重名不等于同一对象
    name_groups = defaultdict(list)
    for entry in objects:
        name_groups[entry.name.lower()].append(entry.sid)
    duplicate_names = {key: sorted(sids) for key, sids in name_groups.items()
                       if len(sids) > 1}
    colliding_sids = set()
    for sids in duplicate_names.values():
        colliding_sids.update(sids)

    # 逐 SID 定类
    sid_records = {}
    for entry in entries:
        sid = entry.sid
        record = {
            'sid': sid,
            'name': entry.name,
            'entry_type': entry.type_label,
            'left': _ptr(entry.sid_left),
            'right': _ptr(entry.sid_right),
            'child': _ptr(entry.sid_child),
            'isect_start': entry.isect_start,
            'size': entry.size_low,
            'parent_sids': sorted(parents.get(sid, [])),
            'evidence_paths': [],
            'notes': [],
        }
        if entry.entry_type == STGTY_EMPTY:
            record['category'] = SLOT_EMPTY
            sid_records[sid] = record
            continue
        if not entry.valid_type:
            record['category'] = SLOT_INVALID
            record['notes'].append('目录项类型不在规范允许范围内')
            sid_records[sid] = record
            continue

        if sid == 0:
            record['category'] = ROOT_REACHABLE
            record['evidence_paths'].append([0])
        elif sid in cyclic_sids or len(parents.get(sid, ())) >= 2:
            record['category'] = MULTIPLE_ATTRIBUTION
            for owner in parents.get(sid, ()):
                if owner in root_reachable:
                    path = shortest_path(adjacency, 0, sid)
                else:
                    path = shortest_path(adjacency, owner, sid)
                if path:
                    record['evidence_paths'].append(path)
            if sid in cyclic_sids:
                record['notes'].append('卷入环或同一存储内的重复引用')
                for cycle_path in cycle_evidence.get(sid, ()):
                    if cycle_path not in record['evidence_paths']:
                        record['evidence_paths'].append(cycle_path)
        elif sid in root_reachable:
            record['category'] = ROOT_REACHABLE
            path = shortest_path(adjacency, 0, sid)
            if path:
                record['evidence_paths'].append(path)
        else:
            record['category'] = DISCONNECTED_ONLY
            owner_list = parents.get(sid, ())
            if owner_list:
                path = shortest_path(adjacency, owner_list[0], sid)
                if path:
                    record['evidence_paths'].append(path)
            else:
                record['notes'].append('目录区存在但无任何存储引用它')

        if sid in colliding_sids:
            record['notes'].append('与其他 SID 重名, 按 SID 区分而非按名称合并')
        sid_records[sid] = record

    summary = {ROOT_REACHABLE: 0, DISCONNECTED_ONLY: 0,
               MULTIPLE_ATTRIBUTION: 0, SLOT_EMPTY: 0, SLOT_INVALID: 0}
    for record in sid_records.values():
        summary[record['category']] += 1

    return {
        'total_slots': total,
        'summary': dict(summary),
        'entries': sid_records,
        'edges': [{'owner': o, 'child': c, 'role': r}
                  for (o, c, r) in unique_edges],
        'root_reachable': sorted(root_reachable),
        'disconnected_trees': disconnected_heads,
        'duplicate_names': duplicate_names,
        'anomalies': anomalies,
        'olefile_parsing_issues': [
            {'type': exc.__name__, 'message': msg}
            for exc, msg in parsing_issues],
    }


def audit_file(path):
    with open(path, 'rb') as fp:
        data = fp.read()
    result = audit_data(data)
    result['source_file'] = path
    return result


def render_markdown(result):
    entries = result['entries']
    lines = []
    lines.append(u'# OLE 目录项缺失清点报告')
    lines.append(u'')
    lines.append(u'源文件: `%s`' % result.get('source_file', u'<内存数据>'))
    lines.append(u'目录流槽位总数: %d' % result['total_slots'])
    lines.append(u'')
    lines.append(u'## 分类汇总')
    lines.append(u'')
    for category in (ROOT_REACHABLE, DISCONNECTED_ONLY,
                     MULTIPLE_ATTRIBUTION, SLOT_EMPTY, SLOT_INVALID):
        lines.append(u'- %s: %d 项' % (
            CATEGORY_LABEL[category], result['summary'].get(category, 0)))
    lines.append(u'')

    lines.append(u'## 按目录项编号 (SID) 总表')
    lines.append(u'')
    lines.append(u'| SID | 名称 | 类型 | 归类 | 父项 SID | 备注 |')
    lines.append(u'| --- | --- | --- | --- | --- | --- |')
    for sid in sorted(entries):
        record = entries[sid]
        notes = u'; '.join(record['notes'])
        parents = u','.join(str(p) for p in record['parent_sids']) or u'-'
        lines.append(u'| %d | %r | %s | %s | %s | %s |' % (
            sid, record['name'], record['entry_type'],
            CATEGORY_LABEL[record['category']], parents, notes))
    lines.append(u'')

    def section(title, category):
        rows = [r for r in entries.values() if r['category'] == category]
        lines.append(u'## %s' % title)
        lines.append(u'')
        if not rows:
            lines.append(u'(无)')
            lines.append(u'')
            return
        for record in sorted(rows, key=lambda r: r['sid']):
            lines.append(u'- SID%d %r (类型: %s, 父项: %s)' % (
                record['sid'], record['name'], record['entry_type'],
                u','.join(str(p) for p in record['parent_sids']) or u'-'))
            for path in record['evidence_paths']:
                lines.append(u'  - 证据路径: %s' %
                             ' -> '.join('SID%d' % s for s in path))
            for note in record['notes']:
                lines.append(u'  - 说明: %s' % note)
        lines.append(u'')

    section(u'一、能从根走到的对象', ROOT_REACHABLE)
    section(u'二、只在断开存储树里出现的对象', DISCONNECTED_ONLY)
    section(u'三、有多种可能归属的对象', MULTIPLE_ATTRIBUTION)

    lines.append(u'## 断开的存储树')
    lines.append(u'')
    if not result['disconnected_trees']:
        lines.append(u'(无)')
    for tree in result['disconnected_trees']:
        lines.append(u'- 树头 SID%d, 成员 SID: %s' % (
            tree['head'], u', '.join(str(s) for s in tree['members'])))
    lines.append(u'')

    lines.append(u'## 绕回自身 / 重复引用的有限证据路径')
    lines.append(u'')
    cycle_items = result['anomalies']['cycle'] + \
        result['anomalies']['repeat']
    if not cycle_items:
        lines.append(u'(无)')
    for item in cycle_items:
        path = item['evidence_path']
        lines.append(u'- 归属存储 SID%d, 回到 SID%d: %s' % (
            item['owner'], item['sid'],
            ' -> '.join('SID%d' % s for s in path)))
    lines.append(u'')

    lines.append(u'## 重名分组 (重名不等于同一对象)')
    lines.append(u'')
    if not result['duplicate_names']:
        lines.append(u'(无)')
    for key, sids in sorted(result['duplicate_names'].items()):
        names = u', '.join('SID%d%r' % (sid, entries[sid]['name'])
                           for sid in sids)
        lines.append(u'- 名称键 %r: %s' % (key, names))
    lines.append(u'')

    lines.append(u'## 指针异常与空 / 无效槽位')
    lines.append(u'')
    anoms = result['anomalies']
    anomaly_lines = []
    for item in anoms['dangling']:
        anomaly_lines.append(u'- 越界指针: 存储 SID%d 的 %s 指向 SID%s' % (
            item['owner'], item['role'], item['sid']))
    for item in anoms['empty_ptr']:
        anomaly_lines.append(u'- 指向空槽: 存储 SID%d 的 %s 指向 SID%d' % (
            item['owner'], item['role'], item['sid']))
    for item in anoms['stream_with_child']:
        anomaly_lines.append(u'- 流对象不应带 child 指针: SID%d -> SID%d' % (
            item['sid'], item['sid_child']))
    for item in anoms['invalid_type']:
        anomaly_lines.append(u'- 无效目录项类型: SID%d type=%d' % (
            item['sid'], item['entry_type']))
    empties = [sid for sid, r in entries.items()
               if r['category'] == SLOT_EMPTY]
    if empties:
        anomaly_lines.append(u'- 空槽位: %s' %
                             u', '.join('SID%d' % s for s in sorted(empties)))
    lines.extend(anomaly_lines or [u'(无)'])
    lines.append(u'')
    return u'\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=u'OLE 复合文档目录项缺失清点 (按 SID 分类)')
    parser.add_argument('ole_file', help=u'待清点的 OLE/CFB 原文件')
    parser.add_argument('--md', default='ole_audit_report.md',
                        help=u'Markdown 报告输出路径')
    parser.add_argument('--json', default='ole_audit_report.json',
                        help=u'JSON 明细输出路径')
    args = parser.parse_args(argv)

    result = audit_file(args.ole_file)
    report = render_markdown(result)
    with open(args.md, 'w', encoding='utf-8') as fp:
        fp.write(report)
    with open(args.json, 'w', encoding='utf-8') as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2, sort_keys=True)
    print(report)
    print(u'\n报告已写入: %s' % args.md)
    print(u'JSON 明细已写入: %s' % args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
