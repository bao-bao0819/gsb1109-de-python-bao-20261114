#-------------------------------------------------------------------------------
# elftools: elf/debuglink.py
#
# Matching ELF files with separately archived external debug information.
#
# This module is in the public domain.
#-------------------------------------------------------------------------------
"""Find external debug files for ELF files from an offline archive.

The GNU separate-debug-file convention provides two independent pieces of
identity:

* ``.gnu_debuglink`` contains a file name and the CRC32 of the external file.
* ``.note.gnu.build-id`` contains a build ID which normally also exists in the
  debug file produced by ``objcopy --only-keep-debug``.

Offline archives frequently contain several builds that share a file name
(``libfoo.so.debug`` is by far the most common case).  Matching by name alone
is therefore unsafe, and a matching name with a wrong debuglink checksum is
never acceptable.  This module evaluates every candidate against both
identifiers, keeps the evidence for rejected candidates, and deliberately
reports ambiguity instead of guessing when the available identifiers cannot
tell two different files apart.
"""
from __future__ import annotations

import hashlib
import json
import os
import zlib
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, Any

from ..common.exceptions import ELFError
from .elffile import ELFFile

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


STATUS_MATCHED = 'matched'
STATUS_AMBIGUOUS = 'ambiguous'
STATUS_UNMATCHED = 'unmatched'
STATUS_UNIDENTIFIED = 'unidentified'
STATUS_ERROR = 'error'

# Rejection reasons are stable identifiers so callers can process reports
# without parsing the human-readable evidence strings.
REASON_CHECKSUM_MISMATCH = 'debuglink_checksum_mismatch'
REASON_FILENAME_MISMATCH = 'debuglink_filename_mismatch'
REASON_BUILD_ID_MISMATCH = 'build_id_mismatch'
REASON_NO_DEBUG_INFO = 'no_debug_info'
REASON_NO_IDENTITY = 'no_identity'
REASON_SELF_REFERENCE = 'self_reference'
REASON_UNREADABLE = 'unreadable'
REASON_NO_IDENTIFIER_MATCH = 'no_identifier_match'
REASON_EXCLUDED_BY_BUILD_ID = 'excluded_by_build_id'


def _hex32(value: int) -> str:
    return f'0x{value & 0xffffffff:08x}'


@dataclass(frozen=True)
class DebugLink:
    """The contents of a ``.gnu_debuglink`` section."""

    filename: str
    checksum: int

    @property
    def checksum_hex(self) -> str:
        return _hex32(self.checksum)

    def to_dict(self) -> dict[str, Any]:
        return {
            'filename': self.filename,
            'checksum': self.checksum,
            'checksum_hex': self.checksum_hex,
        }


