import pytest

import pysubs2
from pysubs2 import SSAEvent, SSAFile, make_time
from pysubs2.karaoke import KaraokeAlignment, KaraokeConflict, KaraokeWord, align_event, align_file, align_text


def test_simple_words_have_absolute_times_and_line_context() -> None:
    ev = SSAEvent(start=make_time(s=10), end=make_time(s=14),
                  text=r"{\k100}SUN{\k33}DAY")
    result = align_event(ev, line_index=3)

    assert isinstance(result, KaraokeAlignment)
    assert result.ok
    assert len(result) == 2

    sun, day = result
    assert isinstance(sun, KaraokeWord)
    assert sun.text == "SUN"
    assert (sun.start, sun.end) == (make_time(s=10), make_time(s=11))
    assert (day.text, day.start, day.end) == ("DAY", make_time(s=11), make_time(s=11, ms=330))
    assert [w.word_index for w in result] == [0, 1]
    assert [w.word_number for w in result] == [1, 2]
    assert all(w.line_index == 3 for w in result)
    assert all(w.line_text == ev.text for w in result)
    assert all(w.line_plaintext == "SUNDAY" for w in result)
    assert sun.line_start == ev.start and sun.line_end == ev.end


def test_empty_beat_consumes_time_and_does_not_increment_word_index() -> None:
    result = align_text(r"{\k5}{\k5}Hi{\k5}!", start=1000, end=1200)

    assert result.ok
    assert len(result) == 2

    hi, bang = result
    assert hi.text == "Hi"
    assert hi.start == 1050
    assert hi.end == 1100
    assert hi.beat_index == 1
    assert hi.beat_number == 2
    assert hi.word_index == 0

    assert bang.text == "!"
    assert bang.start == 1100
    assert bang.end == 1150
    assert bang.word_index == 1
    assert bang.beat_index == 2


def test_formatting_override_inside_word_does_not_split_it() -> None:
    text = r"{\k50}SUN{\i1}DAY{\i0}{\k50}!"
    result = align_text(text, start=0, end=1000)

    assert result.ok
    assert [w.text for w in result] == ["SUNDAY", "!"]
    assert result[0].start == 0
    assert result[0].end == 500
    assert result[0].beat_index == 0


def test_formatting_only_blocks_are_ignored_between_beats() -> None:
    result = align_text(r"{\k10}one{\b1}{\k10}two{\r}", start=0, end=200)

    assert result.ok
    assert [w.text for w in result] == ["one", "two"]
    assert [(w.start, w.end) for w in result] == [(0, 100), (100, 200)]


def test_multiple_empty_beats_delay_following_text_cumulatively() -> None:
    result = align_text(r"{\k5}{\k7}{\k5}word", start=100, end=1000)

    assert result.ok
    word, = result
    assert word.start == 100 + 50 + 70
    assert word.end == 100 + 50 + 70 + 50


def test_trailing_empty_beat_can_overflow_after_last_word() -> None:
    result = align_text(r"{\k10}word{\k10}", start=0, end=100)

    assert [w.text for w in result] == ["word"]
    conflict, = result.conflicts
    assert conflict.beat_index == 1
    assert conflict.word_index == 1
    assert conflict.beat_start == 100
    assert conflict.beat_end == 200


def test_karaoke_tag_inside_transform_argument_is_not_a_beat() -> None:
    result = align_text(r"{\k10}one{\t(0,100,\k20)}{\k10}two", start=0, end=200)

    assert result.ok
    assert [w.text for w in result] == ["one", "two"]
    assert [(w.start, w.end) for w in result] == [(0, 100), (100, 200)]


def test_tags_in_one_override_block_are_all_applied() -> None:
    result = align_text(r"{\i1\k10\b1}one{\k10}two", start=0, end=200)

    assert result.ok
    assert [w.text for w in result] == ["one", "two"]
    assert [(w.start, w.end) for w in result] == [(0, 100), (100, 200)]


def test_kf_ko_and_uppercase_tags_are_supported() -> None:
    result = align_text(r"{\kf25}one{\KO25}two", start=0, end=500)

    assert result.ok
    assert [(w.text, w.start, w.end) for w in result] == [("one", 0, 250), ("two", 250, 500)]


