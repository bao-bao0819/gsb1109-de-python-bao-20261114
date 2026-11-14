"""
Interface for the Polyline library.
"""
from .polyline import decode, encode
from .suffix import (
    Branch,
    MakeupPackage,
    Receipt,
    ReceiveResult,
    ReceiveStatus,
    Rejection,
    TripArchive,
    decode_suffix,
    encode_suffix,
    extend_identity,
    prefix_identity,
)

__all__ = [
    'decode', 'encode',
    'decode_suffix', 'encode_suffix', 'prefix_identity', 'extend_identity',
    'MakeupPackage', 'TripArchive', 'ReceiveResult', 'ReceiveStatus',
    'Receipt', 'Branch', 'Rejection',
]