@dataclass(frozen=True)
class CandidateManifest:
    """A list of files and directories that may contain debug files.

    ``directories`` are searched (recursively by default); ``files`` are used
    exactly as listed.  Relative entries are resolved against ``base_dir`` when
    the manifest was loaded from a file.
    """

    directories: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    elfs: tuple[str, ...] = ()
    recursive: bool = True
    base_dir: str | None = None
    warnings: tuple[str, ...] = ()

    def iter_candidate_paths(self) -> Iterator[str]:
        """Yield candidate paths in a deterministic order, without duplicates.

        A directory entry contributes all regular files below it.  Missing
        entries are reported through ``warnings`` by :meth:`from_file` and are
        silently skipped here.
        """
        seen: set[str] = set()
        for path in self.files:
            if not os.path.isfile(path):
                continue
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            yield path
        for directory in self.directories:
            if not os.path.isdir(directory):
                continue
            for path in _walk_files(directory, self.recursive):
                real = os.path.realpath(path)
                if real in seen:
                    continue
                seen.add(real)
                yield path

    def to_dict(self) -> dict[str, Any]:
        return {
            'directories': list(self.directories),
            'files': list(self.files),
            'elfs': list(self.elfs),
            'recursive': self.recursive,
            'warnings': list(self.warnings),
        }

    def _replace_warnings(self, warnings: tuple[str, ...]) -> CandidateManifest:
        return CandidateManifest(
            directories=self.directories,
            files=self.files,
            elfs=self.elfs,
            recursive=self.recursive,
            base_dir=self.base_dir,
            warnings=warnings,
        )

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> CandidateManifest:
        """Load a JSON or line-oriented manifest.

        JSON accepts either a list of paths, or an object with ``directories``
        (aliases: ``dirs``, ``search_dirs``, ``search_paths``), ``files``
        (alias: ``candidates``), ``elfs`` (aliases: ``elf``, ``executables``)
        and ``recursive``.  A plain text manifest contains one path per line;
        blank lines and lines starting with ``#`` are ignored.
        """
        manifest_path = os.fspath(path)
        with open(manifest_path, 'r', encoding='utf-8') as stream:
            text = stream.read()
        base_dir = os.path.dirname(os.path.abspath(manifest_path))

        stripped = text.lstrip()
        if not stripped:
            return cls(base_dir=base_dir)
        if stripped[0] in '[{':
            try:
                document = json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError(f'Invalid debug manifest JSON: {error}') from error
            manifest = cls._from_json_document(document, base_dir)
        else:
            entries = [
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith('#')
            ]
            manifest = cls._from_entries(entries, base_dir, recursive=True)

        warnings = list(manifest.warnings)
        for directory in manifest.directories:
            if not os.path.isdir(directory):
                warnings.append(f'Manifest directory does not exist: {directory}')
        for file_path in manifest.files:
            if not os.path.isfile(file_path):
                warnings.append(f'Manifest file does not exist: {file_path}')
        for elf_path in manifest.elfs:
            if not os.path.isfile(elf_path):
                warnings.append(f'Manifest ELF does not exist: {elf_path}')
        if warnings != list(manifest.warnings):
            manifest = manifest._replace_warnings(tuple(warnings))
        return manifest

    @classmethod
    def from_json(cls, document: Any, base_dir: str | None = None) -> CandidateManifest:
        """Build a manifest from an already-decoded JSON document."""
        return cls._from_json_document(document, base_dir)

    @classmethod
    def from_paths(
        cls,
        directories: Iterable[str | os.PathLike[str]] = (),
        files: Iterable[str | os.PathLike[str]] = (),
        *,
        recursive: bool = True,
    ) -> CandidateManifest:
        return cls(
            directories=tuple(os.fspath(path) for path in directories),
            files=tuple(os.fspath(path) for path in files),
            recursive=recursive,
        )

    @classmethod
    def _from_json_document(
        cls,
        document: Any,
        base_dir: str | None,
    ) -> CandidateManifest:
        if isinstance(document, list):
            return cls._from_entries(document, base_dir, recursive=True)
        if not isinstance(document, dict):
            raise ValueError('Debug manifest must be a JSON object or array')

        directories: list[str] = []
        files: list[str] = []
        elfs: list[str] = []
        warnings: list[str] = []

        for key in ('directories', 'dirs', 'search_dirs', 'search_paths'):
            directories.extend(_flatten_entries(document.get(key), 'directory',
                                                base_dir, warnings))
        for key in ('files', 'candidates'):
            files.extend(_flatten_entries(document.get(key), 'file',
                                         base_dir, warnings))
        for key in ('elfs', 'elf', 'executables'):
            elfs.extend(_flatten_entries(document.get(key), 'elf',
                                        base_dir, warnings))

        recursive = document.get('recursive', True)
        if not isinstance(recursive, bool):
            raise ValueError('Debug manifest "recursive" must be a boolean')
        return cls(
            directories=tuple(directories),
            files=tuple(files),
            elfs=tuple(elfs),
            recursive=recursive,
            base_dir=base_dir,
            warnings=tuple(warnings),
        )

    @classmethod
    def _from_entries(
        cls,
        entries: Sequence[Any],
        base_dir: str | None,
        *,
        recursive: bool,
    ) -> CandidateManifest:
        directories: list[str] = []
        files: list[str] = []
        elfs: list[str] = []
        warnings: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                resolved = _resolve_path(entry, base_dir)
                if os.path.isdir(resolved):
                    directories.append(resolved)
                else:
                    files.append(resolved)
                continue
            if not isinstance(entry, dict):
                warnings.append(f'Ignored manifest entry of type {type(entry).__name__}')
                continue
            kind = entry.get('kind')
            if not isinstance(kind, str) or kind not in (
                    'directory', 'file', 'elf', 'path'):
                warnings.append(f'Ignored manifest entry with invalid kind {kind!r}')
                continue
            for key in ('path', 'directory', 'dir', 'file', 'elf', 'elfs'):
                if key not in entry:
                    continue
                effective_kind = kind
                if key in ('directory', 'dir'):
                    effective_kind = 'directory'
                elif key == 'file':
                    effective_kind = 'file'
                elif key in ('elf', 'elfs'):
                    effective_kind = 'elf'
                values = entry[key] if isinstance(entry[key], list) else [entry[key]]
                for value in values:
                    if not isinstance(value, str):
                        warnings.append(f'Ignored non-string manifest entry {value!r}')
                        continue
                    resolved = _resolve_path(value, base_dir)
                    if effective_kind == 'elf':
                        elfs.append(resolved)
                    elif effective_kind == 'directory' or (
                            effective_kind == 'path' and os.path.isdir(resolved)):
                        directories.append(resolved)
                    else:
                        files.append(resolved)
        return cls(
            directories=tuple(directories),
            files=tuple(files),
            elfs=tuple(elfs),
            recursive=recursive,
            base_dir=base_dir,
            warnings=tuple(warnings),
        )


