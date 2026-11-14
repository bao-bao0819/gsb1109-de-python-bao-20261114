#-------------------------------------------------------------------------------
# elftools: elf/firmware.py
#
# Offline firmware provider selection driven by ELF dynamic dependencies.
#
# This code is in the public domain.
#-------------------------------------------------------------------------------
"""Selection of provider library versions for offline firmware images.

The module reads ``DT_NEEDED`` dependency names from ELF files and solves a
small combinatorial optimisation problem:

* every firmware target enables a set of modules;
* the requirements of a target are the union of the ``DT_NEEDED`` names of
  its enabled modules (plus any explicitly configured requirements);
* a provider is one concrete library file with a SONAME, a version, an
  archive size and the set of requirements it can satisfy;
* a target may use at most one version of any SONAME, but different targets
  may use different versions;
* a provider file shared by several targets is stored once in the archive.

The solver returns the selection with the smallest total archive size.  When
a target cannot be assembled, a subset-minimal mutually exclusive group of
requirements is reported.

The typical entry points are :func:`read_dynamic_dependencies`,
:func:`select_providers`, :func:`load_catalog` and :func:`load_config`.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Any

from .dynamic import DynamicSection, DynamicTag
from .elffile import ELFFile

__all__ = [
    'ArchiveSelection',
    'Conflict',
    'ElfDynamicInfo',
    'FirmwareSelection',
    'FirmwareTarget',
    'Module',
    'Provider',
    'load_catalog',
    'load_config',
    'read_dynamic_dependencies',
    'select_providers',
]


ProviderKey = tuple[str, str]


@dataclass(frozen=True)
class ElfDynamicInfo:
    """The dynamic-linking information needed by the provider solver.

    ``needed`` preserves the order in which ``DT_NEEDED`` entries appear in
    the ELF file.  ``size`` is the size of the file on disk, in bytes.
    """
    path: str | None
    soname: str | None
    needed: tuple[str, ...]
    size: int


def _iter_dynamic_tags(elf: ELFFile) -> Iterable[DynamicTag]:
    """Yield dynamic tags from the section table, falling back to segments.

    Stripped files may not carry section headers, so the ``PT_DYNAMIC``
    segment is used when no ``SHT_DYNAMIC`` section is available.
    """
    found = False
    for section in elf.iter_sections():
        if isinstance(section, DynamicSection):
            found = True
            yield from section.iter_tags()
    if found:
        return

    for segment in elf.iter_segments():
        if segment.header.p_type == 'PT_DYNAMIC':
            yield from segment.iter_tags()


def _read_dynamic_info(
        stream: IO[bytes], path: str | None, size: int) -> ElfDynamicInfo:
    elf = ELFFile(stream)
    soname: str | None = None
    needed: list[str] = []
    for tag in _iter_dynamic_tags(elf):
        tag_name = tag.entry.d_tag
        if tag_name == 'DT_SONAME':
            if soname is None:
                soname = tag.soname
        elif tag_name == 'DT_NEEDED':
            needed.append(tag.needed)
    return ElfDynamicInfo(
        path=path, soname=soname, needed=tuple(needed), size=size)


def read_dynamic_dependencies(
        source: str | os.PathLike[str] | IO[bytes],
        *,
        size: int | None = None) -> ElfDynamicInfo:
    """Read ``DT_SONAME`` and ``DT_NEEDED`` from an ELF file.

    ``source`` may be a filesystem path or an open binary stream.  When it is
    a path, the returned ``size`` is taken from the file itself; for a stream
    the stream length is used.  Pass ``size`` explicitly to override either
    default (useful when parsing a slice of a larger file).
    """
    if isinstance(source, (str, os.PathLike)):
        path = os.fspath(source)
        with open(path, 'rb') as stream:
            actual_size = os.path.getsize(path)
            return _read_dynamic_info(
                stream, path, actual_size if size is None else size)

    stream = source
    resolved_size = size
    if resolved_size is None:
        try:
            position = stream.tell()
            stream.seek(0, os.SEEK_END)
            resolved_size = stream.tell()
            stream.seek(position)
        except (OSError, AttributeError):
            resolved_size = 0
    return _read_dynamic_info(stream, None, resolved_size)


@dataclass(frozen=True)
class Provider:
    """One concrete library file that can satisfy module requirements.

    ``soname`` identifies the library family.  ``version`` distinguishes
    concrete files within that family.  Two providers with the same
    ``(soname, version)`` denote the same archive file.

    ``satisfies`` holds the requirement names this version can satisfy.  It
    is deliberately independent of ``soname`` so a catalog can express
    capabilities that do not map one-to-one to library names.
    """
    soname: str
    version: str
    archive_size: int
    satisfies: frozenset[str]
    path: str | None = None

    def __post_init__(self) -> None:
        if not self.soname:
            raise ValueError('provider soname must not be empty')
        if not self.version:
            raise ValueError('provider version must not be empty')
        if self.archive_size < 0:
            raise ValueError('provider archive_size must be non-negative')
        object.__setattr__(self, 'satisfies', frozenset(self.satisfies))

    @property
    def key(self) -> ProviderKey:
        return (self.soname, self.version)

    @classmethod
    def from_elf(
            cls,
            path: str | os.PathLike[str],
            *,
            version: str,
            soname: str | None = None,
            satisfies: Iterable[str] | None = None,
            archive_size: int | None = None) -> Provider:
        """Build a provider from an ELF file's SONAME and file size.

        If ``satisfies`` is omitted the provider satisfies its own SONAME.
        """
        info = read_dynamic_dependencies(path)
        resolved_soname = soname or info.soname
        if not resolved_soname:
            raise ValueError(f'{os.fspath(path)!r} has no DT_SONAME')
        if soname is not None and info.soname is not None \
                and soname != info.soname:
            raise ValueError(
                f'provider soname {soname!r} does not match DT_SONAME '
                f'{info.soname!r} in {os.fspath(path)!r}')
        return cls(
            soname=resolved_soname,
            version=version,
            archive_size=info.size if archive_size is None else archive_size,
            satisfies=frozenset(
                (resolved_soname,) if satisfies is None else satisfies),
            path=os.fspath(path))


@dataclass(frozen=True)
class Module:
    """An offline acquisition module and the dependencies it needs.

    ``dependencies`` are the ``DT_NEEDED`` names read from the module's ELF
    file, optionally extended with verified explicit requirements.
    """
    name: str
    dependencies: frozenset[str]
    path: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError('module name must not be empty')
        object.__setattr__(
            self, 'dependencies', frozenset(self.dependencies))

    @classmethod
    def from_elf(
            cls,
            name: str,
            path: str | os.PathLike[str],
            *,
            extra_requirements: Iterable[str] = ()) -> Module:
        info = read_dynamic_dependencies(path)
        dependencies = set(info.needed)
        dependencies.update(extra_requirements)
        return cls(
            name=name,
            dependencies=frozenset(dependencies),
            path=os.fspath(path))


@dataclass(frozen=True)
class FirmwareTarget:
    """One firmware version and the requirement names it must satisfy."""
    name: str
    requirements: frozenset[str]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError('firmware name must not be empty')
        object.__setattr__(
            self, 'requirements', frozenset(self.requirements))

    @classmethod
    def from_modules(
            cls,
            name: str,
            modules: Iterable[Module],
            *,
            extra_requirements: Iterable[str] = ()) -> FirmwareTarget:
        requirements: set[str] = set(extra_requirements)
        for module in modules:
            requirements.update(module.dependencies)
        return cls(name=name, requirements=frozenset(requirements))


@dataclass(frozen=True)
class Conflict:
    """A subset-minimal group of requirements that cannot coexist."""
    firmware: str
    requirements: frozenset[str]
    detail: str


@dataclass(frozen=True)
class FirmwareSelection:
    """The concrete assignment chosen for one firmware target.

    ``assignment`` maps every requirement of the target to the provider
    selected for it.  ``providers`` is the deduplicated set of files used by
    this target; at most one entry per SONAME appears there.
    """
    firmware: str
    assignment: Mapping[str, Provider]
    providers: frozenset[Provider]


@dataclass(frozen=True)
class ArchiveSelection:
    """The result of :func:`select_providers`.

    ``archive`` is sorted by ``(soname, version)`` and contains each shared
    file exactly once, so ``total_size`` is the sum of ``archive`` sizes.
    When ``feasible`` is false, ``conflicts`` describes every firmware target
    that cannot be assembled and the other selection fields are empty.
    """
    feasible: bool
    total_size: int
    archive: tuple[Provider, ...]
    firmwares: tuple[FirmwareSelection, ...]
    conflicts: tuple[Conflict, ...]


def _canonicalize(providers: Iterable[Provider]) -> dict[ProviderKey, Provider]:
    by_key: dict[ProviderKey, Provider] = {}
    for provider in providers:
        existing = by_key.get(provider.key)
        if existing is None:
            by_key[provider.key] = provider
            continue
        if (existing.archive_size != provider.archive_size
                or existing.satisfies != provider.satisfies
                or existing.path != provider.path):
            raise ValueError(
                f'conflicting definitions for provider {provider.key!r}')
    return by_key


def _prune_dominated(
        providers: Mapping[ProviderKey, Provider]) -> dict[ProviderKey, Provider]:
    """Drop versions that another version of the same SONAME dominates.

    A provider Q dominates P when both share a SONAME, Q satisfies every
    requirement P satisfies and Q is no larger.  Replacing P by Q can never
    hurt: the SONAME slot is unchanged and coverage and cost do not get
    worse.  Identical versions in every respect keep the lowest version name
    so the result is deterministic.
    """
    by_soname: dict[str, list[Provider]] = {}
    for provider in providers.values():
        by_soname.setdefault(provider.soname, []).append(provider)

    kept: dict[ProviderKey, Provider] = {}
    for soname, group in by_soname.items():
        group.sort(key=lambda p: (
            -len(p.satisfies), p.archive_size, p.version))
        selected: list[Provider] = []
        for provider in group:
            if any(
                    other.satisfies >= provider.satisfies
                    and other.archive_size <= provider.archive_size
                    for other in selected):
                continue
            selected.append(provider)
            kept[provider.key] = provider
    return kept


def _candidate_map(
        providers: Mapping[ProviderKey, Provider],
        requirements: Iterable[str]) -> dict[str, tuple[Provider, ...]]:
    wanted = set(requirements)
    candidates: dict[str, list[Provider]] = {name: [] for name in wanted}
    for provider in providers.values():
        for requirement in provider.satisfies:
            if requirement in candidates:
                candidates[requirement].append(provider)
    return {
        name: tuple(sorted(items, key=lambda p: (
            p.archive_size, p.soname, p.version)))
        for name, items in candidates.items()
    }


def _satisfiable(
        requirements: Iterable[str],
        candidates: Mapping[str, Sequence[Provider]]) -> bool:
    ordered = sorted(
        requirements, key=lambda name: (len(candidates.get(name, ())), name))
    chosen: dict[str, Provider] = {}

    def assign(index: int) -> bool:
        if index == len(ordered):
            return True
        requirement = ordered[index]
        for provider in candidates.get(requirement, ()):
            existing = chosen.get(provider.soname)
            if existing is not None and existing.key != provider.key:
                continue
            if existing is None:
                chosen[provider.soname] = provider
                if assign(index + 1):
                    return True
                del chosen[provider.soname]
            elif assign(index + 1):
                return True
        return False

    return assign(0)


def _enumerate_plans(
        requirements: Iterable[str],
        candidates: Mapping[str, Sequence[Provider]]) -> dict[
            frozenset[ProviderKey], dict[str, Provider]]:
    """Enumerate feasible provider sets for a single target.

    The same provider may satisfy several requirements.  A target can use
    only one version per SONAME, so two different versions of the same
    library may never be chosen together even if each satisfies a distinct
    requirement.
    """
    ordered = sorted(
        requirements, key=lambda name: (len(candidates.get(name, ())), name))
    chosen: dict[str, Provider] = {}
    assignment: dict[str, Provider] = {}
    plans: dict[frozenset[ProviderKey], dict[str, Provider]] = {}

    def assign(index: int) -> None:
        if index == len(ordered):
            plan_key = frozenset(
                (provider.soname, provider.version)
                for provider in chosen.values())
            plans.setdefault(plan_key, dict(assignment))
            return

        requirement = ordered[index]
        for provider in candidates.get(requirement, ()):
            existing = chosen.get(provider.soname)
            if existing is not None and existing.key != provider.key:
                continue
            added = existing is None
            if added:
                chosen[provider.soname] = provider
            assignment[requirement] = provider
            assign(index + 1)
            del assignment[requirement]
            if added:
                del chosen[provider.soname]

    assign(0)
    return plans


def _minimal_plans(
        plans: Mapping[frozenset[ProviderKey], dict[str, Provider]],
        canonical: Mapping[ProviderKey, Provider]) -> list[
            tuple[frozenset[ProviderKey], dict[str, Provider]]]:
    """Drop plans that are strict supersets of another feasible plan.

    Replacing a plan by one of its subsets never increases the archive
    because every provider size is non-negative and the smaller plan might
    share fewer files, not more.
    """
    ordered = sorted(
        plans.items(),
        key=lambda item: (
            sum(canonical[key].archive_size for key in item[0]),
            len(item[0]),
            tuple(sorted(item[0])),
        ))
    kept: list[tuple[frozenset[ProviderKey], dict[str, Provider]]] = []
    for plan, assignment in ordered:
        if any(previous <= plan for previous, _ in kept):
            continue
        kept.append((plan, assignment))
    return kept


def _minimal_conflict(
        target: FirmwareTarget,
        candidates: Mapping[str, Sequence[Provider]]) -> frozenset[str]:
    """Return a subset-minimal unsatisfiable requirement group.

    Requirements are removed one at a time as long as the remainder is still
    unsatisfiable.  The result is therefore subset-minimal: dropping any one
    member makes the group satisfiable.
    """
    core = set(target.requirements)
    for requirement in sorted(target.requirements):
        if requirement not in core:
            continue
        trial = core - {requirement}
        if not _satisfiable(trial, candidates):
            core = trial
    return frozenset(core)


def _describe_conflict(
        target: FirmwareTarget,
        core: frozenset[str],
        candidates: Mapping[str, Sequence[Provider]]) -> str:
    parts: list[str] = []
    for requirement in sorted(core):
        available = candidates.get(requirement, ())
        if not available:
            parts.append(f'{requirement}: no provider')
            continue
        by_soname: dict[str, list[str]] = {}
        for provider in available:
            by_soname.setdefault(provider.soname, []).append(provider.version)
        choices = '; '.join(
            f"{soname} ({', '.join(sorted(versions))})"
            for soname, versions in sorted(by_soname.items()))
        parts.append(f'{requirement}: {choices}')
    return (
        f"firmware {target.name!r} cannot satisfy "
        f"{sorted(core)!r}: " + ' | '.join(parts))


def _search(
        entries: Sequence[tuple[
            FirmwareTarget,
            list[tuple[frozenset[ProviderKey], dict[str, Provider]]],
        ]],
        canonical: Mapping[ProviderKey, Provider],
) -> tuple[int, list[dict[str, Provider]]]:
    """Find the cheapest union of plans, one plan per firmware target."""
    if not entries:
        return 0, []

    plan_data: list[list[tuple[frozenset[ProviderKey], dict[str, Provider],
                              int]]] = []
    for _, plans in entries:
        plan_data.append([
            (plan, assignment,
             sum(canonical[key].archive_size for key in plan))
            for plan, assignment in plans
        ])

    count = len(entries)
    current: list[dict[str, Provider]] = [{} for _ in range(count)]
    union: set[ProviderKey] = set()
    best_cost: int | None = None
    best_assignment: list[dict[str, Provider]] | None = None

    def lower_bound(start: int) -> int:
        # Any chosen plan for a target contains the intersection of all of
        # that target's plans, so those files are mandatory.  Each target
        # also needs at least its cheapest residual plan.
        mandatory: set[ProviderKey] = set()
        largest_minimum = 0
        for index in range(start, count):
            plans = plan_data[index]
            common = set(plans[0][0])
            for plan, _, _ in plans[1:]:
                common.intersection_update(plan)
            mandatory.update(common)
            minimum = min(
                sum(canonical[key].archive_size
                    for key in plan if key not in union)
                for plan, _, _ in plans)
            largest_minimum = max(largest_minimum, minimum)
        mandatory_cost = sum(
            canonical[key].archive_size
            for key in mandatory if key not in union)
        return max(mandatory_cost, largest_minimum)

    def visit(index: int, cost: int) -> None:
        nonlocal best_cost, best_assignment
        if best_cost is not None and cost >= best_cost:
            return
        if index == count:
            best_cost = cost
            best_assignment = [dict(item) for item in current]
            return

        for plan, assignment, _ in plan_data[index]:
            added = [key for key in plan if key not in union]
            new_cost = cost + sum(
                canonical[key].archive_size for key in added)
            for key in added:
                union.add(key)
            if best_cost is not None \
                    and new_cost + lower_bound(index + 1) >= best_cost:
                for key in added:
                    union.discard(key)
                continue
            current[index] = assignment
            visit(index + 1, new_cost)
            current[index] = {}
            for key in added:
                union.discard(key)

    visit(0, 0)
    assert best_cost is not None and best_assignment is not None
    return best_cost, best_assignment


def select_providers(
        firmwares: Sequence[FirmwareTarget],
        providers: Iterable[Provider]) -> ArchiveSelection:
    """Choose provider versions that let every firmware be assembled.

    The objective is the total size of the archive: a provider file used by
    several firmware targets is counted once.  Within one firmware target at
    most one version of a given SONAME may be selected; different targets may
    select different versions.

    If any target is infeasible, the result has ``feasible=False`` and one
    :class:`Conflict` per infeasible target, each holding a subset-minimal
    mutually exclusive group of requirements.
    """
    names = [target.name for target in firmwares]
    if len(set(names)) != len(names):
        raise ValueError('firmware names must be unique')

    canonical = _canonicalize(providers)
    usable = _prune_dominated(canonical)
    candidates_by_target: list[
        tuple[FirmwareTarget, dict[str, tuple[Provider, ...]]]] = []
    conflicts: list[Conflict] = []

    for target in firmwares:
        candidates = _candidate_map(usable, target.requirements)
        if not _satisfiable(target.requirements, candidates):
            core = _minimal_conflict(target, candidates)
            conflicts.append(Conflict(
                firmware=target.name,
                requirements=core,
                detail=_describe_conflict(target, core, candidates)))
        candidates_by_target.append((target, candidates))

    if conflicts:
        return ArchiveSelection(
            feasible=False,
            total_size=0,
            archive=(),
            firmwares=(),
            conflicts=tuple(conflicts))

    entries: list[tuple[
        FirmwareTarget,
        list[tuple[frozenset[ProviderKey], dict[str, Provider]]],
    ]] = []
    for target, candidates in candidates_by_target:
        plans = _minimal_plans(
            _enumerate_plans(target.requirements, candidates),
            canonical)
        entries.append((target, plans))

    total_size, assignments = _search(entries, canonical)

    selections: list[FirmwareSelection] = []
    archive_keys: set[ProviderKey] = set()
    for (target, _), assignment in zip(entries, assignments):
        used = frozenset(assignment.values())
        archive_keys.update(provider.key for provider in used)
        selections.append(FirmwareSelection(
            firmware=target.name,
            assignment=dict(assignment),
            providers=used))

    archive = tuple(
        canonical[key] for key in sorted(archive_keys))
    return ArchiveSelection(
        feasible=True,
        total_size=total_size,
        archive=archive,
        firmwares=tuple(selections),
        conflicts=())


def _as_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{field} must be a string or a list of strings')
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f'{field} must contain only strings')
        result.append(item)
    return result


def _first(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _resolve_path(base: str, value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f'{field} must be a non-empty path string')
    if os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(base, value))


def load_catalog(path: str | os.PathLike[str]) -> list[Provider]:
    """Load a provider catalog from JSON.

    The file is either a list of provider objects or an object with a
    ``providers`` list.  Each provider has ``soname``, ``version`` and either
    ``size`` or a ``path`` to an ELF file.  ``satisfies`` lists the
    requirement names the version can satisfy; when omitted it defaults to
    the provider's SONAME.  Relative ELF paths are resolved against the
    catalog file's directory.
    """
    catalog_path = os.fspath(path)
    with open(catalog_path, encoding='utf-8') as stream:
        data = json.load(stream)

    if isinstance(data, Mapping):
        raw_items = _first(data, ('providers', 'libraries', 'versions', 'items'))
        if raw_items is None:
            raw_items = []
    elif isinstance(data, list):
        raw_items = data
    else:
        raise ValueError('catalog must be an object or a list')

    if not isinstance(raw_items, list):
        raise ValueError('catalog "providers" must be a list')

    base = os.path.dirname(os.path.abspath(catalog_path))
    providers: list[Provider] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            raise ValueError(f'provider #{index} must be an object')

        version = item.get('version')
        if version is None:
            raise ValueError(f'provider #{index} is missing "version"')
        version = str(version)

        soname_value = item.get('soname')
        soname = str(soname_value) if soname_value is not None else None

        provider_path: str | None = None
        info: ElfDynamicInfo | None = None
        if item.get('path') is not None:
            provider_path = _resolve_path(
                base, item['path'], f'provider #{index} "path"')
            info = read_dynamic_dependencies(provider_path)
            if soname is None:
                soname = info.soname
            elif info.soname is not None and soname != info.soname:
                raise ValueError(
                    f'provider #{index} soname {soname!r} does not match '
                    f'DT_SONAME {info.soname!r} in {provider_path!r}')

        if soname is None or not soname:
            raise ValueError(
                f'provider #{index} needs "soname" or a readable ELF path')

        size_value = item.get('size', item.get('archive_size'))
        if size_value is None:
            if info is None:
                raise ValueError(
                    f'provider #{index} needs "size" or an ELF "path"')
            archive_size = info.size
        else:
            archive_size = int(size_value)

        satisfies_value = item.get('satisfies', item.get('dependencies'))
        if satisfies_value is None:
            satisfies = [soname]
        else:
            satisfies = _as_string_list(
                satisfies_value, f'provider #{index} "satisfies"')

        providers.append(Provider(
            soname=soname,
            version=version,
            archive_size=archive_size,
            satisfies=frozenset(satisfies),
            path=provider_path))

    return providers


def load_config(path: str | os.PathLike[str]) -> list[FirmwareTarget]:
    """Load firmware targets from JSON.

    The object may define a ``modules`` registry mapping a module name to an
    ELF path (a string) or to an object with ``elf``/``path`` and optional
    ``dependencies``.  A ``firmwares`` list then names the modules each
    target enables.  Targets may also list extra ELF files with ``elves`` and
    explicit requirement names with ``requirements`` (or ``requires``).
    Relative ELF paths are resolved against the config file's directory.
    """
    config_path = os.fspath(path)
    with open(config_path, encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, Mapping):
        raise ValueError('config must be an object')

    base = os.path.dirname(os.path.abspath(config_path))
    modules: dict[str, Module] = {}

    raw_modules = _first(data, ('modules', 'acquisition_modules', 'module_map'))
    if raw_modules is None:
        raw_modules = {}
    if not isinstance(raw_modules, Mapping):
        raise ValueError('config "modules" must be an object')
    for name, spec in raw_modules.items():
        if not isinstance(name, str):
            raise ValueError('module names must be strings')
        extra: list[str] = []
        elf_path: str | None = None
        if isinstance(spec, str):
            elf_path = _resolve_path(
                base, spec, f'module {name!r} template')
        elif isinstance(spec, Mapping):
            if spec.get('elf') is not None:
                elf_path = _resolve_path(
                    base, spec['elf'], f'module {name!r} "elf"')
            elif spec.get('path') is not None:
                elf_path = _resolve_path(
                    base, spec['path'], f'module {name!r} "path"')
            extra = _as_string_list(
                spec.get('dependencies'), f'module {name!r} "dependencies"')
        else:
            raise ValueError(f'module {name!r} must be a string or object')

        dependencies: set[str] = set(extra)
        if elf_path is not None:
            dependencies.update(read_dynamic_dependencies(elf_path).needed)
        modules[name] = Module(
            name=name,
            dependencies=frozenset(dependencies),
            path=elf_path)

    raw_firmwares = _first(data, ('firmwares', 'targets', 'firmware_versions'))
    if raw_firmwares is None:
        raw_firmwares = []
    if not isinstance(raw_firmwares, list):
        raise ValueError('config "firmwares" must be a list')

    targets: list[FirmwareTarget] = []
    for index, item in enumerate(raw_firmwares):
        if not isinstance(item, Mapping):
            raise ValueError(f'firmware #{index} must be an object')
        name = item.get('name')
        if not isinstance(name, str) or not name:
            raise ValueError(f'firmware #{index} needs a non-empty "name"')

        requirements: set[str] = set()
        raw_enabled = item.get('modules')
        enabled: list[str] = []
        if isinstance(raw_enabled, Mapping):
            # A mapping of module name -> bool (or any truthy value) lets a
            # manifest list every known module and flag the enabled ones.
            enabled = [
                module_name for module_name, value
                in raw_enabled.items() if value]
        else:
            enabled = _as_string_list(
                raw_enabled, f'firmware {name!r} "modules"')
        for module_name in enabled:
            try:
                requirements.update(modules[module_name].dependencies)
            except KeyError:
                raise ValueError(
                    f'firmware {name!r} references unknown module '
                    f'{module_name!r}') from None

        for elf_value in _as_string_list(
                item.get('elves'), f'firmware {name!r} "elves"'):
            elf_path = _resolve_path(
                base, elf_value, f'firmware {name!r} "elves"')
            requirements.update(read_dynamic_dependencies(elf_path).needed)

        extra = item.get('requirements', item.get('requires'))
        requirements.update(
            _as_string_list(extra, f'firmware {name!r} "requirements"'))

        targets.append(FirmwareTarget(
            name=name, requirements=frozenset(requirements)))

    return targets
