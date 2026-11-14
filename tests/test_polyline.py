from random import uniform, randint
import json
import time

import pytest

import polyline


def test_decode_multiple_points():
    d = polyline.decode('gu`wFnfys@???nKgE??gE?????oK????fE??fE')
    assert d == [
        (40.641, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ]


def test_decode_multiple_points_precision():
    d = polyline.decode('_epolA~ieoOnF??~{Bo}@??o}@?????_|B????n}@??n}@', 6)
    assert d == [
        (40.64112, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ]


def test_decode_official_example():
    d = polyline.decode('_p~iF~ps|U_ulLnnqC_mqNvxq`@')
    assert d == [
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ]


def test_decode_geojson():
    d = polyline.decode('_p~iF~ps|U_ulLnnqC_mqNvxq`@', geojson=True)
    assert d == [
        (-120.200, 38.500),
        (-120.950, 40.700),
        (-126.453, 43.252)
    ]


def test_decode_official_example_precision():
    d = polyline.decode('_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI', 6)
    assert d == [
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ]


def test_decode_single_point():
    d = polyline.decode('gu`wFf`ys@')
    assert d == [
        (40.641, -8.653)
    ]


def test_decode_single_point_precision():
    d = polyline.decode('o}oolAnkcoO', 6)
    assert d == [
        (40.641, -8.653)
    ]


def test_encode_multiple_points():
    e = polyline.encode([
        (40.641, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ])
    assert e == 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'


def test_encode_multiple_points_precision():
    e = polyline.encode([
        (40.64112345, -8.654),
        (40.641, -8.654),
        (40.641, -8.656),
        (40.642, -8.656),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.655),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.642, -8.653),
        (40.641, -8.653),
        (40.641, -8.654)
    ], 6)
    assert e == 'eepolA~ieoOtF??~{Bo}@??o}@?????_|B????n}@??n}@'

def test_encode_official_example():
    e = polyline.encode([
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ])
    assert e == '_p~iF~ps|U_ulLnnqC_mqNvxq`@'


def test_encode_geojson():
    e = polyline.encode([
        (-120.200, 38.500),
        (-120.950, 40.700),
        (-126.453, 43.252)
    ], geojson=True)
    assert e == '_p~iF~ps|U_ulLnnqC_mqNvxq`@'


def test_encode_official_example_precision():
    e = polyline.encode([
        (38.500, -120.200),
        (40.700, -120.950),
        (43.252, -126.453)
    ], 6)
    assert e == '_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI'


def test_encode_single_point():
    e = polyline.encode([
        (40.64155, -8.65344)
    ])
    assert e == 'ux`wF~bys@'

    e = polyline.encode([
        (40.641552, -8.653441)
    ])
    assert e == 'ux`wF~bys@'


def test_encode_empty():
    assert polyline.encode([]) == ''
    assert polyline.encode([], geojson=True) == ''
    assert polyline.decode(polyline.encode([])) == []


def test_encode_single_point_rounding():
    e = polyline.encode([
        (0, 0.000006),
        (0, 0.000002)
    ])
    assert e == '?A?@'


def test_rounding_py3_match_py2():
    e = polyline.encode([
        (36.05322, -112.084004),
        (36.053573, -112.083914),
        (36.053845, -112.083965)])
    assert e == 'ss`{E~kbkTeAQw@J'


def test_encode_single_point_precision():
    e = polyline.encode([
        (40.641123, -8.653321)
    ], 6)
    assert e == 'eepolAp_doO'

    e = polyline.encode([
        (40.6411233123, -8.6533214234)
    ], 6)
    assert e == 'eepolAp_doO'


def test_a_variety_of_precisions():
    """uses a generator to create a variety of lat-lon's across the global
        and tests a range of precision settings from 4 to 8"""

    def generator():
        while True:
            coords = []
            for i in range(2, randint(4, 10)):
                lat, lon = uniform(-180.0, 180.0), uniform(-180.0, 180.0)
                coords.append((lat, lon))
            yield coords

    patience = 3  # seconds.
    waypoints, okays = 0, 0

    g = generator()
    start = time.time()
    while time.time() < start + patience:
        precision = randint(4, 8)
        wp = next(g)
        waypoints += len(wp)
        poly = polyline.encode(wp, precision)
        wp2 = polyline.decode(poly, precision)
        if wp == wp2:
            okays += len(wp2)
        else:
            for idx, _ in enumerate(wp):
                dx, dy = abs(wp[idx][0] - wp2[idx][0]), abs(wp[idx][1] - wp2[idx][1])
                if dx > 10 ** -(precision - 1) or dy > 10 ** -(precision - 1):
                    print(f"idx={idx}, dx={dx}, dy={dy}")
                else:
                    okays += 1

    assert okays == waypoints
    print(
        f"encoded and decoded {100 * okays / float(waypoints):.2f}% correctly for {waypoints} "
        f"waypoints @ {round(waypoints / patience, 0)} wp/sec")


OFFICIAL = '_p~iF~ps|U_ulLnnqC_mqNvxq`@'


def _split_offsets(encoded):
    """Character ranges of each (lat, lon) pair, independent of the decoder."""
    pairs, index, start = [], 0, 0
    while index < len(encoded):
        for _ in range(2):
            while ord(encoded[index]) - 63 >= 0x20:
                index += 1
            index += 1
        pairs.append((start, index))
        start = index
    return pairs


def test_decode_with_provenance_matches_decode():
    points = polyline.decode_with_provenance(OFFICIAL)
    assert [(p.lat, p.lon) for p in points] == polyline.decode(OFFICIAL)
    assert [(p.start, p.end) for p in points] == _split_offsets(OFFICIAL)
    assert points[0].start == 0
    assert points[-1].end == len(OFFICIAL)
    assert all(a.end == b.start for a, b in zip(points, points[1:]))


def test_decode_with_provenance_geojson_and_precision():
    encoded = '_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI'
    points = polyline.decode_with_provenance(encoded, 6, geojson=True)
    assert [(p.lat, p.lon) for p in points] == polyline.decode(encoded, 6, geojson=True)
    assert [(p.start, p.end) for p in points] == _split_offsets(encoded)


def test_incremental_every_single_split_matches_whole_string():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    expected = polyline.decode(encoded)
    for cut in range(len(encoded) + 1):
        decoder = polyline.IncrementalDecoder()
        first = decoder.feed(encoded[:cut], 0)
        second = decoder.feed(encoded[cut:], cut)
        assert [p[:2] for p in first] + [p[:2] for p in second] == expected
        points = decoder.points
        assert [p[:2] for p in points] == expected
        assert [(p.start, p.end) for p in points] == _split_offsets(encoded)
        assert decoder.cursor == len(encoded)
        assert decoder.pending is False


def test_partial_point_is_not_emitted_until_complete():
    encoded = 'gu`wFf`ys@'  # one point, 10 chars
    decoder = polyline.IncrementalDecoder()
    # Split inside the first latitude value.
    assert decoder.feed(encoded[:2], 0) == []
    assert decoder.cursor == 2
    assert decoder.pending is True
    assert [p[:2] for p in decoder.feed(encoded[2:], 2)] == [(40.641, -8.653)]
    assert decoder.points[0].provenance == (0, len(encoded))


def test_incomplete_stream_reports_position():
    encoded = 'gu`wFf`ys@'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:9], 0)
    with pytest.raises(polyline.PolylineIncompleteError) as error:
        decoder.finish()
    assert error.value.position == 9
    assert error.value.point_start == 0