def test_text_before_first_beat_is_not_a_word() -> None:
    result = align_text(r"lead{\k100}word", start=0, end=1000)

    assert result.ok
    assert [w.text for w in result] == ["word"]


def test_overflow_reports_first_conflicting_beat_and_keeps_prefix() -> None:
    text = r"{\k10}one{\k10}two{\k10}three"
    result = align_text(text, start=1000, end=1250, line_index=7)

    assert len(result) == 2
    assert [w.text for w in result] == ["one", "two"]
    assert [(w.start, w.end) for w in result] == [(1000, 1100), (1100, 1200)]

    conflict, = result.conflicts
    assert isinstance(conflict, KaraokeConflict)
    assert conflict.line_index == 7
    assert conflict.beat_index == 2
    assert conflict.beat_number == 3
    assert conflict.word_index == 2
    assert conflict.word_number == 3
    assert conflict.text == "three"
    assert conflict.beat_start == 1200
    assert conflict.beat_end == 1300
    assert conflict.line_end == 1250
    assert conflict.overflow == 50
    assert conflict.reason == "duration-overflow"
    assert "beat 3" in conflict.message
    assert conflict.line_text == text


def test_overflow_on_empty_beat_keeps_word_index() -> None:
    result = align_text(r"{\k10}one{\k10}", start=0, end=150)

    assert [w.text for w in result] == ["one"]
    conflict, = result.conflicts
    assert conflict.beat_index == 1
    assert conflict.word_index == 1
    assert conflict.text == ""


def test_exact_line_end_is_not_a_conflict() -> None:
    result = align_text(r"{\k10}one{\k10}two", start=0, end=200)
    assert result.ok
    assert len(result) == 2


def test_no_karaoke_tags_gives_empty_alignment() -> None:
    result = align_text(r"{\i1}plain line", start=0, end=1000)
    assert result.ok
    assert len(result) == 0
    assert result.rows == []


def test_rows_expose_serializable_table() -> None:
    result = align_text(r"{\k5}one", start=10, end=60)
    row, = result.rows
    assert row["text"] == "one"
    assert row["start"] == 10
    assert row["end"] == 60
    assert row["line_index"] == 0
    assert row["word_index"] == 0


def test_align_file_keeps_original_event_indices_and_skips_comments_and_drawings() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(start=0, end=100, text=r"{\k5}a{\k5}b"))
    subs.append(SSAEvent(start=100, end=200, text=r"{\k5}c", type="Comment"))
    subs.append(SSAEvent(start=200, end=300, text=r"{\p1}m 0 0 l 10 0{\p0}"))
    subs.append(SSAEvent(start=300, end=500, text=r"{\k10}d"))

    result = align_file(subs)
    assert [w.text for w in result] == ["a", "b", "d"]
    assert [w.line_index for w in result] == [0, 0, 3]
    assert result.ok

    with_comments = align_file(subs, include_comments=True)
    assert [w.text for w in with_comments] == ["a", "b", "c", "d"]
    assert [w.line_index for w in with_comments] == [0, 0, 1, 3]


def test_align_file_collects_conflicts_from_all_lines() -> None:
    subs = SSAFile()
    subs.append(SSAEvent(start=0, end=150, text=r"{\k10}a{\k10}b"))
    subs.append(SSAEvent(start=1000, end=1200, text=r"{\k10}c"))

    result = align_file(subs)
    assert [w.text for w in result] == ["a", "c"]
    conflict, = result.conflicts
    assert conflict.line_index == 0
    assert conflict.beat_index == 1


def test_json_and_csv_serialization() -> None:
    import csv
    import io
    import json

    result = align_text(r"{\k10}one{\k10}two", start=0, end=200)

    payload = json.loads(result.to_json())
    assert [row["text"] for row in payload] == ["one", "two"]
    assert payload[0]["line_plaintext"] == "onetwo"

    reader = csv.DictReader(io.StringIO(result.to_csv()))
    rows = list(reader)
    assert [row["text"] for row in rows] == ["one", "two"]
    assert list(rows[0]) == list(KaraokeWord.__dataclass_fields__)


def test_slice_access_returns_words() -> None:
    result = align_text(r"{\k10}a{\k10}b{\k10}c", start=0, end=300)
    assert [w.text for w in result[1:]] == ["b", "c"]