def _resolve_path(path: str, base_dir: str | None) -> str:
    path = os.path.expanduser(path)
    if base_dir is not None and not os.path.isabs(path):
        return os.path.normpath(os.path.join(base_dir, path))
    return os.path.normpath(path)


def _flatten_entries(
    value: Any,
    kind: str,
    base_dir: str | None,
    warnings: list[str],
) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if isinstance(item, str):
            result.append(_resolve_path(item, base_dir))
        elif isinstance(item, dict) and isinstance(item.get('path'), str):
            result.append(_resolve_path(item['path'], base_dir))
        else:
            warnings.append(f'Ignored invalid {kind} manifest entry {item!r}')
    return result


def _walk_files(directory: str, recursive: bool) -> Iterator[str]:
    if not recursive:
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                yield path
        return

    for root, dirs, files in os.walk(directory):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                yield path


def _checksum_file(path: str) -> int:
    checksum = 0
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            checksum = zlib.crc32(chunk, checksum)
    return checksum & 0xffffffff


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class _CandidateFile:
    """Immutable facts about a candidate file, shared between ELF matches.

    The CRC32 and SHA-256 values are deliberately not computed here.  An
    offline archive may contain many unrelated files, so those hashes are
    calculated on demand by :class:`DebugInfoMatcher` for the candidates that
    actually take part in a match.
    """

    path: str
    realpath: str
    basename: str
    size: int | None
    build_ids: tuple[str, ...]
    is_elf: bool
    has_debug_info: bool
    error: str | None

    @property
    def build_id(self) -> str | None:
        return self.build_ids[0] if self.build_ids else None

    @classmethod
    def inspect(cls, path: str) -> _CandidateFile:
        realpath = os.path.realpath(path)
        basename = os.path.basename(path)
        try:
            size = os.stat(path).st_size
            with open(path, 'rb') as stream:
                is_elf = stream.read(4) == b'\x7fELF'
        except OSError as error:
            return cls(path, realpath, basename, None, (), False, False,
                       str(error))

        build_ids: tuple[str, ...] = ()
        has_debug_info = False
        if is_elf:
            # Construct the ELFFile explicitly so a malformed header cannot
            # leak the stream: load_from_path() has no way to close on failure.
            with open(path, 'rb') as stream:
                try:
                    elffile = ELFFile(
                        stream, ELFFile.make_relative_loader(path))
                except (ELFError, OSError, ValueError):
                    elffile = None
                if elffile is not None:
                    try:
                        build_ids = tuple(elffile.get_build_ids())
                        has_debug_info = elffile.has_dwarf_info(strict=True)
                    except (ELFError, OSError, ValueError):
                        # A file with a matching debuglink name and CRC is
                        # still useful evidence, but it cannot contribute a
                        # build ID.
                        build_ids = ()
                        has_debug_info = False
                    finally:
                        elffile.close()
        return cls(path, realpath, basename, size, build_ids, is_elf,
                   has_debug_info, None)


