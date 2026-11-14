# mypy: disable-error-code="override"

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, NotRequired, TextIO, Unpack, override

from ..ssaevent import SSAEvent
from ..time import make_time
from ..vttsegments import VTTSegment, import_vtt_segments
from .subrip import SubripFormat

if TYPE_CHECKING:
    from ..ssafile import SSAFile


class WebVTTFormat(SubripFormat):
    """
    Web Video Text Tracks (WebVTT) subtitle format implementation

    Currently, this shares implementation with :class:`pysubs2.formats.subrip.SubripFormat`.
    """
    TIMESTAMP = re.compile(r"(\d{0,4}:)?(\d{2}):(\d{2})\.(\d{2,3})")

    class ReaderArgs(SubripFormat.ReaderArgs):
        segments: NotRequired[Iterable[VTTSegment]]
        wrap_period_ms: NotRequired[int]

    class WriterArgs(SubripFormat.WriterArgs):
        pass

    @staticmethod
    @override
    def ms_to_timestamp(ms: int) -> str:
        result = SubripFormat.ms_to_timestamp(ms)
        return result.replace(',', '.')

    @staticmethod
    @override
    def timestamp_to_ms(groups: Sequence[str]) -> int:
        _h, _m, _s, _ms = groups
        if not _h:
            h = 0
        else:
            h = int(_h.strip(":"))
        m, s, ms = map(int, (_m, _s, _ms))
        return make_time(h=h, m=m, s=s, ms=ms)

    @classmethod
    @override
    def guess_format(cls, text: str) -> str | None:
        """See :meth:`pysubs2.formats.FormatBase.guess_format()`"""
        if text.lstrip().startswith("WEBVTT"):
            return "vtt"
        else:
            return None

    @classmethod
    @override
    def from_file(cls, subs: "SSAFile", fp: TextIO, format_: str, **kwargs: Unpack[ReaderArgs]) -> None:
        """
        See :meth:`pysubs2.formats.SubripFormat.from_file()`, additional SRT options are supported by VTT as well

        Keyword args:
            segments: Optional iterable of :class:`pysubs2.vttsegments.VTTSegment`
                describing archived livestream segments with sequence numbers,
                discontinuity markers and per-segment time maps. When given, the
                segments are merged onto a single program timeline (handling
                media clock wraparound and program source switches) instead of
                parsing ``fp`` as a single WebVTT file. Cues without a usable
                timeline anchor are placed on :attr:`SSAFile.quarantine`.
                See :func:`pysubs2.vttsegments.import_vtt_segments` for details.
        """
        segments = kwargs.get("segments")
        if segments is None:
            return super().from_file(
                subs,
                fp,
                format_,
                keep_html_tags=kwargs.get("keep_html_tags", False),
                keep_unknown_html_tags=kwargs.get("keep_unknown_html_tags", False),
            )

        wrap_period_ms = kwargs.get("wrap_period_ms")
        if wrap_period_ms is None:
            result = import_vtt_segments(segments)
        else:
            result = import_vtt_segments(segments, wrap_period_ms=wrap_period_ms)
        subs.events.extend(result.subs.events)
        subs.quarantine.extend(result.quarantine)

    @classmethod
    @override
    def to_file(cls, subs: "SSAFile", fp: TextIO, format_: str, **kwargs: Unpack[WriterArgs]) -> None:
        """
        See :meth:`pysubs2.formats.SubripFormat.to_file()`, additional SRT options are supported by VTT as well
        """
        print("WEBVTT\n", file=fp)
        return super().to_file(subs=subs, fp=fp, format_=format_, **kwargs)

    @classmethod
    @override
    def _get_visible_lines(cls, subs: "SSAFile") -> list[SSAEvent]:
        visible_lines = super()._get_visible_lines(subs)
        visible_lines.sort(key=lambda e: e.start)
        return visible_lines
