"""
Interface for the Polyline library.
"""
from .feasibility import (
    CellRange,
    Comparison,
    PointResult,
    cell_range,
    compare,
    decode_grid,
    render_comparison,
)
from .polyline import decode, encode

__all__ = [
    'decode',
    'encode',
    'decode_grid',
    'cell_range',
    'compare',
    'render_comparison',
    'CellRange',
    'PointResult',
    'Comparison',
]
