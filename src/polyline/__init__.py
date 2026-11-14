"""
Interface for the Polyline library.
"""
from .polyline import (CONFIRMED, RESTARTABLE, SUSPICIOUS, AuditReport,
                       Checkpoint, Segment, audit, decode, encode)

__all__ = ['decode', 'encode', 'audit', 'Checkpoint', 'Segment', 'AuditReport',
           'CONFIRMED', 'SUSPICIOUS', 'RESTARTABLE']
