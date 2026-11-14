"""
Interface for the Polyline library.
"""
from .polyline import (
    DecodedPoint,
    DecodeResult,
    DecoderState,
    FeedResult,
    IncrementalDecoder,
    PolylineConflictError,
    PolylineDecodeError,
    PolylineGapError,
    PolylineIncompleteError,
    PolylineStateError,
    decode,
    decode_chunk,
    decode_chunks,
    decode_incremental,
    decode_packets,
    decode_state,
    decode_stream,
    decode_with_provenance,
    encode,
)

PolylineDecoder = IncrementalDecoder
StreamingDecoder = IncrementalDecoder

__all__ = [
    'DecodedPoint',
    'DecodeResult',
    'DecoderState',
    'FeedResult',
    'IncrementalDecoder',
    'PolylineConflictError',
    'PolylineDecodeError',
    'PolylineDecoder',
    'PolylineGapError',
    'PolylineIncompleteError',
    'PolylineStateError',
    'StreamingDecoder',
    'decode',
    'decode_chunk',
    'decode_chunks',
    'decode_incremental',
    'decode_packets',
    'decode_state',
    'decode_stream',
    'decode_with_provenance',
    'encode',
]
