"""Test reconstruction of observations from multiple METAR archives."""

from datetime import datetime, timezone

import pytest

from metar.Archive import (
    STATUS_CONFLICT,
    STATUS_MISSING,
    STATUS_OBSERVED,
    Archive,
    ArchiveError,
)

YEAR, MONTH, DAY = 2026, 11, 12
HOUR, MINUTE = 16, 51


def utc(hour, minute, second=0):
    """Return an aware UTC datetime on the test observation day."""
    return datetime(YEAR, MONTH, DAY, hour, minute, second, tzinfo=timezone.utc)


OBS_TIME = utc(HOUR, MINUTE)
OBS_TIME_NAIVE = OBS_TIME.replace(tzinfo=None)

ORIGINAL = "METAR KEWR 121651Z 22015KT 10SM FEW020 19/13 A2992"
RETRANSMITTED = "KEWR 121651Z 22015KT 10SM FEW020 19/13 A2992="
CORRECTED = "METAR KEWR 121651Z COR 22015KT 10SM FEW020 19/14 A2992"
CORRECTED_AGAIN = "METAR KEWR 121651Z COR 22015KT 10SM FEW020 19/15 A2992"
CCB = "METAR KEWR 121651Z CCB 22015KT 10SM FEW020 19/16 A2992"


def build(*records):
    archive = Archive(month=MONTH, year=YEAR)
    for code, received, source in records:
        archive.add(code, received, source=source)
    return archive


def test_as_of_cutoff_sees_only_reports_received_by_then():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 58), "primary"),
    )

    before = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 53))
    after = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 59))

    assert before.status == STATUS_OBSERVED
    assert before.code == ORIGINAL
    assert after.status == STATUS_OBSERVED
    assert after.code == CORRECTED


def test_cutoff_is_inclusive_and_returns_missing_before_first_reception():
    archive = build((ORIGINAL, utc(16, 52), "primary"))

    assert archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 51)).status == STATUS_MISSING
    visible = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 52))
    assert visible.status == STATUS_OBSERVED
    assert visible.code == ORIGINAL


def test_cutoff_alias_matches_positional_as_of():
    archive = build((ORIGINAL, utc(16, 52), "primary"))

    by_keyword = archive.lookup("KEWR", OBS_TIME, cutoff=utc(16, 53))
    by_position = archive.lookup("KEWR", OBS_TIME, utc(16, 53))
    assert by_keyword.status == by_position.status == STATUS_OBSERVED
    assert by_keyword.code == by_position.code == ORIGINAL


def test_naive_cutoff_is_interpreted_as_utc():
    archive = build((ORIGINAL, utc(16, 52), "primary"))

    visible = archive.lookup("KEWR", OBS_TIME, as_of=datetime(YEAR, MONTH, DAY, 16, 53))
    assert visible.status == STATUS_OBSERVED
    assert visible.code == ORIGINAL


def test_superseded_by_finds_the_later_correction():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 58), "primary"),
    )
    original = archive.history("KEWR", OBS_TIME)[0]

    assert archive.superseded_by(original).code == CORRECTED


def test_visible_resolution_reports_its_replacement():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 58), "primary"),
    )

    visible = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 53))

    assert visible.superseded_by is not None
    assert visible.superseded_by.code == CORRECTED
    assert visible.successor.code == CORRECTED
    assert visible.final.code == CORRECTED


def test_an_uncorrected_report_has_no_successor():
    archive = build((ORIGINAL, utc(16, 52), "primary"))
    resolution = archive.lookup("KEWR", OBS_TIME)

    assert resolution.superseded_by is None
    assert resolution.final is None


def test_retransmission_is_not_a_revision():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (RETRANSMITTED, utc(16, 54), "secondary"),
    )
    resolution = archive.lookup("KEWR", OBS_TIME)

    assert resolution.status == STATUS_OBSERVED
    assert len(resolution.candidates) == 2
    assert len(resolution.variants) == 1
    assert archive.superseded_by(resolution.report) is None


def test_same_level_corrections_with_different_values_conflict():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 56), "feed-a"),
        (CORRECTED_AGAIN, utc(16, 57), "feed-b"),
    )
    resolution = archive.lookup("KEWR", OBS_TIME)

    assert resolution.status == STATUS_CONFLICT
    assert resolution.report is None
    assert resolution.superseded_by is None
    assert len(resolution.variants) == 2


