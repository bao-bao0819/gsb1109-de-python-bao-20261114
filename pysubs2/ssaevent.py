import dataclasses
import re
import unicodedata
import warnings
from typing import Any, ClassVar, Literal

from .common import IntOrFloat
from .time import make_time, ms_to_str


@dataclasses.dataclass(frozen=True)
class DisplayChar:
    """
    One selectable character of subtitle display text, mapped back to its source.

    Display text is what remains of :attr:`SSAEvent.text` after override tags are
    removed and ``\\N``/``\\n``/``\\h`` escapes are resolved, ie. :attr:`SSAEvent.plaintext`.
    Each ``DisplayChar`` corresponds to one user-perceived character: a newline
    escape counts as a single character, and so does a base letter together with
    its combining accents.

    Attributes:
        char: The displayed character (may consist of multiple codepoints)
        start: Offset in :attr:`SSAEvent.text` where the character's source starts
        end: Offset in :attr:`SSAEvent.text` where the character's source ends

    """
    char: str
    start: int
    end: int


_ESCAPED_CHARS = {"N": "\n", "n": "\n", "h": " "}
_ZERO_WIDTH_JOINER = "\u200d"


def _is_combining(char: str) -> bool:
    return (unicodedata.combining(char) != 0
            or unicodedata.category(char) in ("Mn", "Mc", "Me")
            or 0xFE00 <= ord(char) <= 0xFE0F)  # variation selectors


def _display_atoms(text: str) -> list[DisplayChar]:
    atoms: list[DisplayChar] = []
    pos = 0
    for match in SSAEvent.OVERRIDE_SEQUENCE.finditer(text):
        atoms.extend(_fragment_atoms(text, pos, match.start()))
        pos = match.end()
    atoms.extend(_fragment_atoms(text, pos, len(text)))
    return atoms


def _fragment_atoms(text: str, begin: int, end: int) -> list[DisplayChar]:
    atoms: list[DisplayChar] = []
    i = begin
    while i < end:
        if text[i] == "\\" and i + 1 < end and text[i + 1] in _ESCAPED_CHARS:
            atoms.append(DisplayChar(_ESCAPED_CHARS[text[i + 1]], i, i + 2))
            i += 2
        else:
            atoms.append(DisplayChar(text[i], i, i + 1))
            i += 1
    return atoms


def _merge_graphemes(atoms: list[DisplayChar]) -> list[DisplayChar]:
    chars: list[DisplayChar] = []
    for atom in atoms:
        # only merge atoms that are adjacent in the source, so that a character
        # never spans an override tag (replacing it would swallow the tag)
        if (chars and chars[-1].end == atom.start
                and (_is_combining(atom.char)
                     or atom.char == _ZERO_WIDTH_JOINER
                     or chars[-1].char.endswith(_ZERO_WIDTH_JOINER))):
            prev = chars[-1]
            chars[-1] = DisplayChar(prev.char + atom.char, prev.start, atom.end)
        else:
            chars.append(atom)
    return chars


