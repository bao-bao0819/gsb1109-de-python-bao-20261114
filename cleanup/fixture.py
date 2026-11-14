"""
Builds the three sample .dbf tables exercising every cleanup rule.

Run directly to (re)create the tables in a directory::

    python -m cleanup.fixture ./data

Scenario map (recno is the 0-based physical record number)::

    CUST customers.dbf (CUSTID PK, NAME, MGRID self-FK)
      #0  C00001 active                       anchor customer
      #1  C00002 active MGRID=C00001          active -> active
      #2  C00003 deleted                      held by active order/item
      #3  C00004 deleted MGRID=C00001         held through a marked order
      #4  C00005 deleted MGRID=C00006         } cycle, no outside use:
      #5  C00006 deleted MGRID=C00005         } released together
      #6  C00009 deleted                      } duplicate CUSTID, both
      #7  C00009 deleted                      } retained (ambiguous)
      #8  C00010 deleted                      dead pair with O1010
      #9  C00007 deleted                      unreferenced, removable

    ORD orders.dbf (ORDERID PK, CUSTID FK)
      #0 O1001 active CUSTID=C00003           holds C00003
      #1 O1002 active CUSTID=C00001
      #2 O1003 deleted CUSTID=C00004          held by active item I002
      #3 O1004 deleted CUSTID=C00001          unreferenced, removable
      #4 O1005 deleted CUSTID=""              blank FK, removable
      #5 O1009 active CUSTID=C00009           ambiguous reference
      #6 O1010 deleted CUSTID=C00010          dead pair with C00010
      #7 O1011 active CUSTID=C99999           dangling FK

    ITM items.dbf (ITEMID PK, ORDERID FK, CUSTID FK)
      #0 I001 active ORDERID=O1001 CUSTID=C00003
      #1 I002 active ORDERID=O1003 CUSTID=C00004  holds O1003 and C00004
      #2 I003 deleted ORDERID=O1001 CUSTID=""     unreferenced, removable
      #3 I004 deleted ORDERID=O1004 CUSTID=C00001 removable with O1004
      #4 I005 active ORDERID="" CUSTID=""         blank FKs, stays active
      #5 I006 deleted ORDERID=O10999 CUSTID=""    dangling, removable
"""

from __future__ import annotations

import os

import dbf

from .schema import FIELD_SPECS, SCHEMA

# table -> list of (field tuple, deleted?) in physical order
SCENARIO = {
    "CUST": [
        (("C00001", "Anchor", ""), False),
        (("C00002", "Second", "C00001"), False),
        (("C00003", "Held direct", ""), True),
        (("C00004", "Held via order", "C00001"), True),
        (("C00005", "Cycle A", "C00006"), True),
        (("C00006", "Cycle B", "C00005"), True),
        (("C00009", "Dup key one", ""), True),
        (("C00009", "Dup key two", ""), True),
        (("C00010", "Dead with order", ""), True),
        (("C00007", "Lonely marked", ""), True),
    ],
    "ORD": [
        (("O1001", "C00003"), False),
        (("O1002", "C00001"), False),
        (("O1003", "C00004"), True),
        (("O1004", "C00001"), True),
        (("O1005", ""), True),
        (("O1009", "C00009"), False),
        (("O1010", "C00010"), True),
        (("O1011", "C99999"), False),
    ],
    "ITM": [
        (("I001", "O1001", "C00003"), False),
        (("I002", "O1003", "C00004"), False),
        (("I003", "O1001", ""), True),
        (("I004", "O1004", "C00001"), True),
        (("I005", "", ""), False),
        (("I006", "O10999", ""), True),
    ],
}

# Expected analysis answers, used by the test suite.
EXPECTED_REMOVABLE = {
    "CUST": {4, 5, 8, 9},          # C00005, C00006, C00010, C00007
    "ORD": {3, 4, 6},              # O1004, O1005, O1010
    "ITM": {2, 3, 5},              # I003, I004, I006
}
EXPECTED_RETAINED = {
    "CUST": {2, 3, 6, 7},
    "ORD": {2},
    "ITM": set(),
}
EXPECTED_CYCLE = {("CUST", 4), ("CUST", 5)}
EXPECTED_AMBIGUOUS_TARGETS = {("CUST", 6), ("CUST", 7)}
EXPECTED_DANGLING = {
    ("ORD", 7, "ORD_CUST"),
    ("ITM", 5, "ITM_ORD"),
}


def build_fixture(data_dir):
    """Create the three tables under ``data_dir``; returns the directory."""

    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)

    for spec in SCHEMA.tables:
        path = os.path.join(data_dir, spec.filename)
        table = dbf.Table(
            path,
            field_specs=FIELD_SPECS[spec.name],
            dbf_type="db3",
            codepage="utf8",
            on_disk=True,
            overwrite=True,
        )
        table.open(dbf.READ_WRITE)
        try:
            for values, deleted in SCENARIO[spec.name]:
                table.append(tuple(values))
                if deleted:
                    dbf.delete(table[len(table) - 1])
        finally:
            table.close()
    return data_dir


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "./data"
    build_fixture(target)
    print("fixture tables written to %s" % os.path.abspath(target))