def test_conflict_is_visible_once_the_second_variant_arrives():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 56), "feed-a"),
    )
    assert archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 56)).code == CORRECTED

    archive.add(CORRECTED_AGAIN, utc(16, 57), source="feed-b")

    visible = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 57))
    assert visible.status == STATUS_CONFLICT
    assert archive.conflicts(as_of=utc(16, 57))[0].status == STATUS_CONFLICT


def test_trustworthy_sequence_orders_same_level_corrections():
    archive = Archive(month=MONTH, year=YEAR)
    archive.add(ORIGINAL, utc(16, 52), source="primary", sequence=1)
    archive.add(CORRECTED_AGAIN, utc(16, 57), source="primary", sequence=3)
    archive.add(CORRECTED, utc(16, 56), source="primary", sequence=2)

    resolution = archive.lookup("KEWR", OBS_TIME)

    assert resolution.status == STATUS_OBSERVED
    assert resolution.code == CORRECTED_AGAIN
    assert archive.superseded_by(archive.history("KEWR", OBS_TIME)[0]).code == CORRECTED


def test_partial_or_duplicate_sequences_do_not_order_a_conflict():
    archive = Archive(month=MONTH, year=YEAR)
    archive.add(ORIGINAL, utc(16, 52), source="primary")
    archive.add(CORRECTED, utc(16, 56), source="feed-a", sequence=2)
    archive.add(CORRECTED_AGAIN, utc(16, 57), source="feed-b")
    assert archive.lookup("KEWR", OBS_TIME).status == STATUS_CONFLICT

    duplicate = Archive(month=MONTH, year=YEAR)
    duplicate.add(ORIGINAL, utc(16, 52), source="primary")
    duplicate.add(CORRECTED, utc(16, 56), source="feed-a", sequence=2)
    duplicate.add(CORRECTED_AGAIN, utc(16, 57), source="feed-b", sequence=2)
    assert duplicate.lookup("KEWR", OBS_TIME).status == STATUS_CONFLICT


def test_same_level_corrections_with_equal_values_are_not_a_conflict():
    duplicate = CORRECTED.replace("19/14", "19/14") + "="
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 56), "feed-a"),
        (duplicate, utc(16, 57), "feed-b"),
    )
    resolution = archive.lookup("KEWR", OBS_TIME)

    assert resolution.status == STATUS_OBSERVED
    assert resolution.code == CORRECTED


def test_reversed_import_order_produces_the_same_result():
    forward = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 58), "primary"),
    )
    reverse = build(
        (CORRECTED, utc(16, 58), "primary"),
        (ORIGINAL, utc(16, 52), "primary"),
    )

    for cutoff in (utc(16, 53), utc(16, 59)):
        assert forward.lookup("KEWR", OBS_TIME, as_of=cutoff).code == reverse.lookup(
            "KEWR", OBS_TIME, as_of=cutoff
        ).code
    assert forward.superseded_by(forward.history("KEWR", OBS_TIME)[0]).code == (
        reverse.superseded_by(reverse.history("KEWR", OBS_TIME)[0]).code
    )


def test_reversed_files_do_not_turn_a_conflict_into_a_winner():
    records = [
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 56), "feed-a"),
        (CORRECTED_AGAIN, utc(16, 57), "feed-b"),
    ]
    forward = build(*records)
    reverse = build(*reversed(records))

    assert forward.lookup("KEWR", OBS_TIME).status == STATUS_CONFLICT
    assert reverse.lookup("KEWR", OBS_TIME).status == STATUS_CONFLICT


def test_replaying_the_same_report_is_idempotent():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 58), "primary"),
    )
    before = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 59))
    count = len(archive)

    archive.add(CORRECTED, utc(16, 58), source="primary")
    archive.add(CORRECTED, utc(18, 0), source="primary")

    assert len(archive) == count
    after = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 59))
    assert after.code == before.code
    assert after.superseded_by == before.superseded_by


def test_reimporting_a_file_does_not_duplicate_occurrences(tmp_path):
    path = tmp_path / "archive.txt"
    path.write_text("%s\n%s\n" % (ORIGINAL, CORRECTED), encoding="utf-8")
    archive = Archive(month=MONTH, year=YEAR)

    first = archive.ingest_file(path, received_at=utc(17, 0))
    second = archive.ingest_file(path, received_at=utc(17, 0))

    assert len(first) == len(second) == 2
    assert len(archive) == 2
    assert [report.uid for report in first] == [report.uid for report in second]


