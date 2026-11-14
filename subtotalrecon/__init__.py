# -*- coding: utf-8 -*-
"""Ingest financial tables with store details, city subtotals and a grand total.

The table lists one detail row per store, inserts a subtotal row per city,
and closes with a grand total row.  This package splits the sheet into
bottom-level details and the member set of every subtotal, so that
recomputing never counts the same detail twice.  Membership is derived
from the declared subtotal hierarchy (ODS row groups) matched against the
actual row order -- never from comparing amounts, because two adjacent
cities may legitimately total the same.  Stored subtotal values are kept
next to the recomputed ones so manual adjustments survive as an explicit
difference, and deleting a detail row reports exactly which city subtotal
and which grand total it feeds.
"""

from .model import Detail, Subtotal, GrandTotal, Impact, Ledger
from .parser import StructureError, parse_ods

__all__ = [
    "Detail",
    "Subtotal",
    "GrandTotal",
    "Impact",
    "Ledger",
    "StructureError",
    "parse_ods",
]