@dataclass
class CandidateEvaluation:
    """How one candidate relates to one ELF file."""

    path: str
    basename: str
    size: int | None
    checksum: int | None
    sha256: str | None
    build_id: str | None
    build_ids: tuple[str, ...]
    is_elf: bool
    has_debug_info: bool
    filename_matches: bool
    checksum_matches: bool | None
    build_id_matches: bool | None
    eligible: bool
    accepted_by: tuple[str, ...]
    rejection_reason: str | None
    selected: bool
    evidence: tuple[str, ...]
    error: str | None = None

    @property
    def checksum_hex(self) -> str | None:
        return None if self.checksum is None else _hex32(self.checksum)

    def to_dict(self) -> dict[str, Any]:
        return {
            'path': self.path,
            'basename': self.basename,
            'size': self.size,
            'checksum': self.checksum,
            'checksum_hex': self.checksum_hex,
            'sha256': self.sha256,
            'build_id': self.build_id,
            'build_ids': list(self.build_ids),
            'is_elf': self.is_elf,
            'has_debug_info': self.has_debug_info,
            'filename_matches': self.filename_matches,
            'checksum_matches': self.checksum_matches,
            'build_id_matches': self.build_id_matches,
            'eligible': self.eligible,
            'accepted_by': list(self.accepted_by),
            'rejection_reason': self.rejection_reason,
            'selected': self.selected,
            'evidence': list(self.evidence),
            'error': self.error,
        }


@dataclass
class DebugInfoMatch:
    """The matching result for one ELF file."""

    elf: str
    status: str
    build_ids: tuple[str, ...]
    debuglink: DebugLink | None
    matched_by: tuple[str, ...]
    selected: CandidateEvaluation | None
    candidates: list[CandidateEvaluation]
    evidence: list[str] = field(default_factory=list)
    error: str | None = None
    elf_class: int | None = None
    machine: str | None = None
    elf_type: str | None = None

    @property
    def build_id(self) -> str | None:
        return self.build_ids[0] if self.build_ids else None

    @property
    def matched(self) -> bool:
        return self.status == STATUS_MATCHED

    @property
    def ambiguous(self) -> bool:
        return self.status == STATUS_AMBIGUOUS

    def to_dict(self) -> dict[str, Any]:
        return {
            'elf': self.elf,
            'status': self.status,
            'matched': self.matched,
            'ambiguous': self.ambiguous,
            'identity': {
                'build_id': self.build_id,
                'build_ids': list(self.build_ids),
                'debuglink': None if self.debuglink is None else self.debuglink.to_dict(),
            },
            'matched_by': list(self.matched_by),
            'selected': None if self.selected is None else self.selected.to_dict(),
            'candidates': [candidate.to_dict() for candidate in self.candidates],
            'evidence': list(self.evidence),
            'error': self.error,
            'elf_class': self.elf_class,
            'machine': self.machine,
            'elf_type': self.elf_type,
        }


