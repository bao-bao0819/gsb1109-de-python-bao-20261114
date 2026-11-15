"""Split SubStation event text into readable text and drawing commands.

The functions here walk an event's raw text once and report each run of text
together with the half-open source offsets it came from. The offsets refer to
the original, untouched event text, so extracted values can always be traced
back to the exact substring they came from.

Override sequences (``{...}``) are never part of a fragment. Drawing state is
tracked across override sequences: ``\\p1`` (or any positive scale) switches
the following text into drawing mode, ``\\p0`` switches it back, and ``\\r``
or ``\\rStyleName`` resets the line style, which also leaves drawing mode.
"""

import dataclasses
import re
from collections.abc import Iterable, Iterator

from .ssaevent import SSAEvent
from .ssastyle import SSAStyle


#: Matches one override sequence (kept in sync with ``SSAEvent``).
_OVERRIDE_SEQUENCE = SSAEvent.OVERRIDE_SEQUENCE

#: Matches one backslash-delimited tag body inside an override sequence.
_TAG_BODY = re.compile(r"\\([^\\}]*)")

#: Matches a ``\t(...)`` animation block, including nested parentheses.
_ANIMATION_BLOCK = re.compile(r"\\t\((?:[^()]|\([^()]*\))*\)")

#: Escape sequences which render as a single character (or a line break).
_ESCAPE = re.compile(r"\\[Nnh]")


@dataclasses.dataclass(frozen=True)
class Fragment:
    """One run of event text, with the source offsets it was read from.

    Attributes:
        text: Text of the fragment as it is meant to be read. For readable
            fragments, ``\\N``/``\\n`` are newlines and ``\\h`` is a space.
            For drawing fragments this is the drawing payload as written
            (for example ``m 0 0 l 100 0``).
        start: Offset of the first character of the fragment in
            :attr:`SSAEvent.text` (inclusive).
        end: Offset one past the last character of the fragment (exclusive).
        raw: The exact substring ``text[start:end]`` of the event, including
            any newline escapes. This is what makes the fragment traceable to
            its source.
        drawing: True when the fragment belongs to a drawing.
        drawing_scale: The active ``\\p`` scale (0 outside drawings).
        style: Style in effect for the fragment. Its :attr:`SSAStyle.drawing`
            attribute always matches :attr:`Fragment.drawing`.

    """
    text: str
    start: int
    end: int
    drawing: bool = False
    drawing_scale: int = 0
    style: SSAStyle = dataclasses.field(default_factory=lambda: SSAStyle.DEFAULT_STYLE.copy())
    raw: str = ""

    def __post_init__(self) -> None:
        if not self.raw:
            # A fragment constructed by hand does not know its source text;
            # default to the displayed text, which is exact for fragments
            # without escape sequences.
            object.__setattr__(self, "raw", self.text)

    @property
    def source_start(self) -> int:
        """Alias for :attr:`Fragment.start`."""
        return self.start

    @property
    def source_end(self) -> int:
        """Alias for :attr:`Fragment.end`."""
        return self.end

    @property
    def is_text(self) -> bool:
        """True for readable text fragments, False for drawing fragments."""
        return not self.drawing


#: Alias for :class:`Fragment`.
TextFragment = Fragment


@dataclasses.dataclass(frozen=True)
class StationName:
    """A station name extracted from one event.

    Attributes:
        text: The extracted, whitespace-stripped station name.
        event: The event the name came from.
        spans: Half-open ``(start, end)`` intervals in
            :attr:`SSAEvent.text` which make up the trimmed name. There is
            one span per uninterrupted source run, so an inline tag which
            splits the name results in more than one span.
        fragments: The readable fragments the name was assembled from.

    """
    text: str
    event: SSAEvent
    spans: tuple[tuple[int, int], ...]
    fragments: tuple[Fragment, ...]

    @property
    def start(self) -> int:
        """Start time of the source event, in milliseconds."""
        return self.event.start

    @property
    def end(self) -> int:
        """End time of the source event, in milliseconds."""
        return self.event.end

    @property
    def raw_spans(self) -> tuple[str, ...]:
        """The exact source substrings which make up the name."""
        return tuple(self.event.text[start:end] for start, end in self.spans)


def _strip_animation_blocks(override: str) -> str:
    """Remove ``\\t(...)`` blocks so their tags are not applied immediately."""
    return _ANIMATION_BLOCK.sub("", override)


def _tag_bodies(override: str) -> list[str]:
    """Return the body of every override tag in one override sequence.

    For example ``{\\p1\\rAlt}`` yields ``["p1", "rAlt"]``. Tags nested in a
    ``\\t`` animation are ignored, since they do not change the static state.
    """
    return _TAG_BODY.findall(_strip_animation_blocks(override))