def test_strict_mode_raises_with_partial_alignment() -> None:
    from pysubs2 import KaraokeOverflowError, KaraokeTimingError

    with pytest.raises(KaraokeTimingError) as excinfo:
        align_text(r"{\k10}one{\k10}two{\k10}three", start=0, end=250, strict=True)

    error = excinfo.value
    assert isinstance(error, KaraokeOverflowError)
    assert error.beat_index == 2
    assert error.word_index == 2
    assert [w.text for w in error.words] == ["one", "two"]
    assert error.conflict.beat_start == 200
    assert error.conflict.beat_end == 300


def test_public_api_exports() -> None:
    assert pysubs2.KaraokeWord is KaraokeWord
    assert pysubs2.KaraokeConflict is KaraokeConflict
    assert pysubs2.KaraokeAlignment is KaraokeAlignment
    assert pysubs2.align_text is align_text
    assert pysubs2.align_event is align_event
    assert pysubs2.align_file is align_file


def test_event_property_and_file_helper() -> None:
    ev = SSAEvent(start=0, end=200, text=r"{\k10}a{\k10}b")
    assert [w.text for w in ev.karaoke] == ["a", "b"]

    subs = SSAFile()
    subs.append(ev)
    subs.append(SSAEvent(start=200, end=300, text=r"{\k10}c", type="Comment"))
    assert [w.text for w in subs.align_karaoke()] == ["a", "b"]
    assert [w.text for w in subs.align_karaoke(include_comments=True)] == ["a", "b", "c"]


def test_cli_karaoke_json_outputs_words_and_reports_conflict(tmp_path, capsys) -> None:
    from pysubs2.cli import Pysubs2CLI

    inpath = tmp_path / "lyrics.ass"
    inpath.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "[V4+ Styles]\n"
        "[Events]\n"
        "Dialogue: 0,0:00:10.00,0:00:13.00,Default,,0,0,0,,{\\k50}SUN{\\k50}DAY\n"
        "Dialogue: 0,0:00:20.00,0:00:20.15,Default,,0,0,0,,{\\k10}one{\\k10}two\n",
        encoding="utf-8",
    )

    cli = Pysubs2CLI()
    rv = cli(["--karaoke", "json", str(inpath)])

    captured = capsys.readouterr()
    payload = __import__("json").loads(captured.out)
    assert [row["text"] for row in payload] == ["SUN", "DAY", "one"]
    assert "beat 2" in captured.err
    assert rv == 1


def test_cli_karaoke_csv_outputs_header(tmp_path, capsys) -> None:
    from pysubs2.cli import Pysubs2CLI

    inpath = tmp_path / "lyrics.ass"
    inpath.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "[V4+ Styles]\n"
        "[Events]\n"
        "Dialogue: 0,0:00:10.00,0:00:11.00,Default,,0,0,0,,{\\k10}word\n",
        encoding="utf-8",
    )

    cli = Pysubs2CLI()
    rv = cli(["--karaoke", "csv", str(inpath)])

    captured = capsys.readouterr()
    assert captured.out.splitlines()[0].startswith("text,start,end,line_index")
    assert rv == 0


def test_real_aegisub_lyrics_line() -> None:
    text = (r"{\k100}{\k33}SUN{\k4}{\k42}DAY {\k4}{\k38}MON{\k4}{\k29}DAY "
            r"{\k4}{\k33}CHU-{\k4}{\k21}CHU {\k4}{\k21}TUES{\k4}{\k50}DAY")
    ev = SSAEvent(start=40, end=4000, text=text)
    result = ev.karaoke

    assert result.ok
    assert len(result) == 8
    assert [w.text for w in result] == ["SUN", "DAY", "MON", "DAY", "CHU-", "CHU", "TUES", "DAY"]
    assert result[0].start == 1040
    assert result[0].end == 1370
    assert result[-1].end == 3990
    assert [w.word_index for w in result] == list(range(8))
    assert [w.beat_index for w in result] == [1, 3, 5, 7, 9, 11, 13, 15]
    assert all(w.line_plaintext == "SUNDAY MONDAY CHU-CHU TUESDAY" for w in result)