class DebugInfoMatcher:
    """Match ELF files against a fixed collection of candidate files."""

    def __init__(
        self,
        candidates: CandidateManifest | Iterable[str | os.PathLike[str]],
        *,
        recursive: bool = True,
    ) -> None:
        if isinstance(candidates, CandidateManifest):
            self.manifest = candidates
        else:
            self.manifest = CandidateManifest(
                files=tuple(os.fspath(path) for path in candidates),
                recursive=recursive,
            )
        self._candidate_cache: dict[str, _CandidateFile] = {}
        self._checksum_cache: dict[str, int | None] = {}
        self._sha256_cache: dict[str, str | None] = {}
        self._paths: list[str] | None = None

    @property
    def candidate_paths(self) -> tuple[str, ...]:
        if self._paths is None:
            self._paths = list(self.manifest.iter_candidate_paths())
        return tuple(self._paths)

    def _inspect(self, path: str) -> _CandidateFile:
        real = os.path.realpath(path)
        cached = self._candidate_cache.get(real)
        if cached is None:
            cached = _CandidateFile.inspect(path)
            self._candidate_cache[real] = cached
        return cached

    def _checksum(self, path: str) -> int | None:
        real = os.path.realpath(path)
        if real not in self._checksum_cache:
            try:
                self._checksum_cache[real] = _checksum_file(path)
            except OSError:
                self._checksum_cache[real] = None
        return self._checksum_cache[real]

    def _sha256(self, path: str) -> str | None:
        real = os.path.realpath(path)
        if real not in self._sha256_cache:
            try:
                self._sha256_cache[real] = _sha256_file(path)
            except OSError:
                self._sha256_cache[real] = None
        return self._sha256_cache[real]

    def match(self, elf_path: str | os.PathLike[str]) -> DebugInfoMatch:
        path = os.fspath(elf_path)
        try:
            with ELFFile.load_from_path(path) as elffile:
                build_ids = tuple(elffile.get_build_ids())
                debuglink = _read_debuglink(elffile)
                elf_class = elffile.elfclass
                machine = elffile.get_machine_arch()
                elf_type = str(elffile['e_type'])
        except (ELFError, OSError, ValueError) as error:
            return DebugInfoMatch(
                elf=path,
                status=STATUS_ERROR,
                build_ids=(),
                debuglink=None,
                matched_by=(),
                selected=None,
                candidates=[],
                evidence=[f'Cannot parse ELF file: {error}'],
                error=str(error),
            )

        result = DebugInfoMatch(
            elf=path,
            status=STATUS_UNIDENTIFIED,
            build_ids=build_ids,
            debuglink=debuglink,
            matched_by=(),
            selected=None,
            candidates=[],
            elf_class=elf_class,
            machine=machine,
            elf_type=elf_type,
        )
        if debuglink is not None:
            result.evidence.append(
                f'.gnu_debuglink: filename={debuglink.filename!r}, '
                f'checksum={debuglink.checksum_hex} ({debuglink.checksum})')
        if build_ids:
            result.evidence.append('ELF GNU build ID: ' + ', '.join(build_ids))
            if len(build_ids) > 1:
                result.evidence.append(
                    'ELF contains conflicting GNU build IDs: ' + ', '.join(build_ids))
        if debuglink is None and not build_ids:
            result.evidence.append(
                'ELF has neither .gnu_debuglink nor a GNU build ID; '
                'no candidate can be selected without guessing')
            for candidate in self._evaluate_candidates(result, path):
                candidate.eligible = False
                candidate.rejection_reason = REASON_NO_IDENTITY
                candidate.evidence = candidate.evidence + (
                    'ELF has no usable identity for matching',)
                result.candidates.append(candidate)
            return result

        result.candidates = self._evaluate_candidates(result, path)
        self._resolve(result)
        return result

    def match_all(
        self,
        elf_paths: Iterable[str | os.PathLike[str]],
    ) -> list[DebugInfoMatch]:
        return [self.match(path) for path in elf_paths]

    def _evaluate_candidates(
        self,
        result: DebugInfoMatch,
        elf_path: str,
    ) -> list[CandidateEvaluation]:
        elf_realpath = os.path.realpath(elf_path)
        elf_identity_build_id = _single_build_id(result.build_ids)
        debuglink = result.debuglink
        declared_name = None
        if debuglink is not None:
            declared_name = os.path.basename(debuglink.filename)

        evaluations: list[CandidateEvaluation] = []
        for candidate_path in self.candidate_paths:
            candidate = self._inspect(candidate_path)
            if candidate.realpath == elf_realpath:
                evaluations.append(self._make_evaluation(
                    candidate, False, None, None, False, (),
                    REASON_SELF_REFERENCE,
                    ('Candidate is the ELF file itself, not external debug info',)))
                continue
            if candidate.error is not None:
                evaluations.append(self._make_evaluation(
                    candidate, False, None, None, False, (),
                    REASON_UNREADABLE,
                    (f'Cannot read candidate: {candidate.error}',)))
                continue

            filename_matches = (
                declared_name is not None and
                candidate.basename == declared_name)
            candidate_checksum: int | None = None
            checksum_matches: bool | None = None
            if debuglink is not None and filename_matches:
                candidate_checksum = self._checksum(candidate.path)
                if candidate_checksum is not None:
                    checksum_matches = candidate_checksum == debuglink.checksum

            build_id_matches: bool | None = None
            if elf_identity_build_id is not None:
                candidate_build_id = _single_build_id(candidate.build_ids)
                if candidate_build_id is not None:
                    build_id_matches = candidate_build_id == elf_identity_build_id

            evidence: list[str] = []
            if debuglink is not None:
                if filename_matches:
                    evidence.append(
                        f"filename matches .gnu_debuglink entry "
                        f"{debuglink.filename!r}")
                else:
                    evidence.append(
                        f"filename {candidate.basename!r} does not match "
                        f".gnu_debuglink entry {debuglink.filename!r}")
            if checksum_matches is True:
                evidence.append(
                    f'CRC32 {_hex32(candidate_checksum)} matches .gnu_debuglink '
                    f'checksum {debuglink.checksum_hex if debuglink else ""}')
            elif candidate_checksum is not None and debuglink is not None:
                evidence.append(
                    f'CRC32 {_hex32(candidate_checksum)} does not match '
                    f'.gnu_debuglink checksum {debuglink.checksum_hex}')
            if build_id_matches is True:
                evidence.append(
                    f'GNU build ID {candidate.build_id} matches ELF build ID')
            elif build_id_matches is False:
                evidence.append(
                    f'GNU build ID {candidate.build_id} differs from ELF build ID '
                    + ', '.join(result.build_ids))
            elif elf_identity_build_id is not None and not candidate.build_ids:
                evidence.append(
                    'candidate has no GNU build ID; it cannot resolve a '
                    'build-ID collision')
            if candidate.has_debug_info:
                evidence.append('candidate contains DWARF debug sections')

            reason: str | None = None
            accepted_by: tuple[str, ...] = ()
            if filename_matches and checksum_matches is False:
                reason = REASON_CHECKSUM_MISMATCH
                evidence.append(
                    'debuglink filename matches but its checksum does not; '
                    'the candidate is disqualified')
            elif filename_matches and checksum_matches is True:
                # The GNU debuglink name+CRC pair is an identity in its own
                # right. A differing build ID is recorded as evidence and can
                # break a tie between same-name, same-CRC contents, but it does
                # not by itself disqualify the file the ELF names and checksums.
                accepted_by = ('debuglink',)
                if build_id_matches is True:
                    accepted_by = ('build_id', 'debuglink')
                elif build_id_matches is False:
                    evidence.append(
                        'the debuglink identity matches but the GNU build ID '
                        'differs; this is noted as evidence')
            elif build_id_matches is True:
                if candidate.has_debug_info:
                    accepted_by = ('build_id',)
                else:
                    reason = REASON_NO_DEBUG_INFO
                    evidence.append(
                        'build ID matches but the candidate has no DWARF '
                        'debug sections')
            elif build_id_matches is False:
                reason = REASON_BUILD_ID_MISMATCH
            elif debuglink is not None:
                reason = REASON_FILENAME_MISMATCH
            else:
                reason = REASON_NO_IDENTIFIER_MATCH

            evaluations.append(self._make_evaluation(
                candidate, filename_matches, checksum_matches,
                build_id_matches, bool(accepted_by), accepted_by, reason,
                tuple(evidence)))
        return evaluations

    def _make_evaluation(
        self,
        candidate: _CandidateFile,
        filename_matches: bool,
        checksum_matches: bool | None,
        build_id_matches: bool | None,
        eligible: bool,
        accepted_by: tuple[str, ...],
        reason: str | None,
        evidence: tuple[str, ...],
    ) -> CandidateEvaluation:
        return CandidateEvaluation(
            path=candidate.path,
            basename=candidate.basename,
            size=candidate.size,
            # Record hashes only for candidates that took part in a match.
            # This keeps large offline archives cheap: unrelated files are
            # listed but never read past their ELF header.
            checksum=(
                self._checksum(candidate.path)
                if filename_matches or eligible else None),
            sha256=(
                self._sha256(candidate.path)
                if filename_matches or eligible else None),            build_id=candidate.build_id,
            build_ids=candidate.build_ids,
            is_elf=candidate.is_elf,
            has_debug_info=candidate.has_debug_info,
            filename_matches=filename_matches,
            checksum_matches=checksum_matches,
            build_id_matches=build_id_matches,
            eligible=eligible,
            accepted_by=accepted_by,
            rejection_reason=reason,
            selected=False,
            evidence=evidence,
            error=candidate.error,
        )

    def _resolve(self, result: DebugInfoMatch) -> None:
        eligible = [candidate for candidate in result.candidates if candidate.eligible]
        if not eligible:
            result.status = STATUS_UNMATCHED
            result.evidence.append(
                'No candidate satisfies the ELF debuglink or build-ID identity')
            return

        groups = _group_by_content(eligible)
        if len(groups) == 1:
            _select_group(result, groups[0])
            return

        # Same identity, different contents.  A group can be dismissed only
        # when every copy in it is positively identified as a different build
        # by its GNU build ID.  Anything less is not evidence enough to pick a
        # winner, matching the conservative rule for offline archives.
        excluded = [group for group in groups if _positively_other_build(group)]
        remaining = [group for group in groups if not _positively_other_build(group)]

        for group in excluded:
            for candidate in group:
                candidate.eligible = False
                candidate.rejection_reason = REASON_EXCLUDED_BY_BUILD_ID
                candidate.evidence = candidate.evidence + (
                    'excluded because its GNU build ID differs from the ELF '
                    'build ID while another candidate matches the ELF identity',)
                result.evidence.append(
                    f'Excluded {candidate.path}: debuglink name/CRC32 and/or '
                    f'build ID identify a candidate, but candidate build ID '
                    f'{candidate.build_id} differs from ELF build ID '
                    f'{result.build_id}; it belongs to a different build')

        if not remaining:
            result.status = STATUS_UNMATCHED
            result.evidence.append(
                'Every candidate matching the available identity is positively '
                'identified as a different build by its GNU build ID')
            return

        if len(remaining) == 1:
            _select_group(result, remaining[0])
            return

        if any(candidate.build_id_matches is True
               for group in remaining for candidate in group):
            result.evidence.append(
                'Ambiguous: different contents remain; at least one carries the '
                'matching GNU build ID, but at least one other cannot be '
                'positively excluded because it has no usable GNU build ID')
        else:
            result.evidence.append(
                'Ambiguous: different contents match the same debuglink name/'
                'CRC32 or build ID, and the available identifiers cannot '
                'separate them')
        _mark_ambiguous(result, remaining)


