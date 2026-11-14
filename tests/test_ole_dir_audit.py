# -*- coding: utf-8 -*-
"""
tests/test_ole_dir_audit.py
===========================

对 tools/ole_dir_audit.py 的目录项清点逻辑做测试。

损坏样本不手工捏造整个 OLE 文件, 而是以仓库自带的真实复合文档
tests/images/test-ole-file.doc 为底, 仅改写目录流里若干 128 字节槽位的
字段 (类型 / 红黑树指针 / child 指针 / 名称), 从而精确构造:
  - 根树整体断开;
  - 断开存储树中的孤立对象;
  - 同一对象被两个存储引用 (多种可能归属);
  - 存储 child 指针绕回自身;
  - 红黑树左右指针重复指向同一 SID;
  - 不同 SID 重名。
"""

from __future__ import print_function

import os
import struct
import sys
import unittest

_thismodule_dir = os.path.normpath(os.path.abspath(os.path.dirname(__file__)))
_parent_dir = os.path.normpath(os.path.join(_thismodule_dir, '..'))
_tools_dir = os.path.normpath(os.path.join(_parent_dir, 'tools'))
for path in (_parent_dir, _tools_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

import olefile
import ole_dir_audit as audit

OLE_FILE = os.path.join(_parent_dir, 'tests', 'images', 'test-ole-file.doc')
NOSTREAM = 0xFFFFFFFF
DIRENTRY_SIZE = 128

# 目录项内部字段偏移 (对应 STRUCT_DIRENTRY = '<64sHBBIII16sIQQIII')
OFF_NAME = 0
OFF_NAMELENGTH = 64
OFF_TYPE = 66
OFF_LEFT = 68
OFF_RIGHT = 72
OFF_CHILD = 76


def load_image():
    with open(OLE_FILE, 'rb') as fp:
        return bytearray(fp.read())


def directory_offsets(image):
    """返回 {SID: 该目录项在文件镜像中的字节偏移}。"""
    ole = olefile.OleFileIO(bytes(image), raise_defects=100)
    try:
        sector_size = ole.sectorsize
        chain = []
        sector = ole.first_dir_sector
        seen = set()
        while sector not in (olefile.ENDOFCHAIN, olefile.NOSTREAM) \
                and sector not in seen:
            seen.add(sector)
            chain.append(sector)
            sector = ole.fat[sector]
        entries_per_sector = sector_size // DIRENTRY_SIZE
    finally:
        ole.close()
    offsets = {}
    sid = 0
    for sector in chain:
        base = (sector + 1) * sector_size
        for index in range(entries_per_sector):
            offsets[sid] = base + index * DIRENTRY_SIZE
            sid += 1
    return offsets


def patch_entry(image, offsets, sid, **fields):
    offset = offsets[sid]
    if 'entry_type' in fields:
        image[offset + OFF_TYPE] = fields['entry_type']
    if 'left' in fields:
        struct.pack_into('<I', image, offset + OFF_LEFT, fields['left'])
    if 'right' in fields:
        struct.pack_into('<I', image, offset + OFF_RIGHT, fields['right'])
    if 'child' in fields:
        struct.pack_into('<I', image, offset + OFF_CHILD, fields['child'])
    if 'name' in fields:
        encoded = fields['name'].encode('utf-16-le')
        image[offset + OFF_NAME:offset + OFF_NAME + 64] = b'\x00' * 64
        image[offset + OFF_NAME:offset + OFF_NAME + len(encoded)] = encoded
        struct.pack_into('<H', image, offset + OFF_NAMELENGTH,
                         len(encoded) + 2)


def categories(result):
    return {sid: record['category']
            for sid, record in result['entries'].items()}


class CleanFileTests(unittest.TestCase):

    def test_all_real_streams_root_reachable(self):
        result = audit.audit_data(bytes(load_image()))
        cats = categories(result)
        for sid in range(6):
            self.assertEqual(cats[sid], audit.ROOT_REACHABLE,
                             'SID%d 应当根可达' % sid)
        self.assertEqual(cats[6], audit.SLOT_EMPTY)
        self.assertEqual(cats[7], audit.SLOT_EMPTY)
        self.assertEqual(result['summary'][audit.DISCONNECTED_ONLY], 0)
        self.assertEqual(result['summary'][audit.MULTIPLE_ATTRIBUTION], 0)

    def test_every_slot_is_classified_once(self):
        result = audit.audit_data(bytes(load_image()))
        self.assertEqual(sorted(result['entries']),
                         list(range(result['total_slots'])))
        valid = {audit.ROOT_REACHABLE, audit.DISCONNECTED_ONLY,
                 audit.MULTIPLE_ATTRIBUTION, audit.SLOT_EMPTY,
                 audit.SLOT_INVALID}
        for record in result['entries'].values():
            self.assertIn(record['category'], valid)


class DisconnectedTreeTests(unittest.TestCase):

    def test_root_tree_cut_moves_all_streams_to_disconnected(self):
        image = load_image()
        offsets = directory_offsets(image)
        # 根存储不再挂任何红黑树 => SID1..5 整体成为断开的存储树
        patch_entry(image, offsets, 0, child=NOSTREAM)
        result = audit.audit_data(bytes(image))
        cats = categories(result)
        self.assertEqual(cats[0], audit.ROOT_REACHABLE)
        for sid in range(1, 6):
            self.assertEqual(cats[sid], audit.DISCONNECTED_ONLY,
                             'SID%d 应只出现在断开存储树' % sid)
        heads = [tree['head'] for tree in result['disconnected_trees']]
        # 该片段全是流、没有存储, 按最小 SID 取代表树头
        self.assertEqual(heads, [1])
        self.assertEqual(result['disconnected_trees'][0]['members'],
                         [1, 2, 3, 4, 5])

    def test_orphan_stream_without_parent(self):
        image = load_image()
        offsets = directory_offsets(image)
        patch_entry(image, offsets, 0, child=NOSTREAM)
        # 空槽 SID7 变成一个不被任何存储引用的流
        patch_entry(image, offsets, 7, entry_type=audit.STGTY_STREAM,
                     name='LostStream')
        result = audit.audit_data(bytes(image))
        record = result['entries'][7]
        self.assertEqual(record['category'], audit.DISCONNECTED_ONLY)
        self.assertEqual(record['parent_sids'], [])
        self.assertIn('无任何存储引用它', ' '.join(record['notes']))


class MultipleAttributionTests(unittest.TestCase):

    def _shared_child_image(self):
        image = load_image()
        offsets = directory_offsets(image)
        # SID6 (原空槽) 变成断开的存储, 与根树中的 SID1 共同引用 SID5
        patch_entry(image, offsets, 6, entry_type=audit.STGTY_STORAGE,
                     name='OrphanStore', child=5)
        return image, offsets

    def test_shared_child_is_multiple_attribution(self):
        image, _ = self._shared_child_image()
        result = audit.audit_data(bytes(image))
        cats = categories(result)
        self.assertEqual(cats[5], audit.MULTIPLE_ATTRIBUTION)
        record = result['entries'][5]
        # 红黑树成员的归属父项是存储本身: 根 SID0 与断开存储 SID6
        self.assertEqual(sorted(record['parent_sids']), [0, 6])
        # 两条归属都应能查到证据路径
        path_owners = {path[0] for path in record['evidence_paths']}
        self.assertIn(0, path_owners)
        self.assertIn(6, path_owners)

    def test_disconnected_storage_head_classified(self):
        image, _ = self._shared_child_image()
        result = audit.audit_data(bytes(image))
        self.assertEqual(categories(result)[6], audit.DISCONNECTED_ONLY)
        heads = [tree['head'] for tree in result['disconnected_trees']]
        self.assertEqual(heads, [6])


class CycleAndRepeatTests(unittest.TestCase):

    def test_storage_child_pointing_back_to_itself(self):
        image = load_image()
        offsets = directory_offsets(image)
        patch_entry(image, offsets, 0, child=NOSTREAM)
        # SID6 存储的 child 绕回自身
        patch_entry(image, offsets, 6, entry_type=audit.STGTY_STORAGE,
                     name='LoopStore', child=6)
        result = audit.audit_data(bytes(image))
        self.assertEqual(categories(result)[6],
                         audit.MULTIPLE_ATTRIBUTION)
        record = result['entries'][6]
        self.assertIn([6, 6], record['evidence_paths'])
        cycles = result['anomalies']['cycle']
        self.assertTrue(any(item['sid'] == 6
                            and item['evidence_path'] == [6, 6]
                            for item in cycles))

    def test_redblack_left_and_right_repeat_same_sid(self):
        image = load_image()
        offsets = directory_offsets(image)
        patch_entry(image, offsets, 0, child=NOSTREAM)
        # SID6 存储挂 SID7 流; SID7 的左右指针都指向自己 => 重复到达
        patch_entry(image, offsets, 6, entry_type=audit.STGTY_STORAGE,
                     name='Store', child=7)
        patch_entry(image, offsets, 7, entry_type=audit.STGTY_STREAM,
                     name='Repeated', left=7, right=7)
        result = audit.audit_data(bytes(image))
        self.assertEqual(categories(result)[7],
                         audit.MULTIPLE_ATTRIBUTION)
        evidence = [item for item in
                    result['anomalies']['repeat'] +
                    result['anomalies']['cycle']
                    if item['sid'] == 7]
        self.assertTrue(evidence)
        for item in evidence:
            # 证据路径必须有限且明确标出绕回点
            self.assertEqual(item['evidence_path'][0], 6)
            self.assertEqual(item['evidence_path'][-1], 7)
            self.assertLessEqual(len(item['evidence_path']), 4)


class DuplicateNameTests(unittest.TestCase):

    def test_same_name_different_sid_kept_distinct(self):
        image = load_image()
        offsets = directory_offsets(image)
        # SID7 取一个与 SID1 相同的名字, 但二者是不同 SID 的对象
        patch_entry(image, offsets, 7, entry_type=audit.STGTY_STREAM,
                     name='1Table')
        result = audit.audit_data(bytes(image))
        self.assertEqual(result['duplicate_names']['1table'], [1, 7])
        note_text = ' '.join(result['entries'][7]['notes'])
        self.assertIn('重名', note_text)
        self.assertEqual(categories(result)[1], audit.ROOT_REACHABLE)
        self.assertEqual(categories(result)[7], audit.DISCONNECTED_ONLY)


class ReportRenderingTests(unittest.TestCase):

    def test_markdown_and_json_cover_every_sid(self):
        result = audit.audit_data(bytes(load_image()))
        markdown = audit.render_markdown(result)
        for sid in result['entries']:
            self.assertIn('SID%d' % sid, markdown)
        for category_label in audit.CATEGORY_LABEL.values():
            self.assertIn(category_label, markdown)


if __name__ == '__main__':
    unittest.main()
