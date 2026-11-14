# -*- coding: utf-8 -*-
"""
tools/make_demo_damaged.py
==========================

以仓库自带的真实复合文档 tests/images/test-ole-file.doc 为底, 仅改写目录
槽位指针, 生成一个同时包含三种归类的损坏样本 reports/demo-damaged.doc:

  - 根树只保留 SID4, 并让 SID4 左挂 SID5;
  - 新存储 SID6 的断开红黑树覆盖 SID1/2/3, 并经 SID1 右挂到 SID5,
    于是 SID5 同时被根 SID0 和断开存储 SID6 引用 => 多种可能归属;
  - 新存储 SID7 的 child 绕回自身 => 有限证据路径 [SID7, SID7]。

该脚本只用于演示清点工具的分类能力, 不修改原文件。
"""

from __future__ import print_function

import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.normpath(os.path.join(_this_dir, '..'))
sys.path.insert(0, _this_dir)
sys.path.insert(0, os.path.join(_parent, 'tests'))

from test_ole_dir_audit import (  # noqa: E402
    load_image, directory_offsets, patch_entry, OLE_FILE)
import ole_dir_audit as audit  # noqa: E402

NOSTREAM = 0xFFFFFFFF


def main():
    image = load_image()
    offsets = directory_offsets(image)

    # 根树收缩为 SID4, SID4 左挂 SID5
    patch_entry(image, offsets, 0, child=4)
    patch_entry(image, offsets, 4, left=5, right=NOSTREAM)

    # 断开的存储 SID6: child=3, 红黑链 3(left=2) -> 2(left=1) -> 1(right=5)
    patch_entry(image, offsets, 6, entry_type=audit.STGTY_STORAGE,
                name='OrphanStore', child=3)
    patch_entry(image, offsets, 3, left=2, right=NOSTREAM)
    patch_entry(image, offsets, 2, left=1, right=NOSTREAM)
    patch_entry(image, offsets, 1, left=NOSTREAM, right=5)
    patch_entry(image, offsets, 5, left=NOSTREAM, right=NOSTREAM)

    # 存储 SID7 的 child 绕回自身
    patch_entry(image, offsets, 7, entry_type=audit.STGTY_STORAGE,
                name='LoopStore', child=7)

    out_path = os.path.join(_parent, 'reports', 'demo-damaged.doc')
    with open(out_path, 'wb') as fp:
        fp.write(bytes(image))
    print('损坏样本已生成: %s (基底 %s)' % (out_path, OLE_FILE))

    result = audit.audit_data(bytes(image))
    result['source_file'] = out_path
    report = audit.render_markdown(result)
    report_path = os.path.join(_parent, 'reports', 'report_demo-damaged.md')
    with open(report_path, 'w', encoding='utf-8') as fp:
        fp.write(report)
    print('演示报告已生成: %s' % report_path)


if __name__ == '__main__':
    main()
