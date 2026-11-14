"""Scan an OFX archive, establish per-account proven coverage windows and
emit a backfill plan for the gaps.

Coverage rules
--------------
* A statement proves coverage of [start_date, end_date] only when the file
  explicitly declares both. A zero-transaction statement with a declared
  range still proves the full period was checked.
* A file with transactions but no declared range proves nothing about the
  days in between; it is reported as "unproven".

Planning rules
--------------
* The bank interface returns at most one month per request, so every
  planned request spans at most ``--max-span-days`` days (default 31).
* Requests may overlap already-covered territory (and each other) by at
  most ``--overlap-days`` days (default 3) to avoid boundary misses.
* The union of the planned requests must cover every gap, including across
  month boundaries, but a request never extends further than the allowed
  overlap into covered territory, so an already fully covered calendar
  month is never re-pulled wholesale.
"""

import argparse
import glob
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ofxparse import OfxParser  # noqa: E402

ACCOUNT_TYPES = {1: 'BANK', 2: 'CREDITCARD', 3: 'INVESTMENT'}


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def scan_archive(patterns):
    """Parse every matching OFX file.

    Returns (accounts, failures) where accounts maps an account id to a
    dict with proven ``windows`` ([(start, end, source)]), ``unproven``
    files and out-of-window transaction warnings.
    """
    accounts = defaultdict(lambda: {
        'type': None, 'orgs': set(), 'windows': [], 'unproven': [],
        'warnings': [],
    })
    failures = []
    files = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern))
        files.extend(matched if matched else [pattern])
    for fname in files:
        try:
            with open(fname, 'rb') as fh:
                ofx = OfxParser.parse(fh)
        except Exception as exc:
            failures.append((fname, '%s: %s' % (type(exc).__name__, exc)))
            continue
        for account in ofx.accounts:
            entry = accounts[account.account_id]
            entry['type'] = account.type
            org = getattr(getattr(account, 'institution', None), 'organization', None)
            if org:
                entry['orgs'].add(org)
            statement = account.statement
            start = as_date(getattr(statement, 'start_date', None))
            end = as_date(getattr(statement, 'end_date', None))
            if start and end:
                entry['windows'].append((start, end, fname))
                for txn in getattr(statement, 'transactions', []) or []:
                    txn_date = as_date(getattr(txn, 'date', None)
                                       or getattr(txn, 'tradeDate', None))
                    if txn_date and not (start <= txn_date <= end):
                        entry['warnings'].append(
                            '%s: transaction dated %s outside declared '
                            'window %s..%s' % (fname, txn_date, start, end))
            else:
                txn_dates = [as_date(getattr(t, 'date', None)
                                     or getattr(t, 'tradeDate', None))
                             for t in getattr(statement, 'transactions', []) or []]
                txn_dates = sorted(d for d in txn_dates if d)
                entry['unproven'].append((fname, txn_dates))
    return accounts, failures


def merge_windows(windows):
    """Merge [(start, end, source)] into sorted disjoint [(start, end)]."""
    merged = []
    for start, end, _source in sorted(windows):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def find_gaps(merged):
    """Interior gaps between merged windows, as inclusive (start, end)."""
    gaps = []
    for (prev_start, prev_end), (next_start, next_end) in zip(merged, merged[1:]):
        if next_start > prev_end + timedelta(days=1):
            gaps.append((prev_end + timedelta(days=1),
                         next_start - timedelta(days=1)))
    return gaps


def plan_backfill(gap_start, gap_end, max_span_days=31, overlap_days=3,
                  today=None, lower_bound=None, upper_bound=None):
    """Split one gap into fetch requests.

    Each request covers at most ``max_span_days`` days; consecutive
    requests overlap by ``overlap_days``; the first/last request may reach
    ``overlap_days`` into the neighbouring covered window (never before
    ``lower_bound`` nor after ``upper_bound``/today).
    """
    today = today or date.today()
    if gap_end > today:
        raise ValueError('gap %s..%s extends beyond today' % (gap_start, gap_end))
    first_start = gap_start - timedelta(days=overlap_days)
    if lower_bound and first_start < lower_bound:
        first_start = lower_bound
    hard_end = min(gap_end + timedelta(days=overlap_days), today)
    if upper_bound:
        hard_end = min(hard_end, upper_bound)
    step = max_span_days - overlap_days
    if step <= 0:
        raise ValueError('overlap must be smaller than the max span')
    chunks = []
    start = first_start
    while True:
        end = min(start + timedelta(days=max_span_days - 1), hard_end)
        chunks.append((start, end))
        if end >= gap_end:
            return chunks
        start += timedelta(days=step)


def covered_months(merged):
    """Set of (year, month) fully covered by merged windows."""
    months = set()
    for start, end in merged:
        year, month = start.year, start.month
        while (year, month) < (end.year, end.month) or (year, month) == (end.year, end.month):
            first = date(year, month, 1)
            if month == 12:
                nxt = date(year + 1, 1, 1)
            else:
                nxt = date(year, month + 1, 1)
            if first >= start and nxt - timedelta(days=1) <= end:
                months.add((year, month))
            year, month = nxt.year, nxt.month
    return months


