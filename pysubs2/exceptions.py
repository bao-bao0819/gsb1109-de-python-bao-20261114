from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .karaoke import KaraokeAlignment, KaraokeConflict

__all__ = [
    "FormatAutodetectionError",
    "KaraokeError",
    "KaraokeOverflowError",
    "KaraokeTimingError",
    "Pysubs2Error",
    "UnknownFPSError",
    "UnknownFileExtensionError",
    "UnknownFormatIdentifierError",
]


class Pysubs2Error(Exception):
    """Base class for pysubs2 exceptions."""


class UnknownFPSError(Pysubs2Error):
    """Framerate was not specified and couldn't be inferred otherwise."""


class UnknownFileExtensionError(Pysubs2Error):
    """
    File extension does not pertain to any known subtitle format.

    This exception is raised by `SSAFile.save()` when the ``format_`` parameter
    is not specified. It will try to guess the desired format from output filename
    and raise this exception when it fails.

    Attributes:
        ext (str): File extension
    """

    def __init__(self, ext: str) -> None:
        self.ext = ext
        msg = f"File extension {ext!r} does not match any supported subtitle format"
        super().__init__(msg)

    def __reduce__(self) -> Any:
        return self.__class__, (self.ext,)


class UnknownFormatIdentifierError(Pysubs2Error):
    """
    Unknown subtitle format identifier (ie. string like ``"srt"``).

    This exception is used when interpreting ``format_`` parameter fails,
    eg. in `SSAFile.save()`.

    Attributes:
        format_ (str): Format identifier
    """

    def __init__(self, format_: str) -> None:
        self.format_ = format_
        msg = f"Format identifier {format_!r} does not match any supported subtitle format"
        super().__init__(msg)

    def __reduce__(self) -> Any:
        return self.__class__, (self.format_,)


class FormatAutodetectionError(Pysubs2Error):
    """
    Subtitle format is ambiguous or unknown based on analysis of file fragment

    This exception is raised by `SSAFile.load()` and related methods
    when the ``format_`` parameter is not specified. It will try to guess
    the input format based on reading first few kilobytes of the input file
    and raise this exception if the format cannot be uniquely determined.

    Attributes:
        content (str): Analyzed subtitle file content
        formats (list[str]): Format identifiers for detected formats
    """
    def __init__(self, content: str, formats: list[str]) -> None:
        self.content = content
        self.formats = formats
        if not formats:
            msg = "No suitable formats"
        else:
            msg = f"Multiple suitable formats ({formats!r})"
        super().__init__(msg)

    def __reduce__(self) -> Any:
        return self.__class__, (self.content, self.formats)


class KaraokeError(Pysubs2Error):
    """Base class for errors related to karaoke timing."""


class KaraokeTimingError(KaraokeError):
    """
    Karaoke timing is inconsistent with the subtitle line.

    The error is raised only in strict mode.  It always carries the alignment
    result, including the words which could be aligned before the conflict.

    Attributes:
        conflict: The first :class:`pysubs2.KaraokeConflict` found.
        alignment: :class:`pysubs2.KaraokeAlignment` with partial results.
        beat_index: Zero-based index of the first conflicting beat.
        word_index: Number of words accepted before the conflict.
        words: Accepted words (alias for ``alignment.words``).
    """

    def __init__(self, conflict: "KaraokeConflict", alignment: "KaraokeAlignment") -> None:
        self.conflict = conflict
        self.alignment = alignment
        self.beat_index = conflict.beat_index
        self.word_index = conflict.word_index
        super().__init__(conflict.message)

    @property
    def words(self) -> list[Any]:
        """Words aligned before the conflict."""
        return self.alignment.words

    def __reduce__(self) -> Any:
        return self.__class__, (self.conflict, self.alignment)


class KaraokeOverflowError(KaraokeTimingError):
    """Karaoke beat durations extend past the end of the subtitle line."""
