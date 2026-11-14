"""
Word-level alignment for SubStation karaoke lines.

SubStation karaoke is written using ``\\k`` timing tags, for example::

    {\\k100}{\\k33}SUN{\\k42}DAY

The value after ``\\k`` is a duration in centiseconds.  A tag with no text
before the next tag is an *empty beat*: it still consumes time on the line,
but does not produce a word.

This module turns those beats into :class:`KaraokeWord` records carrying
absolute timestamps (in milliseconds), the original line, and the position of
the word.  It is deliberately tolerant: if the karaoke timing runs past the
end of the event, the words which can be aligned before the offending beat are
returned and a :class:`KaraokeConflict` is reported.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import json
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, TextIO, overload

from .exceptions import KaraokeOverflowError
from .ssaevent import SSAEvent
from .time import ms_to_str

if TYPE_CHECKING:
    from .ssafile import SSAFile


__all__ = [
    "KaraokeAlignment",
    "KaraokeConflict",
    "KaraokeWord",
    "align_event",
    "align_file",
    "align_text",
    "parse_karaoke_text",
]


#: Matches ``\k``, ``\kf``, ``\ko`` and their upper-case variants.
#: The duration is given in centiseconds.
KARAOKE_TAG = re.compile(r"\\k[fo]?(\d+)", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class KaraokeWord:
    """
    One text beat from a karaoke line.

    Attributes:
        text: Text of the beat (without SubStation override tags).
        start: Absolute start time in milliseconds.
        end: Absolute end time in milliseconds.
        line_index: Index of the source event in :attr:`SSAFile.events`.
        word_index: Zero-based index of the word within its line.  Empty beats
            do not increment this index.
        beat_index: Zero-based index of the beat within its line, including
            empty beats.
        line_text: Original event text, including karaoke and formatting tags.
        line_plaintext: Event text with override tags removed.
        line_start: Start time of the source event in milliseconds.
        line_end: End time of the source event in milliseconds.
    """

    text: str
    start: int
    end: int
    line_index: int
    word_index: int
    beat_index: int
    line_text: str
    line_plaintext: str
    line_start: int
    line_end: int

    @property
    def word(self) -> str:
        """Alias for :attr:`text`."""
        return self.text

    @property
    def sentence(self) -> str:
        """Original line without override tags (alias for :attr:`line_plaintext`)."""
        return self.line_plaintext

    @property
    def duration(self) -> int:
        """Word duration in milliseconds."""
        return self.end - self.start

    @property
    def beat_number(self) -> int:
        """One-based beat number (for human-readable reports)."""
        return self.beat_index + 1

    @property
    def word_number(self) -> int:
        """One-based word number within the line."""
        return self.word_index + 1

    def as_dict(self) -> dict[str, Any]:
        """Return the record as a plain dictionary."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class KaraokeConflict:
    """
    Timing conflict found while aligning a karaoke line.

    The conflict is reported at the first beat whose duration would make it
    end after the event's end time.  Words before this beat are still emitted
    by :func:`align_event` / :func:`align_file`.

    Attributes:
        line_index: Index of the source event in :attr:`SSAFile.events`.
        beat_index: Zero-based index of the first conflicting beat, counting
            empty beats.
        word_index: Zero-based word index the conflicting beat would have had
            if it contained text.  This is also the number of words accepted
            before the conflict.
        beat_start: Computed start time of the conflicting beat (milliseconds).
        beat_end: Computed end time of the conflicting beat (milliseconds),
            which lies after :attr:`line_end`.
        line_end: End time of the source event (milliseconds).
        text: Text of the conflicting beat, if any.
        line_text: Original event text, including karaoke and formatting tags.
    """

    line_index: int
    beat_index: int
    word_index: int
    beat_start: int
    beat_end: int
    line_end: int
    text: str
    line_text: str

    @property
    def beat_number(self) -> int:
        """One-based beat number (for human-readable reports)."""
        return self.beat_index + 1

    @property
    def word_number(self) -> int:
        """One-based word number the conflicting beat would have had."""
        return self.word_index + 1

    @property
    def overflow(self) -> int:
        """How many milliseconds the conflicting beat extends past the line end."""
        return self.beat_end - self.line_end

    @property
    def reason(self) -> str:
        """Machine-readable conflict kind."""
        return "duration-overflow"

    @property
    def message(self) -> str:
        """Human-readable description of the conflict."""
        beat = f"beat {self.beat_number}" if not self.text else f"beat {self.beat_number} ({self.text!r})"
        return (
            f"Karaoke duration overflows line {self.line_index}: {beat} ends at "
            f"{ms_to_str(self.beat_end, fractions=True)}, after line end "
            f"{ms_to_str(self.line_end, fractions=True)}"
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the conflict as a plain dictionary."""
        result = dataclasses.asdict(self)
        result["reason"] = self.reason
        result["message"] = self.message
        result["overflow"] = self.overflow
        result["beat_number"] = self.beat_number
        result["word_number"] = self.word_number
        return result


@dataclasses.dataclass
class KaraokeAlignment:
    """
    Result of aligning one line or a whole subtitle file.

    The object behaves like a sequence of :class:`KaraokeWord` records, so it
    can be iterated or indexed directly.  Timing conflicts are available in
    :attr:`conflicts`; alignment data before a conflict remains valid.
    """

    words: list[KaraokeWord] = dataclasses.field(default_factory=list)
    conflicts: list[KaraokeConflict] = dataclasses.field(default_factory=list)

    def __iter__(self) -> Iterator[KaraokeWord]:
        return iter(self.words)

    def __len__(self) -> int:
        return len(self.words)

    @overload
    def __getitem__(self, index: int) -> KaraokeWord:
        pass

    @overload
    def __getitem__(self, index: slice) -> list[KaraokeWord]:
        pass

    def __getitem__(self, index: int | slice) -> KaraokeWord | list[KaraokeWord]:
        return self.words[index]

    @property
    def ok(self) -> bool:
        """True when no timing conflicts were found."""
        return not self.conflicts

    @property
    def issues(self) -> list[KaraokeConflict]:
        """Alias for :attr:`conflicts`."""
        return self.conflicts

    @property
    def first_conflict(self) -> KaraokeConflict | None:
        """First reported conflict, or None if all lines aligned cleanly."""
        return self.conflicts[0] if self.conflicts else None

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Word table as a list of dictionaries (convenience for serialization)."""
        return [word.as_dict() for word in self.words]

    def to_dicts(self) -> list[dict[str, Any]]:
        """Alias for :attr:`rows`."""
        return self.rows

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the word table to JSON (list of word records)."""
        return json.dumps(self.rows, ensure_ascii=False, indent=indent)

    def to_csv(self) -> str:
        """Serialize the word table to CSV text with a header row."""
        buffer = io.StringIO()
        self.write_csv(buffer)
        return buffer.getvalue()

    def write_json(self, fp: TextIO, *, indent: int | None = None) -> None:
        """Write the word table as JSON to a file object."""
        json.dump(self.rows, fp, ensure_ascii=False, indent=indent)

    def write_csv(self, fp: TextIO) -> None:
        """Write the word table as CSV to a file object."""
        fieldnames = list(KaraokeWord.__dataclass_fields__)
        writer = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(self.rows)

    def extend(self, other: "KaraokeAlignment") -> None:
        """Append words and conflicts from another alignment."""
        self.words.extend(other.words)
        self.conflicts.extend(other.conflicts)


@dataclasses.dataclass
class _RawBeat:
    duration_cs: int
    text_parts: list[str] = dataclasses.field(default_factory=list)


def _plain_fragment(fragment: str) -> str:
    """Normalize non-breakable spaces and newline escapes in plain text."""
    return fragment.replace(r"\h", " ").replace(r"\N", "\n").replace(r"\n", "\n")


def _top_level_tags(block: str) -> list[str]:
    """
    Return top-level command text from an override block.

    Arguments of tags like ``\\t(...)`` or ``\\clip(...)`` are removed so that
    a ``\\k`` string inside them is not mistaken for a karaoke beat.
    """
    depth = 0
    commands: list[str] = []
    current = ""

    for char in block:
        if char in "{}":
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth = max(depth - 1, 0)
            continue
        if depth > 0:
            continue

        if char == "\\" and current:
            commands.append(current)
            current = char
        else:
            current += char

    if current:
        commands.append(current)

    return commands


def _extract_beats(text: str) -> list[_RawBeat]:
    """
    Split a karaoke line into beats.

    Formatting tags do not create beats; only ``\\k``-family tags do.  Text
    between two karaoke tags is concatenated even when a formatting override
    block sits in the middle of it.
    """
    beats: list[_RawBeat] = []
    current: _RawBeat | None = None
    position = 0

    for block_match in SSAEvent.OVERRIDE_SEQUENCE.finditer(text):
        plain = _plain_fragment(text[position:block_match.start()])
        if current is not None:
            current.text_parts.append(plain)

        for command in _top_level_tags(block_match.group(0)):
            tag_match = KARAOKE_TAG.match(command)
            if tag_match is None:
                continue
            if current is not None:
                beats.append(current)
            current = _RawBeat(duration_cs=int(tag_match.group(1)))

        position = block_match.end()

    if current is not None:
        current.text_parts.append(_plain_fragment(text[position:]))
        beats.append(current)

    return beats


def align_text(text: str, start: int, end: int, line_index: int = 0,
               *, strict: bool = False) -> KaraokeAlignment:
    """
    Align a raw SubStation karaoke line.

    Arguments:
        text: Event text containing ``\\k``-family tags.
        start: Absolute line start time in milliseconds.
        end: Absolute line end time in milliseconds.
        line_index: Index of the source event, stored in the result records.
        strict: When true, raise :class:`pysubs2.KaraokeTimingError` at the
            first conflict instead of returning the partial alignment.  The
            exception still carries the alignment with usable prefix words.

    Returns:
        :class:`KaraokeAlignment` with accepted words and any conflicts.  If
        karaoke durations run past ``end``, the first offending beat is
        reported and no later beat from that line is emitted.
    """
    line_plaintext = SSAEvent(text=text, start=start, end=end).plaintext
    result = KaraokeAlignment()
    cursor = start
    word_index = 0

    for beat_index, beat in enumerate(_extract_beats(text)):
        beat_start = cursor
        beat_end = beat_start + beat.duration_cs * 10
        beat_text = "".join(beat.text_parts).strip()

        if beat_end > end:
            conflict = KaraokeConflict(
                line_index=line_index,
                beat_index=beat_index,
                word_index=word_index,
                beat_start=beat_start,
                beat_end=beat_end,
                line_end=end,
                text=beat_text,
                line_text=text,
            )
            result.conflicts.append(conflict)
            if strict:
                raise KaraokeOverflowError(conflict, result)
            break

        if beat_text:
            result.words.append(KaraokeWord(
                text=beat_text,
                start=beat_start,
                end=beat_end,
                line_index=line_index,
                word_index=word_index,
                beat_index=beat_index,
                line_text=text,
                line_plaintext=line_plaintext,
                line_start=start,
                line_end=end,
            ))
            word_index += 1

        cursor = beat_end

    return result


def align_event(event: SSAEvent, line_index: int = 0,
                *, strict: bool = False) -> KaraokeAlignment:
    """
    Align one :class:`pysubs2.SSAEvent` karaoke line.

    See :func:`align_text` for timing rules.
    """
    return align_text(event.text, event.start, event.end, line_index=line_index, strict=strict)


def align_file(subs: "SSAFile", *, include_comments: bool = False,
               strict: bool = False) -> KaraokeAlignment:
    """
    Align all text events in a subtitle file.

    Comment lines and SSA drawings are skipped by default because they are not
    sung lyrics.  ``line_index`` in the returned records refers to the original
    position in ``subs.events`` even when some events are skipped.

    Arguments:
        subs: Subtitle file to align.
        include_comments: When true, also align comment events.
        strict: When true, raise :class:`pysubs2.KaraokeTimingError` at the
            first conflicting line instead of collecting conflicts.

    Raises:
        pysubs2.KaraokeTimingError: In strict mode, when a line overflows.
    """
    result = KaraokeAlignment()
    for line_index, event in enumerate(subs):
        if event.is_drawing:
            continue
        if event.is_comment and not include_comments:
            continue
        result.extend(align_event(event, line_index=line_index, strict=strict))
    return result


#: Alias kept for callers who prefer the "parse" terminology.
parse_karaoke_text = align_text
