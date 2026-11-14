#!/usr/bin/env python3
"""端到端演示:构造两张"换过导出工具"的季度档案 DBF,跑列映射核验。

场景设定:
  * 同一长业务名被两个工具截成不同短名(CUST_NAME vs CUSTOMERNA);
  * BALANCE_AMOUNT / BALANCE_ADJUST 撞前缀(BALANCE_AM / BALANCE_AD),
    且本季度两列样本值恰好相同 -> 必然歧义,只能发布其余部分;
  * LIMIT_AMT 声明宽度 14,新表有个撞前缀的 LIMIT_SHR N(8,2) 装不下,
    类型可表示范围会把这条候选边排除;
  * 新表行顺序被打乱,验证按业务键对齐而非按行号。
"""
import datetime
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import dbf

from column_match import match_tables

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')

OLD_SPEC = (
    'CUST_NO C(10); CUST_NAME C(40); CUST_ADDR C(60); '
    'BAL_AMT N(12,2); BAL_ADJ N(12,2); LIMIT_AMT N(14,2); '
    'OPEN_DT D; ACTIVE L; REMARK C(50); OLD_ONLY C(8)'
)
NEW_SPEC = (
    'CUSTNUM C(10); CUSTOMERNA C(40); CUSTOMERAD C(60); '
    'BALANCE_AM N(12,2); BALANCE_AD N(12,2); LIMITAMT N(14,2); '
    'LIMIT_SHR N(8,2); OPEN_DATE D; IS_ACTIVE L; REMARKTXT C(30); '
    'NEW_ONLY C(8)'
)


def fresh_table(path, spec):
    if os.path.exists(path):
        os.remove(path)
    table = dbf.Table(path, spec)
    table.open(dbf.READ_WRITE)
    return table


def build_data():
    os.makedirs(DATA, exist_ok=True)
    old_path = os.path.join(DATA, 'OLD_Q.DBF')
    new_path = os.path.join(DATA, 'NEW_Q.DBF')

    rng = random.Random(20261114)
    rows = []
    for i in range(60):
        amount = round(rng.uniform(100, 9999), 2)
        rows.append({
            'no': 'C%07d' % (1000 + i),
            'name': 'Customer %02d Ltd' % i,
            'addr': 'No.%d Archive Road, District %d' % (i, i % 9),
            'bal': amount,
            'adj': amount,                      # 本季度调整额恰等于余额
            'limit': round(rng.uniform(10, 99), 2),   # 样本很小,但声明宽 14
            'open': datetime.date(2020 + i % 5, 1 + i % 12, 1 + i % 28),
            'active': i % 3 != 0,
            'remark': 'Q1 note %d' % i,         # 实际长度 <= 30
            'old_only': 'X%06d' % i,
        })

    old = fresh_table(old_path, OLD_SPEC)
    for r in rows:
        old.append((r['no'], r['name'], r['addr'], r['bal'], r['adj'],
                    r['limit'], r['open'], r['active'], r['remark'],
                    r['old_only']))
    old.close()

    new = fresh_table(new_path, NEW_SPEC)
    for r in reversed(rows):                    # 打乱行序
        new.append((r['no'], r['name'], r['addr'], r['bal'], r['adj'],
                    r['limit'], None, r['open'], r['active'], r['remark'],
                    'Y%06d' % rows.index(r)))
    new.close()
    return old_path, new_path


def main():
    old_path, new_path = build_data()
    seed = {'CUST_NAME': 'CUSTOMERNA'}          # 人工确认的少量对照
    keys = [('CUST_NO', 'CUSTNUM')]             # 人工确认的业务键

    report = match_tables(old_path, new_path, keys, seed)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    published = {s: v['target'] for s, v in report['published'].items()}
    assert published.get('CUST_NAME') == 'CUSTOMERNA', '种子对照应发布'
    assert published.get('CUST_ADDR') == 'CUSTOMERAD', '唯一候选应发布'
    assert published.get('OPEN_DT') == 'OPEN_DATE'
    assert published.get('ACTIVE') == 'IS_ACTIVE'
    assert published.get('REMARK') == 'REMARKTXT', '样本可装入更短列,应发布'
    assert published.get('LIMIT_AMT') == 'LIMITAMT', \
        'LIMIT_SHR 声明范围不够,应被类型检查排除'
    assert 'BAL_AMT' not in published and 'BAL_ADJ' not in published, \
        '歧义列不应发布'

    assert report['unmatched_source_columns'] == ['OLD_ONLY']
    assert sorted(report['unmatched_target_columns']) == ['LIMIT_SHR', 'NEW_ONLY']

    assert len(report['ambiguous']) == 1, '应恰好一个歧义分量'
    comp = report['ambiguous'][0]
    assert sorted(comp['sources']) == ['BAL_ADJ', 'BAL_AMT']
    assert sorted(comp['targets']) == ['BALANCE_AD', 'BALANCE_AM']
    assert comp['equivalent_matchings'] == 2
    assert len(comp['min_checks']) == 1, '2x2 歧义只需核对 1 条候选边'
    assert report['checks_needed'] == comp['min_checks']

    # 模拟人工核对一条候选边后(作为新种子),歧义应完全消解
    seed2 = dict(seed, BAL_AMT='BALANCE_AM')
    report2 = match_tables(old_path, new_path, keys, seed2)
    published2 = {s: v['target'] for s, v in report2['published'].items()}
    assert published2.get('BAL_AMT') == 'BALANCE_AM'
    assert published2.get('BAL_ADJ') == 'BALANCE_AD', '核对一条后另一条应被锁定'
    assert not report2['ambiguous'], '核对后不应再有歧义'

    print('\n全部断言通过:必然映射已发布,歧义分量与最少核对集正确。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
