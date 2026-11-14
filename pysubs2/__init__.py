from . import cli, exceptions, formats, time
from .common import VERSION, Alignment, Color
from .exceptions import *
from .formats import whisper
from .karaoke import (
    KaraokeAlignment,
    KaraokeConflict,
    KaraokeWord,
    align_event,
    align_file,
    align_text,
)
from .ssaevent import SSAEvent
from .ssafile import SSAFile
from .ssastyle import SSAStyle

__all__ = [
    "VERSION",
    "Alignment",
    "Color",
    "KaraokeAlignment",
    "KaraokeConflict",
    "KaraokeWord",
    "SSAEvent",
    "SSAFile",
    "SSAStyle",
    "align_event",
    "align_file",
    "align_text",
    "cli",
    "exceptions",
    "formats",
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