def test_state_roundtrip_after_every_split():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    expected = polyline.decode(encoded)
    for cut in range(len(encoded) + 1):
        decoder = polyline.IncrementalDecoder()
        decoder.feed(encoded[:cut], 0)
        snapshot = decoder.save_state()
        restored = polyline.IncrementalDecoder.from_state(snapshot)
        restored.feed(encoded[cut:], cut)
        assert [p[:2] for p in restored.points] == expected
        assert [p[:2] for p in decoder.points] == [p[:2] for p in expected[:len(decoder.points)]]
        assert [(p.start, p.end) for p in restored.points] == _split_offsets(encoded)

        json_restored = polyline.IncrementalDecoder.from_state(decoder.save_state(as_json=True))
        json_restored.feed(encoded[cut:], cut)
        assert [p[:2] for p in json_restored.points] == expected


def test_state_keeps_partial_value_and_source_text():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:7], 0)
    snapshot = json.loads(decoder.dump_state())
    assert snapshot['cursor'] == 7
    assert snapshot['raw'] == encoded[:7]
    assert snapshot['phase'] == 'lng'
    restored = polyline.IncrementalDecoder.resume(decoder.dump_state())
    assert restored.cursor == 7
    assert restored.pending is True
    assert restored.raw == encoded[:7]


def test_resend_of_previous_packet_is_idempotent():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    first = decoder.feed(encoded[:10], 0)
    assert len(first) == 1
    cursor = decoder.cursor
    result = decoder.feed(encoded[:10], 0)
    assert result == []
    assert result.duplicate_characters == 10
    assert result.new_characters == 0
    assert decoder.cursor == cursor
    assert [p[:2] for p in decoder.points] == [p[:2] for p in polyline.decode(encoded[:10])]