def test_cycle_file_headers_and_comments_are_skipped(tmp_path):
    path = tmp_path / "2026111216Z.TXT"
    path.write_text(
        "2026/11/12 16:00\n# cycle archive\n\n"
        "%s\n%s=\n" % (ORIGINAL, CORRECTED),
        encoding="utf-8",
    )
    archive = Archive(month=MONTH, year=YEAR)

    reports = archive.ingest_file(path, received_at=utc(17, 0))

    assert [report.code for report in reports] == [ORIGINAL, CORRECTED + "="]


def test_bad_uppercase_line_reports_its_line_number(tmp_path):
    path = tmp_path / "archive.txt"
    path.write_text("METAR KEWR\n", encoding="utf-8")
    archive = Archive(month=MONTH, year=YEAR)

    with pytest.raises(ArchiveError, match="line 1"):
        archive.ingest_file(path, received_at=utc(17, 0))


def test_file_replay_with_a_later_receipt_time_keeps_original_provenance(tmp_path):
    path = tmp_path / "archive.txt"
    path.write_text(ORIGINAL + "\n", encoding="utf-8")
    archive = Archive(month=MONTH, year=YEAR)

    original = archive.ingest_file(path, received_at=utc(16, 52))[0]
    replay = archive.ingest_file(path, received_at=utc(18, 0))[0]

    assert replay is original
    assert replay.received_at == utc(16, 52)
    assert archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 53)).code == ORIGINAL


def test_cc_series_is_a_later_revision_than_cor():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 55), "primary"),
        (CCB, utc(16, 59), "primary"),
    )

    resolution = archive.lookup("KEWR", OBS_TIME)
    assert resolution.status == STATUS_OBSERVED
    assert resolution.code == CCB

    visible = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 53))
    assert visible.superseded_by.code == CORRECTED
    assert visible.final.code == CCB


def test_late_retransmission_does_not_replace_a_newer_correction():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 55), "primary"),
        (CORRECTED, utc(16, 59), "secondary"),
    )
    visible = archive.lookup("KEWR", OBS_TIME, as_of=utc(16, 54))

    assert visible.superseded_by.code == CORRECTED
    assert visible.superseded_by.received_at == utc(16, 55)


def test_visible_at_returns_one_resolution_per_observation():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        ("METAR KEWR 121751Z 23012KT 10SM SCT025 20/14 A2990", utc(17, 53), "primary"),
    )

    snapshot = archive.visible_at(utc(18, 0))

    assert len(snapshot) == 2
    assert {item.time for item in snapshot} == {
        OBS_TIME_NAIVE,
        utc(17, 51).replace(tzinfo=None),
    }
    assert {(item.station_id, item.time): item.code for item in snapshot} == {
        key: value.code for key, value in archive.snapshot(utc(18, 0)).items()
    }


def test_reports_can_be_filtered_by_station_time_and_correction_flag():
    archive = build(
        (ORIGINAL, utc(16, 52), "primary"),
        (CORRECTED, utc(16, 58), "primary"),
    )

    assert len(archive.reports("kewr", OBS_TIME)) == 2
    assert len(archive.reports("KEWR", OBS_TIME, corrections=False)) == 1
    assert len(archive.reports("KEWR", OBS_TIME, corrections=True)) == 1
    assert archive.reports("KJFK", OBS_TIME) == ()


def test_missing_station_and_time_are_reported_as_missing():
    archive = build((ORIGINAL, utc(16, 52), "primary"))

    assert archive.lookup("KJFK", OBS_TIME).status == STATUS_MISSING
    assert archive.lookup("KEWR", utc(17, 51)).status == STATUS_MISSING


def test_archive_rejects_undecodable_or_untimed_reports():
    archive = Archive(month=MONTH, year=YEAR)

    with pytest.raises(ArchiveError):
        archive.add("not a metar", utc(16, 52))
    with pytest.raises(ArchiveError):
        archive.add("METAR KEWR", utc(16, 52))


def test_history_is_ordered_by_reception_not_import_order():
    archive = build(
        (CORRECTED, utc(16, 58), "primary"),
        (ORIGINAL, utc(16, 52), "primary"),
    )

    history = archive.history("KEWR", OBS_TIME)
    assert [report.code for report in history] == [ORIGINAL, CORRECTED]


def test_superseded_by_rejects_a_foreign_report():
    archive = build((ORIGINAL, utc(16, 52), "primary"))
    other = build((ORIGINAL, utc(16, 52), "primary"))

    with pytest.raises(ArchiveError):
        archive.superseded_by(other.reports()[0])
