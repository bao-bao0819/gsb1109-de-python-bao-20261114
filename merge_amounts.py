#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
merge_amounts.py -- 把多年(字段规格变过的)金额表合并为一个 DBF。

对每个数值字段,根据各源表的字段声明 N(w,d) 和实际数值:

- 计算能无损容纳全部【已见值】的目标规格;
- 报告该规格是否同时也能容纳各源表【声明允许的全部未来值】;
- 对显式允许四舍五入到两位的字段(--round2),按 ROUND_HALF_UP 进位,
  进位后可能增加的整数宽度(如 999.995 -> 1000.00)也计入目标宽度;
- 目标宽度有上限(--max-width,默认 DBF 数值上限 20)时,在写出任何记录
  之前完成全部校验:若某行无法无损表达,列出该源行及最小所需规格并放弃
  整个写出 -- 绝不在写了一半时把某一行变成星号。
"""
from __future__ import print_function

import argparse
import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from math import floor

import dbf
from dbf.constants import Field as _FieldLayout

NUMERIC_TYPES = ('N', 'F')          # 带小数的数值字段
INTEGER_TYPES = ('I',)              # 整数字段

TWO_PLACES = Decimal('0.01')


class ExactDecimal(Decimal):
    """让 dbf 库按字段原文返回 Decimal(库默认会把数值转成 float,
    大数会丢精度, 如 1234567890123456.75 被读成 ...6.8)。"""

    def __new__(cls, value):
        if isinstance(value, bytes):
            value = value.decode('ascii')
        return Decimal.__new__(cls, value)


EXACT_TYPES = {'N': (ExactDecimal, type(None)),
               'F': (ExactDecimal, type(None))}   # (取值类, 空值类): 空值读为 None


def open_source(filename):
    """以精确数值方式只读打开源表。"""
    table = dbf.Table(filename, default_data_types=EXACT_TYPES)
    table.open(dbf.READ_ONLY)
    return table


class MergeError(Exception):
    """合并无法无损进行时抛出。"""


class OverflowProblem(object):
    """一行无法在目标最大宽度内无损表达的记录。"""

    def __init__(self, source, row, field, value, min_width, min_decimals):
        self.source = source
        self.row = row
        self.field = field
        self.value = value
        self.min_width = min_width
        self.min_decimals = min_decimals

    def __str__(self):
        return ('  %s 第 %d 行 字段 %s 值 %r -> 最小需要 N(%d,%d)'
                % (self.source, self.row, self.field, self.value,
                   self.min_width, self.min_decimals))


def ftype_symbol(field_type):
    """把 field_info 返回的字段类型统一成单字符字符串 ('N', 'C', ...)。"""
    if isinstance(field_type, bytes):
        return field_type.decode('ascii')
    if isinstance(field_type, str):
        return field_type
    symbol = getattr(field_type, 'symbol', None)
    if symbol is not None:
        return symbol
    return chr(field_type)


def as_decimal(value):
    """把字段值转成精确的 Decimal(源表以 ExactDecimal 读出时原样返回)。"""
    if isinstance(value, Decimal):
        return value
    return Decimal(repr(value))


def value_need(value, round2=False):
    """
    返回无损容纳该数值所需的 (整数位宽[含负号], 小数位数)。
    round2 为真时先按四舍五入(HALF_UP)保留两位,进位导致的整数变宽
    (如 999.995 -> 1000.00)会体现在返回的整数位宽里。
    """
    dec = as_decimal(value)
    if round2:
        dec = dec.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    text = format(dec, 'f')
    negative = text.startswith('-')
    if negative:
        text = text[1:]
    int_part, _, frac_part = text.partition('.')
    int_digits = len(int_part)
    if negative and dec != 0:
        int_digits += 1
    return int_digits, len(frac_part)


def spec_from_need(int_digits, decimals):
    """由 (整数位宽, 小数位数) 得到合法 DBF 规格的总宽度。"""
    if decimals:
        width = int_digits + 1 + decimals
        width = max(width, decimals + 2)    # dbf 库要求 decimals <= length - 2
    else:
        width = max(int_digits, 1)
    return width


def declared_need(length, decimals):
    """声明 N(w,d) 所能表达的最大整数位宽(含负号)与小数位数。"""
    if decimals:
        return length - decimals - 1, decimals
    return length, 0


class FieldPlan(object):
    """一个输出字段的合并计划。"""

    def __init__(self, name, ftype):
        self.name = name
        self.ftype = ftype
        self.round2 = False      # 是否允许四舍五入到两位
        self.seen = False        # 是否见过非空值
        self.int_digits = 0      # 已见值所需最大整数位宽(含负号)
        self.decimals = 0        # 已见值所需最大小数位数
        self.decl_int = 0        # 各源声明允许的最大整数位宽
        self.decl_dec = 0        # 各源声明允许的最大小数位数
        self.char_len = 0        # 字符字段的最大声明长度
        self.problems = []       # 超出目标最大宽度的源行

    @property
    def is_numeric(self):
        return self.ftype in NUMERIC_TYPES + INTEGER_TYPES

    @property
    def width(self):
        if self.ftype == 'C':
            return self.char_len
        if self.ftype in INTEGER_TYPES:
            return spec_from_need(self.int_digits, 0)
        return spec_from_need(self.int_digits, self.decimals)

    @property
    def decl_width(self):
        """要同时容纳全部声明允许值所需的总宽度。"""
        int_digits = max(self.int_digits, self.decl_int)
        decimals = max(self.decimals, self.decl_dec)
        return spec_from_need(int_digits, decimals)

    @property
    def decl_decimals(self):
        return max(self.decimals, self.decl_dec)

    @property
    def fits_declared(self):
        """目标规格是否也能容纳所有声明允许的未来值。"""
        return (self.int_digits >= self.decl_int
                and self.decimals >= self.decl_dec)

    def spec(self):
        if self.ftype == 'C':
            return '%s C(%d)' % (self.name, self.char_len)
        if self.ftype in INTEGER_TYPES:
            return '%s %s' % (self.name, self.ftype)
        if self.ftype in NUMERIC_TYPES:
            return '%s N(%d,%d)' % (self.name, self.width, self.decimals)
        return '%s %s' % (self.name, self.ftype)


def scan_sources(sources, round2=()):
    """
    扫描全部源表,返回按首次出现顺序排列的 FieldPlan 列表。
    只计算规格,不写出任何内容。
    """
    round2 = set(name.upper() for name in round2)
    plans = {}
    order = []
    for source in sources:
        table = open_source(source)
        try:
            for name in table.field_names:
                info = table.field_info(name)
                ftype = ftype_symbol(info.field_type)
                key = name.upper()
                plan = plans.get(key)
                if plan is None:
                    plan = plans[key] = FieldPlan(name, ftype)
                    order.append(key)
                elif plan.ftype != ftype:
                    raise MergeError(
                            '字段 %s 类型冲突: %s 中是 %s, 此前是 %s'
                            % (name, source, ftype, plan.ftype))
                plan.round2 = key in round2 and ftype in NUMERIC_TYPES
                if ftype in NUMERIC_TYPES:
                    decl_int, decl_dec = declared_need(info.length, info.decimal)
                    plan.decl_int = max(plan.decl_int, decl_int)
                    plan.decl_dec = max(plan.decl_dec, decl_dec)
                elif ftype in INTEGER_TYPES:
                    plan.decl_int = max(plan.decl_int, info.length)
                elif ftype == 'C':
                    plan.char_len = max(plan.char_len, info.length)
            numeric_names = [
                    name for name in table.field_names
                    if plans[name.upper()].is_numeric]
            for record in table:
                for name in numeric_names:
                    value = record[name]
                    if value is None:
                        continue
                    plan = plans[name.upper()]
                    plan.seen = True
                    need_int, need_dec = value_need(value, round2=plan.round2)
                    plan.int_digits = max(plan.int_digits, need_int)
                    plan.decimals = max(plan.decimals, need_dec)
        finally:
            table.close()
    result = [plans[key] for key in order]
    for plan in result:
        # 整列皆空的数值字段没有已见值可参考, 保留声明规格以保住未来容量
        if plan.is_numeric and not plan.seen:
            plan.int_digits = plan.decl_int
            plan.decimals = plan.decl_dec
    return result


def find_problems(plans, sources, max_width):
    """
    对合并后总宽超过 max_width 的数值字段,重新扫描源表,逐行找出
    在 max_width 内无法无损表达的记录(小数位数必须保住,能压缩的
    只有整数位宽),把问题行挂到对应 FieldPlan.problems 上。
    """
    at_risk = {}
    for plan in plans:
        if plan.is_numeric and plan.width > max_width:
            # 小数位数是无损底线;max_width 内最多还能容纳的整数位宽
            head = 1 + plan.decimals if plan.decimals else 0
            at_risk[plan.name.upper()] = (plan, max_width - head)
    if not at_risk:
        return
    for source in sources:
        table = open_source(source)
        try:
            names = [n for n in table.field_names if n.upper() in at_risk]
            if not names:
                continue
            for row, record in enumerate(table):
                for name in names:
                    value = record[name]
                    if value is None:
                        continue
                    plan, max_int = at_risk[name.upper()]
                    need_int, need_dec = value_need(value, round2=plan.round2)
                    if need_int > max_int:
                        plan.problems.append(OverflowProblem(
                                source, row, name, value,
                                spec_from_need(need_int, need_dec), need_dec))
        finally:
            table.close()


def report(plans, max_width, stream=None):
    """打印合并计划:目标规格、是否覆盖声明允许的未来值、问题行。"""
    stream = stream or sys.stdout
    stream.write('合并计划(目标最大宽度 %d):\n' % max_width)
    for plan in plans:
        if not plan.is_numeric:
            stream.write('  %s\n' % plan.spec())
            continue
        line = '  %s -- 无损容纳全部已见值' % plan.spec()
        if plan.width > max_width:
            line += '; 但超出目标最大宽度 %d, 该字段最小需要 N(%d,%d)' % (
                    max_width, plan.width, plan.decimals)
        if plan.fits_declared:
            line += '; 也能容纳所有声明允许的未来值'
        else:
            line += ('; 但不能容纳所有声明允许的未来值, 需要 N(%d,%d)'
                     % (plan.decl_width, plan.decl_decimals))
        stream.write(line + '\n')
    problems = [p for plan in plans for p in plan.problems]
    if problems:
        stream.write('以下源行在目标最大宽度 %d 内无法无损表达:\n' % max_width)
        for problem in problems:
            stream.write(str(problem) + '\n')
    return problems


def build_target_specs(plans):
    return '; '.join(plan.spec() for plan in plans)


def cell_value(plan, value):
    """写出前对单元格值的唯一变换:round2 字段四舍五入到两位。"""
    if value is None or plan.ftype not in NUMERIC_TYPES or not plan.round2:
        return value
    return as_decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def numeric_text(value, decimals):
    """数值在目标字段里的精确文本(右对齐前)。"""
    return format(as_decimal(value), '.%df' % decimals)


def library_would_mangle(value, text, plan):
    """判断 dbf 库的 update_numeric 是否无法把 value 写成 text。

    库先把值转成 float: 有效位超过约 15 位时会失真; 另外它对负数按
    floor 计算整数位宽(-999.99 被当成 -1000 的宽度), 可能误报溢出。
    """
    fval = float(value)
    max_int = plan.width - (plan.decimals + 1 if plan.decimals else 0)
    if len('%.0f' % floor(fval)) > max_int:
        return True
    return '%.*f' % (plan.decimals, fval) != text


def merge(target, sources, round2=(), max_width=20, plan_only=False,
          force=False, stream=None):
    """
    合并 sources 到 target。先完整校验,再一次性写出;
    有任何无法无损表达的行则不创建/不改动 target。
    """
    stream = stream or sys.stdout
    plans = scan_sources(sources, round2=round2)
    find_problems(plans, sources, max_width)
    problems = report(plans, max_width, stream=stream)
    if problems:
        raise MergeError(
                '有 %d 行无法无损表达, 已放弃写出(未创建/修改 %s)'
                % (len(problems), target))
    if plan_only:
        return plans
    if os.path.exists(target) and not force:
        raise MergeError('目标文件已存在: %s (用 --force 覆盖)' % target)
    out = dbf.Table(target, build_target_specs(plans), on_disk=True)
    out.open(dbf.READ_WRITE)
    patches = []        # (recno, plan, text): 库按 float 写会失真、需按原文补丁的格子
    try:
        for source in sources:
            table = open_source(source)
            try:
                for record in table:
                    row = {}
                    for plan in plans:
                        if plan.name not in table.field_names:
                            continue
                        value = cell_value(plan, record[plan.name])
                        if value is not None and plan.ftype in NUMERIC_TYPES:
                            text = numeric_text(value, plan.decimals)
                            if library_would_mangle(value, text, plan):
                                # 库会把该值先转 float 再格式化(大数失真),
                                # 对负数还按 floor 多算一位整数宽;
                                # 先写空白占位, 关闭后按精确文本直接补丁
                                patches.append((len(out), plan, text))
                                value = None
                        row[plan.name] = value
                    out.append(row)
            finally:
                table.close()
    except Exception:
        out.close()
        os.remove(target)           # 不留下写了一半的文件
        raise
    layout = dict((plan.name.upper(), out._meta[plan.name.upper()])
                  for plan in plans if plan.is_numeric)
    rec_start = out._meta.header.start
    rec_length = out._meta.header.record_length
    out.close()
    if patches:
        apply_patches(target, patches, layout, rec_start, rec_length)
    mismatches = verify(target, sources, plans)
    if mismatches:
        os.remove(target)
        raise MergeError(
                '写出校验失败, %d 个单元格与源值不符, 已删除 %s; 首例: %s'
                % (len(mismatches), target, mismatches[0]))
    stream.write('已写出 %s (%d 条记录)\n' % (target, _count(target)))
    return plans


def apply_patches(filename, patches, layout, rec_start, rec_length):
    """把 float 会写坏的格子按精确十进制文本直接写回文件。"""
    with open(filename, 'r+b') as fd:
        for recno, plan, text in patches:
            field = layout[plan.name.upper()]
            width = field[_FieldLayout.LENGTH]
            start = field[_FieldLayout.START]
            data = text.rjust(width).encode('ascii')
            if len(data) > width:
                raise MergeError(
                        '内部错误: 值 %s 放不进 %s' % (text, plan.spec()))
            fd.seek(rec_start + recno * rec_length + start)
            fd.write(data)


def verify(target, sources, plans):
    """重读目标表,逐格核对数值字段与(可能四舍五入后的)源值一致。"""
    numeric = [plan for plan in plans if plan.is_numeric]
    mismatches = []
    check = dbf.Table(target, default_data_types=EXACT_TYPES)
    check.open(dbf.READ_ONLY)
    try:
        recno = 0
        for source in sources:
            table = open_source(source)
            try:
                for record in table:
                    out_record = check[recno]
                    recno += 1
                    for plan in numeric:
                        if plan.name not in table.field_names:
                            continue
                        expected = cell_value(plan, record[plan.name])
                        if expected is None:
                            continue
                        actual = out_record[plan.name]
                        if actual is None or as_decimal(actual) != as_decimal(expected):
                            mismatches.append(
                                    '%s 第 %d 行 字段 %s: 期望 %r, 实际 %r'
                                    % (source, recno - 1, plan.name,
                                       expected, actual))
            finally:
                table.close()
    finally:
        check.close()
    return mismatches


def _count(filename):
    table = dbf.Table(filename)
    table.open(dbf.READ_ONLY)
    try:
        return len(table)
    finally:
        table.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
            description='把多年的金额 DBF 表无损合并为一个 DBF')
    parser.add_argument('target', help='目标 DBF 文件')
    parser.add_argument('sources', nargs='+', help='源 DBF 文件(按年)')
    parser.add_argument('--round2', nargs='*', default=(), metavar='FIELD',
            help='允许四舍五入到两位的字段名')
    parser.add_argument('--max-width', type=int, default=20,
            help='目标数值字段最大宽度(默认 20, DBF 上限)')
    parser.add_argument('--plan-only', action='store_true',
            help='只打印合并计划,不写出')
    parser.add_argument('--force', action='store_true',
            help='允许覆盖已存在的目标文件')
    args = parser.parse_args(argv)
    try:
        merge(args.target, args.sources, round2=args.round2,
              max_width=args.max_width, plan_only=args.plan_only,
              force=args.force)
    except MergeError as exc:
        sys.stderr.write('合并失败: %s\n' % exc)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
