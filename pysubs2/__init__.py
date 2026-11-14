from . import cli, exceptions, formats, time
from .common import VERSION, Alignment, Color
from .exceptions import *
from .formats import whisper
from .ssaevent import SSAEvent
from .ssafile import SSAFile
from .ssastyle import SSAStyle
from .vttsegments import (
    QuarantinedVTTCue,
    SegmentedVTTImportResult,
    VTTSegment,
    import_vtt_segments,
)

__all__ = [
    "VERSION",
    "Alignment",
    "Color",
    "QuarantinedVTTCue",
    "SSAEvent",
    "SSAFile",
    "SSAStyle",
    "SegmentedVTTImportResult",
    "VTTSegment",
    "cli",
    "exceptions",
    "formats",
    "import_vtt_segments",
    "load",
    "load_from_whisper",
    "make_time",
    "time",
    "whisper",
]

#: Alias for :meth:`SSAFile.load()`.
load = SSAFile.load

#: Alias for :meth:`pysubs2.whisper.load_from_whisper()`.
load_from_whisper = whisper.load_from_whisper

#: Alias for :meth:`pysubs2.time.make_time()`.
make_time = time.make_time

#: Alias for `pysubs2.common.VERSION`.
__version__ = VERSION