def _group_by_content(
    candidates: list[CandidateEvaluation],
) -> list[list[CandidateEvaluation]]:
    groups: dict[str, list[CandidateEvaluation]] = {}
    for candidate in candidates:
        # A candidate whose SHA-256 could not be computed shares no content
        # identity with anything else; keep it in a group of its own.
        key = candidate.sha256 or f'path:{candidate.path}'
        groups.setdefault(key, []).append(candidate)
    return list(groups.values())


def _positively_other_build(group: list[CandidateEvaluation]) -> bool:
    """Whether every copy in a content group is identified as another build."""
    return all(candidate.build_id_matches is False for candidate in group)


def _single_build_id(build_ids: tuple[str, ...]) -> str | None:
    """Return a build ID only when it is unique.

    Multiple distinct build IDs in one file cannot be trusted as an identity:
    either value could match a candidate, so the identifier is unusable.
    """
    return build_ids[0] if len(build_ids) == 1 else None


def _mark_ambiguous(
    result: DebugInfoMatch,
    groups: list[list[CandidateEvaluation]],
) -> None:
    result.status = STATUS_AMBIGUOUS
    for group in groups:
        for candidate in group:
            candidate.eligible = True
            candidate.selected = False
            candidate.evidence = candidate.evidence + (
                'kept ambiguous; the available identifiers cannot justify '
                'a single choice',)