@dataclasses.dataclass(repr=False, eq=False, order=False)
class SSAEvent:
    """
    A SubStation Event, ie. one subtitle.

    In SubStation, each subtitle consists of multiple "fields" like Start, End and Text.
    These are exposed as attributes (note that they are lowercase; see :attr:`SSAEvent.FIELDS` for a list).
    Additionaly, there are some convenience properties like :attr:`SSAEvent.plaintext` or :attr:`SSAEvent.duration`.

    This class defines an ordering with respect to (start, end) timestamps.

    .. tip :: Use :func:`pysubs2.make_time()` to get times in milliseconds.

    Example::

        >>> ev = SSAEvent(start=make_time(s=1), end=make_time(s=2.5), text="Hello World!")

    Attributes:
        start: Subtitle start time (in milliseconds)
        end: Subtitle end time (in milliseconds)
        text: Text of subtitle (with SubStation override tags)
        marked: (SSA only)
        layer: Layer number, 0 is the lowest layer (ASS only)
        style: Style name
        name: Actor name
        marginl: Left margin
        marginr: Right margin
        marginv: Vertical margin
        effect: Line effect
        type: Line type (Dialogue/Comment)

    """
    OVERRIDE_SEQUENCE: ClassVar[re.Pattern[str]] = re.compile(r"{[^}]*}")

    start: int = 0
    end: int = 10000
    text: str = ""
    marked: bool = False
    layer: int = 0
    style: str = "Default"
    name: str = ""
    marginl: int = 0
    marginr: int = 0
    marginv: int = 0
    effect: str = ""
    type: Literal["Dialogue", "Comment"] = "Dialogue"

    @property
    def FIELDS(self) -> frozenset[str]:
        """All fields in SSAEvent."""
        warnings.warn("Deprecated in 1.2.0 - it's a dataclass now", DeprecationWarning)
        return frozenset(field.name for field in dataclasses.fields(self))

    @property
    def duration(self) -> IntOrFloat:
        """
        Subtitle duration in milliseconds (read/write property).

        Writing to this property adjusts :attr:`SSAEvent.end`.
        Setting negative durations raises :exc:`ValueError`.
        """
        return self.end - self.start

    @duration.setter
    def duration(self, ms: int) -> None:
        if ms >= 0:
            self.end = self.start + ms
        else:
            raise ValueError("Subtitle duration cannot be negative")

    @property
    def is_comment(self) -> bool:
        """
        When true, the subtitle is a comment, ie. not visible (read/write property).

        Setting this property is equivalent to changing
        :attr:`SSAEvent.type` to ``"Dialogue"`` or ``"Comment"``.
        """
        return self.type == "Comment"

    @is_comment.setter
    def is_comment(self, value: bool) -> None:
        if value:
            self.type = "Comment"
        else:
            self.type = "Dialogue"

    @property
    def is_drawing(self) -> bool:
        """Returns True if line is SSA drawing tag (ie. not text)"""
        from .formats.substation import parse_tags
        return any(sty.drawing for _, sty in parse_tags(self.text))

    @property
    def is_text(self) -> bool:
        """
        Returns False for SSA drawings and comment lines, True otherwise

        In general, for non-SSA formats these events should be ignored.
        """
        return not (self.is_comment or self.is_drawing)

    @property
    def plaintext(self) -> str:
        """
        Subtitle text as multi-line string with no tags (read/write property).

        Writing to this property replaces :attr:`SSAEvent.text` with given plain
        text. Newlines are converted to ``\\N`` tags.
        """
        text = self.text
        text = self.OVERRIDE_SEQUENCE.sub("", text)
        text = text.replace(r"\h", " ")
        text = text.replace(r"\n", "\n")
        text = text.replace(r"\N", "\n")
        return text

    @plaintext.setter
    def plaintext(self, text: str) -> None:
        self.text = text.replace("\n", r"\N")

    @property
    def display_chars(self) -> list[DisplayChar]:
        """
        Display text as a list of selectable characters with source coordinates.

        This is a position-aware version of :attr:`SSAEvent.plaintext`: joining
        ``char`` of all entries gives the same string, and each entry carries
        ``(start, end)`` offsets of its source in :attr:`SSAEvent.text`.
        Override tags are not part of any character, so source spans never
        include them. Use :meth:`SSAEvent.raw_spans()` to map a selection
        in display coordinates back to the raw text.

        Example::

            >>> ev = SSAEvent(text=r"你{\\c&H0000FF&}好")
            >>> ev.display_chars
            [DisplayChar(char='你', start=0, end=1), DisplayChar(char='好', start=14, end=15)]

        """
        return _merge_graphemes(_display_atoms(self.text))

    def raw_spans(self, start: int, end: int) -> list[tuple[int, int]]:
        """
        Map a selection in display coordinates to spans in :attr:`SSAEvent.text`.

        Given a half-open range ``[start, end)`` over display characters (see
        :attr:`SSAEvent.display_chars`), returns the corresponding half-open
        offset ranges in the raw text, ie. the fragments a proofreading tool
        should replace. Override tags between selected characters are never
        part of the result; instead, the selection is returned as multiple
        spans, so replacing them one by one cannot swallow any tags.

        An empty selection (``start == end``) returns a single zero-width
        span ``(offset, offset)`` marking the insertion point: just before
        the source of the display character at ``start``, or at the end of
        the text when the selection is past the last character.

        Example::

            >>> ev = SSAEvent(text=r"你{\\c&H0000FF&}好")
            >>> ev.raw_spans(0, 2)
            [(0, 1), (14, 15)]
            >>> ev.raw_spans(1, 1)
            [(14, 14)]

        Raises:
            IndexError: If the range is out of bounds for the display text.

        """
        chars = self.display_chars
        if start < 0 or end < start or end > len(chars):
            raise IndexError(f"Display range ({start}, {end}) out of bounds for {len(chars)} display characters")
        if start == end:
            offset = chars[start].start if start < len(chars) else len(self.text)
            return [(offset, offset)]
        spans: list[tuple[int, int]] = []
        for char in chars[start:end]:
            if spans and spans[-1][1] == char.start:
                spans[-1] = (spans[-1][0], char.end)
            else:
                spans.append((char.start, char.end))
        return spans

    def shift(self, h: IntOrFloat = 0, m: IntOrFloat = 0, s: IntOrFloat = 0, ms: IntOrFloat = 0,
              frames: int | None = None, fps: float | None = None) -> None:
        """
        Shift start and end times.

        See :meth:`SSAFile.shift()` for full description.

        """
        delta = make_time(h=h, m=m, s=s, ms=ms, frames=frames, fps=fps)
        self.start += delta
        self.end += delta

    def copy(self) -> "SSAEvent":
        """Return a copy of the SSAEvent."""
        return SSAEvent(**self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        # dataclasses.asdict() would recursively dictify Color objects, which we don't want
        return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}

    def equals(self, other: "SSAEvent") -> bool:
        """Field-based equality for SSAEvents."""
        if isinstance(other, SSAEvent):
            return self.as_dict() == other.as_dict()
        else:
            raise TypeError("Cannot compare to non-SSAEvent object")

    def __eq__(self, other: object) -> bool:
        # XXX document this
        if not isinstance(other, SSAEvent):
            return NotImplemented
        return self.start == other.start and self.end == other.end

    def __ne__(self, other: object) -> bool:
        if not isinstance(other, SSAEvent):
            return NotImplemented
        return self.start != other.start or self.end != other.end

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SSAEvent):
            return NotImplemented
        return (self.start, self.end) < (other.start, other.end)

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SSAEvent):
            return NotImplemented
        return (self.start, self.end) <= (other.start, other.end)

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SSAEvent):
            return NotImplemented
        return (self.start, self.end) > (other.start, other.end)

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, SSAEvent):
            return NotImplemented
        return (self.start, self.end) >= (other.start, other.end)

    def __repr__(self) -> str:
        return f"<SSAEvent type={self.type} start={ms_to_str(self.start)} end={ms_to_str(self.end)} text={self.text!r}>"