def verify_plan(gaps, chunks, merged, max_span_days, overlap_days):
    """Assert the plan fills every gap without re-pulling covered months."""
    problems = []
    for gap_start, gap_end in gaps:
        day = gap_start
        for start, end in sorted(chunks):
            if end < day:
                continue
            if start > day:
                problems.append('gap %s..%s not covered from %s'
                                % (gap_start, gap_end, day))
                break
            day = end + timedelta(days=1)
            if day > gap_end:
                break
        if day <= gap_end:
            problems.append('gap %s..%s not covered after %s'
                            % (gap_start, gap_end, day - timedelta(days=1)))
    full_months = covered_months(merged)
    for start, end in chunks:
        if (end - start).days >= max_span_days:
            problems.append('request %s..%s exceeds %d days'
                            % (start, end, max_span_days))
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            first = date(year, month, 1)
            nxt = (date(year + 1, 1, 1) if month == 12
                   else date(year, month + 1, 1))
            if (first >= start and nxt - timedelta(days=1) <= end
                    and (year, month) in full_months):
                problems.append('request %s..%s re-pulls fully covered '
                                'month %04d-%02d' % (start, end, year, month))
            year, month = nxt.year, nxt.month
    return problems


def build_report(accounts, failures, max_span_days, overlap_days, today):
    lines = []
    all_problems = []
    for account_id in sorted(accounts):
        entry = accounts[account_id]
        kind = ACCOUNT_TYPES.get(entry['type'], str(entry['type']))
        orgs = '/'.join(sorted(entry['orgs'])) or '-'
        lines.append('=' * 72)
        lines.append('账户 %s  [%s]  机构: %s' % (account_id, kind, orgs))
        merged = merge_windows(entry['windows'])
        if merged:
            lines.append('已证明覆盖窗口:')
            for start, end in merged:
                days = (end - start).days + 1
                lines.append('  %s .. %s  (%d 天)' % (start, end, days))
        else:
            lines.append('已证明覆盖窗口: 无')
        for fname, txn_dates in entry['unproven']:
            span = ('%s..%s' % (txn_dates[0], txn_dates[-1])
                    if txn_dates else '无交易')
            lines.append('  未声明起止(不能证明覆盖): %s  交易日期 %s'
                         % (fname, span))
        for warning in entry['warnings']:
            lines.append('  警告: %s' % warning)
        gaps = find_gaps(merged)
        if not gaps:
            if merged:
                lines.append('缺口: 无(窗口连续或仅一段)')
                tail_start = merged[-1][1] + timedelta(days=1)
                if tail_start <= today:
                    lines.append('末尾窗口 %s 之后至今未覆盖(换账号后由新账户承接,'
                                 '旧账户不补采)' % merged[-1][1])
            else:
                lines.append('缺口: 无法判定(无任何已证明窗口, 需另行确认账户'
                             '存续期后全量补采)')
            continue
        lines.append('缺口与补采计划(单次<= %d 天, 回叠<= %d 天):'
                     % (max_span_days, overlap_days))
        for gap_start, gap_end in gaps:
            prev_start = max(s for s, e in merged if e < gap_start)
            next_end = min(e for s, e in merged if s > gap_end)
            lines.append('  缺口 %s .. %s (%d 天)'
                         % (gap_start, gap_end, (gap_end - gap_start).days + 1))
            try:
                chunks = plan_backfill(gap_start, gap_end, max_span_days,
                                       overlap_days, today,
                                       lower_bound=prev_start,
                                       upper_bound=next_end)
            except ValueError as exc:
                lines.append('    无法生成计划: %s' % exc)
                continue
            problems = verify_plan([(gap_start, gap_end)], chunks, merged,
                                   max_span_days, overlap_days)
            all_problems.extend(problems)
            for i, (start, end) in enumerate(chunks, 1):
                lines.append('    请求 %2d: %s .. %s (%d 天)'
                             % (i, start, end, (end - start).days + 1))
            if problems:
                for problem in problems:
                    lines.append('    校验失败: %s' % problem)
            else:
                lines.append('    校验通过: 并集填满缺口, 未整月重拉已覆盖年月')
    if failures:
        lines.append('=' * 72)
        lines.append('解析失败文件:')
        for fname, error in failures:
            lines.append('  %s: %s' % (fname, error))
    lines.append('=' * 72)
    lines.append('总体校验: %s' % ('全部通过' if not all_problems
                                   else '%d 处问题' % len(all_problems)))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='按账户整理 OFX 归档的已证明覆盖窗口并生成补采计划')
    parser.add_argument('files', nargs='+',
                        help='OFX 文件或 glob, 如 "tests/fixtures/*.ofx"')
    parser.add_argument('--max-span-days', type=int, default=31,
                        help='接口单次最大跨度(默认 31 天, 约一个月)')
    parser.add_argument('--overlap-days', type=int, default=3,
                        help='允许的回叠天数(默认 3)')
    parser.add_argument('--today', type=date.fromisoformat,
                        default=date.today(), help='基准日期 YYYY-MM-DD')
    args = parser.parse_args(argv)
    accounts, failures = scan_archive(args.files)
    print(build_report(accounts, failures, args.max_span_days,
                       args.overlap_days, args.today))


if __name__ == '__main__':
    main()
