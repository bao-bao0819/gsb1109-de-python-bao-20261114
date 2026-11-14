"""
The three legacy tables and their cross-table key relationships.

Tables (dBase III, character keys, whitespace trimmed on compare)::

    CUST  customers.dbf   CUSTID C(6)  PK
                          NAME   C(20)
                          MGRID  C(6)  -> CUST.CUSTID   (self reference)

    ORD   orders.dbf      ORDERID C(8) PK
                          CUSTID  C(6) -> CUST.CUSTID

    ITM   items.dbf       ITEMID  C(10) PK
                          ORDERID C(8)  -> ORD.ORDERID
                          CUSTID  C(6)  -> CUST.CUSTID

A blank / whitespace-only foreign key means "no reference".
"""

from .model import Ref, TableSpec, make_schema


CUST = TableSpec(
    name="CUST",
    filename="customers.dbf",
    key_fields=("CUSTID",),
)
ORD = TableSpec(
    name="ORD",
    filename="orders.dbf",
    key_fields=("ORDERID",),
)
ITM = TableSpec(
    name="ITM",
    filename="items.dbf",
    key_fields=("ITEMID",),
)

SCHEMA = make_schema(
    tables=(CUST, ORD, ITM),
    refs=(
        Ref(
            name="ORD_CUST",
            child_table="ORD",
            child_fields=("CUSTID",),
            parent_table="CUST",
            parent_fields=("CUSTID",),
        ),
        Ref(
            name="ITM_ORD",
            child_table="ITM",
            child_fields=("ORDERID",),
            parent_table="ORD",
            parent_fields=("ORDERID",),
        ),
        Ref(
            name="ITM_CUST",
            child_table="ITM",
            child_fields=("CUSTID",),
            parent_table="CUST",
            parent_fields=("CUSTID",),
        ),
        Ref(
            name="CUST_MGR",
            child_table="CUST",
            child_fields=("MGRID",),
            parent_table="CUST",
            parent_fields=("CUSTID",),
        ),
    ),
)

# Field specs used when (re)building sample tables.
FIELD_SPECS = {
    "CUST": "CUSTID C(6); NAME C(20); MGRID C(6)",
    "ORD": "ORDERID C(8); CUSTID C(6)",
    "ITM": "ITEMID C(10); ORDERID C(8); CUSTID C(6)",
}
