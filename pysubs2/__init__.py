from . import cli, exceptions, formats, revision, time
from .common import VERSION, Alignment, Color
from .exceptions import *
from .formats import whisper
from .revision import merge_revisions
from .ssaevent import SSAEvent
from .ssafile import SSAFile
from .ssastyle import SSAStyle

__all__ = [
    "VERSION",
    "Alignment",
    "Color",
    "SSAEvent",
    "SSAFile",
    "SSAStyle",
    "cli",
    "exceptions",
    "formats",
    "load",
    "load_from_whisper",
    "make_time",
    "merge_revisions",
    "revision",
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
