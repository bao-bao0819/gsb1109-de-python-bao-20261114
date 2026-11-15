"""
Interface for the Polyline library.
"""
from .polyline import decode, encode
from .merge import (
    MAX_MERGE_FRAGMENTS,
    MergeCandidate,
    merge,
    merge_all,
    merge_candidates,
    merge_encoded,
    merge_polylines,
    merge_segments,
    shortest_merged_route,
)

__all__ = [
    'decode',
    'encode',
    'MAX_MERGE_FRAGMENTS',
    'MergeCandidate',
    'merge',
    'merge_all',
    'merge_candidates',
    'merge_encoded',
    'merge_polylines',
    'merge_segments',
    'shortest_merged_route',
]