def _select_group(
    result: DebugInfoMatch,
    group: list[CandidateEvaluation],
) -> None:
    chosen = sorted(group, key=lambda candidate: candidate.path)[0]
    chosen.selected = True
    result.status = STATUS_MATCHED
    result.selected = chosen
    result.matched_by = chosen.accepted_by
    result.evidence.append(
        f'Selected {chosen.path} using ' + ' and '.join(result.matched_by))
    for candidate in group:
        if candidate is chosen:
            continue
        candidate.evidence = candidate.evidence + (
            f'same content as selected candidate ({chosen.path})',)
        result.evidence.append(
            f'Duplicate content: {candidate.path} is byte-identical to '
            f'{chosen.path}')


def _read_debuglink(elffile: ELFFile) -> DebugLink | None:
    link = elffile.get_dwarf_link()
    if link is None:
        return None
    return DebugLink(
        filename=os.fsdecode(link.filename),
        checksum=int(link.checksum),
    )


def match_debug_info(
    elf_path: str | os.PathLike[str],
    candidates: CandidateManifest | Iterable[str | os.PathLike[str]],
    *,
    recursive: bool = True,
) -> DebugInfoMatch:
    """Match one ELF file against candidate debug files.

    ``candidates`` may be a :class:`CandidateManifest` or an iterable of file
    paths.  The result contains the selected candidate, every evaluated
    candidate path, and the evidence used to accept or reject each one.
    """
    return DebugInfoMatcher(candidates, recursive=recursive).match(elf_path)


