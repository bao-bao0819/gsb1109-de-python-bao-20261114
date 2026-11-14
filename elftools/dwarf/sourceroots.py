#-------------------------------------------------------------------------------
# elftools: dwarf/sourceroots.py
#
# Attribute DWARF line-table file entries to relocated source trees.
#
# Line programs emitted on build machines keep absolute paths in their
# directory table while the archived source trees have been re-rooted.
# This module interprets each file-table entry per compilation unit and
# directory index, then finds the source trees that explain every entry
# through one consistent directory-prefix mapping.  Trees that only share
# an unrelated file (for example an identical README while the sibling
# sources differ) are rejected when checksums or the remaining entries
# expose the difference; trees that the evidence cannot distinguish are
# all retained as candidates.
#
# Eli Bendersky (eliben@gmail.com)
# This code is in the public domain
#-------------------------------------------------------------------------------
from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, NamedTuple

if TYPE_CHECKING:
    from .dwarfinfo import DWARFInfo
    from .lineprogram import LineProgram


# File-table indices are 1-based before DWARF v5 and 0-based in v5.
def _file_table_base(version: int) -> int:
    return 0 if version >= 5 else 1


class SourceMatch(NamedTuple):
    """ One consistent explanation of a line-table file entry.

        tree:
            Source tree root that owns the file.
        build_prefix:
            Absolute build-machine directory prefix replaced by tree.
            The empty string means the recorded path was relative.
        relative_path:
            POSIX-style path of the file below the tree root.
        filesystem_path:
            Actual path of the matched file on disk.
        checksum:
            (algorithm, digest) when a checksum was supplied and
            verified, otherwise None.
    """
    tree: str
    build_prefix: str
    relative_path: str
    filesystem_path: str
    checksum: tuple[str, str] | None


class FileOrigin(NamedTuple):
    """ Attribution result for one line-table file entry.

        cu_name:
            Name of the owning compilation unit (best effort).
        file_index:
            Index as encoded for the line state machine (1-based for
            DWARF < 5, 0-based for DWARF v5).
        name:
            Raw base name from the file table.
        dir_index:
            Raw directory index from the file table.
        build_path:
            Path decoded from the directory table, or the bare base
            name when the entry records no directory.
        status:
            'unique' when exactly one mapping explains the entry,
            'ambiguous' when several mappings remain indistinguishable,
            'unmatched' when no mapping explains the entry.
        sources:
            All surviving SourceMatch candidates; one element for
            'unique', several for 'ambiguous', empty for 'unmatched'.
        mtime:
            Recorded modification time when present, else None.
        size:
            Recorded file size when present, else None.
    """
    cu_name: str | None
    file_index: int
    name: str
    dir_index: int | None
    build_path: str
    status: str
    sources: tuple[SourceMatch, ...]
    mtime: int | None
    size: int | None


class RejectedCandidate(NamedTuple):
    """ Why a (tree, build prefix) mapping could not be accepted. """
    tree: str
    build_prefix: str
    reason: str
    build_path: str
    filesystem_path: str


class LineProgramResolution(NamedTuple):
    """ Full attribution for the file table of one compilation unit. """
    cu_name: str | None
    version: int
    origins: tuple[FileOrigin, ...]
    mappings: tuple[SourceMatch, ...]
    rejected: tuple[RejectedCandidate, ...]

    def unique_origins(self) -> tuple[FileOrigin, ...]:
        return tuple(o for o in self.origins if o.status == 'unique')

    def ambiguous_origins(self) -> tuple[FileOrigin, ...]:
        return tuple(o for o in self.origins if o.status == 'ambiguous')

    def unmatched_origins(self) -> tuple[FileOrigin, ...]:
        return tuple(o for o in self.origins if o.status == 'unmatched')


_DIGEST_LENGTHS = {32: 'md5', 40: 'sha1', 64: 'sha256', 128: 'sha512'}


def _decode_name(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='surrogateescape')
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _normalize_build_path(path: str) -> str:
    normalized = posixpath.normpath(path)
    return '' if normalized == '.' else normalized


