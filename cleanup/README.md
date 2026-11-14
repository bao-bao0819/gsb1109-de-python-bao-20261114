# DBF deletion-mark cleanup

Referential-integrity aware cleanup for three legacy `.dbf` tables
whose deletion-marked rows may still be referenced by live child
records.

## Tables and cross-table keys

| Table | File | Key | Foreign keys |
| --- | --- | --- | --- |
| `CUST` | `customers.dbf` | `CUSTID` | `MGRID -> CUST.CUSTID` (self reference) |
| `ORD` | `orders.dbf` | `ORDERID` | `CUSTID -> CUST.CUSTID` |
| `ITM` | `items.dbf` | `ITEMID` | `ORDERID -> ORD.ORDERID`, `CUSTID -> CUST.CUSTID` |

Keys are character fields; trailing whitespace is trimmed on compare
and a blank / null foreign key means "no reference".

## Cleanup rules

1. Every row without a deletion marker always survives.
2. A marked row survives while at least one surviving row references
   it. References resolve to a fixed point, so a marked order held by
   an active item also keeps the marked customer it points at.
3. A foreign key matching several parent rows (duplicate parent key)
   conservatively holds **every** matching parent -- the child cannot
   prove which one it means.
4. A group of marked rows that only reference each other (a cycle with
   no incoming reference from a surviving row) is released together.
5. The maximum safe removal set is the largest set of marked rows that
   no surviving row depends on; the fixed-point analysis computes
   exactly that set.

## Usage

```sh
# (re)build the sample tables in ./data
python -m cleanup.cli --fixture ./data

# dry run: read-only analysis, hashes every file before and after
python -m cleanup.cli --data ./data

# physical cleanup (undelete kept rows, pack, re-mark kept rows)
python -m cleanup.cli --data ./data --apply
```

A dry run opens every table `READ_ONLY` and fails if any guarded file
(`.dbf` plus companion `.dbt`/`.fpt`) changes. The report lists:

- the maximum removable set, per table, with physical record numbers;
- every marked row retained and the exact surviving record(s) and
  relation(s) holding it back;
- cyclic groups released together;
- ambiguous duplicate-key references;
- dangling foreign keys (informational).

`--apply` verifies itself: after packing, the tables are reopened
read-only and the surviving `(table, key, marker)` multiset is compared
with the plan, and a fresh analysis must find no remaining removable
row. Rows kept despite their marker keep the marker after compaction.

## Layout

- `model.py` - read-only snapshot, graph build, fixed-point analysis,
  Tarjan cyclic components, file hashing.
- `schema.py` - the three tables and the four relations.
- `fixture.py` - reproducible sample tables covering every rule, plus
  the oracle expectations used by the tests.
- `apply.py` - the only component that writes to disk.
- `report.py` - plain-text report rendering.
- `cli.py` / `__main__.py` - command line entry point.
- `test_cleanup.py` - 18 pytest cases (rules, maximality, dry-run
  byte identity, apply round trip, idempotence).

Run the tests with:

```sh
python -m pytest cleanup/test_cleanup.py -v
```
