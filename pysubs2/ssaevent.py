import dataclasses
import re
import warnings
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from .common import IntOrFloat
from .time import make_time, ms_to_str

if TYPE_CHECKING:
    from .textfragments import Fragment


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
        """
        Returns True if line contains an SSA drawing tag (ie. it draws something).

        This is a conservative check kept for compatibility with subtitle
        writers: a mixed line which draws a background and then shows a
        station name still counts as a drawing here. Use :attr:`has_text` or
        :attr:`station_name` when you care about the readable part only.
        """
        from .formats.substation import parse_tags
        return any(sty.drawing for _, sty in parse_tags(self.text))

    @property
    def has_text(self) -> bool:
        """Returns True when the line has at least one readable text fragment."""
        from .textfragments import split_fragments
        return any(not fragment.drawing for fragment in split_fragments(self.text))

    @property
    def fragments(self) -> list["Fragment"]:
        """
        Readable text and drawing fragments with their source offsets.

        See :func:`pysubs2.textfragments.split_fragments` for details. The
        offsets always refer to :attr:`SSAEvent.text`.
        """
        from .textfragments import split_fragments
        return split_fragments(self.text)

    @property
    def readable_text(self) -> str:
        """
        Concatenated readable text of the line, without drawing payloads.

        Override sequences and drawing commands are dropped; ``\\N``/``\\n``
        become newlines and ``\\h`` becomes a space. This is the text that
        should be indexed for searching.
        """
        from .textfragments import readable_text
        return readable_text(self.text)

    @property
    def text_fragments(self) -> list["Fragment"]:
        """Alias for :attr:`SSAEvent.fragments`."""
        return self.fragments

    @property
    def readable_fragments(self) -> list["Fragment"]:
        """Readable text fragments of the line (drawing fragments are omitted)."""
        return [fragment for fragment in self.fragments if not fragment.drawing]

    @property
    def drawing_fragments(self) -> list["Fragment"]:
        """Drawing fragments of the line (readable text fragments are omitted)."""
        return [fragment for fragment in self.fragments if fragment.drawing]

    @property
    def station_name(self) -> str:
        """
        Best-effort station name extracted from the line.

        The name is the concatenation of all readable text fragments, with
        surrounding whitespace stripped. Drawing payloads and override
        sequences are not part of it, and a line which contains only
        drawings returns an empty string rather than a blank name.
        """
        from .textfragments import trim_readable
        name, _, _ = trim_readable(self.fragments)
        return name

    @property
    def station_name_spans(self) -> list[tuple[int, int]]:
        """
        Source offsets of the readable fragments which make up the station name.

        Each tuple is a half-open ``(start, end)`` interval in
        :attr:`SSAEvent.text`. Leading and trailing whitespace is trimmed, and
        an inline tag which splits the name results in more than one interval.
        A drawing-only line has no spans.
        """
        from .textfragments import trim_readable
        _, spans, _ = trim_readable(self.fragments)
        return list(spans)

    @property
    def is_text(self) -> bool:
        """
        Returns False for SSA drawings and comment lines, True otherwise

        In general, for non-SSA formats these events should be ignored.

        .. note:: A mixed line which paints a background and then shows a
                  station name still counts as a drawing here, preserving the
                  historical writer behaviour. Use :attr:`has_text` or
                  :attr:`station_name` when only the readable part matters.
        """
        return not (self.is_comment or self.is_drawing)

    @property
    def plaintext(self) -> str:
        """
        Subtitle text as multi-line string with no tags (read/write property).

        Override sequences are removed and ``\\N``/``\\n`` become newlines,
        ``\\h`` becomes a space. Drawing payloads are *not* removed; use
        :attr:`readable_text` or :attr:`station_name` for that.

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