def _digest_algorithm(digest: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return _DIGEST_LENGTHS.get(len(digest), 'sha256')


class _RecordedFile(NamedTuple):
    file_index: int
    name: str
    dir_index: int | None
    build_path: str
    is_absolute: bool
    mtime: int | None
    size: int | None


def _iter_recorded_files(header: Mapping[str, Any]) -> list[_RecordedFile]:
    """ Turn a parsed line program header into decoded file records.

        Mirrors the index semantics of the line state machine and the
        lpe_filename() example: directory index 0 means "no directory"
        before DWARF v5, while v5 indexes the directory table (whose
        entry 0 is the compilation directory) from zero.
    """
    version = int(header['version'])
    file_entries = header['file_entry']
    directories = header.get('include_directory') or ()
    records: list[_RecordedFile] = []
    base = _file_table_base(version)

    for position, entry in enumerate(file_entries):
        name = _decode_name(entry['name'])
        if not name:
            continue
        raw_dir_index = entry.get('dir_index')
        dir_index = None if raw_dir_index is None else int(raw_dir_index)

        directory = ''
        if version >= 5:
            # A missing directory index is equivalent to index 0, the
            # compilation directory.
            effective_index = 0 if dir_index is None else dir_index
            if directories and 0 <= effective_index < len(directories):
                directory = _decode_name(directories[effective_index])
        elif dir_index and 0 < dir_index <= len(directories):
            directory = _decode_name(directories[dir_index - 1])

        build_path = posixpath.join(directory, name) if directory else name
        records.append(_RecordedFile(
            file_index=position + base,
            name=name,
            dir_index=dir_index,
            build_path=_normalize_build_path(build_path),
            is_absolute=build_path.startswith('/'),
            mtime=_optional_int(entry.get('mtime')),
            size=_optional_int(entry.get('length')),
        ))
    return records


def _strip_prefix(build_path: str, prefix: str) -> str | None:
    """ Remove a build prefix, or None when the path is outside it. """
    if prefix == '/':
        return build_path.lstrip('/')
    if not build_path.startswith(prefix + '/'):
        return None
    return build_path[len(prefix) + 1:]


def _prefix_universe(absolute_paths: list[str]) -> list[str]:
    """ All prefixes appearing in the recorded absolute paths.

        Each candidate tree is tested against every prefix, so one tree
        can explain a subset of the entries while missing files are simply
        left to other trees, without weakening prefix consistency.
    """
    prefixes = {'/'}
    for build_path in absolute_paths:
        components = build_path.split('/')[1:]
        for cut in range(1, len(components) + 1):
            prefixes.add('/' + '/'.join(components[:cut]))
    return sorted(prefixes, key=lambda value: (value.count('/'), len(value)))


class ChecksumStore:
    """ Expected file digests keyed by tree-relative POSIX paths.

        Values are plain hex digests (algorithm inferred from the digest
        length) or (algorithm, digest) pairs.  Keys may be plain relative
        paths or prefixed with the tree root name, which disambiguates
        trees that contain the same relative path with different bytes.
        Only files present in the store are checked; a mismatch excludes
        that file from the offending tree but leaves the tree eligible
        for other files it genuinely explains.
    """

    def __init__(
        self,
        checksums: Mapping[str, str | tuple[str, str]] | None = None,
    ) -> None:
        self._checksums: dict[str, tuple[str, str]] = {}
        for key, value in (checksums or {}).items():
            relative = posixpath.normpath(key).lstrip('./') or key
            if isinstance(value, tuple):
                algorithm, digest = value
            else:
                digest = value.lower()
                algorithm = _digest_algorithm(digest, None)
            self._checksums[relative] = (algorithm, digest.lower())

    @classmethod
    def from_manifest(
        cls,
        lines: Iterable[str],
        default_algorithm: str = 'sha256',
    ) -> 'ChecksumStore':
        """ Parse checksum-file lines ("<digest>  <path>").

            The algorithm is inferred from the digest length when it is
            recognized, otherwise default_algorithm is used.
        """
        checksums: dict[str, str | tuple[str, str]] = {}
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            digest, separator, path = stripped.partition('  ')
            if not separator:
                digest, separator, path = stripped.partition(' *')
            digest = digest.lower()
            checksums[path] = (
                _DIGEST_LENGTHS.get(len(digest), default_algorithm), digest)
        return cls(checksums)

    def _lookup(self, root: Path, relative_path: str) -> tuple[str, str] | None:
        expected = self._checksums.get(relative_path)
        if expected is not None:
            return expected
        return self._checksums.get(posixpath.join(root.name, relative_path))

    def verify(
        self,
        root: Path,
        filesystem_path: Path,
        relative_path: str,
    ) -> tuple[str, str] | None:
        expected = self._lookup(root, relative_path)
        if expected is None:
            return None
        algorithm, digest = expected
        hasher = hashlib.new(algorithm)
        with filesystem_path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                hasher.update(chunk)
        if hasher.hexdigest().lower() != digest:
            raise ValueError(f'checksum_mismatch:{relative_path}')
        return expected


class SourceRootResolver:
    """ Attribute line-table file entries to relocated source trees. """

    def __init__(
        self,
        source_roots: Iterable[str | os.PathLike[str]],
        checksums: Mapping[str, str | tuple[str, str]] | ChecksumStore | None = None,
    ) -> None:
        self.roots = tuple(Path(root).resolve() for root in source_roots)
        if isinstance(checksums, ChecksumStore):
            self.checksums = checksums
        else:
            self.checksums = ChecksumStore(checksums)

    def resolve_line_program(
        self,
        line_program: LineProgram | Mapping[str, Any],
        cu_name: str | None = None,
    ) -> LineProgramResolution:
        """ Resolve every file entry of a line program or parsed header.

            For each source tree, every candidate build prefix is scored by
            how many recorded entries it explains through one fixed prefix,
            so the mapping stays consistent inside the compilation unit.
            Only the best-scoring prefix(es) of a tree contribute; ties are
            kept as separate candidates because the evidence cannot choose
            between them.  Each file entry is then attributed independently
            and stays ambiguous while multiple trees explain it equally.
        """
        header = (
            line_program.header
            if hasattr(line_program, 'header') else line_program)
        if hasattr(line_program, 'get_entries'):
            # Materialize entries appended via DW_LNE_define_file, which
            # mutate the header's file table in place.
            line_program.get_entries()

        records = _iter_recorded_files(header)
        absolute_paths = [r.build_path for r in records if r.is_absolute]

        rejected: list[RejectedCandidate] = []
        # build_path -> SourceMatch candidates collected across all trees.
        candidates: dict[str, list[SourceMatch]] = {
            record.build_path: [] for record in records}

        for root in self.roots:
            if not root.is_dir():
                continue
            prefixes = _prefix_universe(absolute_paths) if absolute_paths else ['']

            # prefix -> (matches by build_path, score)
            hypotheses: dict[str, dict[str, SourceMatch]] = {}
            for prefix in prefixes:
                matches: dict[str, SourceMatch] = {}
                for record in records:
                    match = self._evaluate_file(root, prefix, record, rejected)
                    if match is not None:
                        matches[record.build_path] = match
                if matches:
                    hypotheses[prefix] = matches
            if not hypotheses:
                continue
            best_score = max(len(matches) for matches in hypotheses.values())
            for matches in hypotheses.values():
                if len(matches) != best_score:
                    continue
                for build_path, match in matches.items():
                    candidates[build_path].append(match)

        origins = tuple(
            self._build_origin(record, candidates[record.build_path], cu_name)
            for record in records)
        mappings = tuple(
            {m.filesystem_path: m
             for matches in candidates.values() for m in matches}.values())
        return LineProgramResolution(
            cu_name=cu_name,
            version=int(header['version']),
            origins=origins,
            mappings=mappings,
            rejected=tuple(rejected),
        )

    def resolve_dwarf_info(
        self,
        dwarfinfo: DWARFInfo,
    ) -> tuple[LineProgramResolution, ...]:
        """ Resolve the line program of every compilation unit. """
        resolutions = []
        for cu in dwarfinfo.iter_CUs():
            line_program = dwarfinfo.line_program_for_CU(cu)
            if line_program is None:
                continue
            resolutions.append(
                self.resolve_line_program(line_program, self._cu_name(cu)))
        return tuple(resolutions)

    def _evaluate_file(
        self,
        root: Path,
        prefix: str,
        record: _RecordedFile,
        rejected: list[RejectedCandidate],
    ) -> SourceMatch | None:
        if record.is_absolute:
            relative = _strip_prefix(record.build_path, prefix)
            if relative is None:
                # This prefix simply does not cover the record.
                return None
        else:
            relative = record.build_path
        if not relative:
            rejected.append(RejectedCandidate(
                str(root), prefix, 'empty_relative_path',
                record.build_path, str(root)))
            return None
        filesystem_path = root / relative
        if not filesystem_path.is_file():
            rejected.append(RejectedCandidate(
                str(root), prefix, 'missing_file',
                record.build_path, str(filesystem_path)))
            return None
        # DWARF v2-v4 file entries carry the source size in 'length'.
        # A recorded non-zero size is content metadata that survives
        # re-rooting, so a mismatch rules this file out.  mtime is kept
        # informational because archiving usually rewrites timestamps.
        if record.size:
            actual_size = filesystem_path.stat().st_size
            if actual_size != record.size:
                rejected.append(RejectedCandidate(
                    str(root), prefix, 'size_mismatch',
                    record.build_path, str(filesystem_path)))
                return None
        try:
            checksum = self.checksums.verify(root, filesystem_path, relative)
        except ValueError:
            rejected.append(RejectedCandidate(
                str(root), prefix, 'checksum_mismatch',
                record.build_path, str(filesystem_path)))
            return None
        return SourceMatch(
            tree=str(root),
            build_prefix=prefix,
            relative_path=relative,
            filesystem_path=str(filesystem_path),
            checksum=checksum,
        )

    def _build_origin(
        self,
        record: _RecordedFile,
        sources: list[SourceMatch],
        cu_name: str | None,
    ) -> FileOrigin:
        unique_sources = tuple(
            {s.filesystem_path: s for s in sources}.values())
        if len(unique_sources) == 1:
            status = 'unique'
        elif len(unique_sources) > 1:
            status = 'ambiguous'
        else:
            status = 'unmatched'
        return FileOrigin(
            cu_name=cu_name,
            file_index=record.file_index,
            name=record.name,
            dir_index=record.dir_index,
            build_path=record.build_path,
            status=status,
            sources=unique_sources,
            mtime=record.mtime,
            size=record.size,
        )

    @staticmethod
    def _cu_name(cu: Any) -> str | None:
        try:
            die = cu.get_top_DIE()
        except Exception:
            return None
        attribute = die.attributes.get('DW_AT_name')
        if attribute is None:
            return None
        value = attribute.value
        if isinstance(value, bytes):
            return value.decode('utf-8', errors='surrogateescape')
        return str(value)


def resolve_sources(
    line_program: LineProgram | Mapping[str, Any],
    source_roots: Iterable[str | os.PathLike[str]],
    checksums: Mapping[str, str | tuple[str, str]] | ChecksumStore | None = None,
    cu_name: str | None = None,
) -> LineProgramResolution:
    """ Convenience wrapper for a single line program or parsed header. """
    return SourceRootResolver(source_roots, checksums).resolve_line_program(
        line_program, cu_name)


def resolve_dwarf_sources(
    dwarfinfo: DWARFInfo,
    source_roots: Iterable[str | os.PathLike[str]],
    checksums: Mapping[str, str | tuple[str, str]] | ChecksumStore | None = None,
) -> tuple[LineProgramResolution, ...]:
    """ Convenience wrapper resolving every compilation unit. """
    return SourceRootResolver(source_roots, checksums).resolve_dwarf_info(dwarfinfo)


__all__ = [
    'ChecksumStore',
    'FileOrigin',
    'LineProgramResolution',
    'RejectedCandidate',
    'SourceMatch',
    'SourceRootResolver',
    'resolve_dwarf_sources',
    'resolve_sources',
]
