#-------------------------------------------------------------------------------
# elftools example: elf_address_provenance.py
#
# Build a provenance archive for sampled virtual address ranges: for every
# half-open interval, record which file bytes of the ELF's PT_LOAD segments
# it maps to, which parts are memory-only zero fill, and which parts fall
# into unmapped address holes. The archive is emitted as JSON so it can be
# checked offline against the file.
#
# This code is in the public domain
#-------------------------------------------------------------------------------
import json
import sys

from elftools.elf.addressmap import LoadSegmentMap, merge_provenance
from elftools.elf.elffile import ELFFile


def _hex(value):
    return hex(value)


def _record_to_json(record):
    return {
        'start': _hex(record.start),
        'end': _hex(record.end),
        'kind': record.kind,
        'file_sources': [
            {
                'segment': source.segment_index,
                'file_offset': _hex(source.file_offset),
                'size': _hex(source.size),
            }
            for source in record.file_sources
        ],
        'zero_fill_sources': [
            {
                'segment': source.segment_index,
                'size': _hex(source.size),
            }
            for source in record.zero_fill_sources
        ],
    }


def build_archive(elffile, ranges):
    """ Build a provenance archive (a JSON-serializable dict) for an
        iterable of half-open (start, end) address ranges.
    """
    segmap = LoadSegmentMap(elffile.iter_segments())
    archive_ranges = []
    for start, end in ranges:
        records = segmap.query(start, end)
        # Splitting a sampled range into pieces and concatenating the
        # results must yield identical provenance; verify on a midpoint.
        if end - start > 1:
            mid = start + (end - start) // 2
            pieces = segmap.query(start, mid) + segmap.query(mid, end)
            assert merge_provenance(pieces) == records
        archive_ranges.append({
            'start': _hex(start),
            'end': _hex(end),
            'records': [_record_to_json(record) for record in records],
        })
    return {'ranges': archive_ranges}


def _sampled_ranges(elffile):
    """ Sampled ranges for this example: the whole loaded span (including
        any holes between segments) plus one range per PT_LOAD segment.
    """
    loads = [
        seg for seg in elffile.iter_segments()
        if seg['p_type'] == 'PT_LOAD' and seg['p_memsz'] > 0
    ]
    if not loads:
        return []
    span = (
        min(seg['p_vaddr'] for seg in loads),
        max(seg['p_vaddr'] + seg['p_memsz'] for seg in loads),
    )
    return [span] + [
        (seg['p_vaddr'], seg['p_vaddr'] + seg['p_memsz']) for seg in loads
    ]


def process_file(filename):
    print('Processing file:', filename)
    with open(filename, 'rb') as f:
        elffile = ELFFile(f)
        archive = build_archive(elffile, _sampled_ranges(elffile))
    print(json.dumps(archive, indent=2))


if __name__ == '__main__':
    if sys.argv[1] == '--test':
        for filename in sys.argv[2:]:
            process_file(filename)