def test_straddling_resend_only_decodes_new_suffix():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:12], 0)
    result = decoder.feed(encoded[8:20], 8)
    assert result.duplicate_characters == 4
    assert result.new_characters == 8
    assert result.positions == _split_offsets(encoded)[2:4]
    assert [p[:2] for p in decoder.points] == polyline.decode(encoded[:18])


def test_resend_conflict_at_same_offset():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:5], 0)
    with pytest.raises(polyline.PolylineConflictError) as error:
        decoder.feed('gu`wX', 0)
    assert error.value.offset == 4
    assert error.value.expected == encoded[4]
    assert error.value.received == 'X'


def test_conflict_inside_straddling_resend_reports_absolute_offset():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:12], 0)
    with pytest.raises(polyline.PolylineConflictError) as error:
        decoder.feed(encoded[8:10] + 'X' + encoded[11:20], 8)
    assert error.value.offset == 10
    assert error.value.expected == encoded[10]
    assert error.value.received == 'X'
    assert error.value.packet_offset == 8
    assert 10 in {item['position'] for item in decoder.conflicts}


def test_conflict_does_not_advance_cursor():
    encoded = 'gu`wFf`ys@'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:5], 0)
    with pytest.raises(polyline.PolylineConflictError):
        decoder.feed(encoded[3:4] + 'X' + encoded[5:9], 3)
    assert decoder.cursor == 5
    decoder.feed(encoded[5:], 5)
    assert [p[:2] for p in decoder.points] == polyline.decode(encoded)


def test_gap_is_rejected():
    decoder = polyline.IncrementalDecoder()
    decoder.feed('_p~iF', 0)
    with pytest.raises(polyline.PolylineGapError) as error:
        decoder.feed('ps|U', 8)
    assert error.value.offset == 8
    assert error.value.expected == 5


def test_decode_incremental_packet_list_and_offsets():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    expected = polyline.decode(encoded)
    packets = [encoded[i:i + 3] for i in range(0, len(encoded), 3)]
    offsets, cursor = [], 0
    for packet in packets:
        offsets.append(cursor)
        cursor += len(packet)
    assert len(packets) > 1
    points = polyline.decode_incremental(list(zip(offsets, packets)))
    assert [p[:2] for p in points] == expected
    assert [(p.start, p.end) for p in points] == _split_offsets(encoded)
    assert polyline.decode_chunks(packets) == points


def test_resume_mid_packet_matches_single_pass():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    expected = polyline.decode(encoded)
    cuts = (4, 9, 13, 21, 34)
    points = []
    decoder = polyline.IncrementalDecoder()
    previous = 0
    for cut in cuts:
        points.extend(decoder.feed(encoded[previous:cut], previous))
        decoder = polyline.IncrementalDecoder.from_state(decoder.save_state(as_json=True))
        assert decoder.cursor == cut
        previous = cut
    points.extend(decoder.feed(encoded[previous:], previous))
    assert [p[:2] for p in points] == expected
    assert [p.provenance for p in points] == _split_offsets(encoded)


def test_feed_rejects_offsets_that_disagree():
    decoder = polyline.IncrementalDecoder()
    with pytest.raises(ValueError):
        decoder.feed('abc', offset=0, packet_offset=1)


def test_resend_previous_packet_after_restore_is_harmless():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:15], 0)
    state = decoder.save_state(as_json=True)
    resumed = polyline.IncrementalDecoder.from_state(state)
    result = resumed.feed(encoded[:15], 0)
    assert result == []
    assert result.duplicate_characters == 15
    resumed.feed(encoded[15:], 15)
    resumed.finish()
    assert [p.provenance for p in resumed.points] == _split_offsets(encoded)


def test_straddling_resend_after_restore_checks_old_and_new_part():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:15], 0)
    resumed = polyline.IncrementalDecoder.from_state(decoder.save_state())
    with pytest.raises(polyline.PolylineConflictError) as error:
        resumed.feed(encoded[10:13] + 'X' + encoded[14:24], 10)
    assert error.value.offset == 13
    assert error.value.expected == encoded[13]
    assert error.value.received == 'X'
    assert error.value.packet_offset == 10