def apply_override_sequence(
        override: str,
        style: SSAStyle,
        base_style: SSAStyle,
        styles: dict[str, SSAStyle] | None = None,
        drawing_scale: int = 0
) -> tuple[int, SSAStyle]:
    """Apply one override sequence to a style and drawing scale.

    This is the shared state machine used by :func:`split_fragments` and by
    :func:`pysubs2.formats.substation.parse_tags`. It returns the new drawing
    scale and a copy of the style with the supported override tags applied.
    """
    style = style.copy()
    for body in _tag_bodies(override):
        if body.startswith("p"):
            value = body[1:].strip()
            if not value:
                # A bare ``\p`` does not define a scale; keep the state.
                continue
            try:
                drawing_scale = max(int(value), 0)
            except ValueError:
                # Not a ``\p`` scale. This also covers ``\pos(...)`` and
                # ``\pbo``, which must not toggle drawing.
                continue
        elif body.startswith("r"):
            # Both ``\r`` and ``\rStyleName`` restore the line style, which
            # also ends any drawing started by a previous override.
            drawing_scale = 0
            style_name = body[1:].strip()
            if not style_name:
                style = base_style.copy()
            elif styles is not None and style_name in styles:
                style = styles[style_name].copy()
            # An unknown style name leaves the remaining style attributes as
            # they were, matching the historical parse_tags behaviour; only
            # the drawing state is reset above.
        elif body.startswith("fn"):
            fontname = body[2:].strip()
            if fontname:
                style.fontname = fontname
        elif body and body[0] in "ibus" and body[1:].isdigit():
            enabled = int(body[1:]) != 0
            if body[0] == "i":
                style.italic = enabled
            elif body[0] == "b":
                style.bold = enabled
            elif body[0] == "u":
                style.underline = enabled
            elif body[0] == "s":
                style.strikeout = enabled

    style.drawing = drawing_scale > 0
    return drawing_scale, style


def _decode_escapes(raw: str) -> str:
    """Convert display escapes to the characters they represent."""
    return _ESCAPE.sub(lambda match: "\n" if match.group(0) in (r"\N", r"\n") else " ", raw)


def split_fragments(
        text: str,
        style: SSAStyle = SSAStyle.DEFAULT_STYLE,
        styles: dict[str, SSAStyle] | None = None
) -> list[Fragment]:
    """Split event text into readable text and drawing fragments.

    The function walks ``text`` once and returns a list of :class:`Fragment`
    objects in source order. Override sequences are skipped, but they update
    the drawing state and the effective style:

    - ``\\pN`` with ``N > 0`` starts a drawing, ``\\p0`` ends it.
    - ``\\r`` and ``\\rStyleName`` restore the line style and also end a
      drawing, as they reset everything the drawing override changed.
    - ``\\N``/``\\n`` become a newline, ``\\h`` becomes a plain space.

    Fragments are emitted only for non-empty runs. A line which contains
    nothing but drawing overrides therefore produces drawing fragments only,
    never an empty text fragment. Source offsets always refer to ``text``
    itself, and :attr:`Fragment.raw` is the exact source substring.

    Numeric coordinates in ordinary dialogue are left untouched: they are only
    treated as a drawing payload while a ``\\p`` override is active.

    """
    fragments: list[Fragment] = []
    current_style = style.copy()
    base_style = style
    scale = 1 if style.drawing else 0
    position = 0

    def emit(start: int, end: int) -> None:
        if start == end:
            return
        raw = text[start:end]
        fragment_style = current_style.copy()
        fragment_style.drawing = scale > 0
        if scale > 0:
            fragments.append(Fragment(raw, start, end, drawing=True,
                                      drawing_scale=scale, style=fragment_style, raw=raw))
        else:
            fragments.append(Fragment(_decode_escapes(raw), start, end, drawing=False,
                                      drawing_scale=0, style=fragment_style, raw=raw))

    for match in _OVERRIDE_SEQUENCE.finditer(text):
        emit(position, match.start())
        scale, current_style = apply_override_sequence(
            match.group(0), current_style, base_style, styles, scale)
        position = match.end()

    emit(position, len(text))
    return fragments


#: Alias for :func:`split_fragments`.
parse_fragments = split_fragments


def iter_readable_chars(fragments: Iterable[Fragment]) -> Iterator[tuple[str, int, int]]:
    """Yield ``(char, source_start, source_end)`` for each readable character.

    Each character of the displayed text is mapped back to the interval of the
    original event text it came from. A decoded escape such as ``\\N`` is one
    character which maps to both source characters.
    """
    for fragment in fragments:
        if fragment.drawing:
            continue
        position = fragment.start
        while position < fragment.end:
            match = _ESCAPE.match(fragment.raw, position - fragment.start)
            if match:
                escape = match.group(0)
                yield ("\n" if escape in (r"\N", r"\n") else " "), position, position + len(escape)
                position += len(escape)
            else:
                yield fragment.raw[position - fragment.start], position, position + 1
                position += 1


def readable_text(text: str) -> str:
    """Return only the readable text of an event, without drawing payloads."""
    return "".join(fragment.text for fragment in split_fragments(text) if not fragment.drawing)


def trim_readable(
        fragments: Iterable[Fragment]
) -> tuple[str, tuple[tuple[int, int], ...], tuple[Fragment, ...]]:
    """Assemble readable fragments and trim surrounding whitespace.

    Returns ``(name, spans, fragments)`` where ``spans`` are the merged
    half-open source intervals which make up the trimmed text.
    """
    readable = [fragment for fragment in fragments if not fragment.drawing]
    characters = list(iter_readable_chars(readable))
    first = 0
    last = len(characters)
    while first < last and characters[first][0].isspace():
        first += 1
    while last > first and characters[last - 1][0].isspace():
        last -= 1

    selected = characters[first:last]
    name = "".join(char for char, _, _ in selected)
    spans: list[tuple[int, int]] = []
    for _, start, end in selected:
        if spans and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))

    contributing = tuple(
        fragment for fragment in readable
        if any(fragment.start < span_end and span_start < fragment.end
               for span_start, span_end in spans)
    )
    return name, tuple(spans), contributing
