#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ODS 可见记录导出 + 隐藏行原因报告（单一入口）。

每个营业部只交 ODS 中可见的记录；总部另要一份能解释被隐藏行的明细。
本工具一次性产出两份结果：

1. 可见记录（CSV）：只包含既未被手工隐藏、也未落在收起的行组里的逻辑行，
   每行带整表逻辑行号。
2. 隐藏行明细（报告）：按逻辑行区间列出被隐藏的行及原因
   （手工隐藏 / 第 N 层行组收起）。

规则说明：
- ``table:number-rows-repeated`` 按声明次数展开为同等数量的逻辑行，
  行号连续递增，因此分两段导出再拼接，行号与整表导出完全一致。
- 行组可多级嵌套；只有所有外层组都处于展开状态时记录才可见。
  只展开内层组而外层仍收起时，其中的记录依然算作隐藏。
- 默认跳过完全空白且可见的行（LibreOffice 常把尾部空行重复声明几十万次），
  需要原样导出时加 ``--include-empty``。

用法：
    python ods_visible_export.py 文件.ods [--table 表名]
        [--from-row N] [--to-row M] [--include-empty]
        [--csv 输出.csv] [--report 输出.txt]

默认 CSV 写 stdout，报告写 stderr；指定 --csv 后报告默认改写 stdout。
"""
import argparse
import csv
import sys

from odf.opendocument import load
from odf.table import Table
from odf.teletype import extractText
from odf.namespaces import TABLENS

Q_ROW = (TABLENS, 'table-row')
Q_ROW_GROUP = (TABLENS, 'table-row-group')
Q_HEADER_ROWS = (TABLENS, 'table-header-rows')
Q_ROWS = (TABLENS, 'table-rows')
Q_CELL = (TABLENS, 'table-cell')
Q_COVERED_CELL = (TABLENS, 'covered-table-cell')

HIDDEN_VISIBILITIES = ('collapse', 'filter')

REASON_MANUAL = '手工隐藏'
REASON_GROUP = '第%d层行组收起'


class LogicalRow(object):
    """一个逻辑行：整表行号 + 单元格文本 + 隐藏原因列表。"""
    __slots__ = ('number', 'cells', 'reasons')

    def __init__(self, number, cells, reasons):
        self.number = number
        self.cells = cells
        self.reasons = reasons

    @property
    def visible(self):
        return not self.reasons


class HiddenRange(object):
    """一段连续逻辑行，隐藏原因完全相同。"""
    __slots__ = ('start', 'end', 'reasons')

    def __init__(self, start, end, reasons):
        self.start = start
        self.end = end
        self.reasons = reasons

    def clipped(self, lo, hi):
        start = max(self.start, lo)
        end = min(self.end, hi)
        if start > end:
            return None
        return HiddenRange(start, end, self.reasons)

    def __repr__(self):
        return 'HiddenRange(%d, %d, %r)' % (self.start, self.end, self.reasons)


def _as_int(value, default=1):
    if value is None:
        return default
    return int(value)


def _is_displayed(value):
    return str(value).strip().lower() not in ('false', '0')


def _row_cells(row_element):
    """展开 table:number-columns-repeated，去掉尾部空白单元格。"""
    cells = []
    for child in row_element.childNodes:
        qname = getattr(child, 'qname', None)
        if qname == Q_CELL:
            repeat = _as_int(child.getAttribute('numbercolumnsrepeated'))
            text = extractText(child)
            cells.extend([text] * repeat)
        elif qname == Q_COVERED_CELL:
            repeat = _as_int(child.getAttribute('numbercolumnsrepeated'))
            cells.extend([''] * repeat)
    while cells and not cells[-1].strip():
        cells.pop()
    return cells


def _row_reasons(row_element, groups):
    reasons = []
    visibility = row_element.getAttribute('visibility')
    if visibility in HIDDEN_VISIBILITIES:
        reasons.append(REASON_MANUAL)
    for depth, displayed in groups:
        if not displayed:
            reasons.append(REASON_GROUP % depth)
    return reasons


def iter_logical_rows(container, groups=(), counter=None):
    """按文档顺序产出逻辑行，展开重复声明，继承行组收起状态。

    groups: 外层行组栈，元素为 (层级, 是否展开)，层级从 1 起。
    counter: 单元素列表，整表逻辑行号（从 1 起）。
    """
    if counter is None:
        counter = [1]
    for child in container.childNodes:
        qname = getattr(child, 'qname', None)
        if qname == Q_ROW:
            repeat = _as_int(child.getAttribute('numberrowsrepeated'))
            reasons = _row_reasons(child, groups)
            cells = _row_cells(child)
            for _ in range(repeat):
                yield LogicalRow(counter[0], cells, reasons)
                counter[0] += 1
        elif qname == Q_ROW_GROUP:
            displayed = _is_displayed(child.getAttribute('display'))
            nested = groups + ((len(groups) + 1, displayed),)
            for logical_row in iter_logical_rows(child, nested, counter):
                yield logical_row
        elif qname in (Q_HEADER_ROWS, Q_ROWS):
            for logical_row in iter_logical_rows(child, groups, counter):
                yield logical_row


def analyze_table(table, from_row=None, to_row=None, include_empty=False):
    """流式分析一张表，返回 (可见记录, 隐藏区间, 区间端点)。

    可见记录只保留非空（除非 include_empty）；隐藏区间按行号升序、
    原因相同且相邻即合并，并裁剪到 [from_row, to_row]。
    """
    lo = from_row if from_row is not None else 1
    hi = to_row  # None 表示不设上限
    visible = []
    ranges = []
    for logical_row in iter_logical_rows(table):
        number = logical_row.number
        if number < lo:
            continue
        if hi is not None and number > hi:
            break
        if logical_row.visible:
            if include_empty or any(cell.strip() for cell in logical_row.cells):
                visible.append(logical_row)
        else:
            if (ranges and ranges[-1].reasons == logical_row.reasons
                    and ranges[-1].end == number - 1):
                ranges[-1].end = number
            else:
                ranges.append(HiddenRange(number, number, logical_row.reasons))
    return visible, ranges


def format_report(table_name, hidden_ranges, out):
    if not hidden_ranges:
        out.write('表 %s：没有隐藏行\n' % table_name)
        return
    out.write('表 %s：\n' % table_name)
    for item in hidden_ranges:
        if item.start == item.end:
            span = '第%d行' % item.start
        else:
            span = '第%d-%d行' % (item.start, item.end)
        out.write('  %s：%s\n' % (span, '；'.join(item.reasons)))


def write_visible_csv(table_name, visible_rows, out, multi_table):
    width = max([len(row.cells) for row in visible_rows] or [0])
    header = (['table'] if multi_table else []) + ['row'] + \
        ['c%d' % i for i in range(1, width + 1)]
    writer = csv.writer(out)
    writer.writerow(header)
    for row in visible_rows:
        cells = list(row.cells) + [''] * (width - len(row.cells))
        prefix = [table_name] if multi_table else []
        writer.writerow(prefix + [row.number] + cells)


def find_tables(doc, table_name=None):
    tables = []
    for table in doc.spreadsheet.getElementsByType(Table):
        name = table.getAttribute('name') or ''
        if table_name is None or name == table_name:
            tables.append((name, table))
    return tables


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='导出 ODS 可见记录（CSV）并生成隐藏行原因报告。')
    parser.add_argument('ods_file', help='输入的 .ods 文件')
    parser.add_argument('--table', help='只处理指定表名（默认全部表）')
    parser.add_argument('--from-row', type=int, default=None,
                        help='起始逻辑行号（含），默认 1')
    parser.add_argument('--to-row', type=int, default=None,
                        help='结束逻辑行号（含），默认到表尾')
    parser.add_argument('--include-empty', action='store_true',
                        help='可见的空白行也原样导出')
    parser.add_argument('--csv', help='CSV 输出路径（默认 stdout）')
    parser.add_argument('--report', help='报告输出路径（默认：未指定 --csv 时为 stderr，否则为 stdout）')
    args = parser.parse_args(argv)

    doc = load(args.ods_file)
    tables = find_tables(doc, args.table)
    if not tables:
        parser.error('没有找到表：%s' % (args.table or '(空文档)'))

    csv_out = open(args.csv, 'w', newline='', encoding='utf-8') \
        if args.csv else sys.stdout
    if args.report:
        report_out = open(args.report, 'w', encoding='utf-8')
    else:
        report_out = sys.stdout if args.csv else sys.stderr

    multi_table = len(tables) > 1
    try:
        for name, table in tables:
            visible, hidden = analyze_table(
                table, from_row=args.from_row, to_row=args.to_row,
                include_empty=args.include_empty)
            write_visible_csv(name, visible, csv_out, multi_table)
            format_report(name, hidden, report_out)
    finally:
        if args.csv:
            csv_out.close()
        if args.report:
            report_out.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