def test_conflict_after_restore_matches_original_decoder():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    bad_offset = len(encoded) - 1
    bad_packet = encoded[:bad_offset] + ('A' if encoded[bad_offset] != 'A' else 'B')

    original = polyline.IncrementalDecoder()
    original.feed(encoded, 0)
    with pytest.raises(polyline.PolylineConflictError) as original_error:
        original.feed(bad_packet, 0)

    restored = polyline.IncrementalDecoder.from_state(original.save_state(as_json=True))
    with pytest.raises(polyline.PolylineConflictError) as restored_error:
        restored.feed(bad_packet, 0)

    assert restored_error.value.offset == original_error.value.offset == bad_offset
    assert restored_error.value.expected == original_error.value.expected
    assert restored_error.value.received == original_error.value.received


def test_stateless_decode_chunk_roundtrip():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    state = None
    points = []
    cursor = 0
    for size in (2, 5, 1, 7, 3, 20):
        packet = encoded[cursor:cursor + size]
        result = polyline.decode_chunk(packet, state, offset=cursor)
        assert result.cursor == cursor + len(packet)
        points.extend(result.points)
        state = result.state.to_json()
        cursor += len(packet)
    result = polyline.decode_chunk(encoded[cursor:], state, offset=cursor, finish=True)
    points.extend(result.points)
    assert [p[:2] for p in points] == polyline.decode(encoded)
    assert [p.provenance for p in points] == _split_offsets(encoded)
    assert result.state.complete is True
    assert result.state.consumed == len(encoded)


def test_decode_chunk_finish_rejects_partial_point():
    encoded = 'gu`wFf`ys@'
    with pytest.raises(polyline.PolylineIncompleteError):
        polyline.decode_chunk(encoded[:-1], offset=0, finish=True)


def test_decoder_state_is_plain_dict_and_json():
    decoded = polyline.decode_with_provenance(OFFICIAL)
    decoder = polyline.IncrementalDecoder()
    decoder.feed(OFFICIAL[:8], 0)
    state = decoder.save_state()
    assert isinstance(state, dict)
    assert state['cursor'] == 8
    assert state.cursor == 8
    assert state.pending is True
    assert state.complete is False
    assert state.to_dict()['raw'] == OFFICIAL[:8]
    restored = polyline.DecoderState.from_json(state.to_json())
    assert restored.cursor == 8
    assert polyline.IncrementalDecoder.from_state(restored).cursor == 8


def test_conflict_reports_same_offset_for_equivalent_packet_forms():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    decoder = polyline.IncrementalDecoder()
    decoder.feed(encoded[:12], 0)
    with pytest.raises(polyline.PolylineConflictError) as error:
        decoder.feed((8, encoded[8:9] + 'X' + encoded[10:16]))
    assert error.value.offset == 9
    assert error.value.packet_offset == 8


def test_breakpoint_between_latitude_and_half_longitude():
    encoded = 'gu`wFnfys@???nKgE??gE?????oK????fE??fE'
    first_end = _split_offsets(encoded)[0][1]
    # Two characters into the first point's longitude: the latitude is done and
    # the longitude value is only partly written.
    cut = first_end - 3
    assert ord(encoded[cut]) - 63 >= 0x20  # still inside the multi-character value

    decoder = polyline.IncrementalDecoder()
    first = decoder.feed(encoded[:cut], 0)
    assert first == []
    assert decoder.pending is True
    assert decoder.cursor == cut

    saved = decoder.save_state(as_json=True)
    resumed = polyline.IncrementalDecoder.from_state(saved)
    assert resumed.cursor == cut
    assert resumed.pending is True

    # Re-sending the previous packet is harmless when it matches.
    resent = resumed.feed(encoded[:cut], 0)
    assert resent == []
    assert resumed.cursor == cut

    # A re-send with a different character at an already-consumed offset is a
    # conflict, reported at the absolute character position.
    bad = encoded[:cut - 1] + ('A' if encoded[cut - 1] != 'A' else 'B')
    with pytest.raises(polyline.PolylineConflictError) as error:
        resumed.feed(bad, 0)
    assert error.value.offset == cut - 1
    assert error.value.expected == encoded[cut - 1]
    assert error.value.received == bad[cut - 1]

    # After restarting from the saved state, the remaining data completes the
    # point in exactly the same order and with the same provenance.
    resumed = polyline.IncrementalDecoder.from_state(saved)
    rest = resumed.feed(encoded[cut:], cut)
    resumed.finish()
    whole = polyline.decode_with_provenance(encoded)
    assert [tuple(p) for p in first + rest] == [tuple(p) for p in whole]
    assert [p.provenance for p in first + rest] == [p.provenance for p in whole]
    assert resumed.points[0].provenance == (0, first_end)
    assert resumed.cursor == len(encoded)