def match_debug_files(
    elf_paths: Iterable[str | os.PathLike[str]],
    candidates: CandidateManifest | Iterable[str | os.PathLike[str]],
    *,
    recursive: bool = True,
) -> list[DebugInfoMatch]:
    """Match several ELF files independently.

    An ambiguous or unmatched ELF does not prevent the other ELF files from
    producing their own matched result.
    """
    matcher = DebugInfoMatcher(candidates, recursive=recursive)
    return matcher.match_all(elf_paths)


def match_manifest(manifest: CandidateManifest) -> list[DebugInfoMatch]:
    """Match the ELF files named in a manifest against its candidate list."""
    return match_debug_files(manifest.elfs, manifest, recursive=manifest.recursive)


def report_to_dict(
    matches: Iterable[DebugInfoMatch],
    manifest: CandidateManifest | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe report for a group of matching results."""
    collected = list(matches)
    report: dict[str, Any] = {
        'results': [match.to_dict() for match in collected],
        'counts': {
            status: sum(1 for match in collected if match.status == status)
            for status in (
                STATUS_MATCHED, STATUS_AMBIGUOUS, STATUS_UNMATCHED,
                STATUS_UNIDENTIFIED, STATUS_ERROR)
        },
    }
    if manifest is not None:
        report['manifest'] = manifest.to_dict()
        report['candidate_paths'] = list(manifest.iter_candidate_paths())
    return report


def write_report(
    matches: Iterable[DebugInfoMatch],
    output: str | os.PathLike[str] | IO[str],
    *,
    manifest: CandidateManifest | None = None,
    indent: int | None = 2,
) -> None:
    """Write the matching report as UTF-8 JSON.

    ``output`` may be a path or an already-open text stream.  Relative paths in
    the report are those that were matched, so callers can keep the audit
    record next to their archive manifest.
    """
    report = report_to_dict(matches, manifest)
    text = json.dumps(report, ensure_ascii=False, indent=indent) + '\n'
    if hasattr(output, 'write'):
        output.write(text)  # type: ignore[union-attr]
        return
    with open(os.fspath(output), 'w', encoding='utf-8') as stream:
        stream.write(text)


# Short aliases for callers that prefer the terminology used by binutils.
find_debug_info = match_debug_info
find_debug_files = match_debug_files
