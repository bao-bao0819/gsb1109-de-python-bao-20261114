#-------------------------------------------------------------------------------
# elftools: dwarf/inline.py
#
# Decoding of concrete inline call sites (DW_TAG_inlined_subroutine).
#
# The same abstract inline function can be expanded several times, including
# several times in one caller.  Each expansion is a separate DIE with its own
# ranges and DW_AT_call_* attributes; only its name and type live in the
# abstract declaration.  This module builds a row per concrete DIE instead of
# keying anything by the shared declaration.
#
# This code is in the public domain
#-------------------------------------------------------------------------------
from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple, cast

from ..common.utils import bytes2str
from .descriptions import describe_form_class
from .ranges import BaseAddressEntry, RangeEntry

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .compileunit import CompileUnit
    from .die import DIE
    from .dwarfinfo import DWARFInfo
    from .typeunit import TypeUnit


class AddressRange(NamedTuple):
    """A resolved, absolute half-open address range [begin, end)."""
    begin: int
    end: int

    @property
    def start(self) -> int:
        return self.begin

    @property
    def start_address(self) -> int:
        return self.begin

    @property
    def end_address(self) -> int:
        return self.end

    @property
    def low(self) -> int:
        return self.begin

    @property
    def high(self) -> int:
        return self.end

    @property
    def low_pc(self) -> int:
        return self.begin

    @property
    def high_pc(self) -> int:
        return self.end

    @property
    def begin_offset(self) -> int:
        return self.begin

    @property
    def end_offset(self) -> int:
        return self.end

    @property
    def is_absolute(self) -> bool:
        return True

    def contains(self, address: int) -> bool:
        return self.begin <= address < self.end


class Name(str):
    """A decoded DWARF name.

    It behaves like a normal string, but also compares equal to the raw bytes
    found in the DWARF section and accepts ``.decode()`` so callers used to
    pyelftools' byte-oriented attribute values can keep their habits.
    """
    raw: bytes | None

    def __new__(cls, value: str, raw: bytes | None = None) -> Name:
        obj = str.__new__(cls, value)
        obj.raw = raw
        return obj

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bytes):
            return str.__eq__(self, other.decode('latin-1'))
        return str.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def decode(self, encoding: str = 'latin-1', errors: str = 'strict') -> str:
        """Return the decoded name (already a string)."""
        del encoding, errors
        return str(self)

    __hash__ = str.__hash__


class TypeRef(str):
    """A printable type name that still exposes the underlying type DIE.

    ``row.type`` can therefore be used as a string (``row.type == 'int'``)
    while ``row.type.die`` and ``row.type.offset`` remain available for callers
    that need the actual DWARF entry.  ``None`` inside the object means the
    declaration exposed no usable type.
    """
    die: DIE | None

    def __new__(cls, value: str, die: DIE | None = None) -> TypeRef:
        obj = str.__new__(cls, value)
        obj.die = die
        return obj

    @property
    def offset(self) -> int | None:
        return self.die.offset if self.die is not None else None

    @property
    def tag(self) -> str | int | None:
        return self.die.tag if self.die is not None else None

    def __getattr__(self, name: str) -> Any:
        if self.die is None:
            raise AttributeError(name)
        return getattr(self.die, name)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bytes):
            return str.__eq__(self, other.decode('latin-1'))
        return str.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = str.__hash__


class FileRef(str):
    """A source-file reference that compares to both its index and name.

    DWARF stores ``DW_AT_call_file``/``DW_AT_decl_file`` as an index into the
    line program.  Consumers usually want the name, but tests and low-level
    tooling often still have the index.  ``FileRef`` accepts both::

        row.file == 0
        row.file == 'dwarf5-clang.c'
        row.file.file_index == 0
    """
    file_index: int | None

    def __new__(cls, value: str, index: int | None = None) -> FileRef:
        obj = str.__new__(cls, value)
        obj.file_index = index
        return obj

    def __int__(self) -> int:
        if self.file_index is None:
            raise TypeError('FileRef has no index')
        return self.file_index

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.file_index == other
        if isinstance(other, bytes):
            return str.__eq__(self, other.decode('latin-1'))
        return str.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = str.__hash__


class CallSite(NamedTuple):
    """The call site recorded on a concrete inlined instance."""
    file: int | None
    line: int | None
    column: int | None

    @property
    def file_index(self) -> int | None:
        return self.file

    @property
    def line_number(self) -> int | None:
        return self.line

    @property
    def call_file(self) -> int | None:
        return self.file

    @property
    def call_line(self) -> int | None:
        return self.line

    @property
    def call_column(self) -> int | None:
        return self.column


class InlineCallSite:
    """One concrete expansion of an inlined function.

        A row is identified by the offset of its DW_TAG_inlined_subroutine
        DIE.  Two expansions sharing one DW_AT_abstract_origin therefore
        produce two rows.  Metadata that the instance does not carry itself
        (most notably the name and the type) is filled in from the abstract
        declaration chain.

        The following attributes are part of the public record:

        die, offset, cu_offset
            The concrete instance DIE and its section offsets.

        name, linkage_name, type
            Name and type obtained from the instance first, then from its
            abstract declaration/specification chain.  ``type`` is a string
            that also exposes the underlying type DIE as ``type.die``.

        decl_file, decl_line, decl_column
            Declaration location, when available in the chain.

        call_file, call_line, call_column, call_pc, call_return_pc
            The outer call site stored on this concrete instance.

        ranges, low_pc, high_pc
            Resolved absolute address ranges.  ``ranges`` is a list of
            AddressRange objects; it is empty when the producer supplied no
            usable range.  ``low_pc``/``high_pc`` describe the envelope.

        parent, caller, caller_offset, depth
            ``parent`` is the immediate DIE parent (which may be a lexical
            block).  ``caller`` is the nearest enclosing inline call-site row,
            and ``depth`` is the number of such enclosing rows (zero for an
            inline expansion directly under a subprogram).

        declaration
            The first successfully resolved abstract-origin/specification
            DIE, or None when the declaration reference is broken.  A broken
            reference never removes the row or its instance-owned data.
    """

    def __init__(
        self,
        die: DIE,
        *,
        declaration: DIE | None,
        declaration_offset: int | None,
        name: str | None,
        name_raw: bytes | None,
        linkage_name: str | None,
        type_die: DIE | None,
        type_name: str | None,
        decl_file: int | None,
        decl_line: int | None,
        decl_column: int | None,
        call_file: int | None,
        call_line: int | None,
        call_column: int | None,
        call_pc: int | None,
        call_return_pc: int | None,
        ranges: list[AddressRange],
        parent: DIE | None,
        call_file_name: str | None,
        decl_file_name: str | None,
    ) -> None:
        self.die = die
        self.offset = die.offset
        self.cu_offset = die.cu.cu_offset
        self.declaration = declaration
        self.declaration_offset = declaration_offset
        self.name = Name(name, name_raw) if name is not None else None
        self.name_raw = name_raw
        self.linkage_name = Name(linkage_name) if linkage_name is not None else None
        self.type_die = type_die
        self.type_name = type_name
        self.decl_file = decl_file
        self.decl_line = decl_line
        self.decl_column = decl_column
        self.call_file = call_file
        self.call_line = call_line
        self.call_column = call_column
        self.call_pc = call_pc
        self.call_return_pc = call_return_pc
        self.ranges = ranges
        self.parent = parent
        self.call_file_name = call_file_name
        self.decl_file_name = decl_file_name
        self.call_file_index = call_file
        self.decl_file_index = decl_file
        self.call_file_ref = (
            FileRef(call_file_name, call_file) if call_file_name is not None else None)
        self.decl_file_ref = (
            FileRef(decl_file_name, decl_file) if decl_file_name is not None else None)

        self._caller: InlineCallSite | None = None
        self._depth = 0
        self.caller_die: DIE | None = None
        self.caller_die_offset: int | None = None
        self.subprogram: DIE | None = None
        self.subprogram_offset: int | None = None
        self.parent_offset = parent.offset if parent is not None else None

    #------ Aliases used by different consumers ------#

    @property
    def die_offset(self) -> int:
        return self.offset

    @property
    def instance_offset(self) -> int:
        return self.offset

    @property
    def abstract_origin(self) -> DIE | None:
        return self.declaration

    @property
    def origin(self) -> DIE | None:
        return self.declaration

    @property
    def type(self) -> TypeRef | None:
        if self.type_name is None and self.type_die is None:
            return None
        return TypeRef(self.type_name or '', self.type_die)

    @property
    def name_text(self) -> str | None:
        return str(self.name) if self.name is not None else None

    @property
    def type_text(self) -> str | None:
        return self.type_name

    @property
    def name_bytes(self) -> bytes | None:
        if self.name_raw is not None:
            return self.name_raw
        if self.name is None:
            return None
        return str(self.name).encode('latin-1')

    @property
    def linkage_name_bytes(self) -> bytes | None:
        if self.linkage_name is None:
            return None
        return str(self.linkage_name).encode('latin-1')

    @property
    def type_ref(self) -> DIE | None:
        return self.type_die

    @property
    def name_and_type(self) -> tuple[str | None, str | None]:
        return (self.name_text, self.type_text)

    @property
    def full_name(self) -> TypeRef | None:
        return self.type

    @property
    def source_line(self) -> int | None:
        """The call-site line, falling back to the declaration line."""
        return self.call_line if self.call_line is not None else self.decl_line

    @property
    def addr_range(self) -> AddressRange | None:
        """Envelope of this row's ranges, or None when it has none."""
        if not self.ranges:
            return None
        return AddressRange(self.low_pc or 0, self.high_pc or 0)

    @property
    def address_range(self) -> AddressRange | None:
        return self.addr_range

    @property
    def return_type(self) -> TypeRef | None:
        return self.type

    @property
    def address_ranges(self) -> list[AddressRange]:
        return self.ranges

    @property
    def range_entries(self) -> list[AddressRange]:
        return self.ranges

    @property
    def low_pc(self) -> int | None:
        return min((r.begin for r in self.ranges), default=None)

    @property
    def high_pc(self) -> int | None:
        return max((r.end for r in self.ranges), default=None)

    @property
    def size(self) -> int:
        return sum(r.end - r.begin for r in self.ranges)

    @property
    def call_site(self) -> CallSite:
        return CallSite(self.call_file, self.call_line, self.call_column)

    @property
    def outer_call_site(self) -> CallSite:
        return self.call_site

    @property
    def caller(self) -> InlineCallSite | None:
        return self._caller

    @property
    def parent_call_site(self) -> InlineCallSite | None:
        return self._caller

    @property
    def caller_offset(self) -> int | None:
        return self._caller.offset if self._caller is not None else None

    @property
    def depth(self) -> int:
        return self._depth

    @property
    def nesting_level(self) -> int:
        return self._depth

    @property
    def caller_call_site(self) -> CallSite | None:
        return self._caller.call_site if self._caller is not None else None

    @property
    def caller_chain(self) -> list[InlineCallSite]:
        """Enclosing inline call-site rows, from the nearest outward."""
        chain: list[InlineCallSite] = []
        caller = self._caller
        while caller is not None:
            chain.append(caller)
            caller = caller._caller
        return chain

    @property
    def enclosing_inline_calls(self) -> list[InlineCallSite]:
        return self.caller_chain

    #------ Queries ------#

    def contains(self, address: int) -> bool:
        return any(r.contains(address) for r in self.ranges)

    def covers(self, address: int) -> bool:
        return self.contains(address)

    def containing_span(self, address: int) -> int | None:
        """Smallest containing range width, or None if outside all ranges."""
        spans = [r.end - r.begin for r in self.ranges if r.contains(address)]
        return min(spans) if spans else None

    def dominates(self, other: "InlineCallSite") -> bool:
        """Whether this row is an actual inline ancestor of ``other``."""
        caller = other._caller
        while caller is not None:
            if caller is self:
                return True
            caller = caller._caller
        return False

    def __repr__(self) -> str:
        return (
            f'InlineCallSite(offset=0x{self.offset:x}, '
            f'name={self.name!r}, call_line={self.call_line!r}, '
            f'ranges={self.ranges!r})')

    #------ Mapping-style access ------#

    _FIELDS = (
        'offset', 'cu_offset', 'name', 'type', 'linkage_name',
        'decl_file', 'decl_file_name', 'decl_line', 'decl_column',
        'call_file', 'call_file_name', 'call_line', 'call_column',
        'call_pc', 'call_return_pc',
        'ranges', 'low_pc', 'high_pc',
        'caller_offset', 'depth', 'declaration_offset',
    )
    _ALIASES = {
        'die_offset': 'offset',
        'instance_offset': 'offset',
        'name_raw': 'name_raw',
        'type_name': 'type_name',
        'type_die': 'type_die',
        'caller': 'caller',
        'declaration': 'declaration',
        'abstract_origin': 'declaration',
        'address_ranges': 'ranges',
        'range_entries': 'ranges',
        'name_text': 'name_text',
        'type_text': 'type_text',
        'source_line': 'source_line',
    }

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            key = self._FIELDS[key]
        key = self._ALIASES.get(key, key)
        if key in self._FIELDS:
            return getattr(self, key)
        if key in self._ALIASES.values() or hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def keys(self) -> tuple[str, ...]:
        return self._FIELDS

    def values(self) -> tuple[Any, ...]:
        return tuple(getattr(self, field) for field in self._FIELDS)

    def items(self) -> list[tuple[str, Any]]:
        return [(field, getattr(self, field)) for field in self._FIELDS]

    def asdict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self._FIELDS}

    to_dict = asdict


class InlineCallSiteTable:
    """A collection of concrete inline call-site rows.

        Rows are kept in DIE order and are never collapsed by name or by
        abstract declaration.  The table additionally indexes them by their
        concrete DIE offset.
    """

    def __init__(
        self,
        rows: Iterable[InlineCallSite],
    ) -> None:
        self._rows = list(rows)
        self._by_offset = {row.offset: row for row in self._rows}
        self._by_name: dict[str, list[InlineCallSite]] = {}
        self._by_declaration: dict[int, list[InlineCallSite]] = {}
        self._by_caller: dict[int, list[InlineCallSite]] = {}
        for row in self._rows:
            if row.name is not None:
                self._by_name.setdefault(row.name, []).append(row)
            if row.declaration is not None:
                self._by_declaration.setdefault(row.declaration.offset, []).append(row)
            if row.caller_offset is not None:
                self._by_caller.setdefault(row.caller_offset, []).append(row)

    def __iter__(self) -> Iterator[InlineCallSite]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, int):
            return item in self._by_offset or 0 <= item < len(self._rows)
        return item in self._rows

    def __getitem__(self, key: int | slice) -> InlineCallSite | list[InlineCallSite]:
        if isinstance(key, slice):
            return self._rows[key]
        if key < 0:
            return self._rows[key]
        row = self._by_offset.get(key)
        if row is not None:
            return row
        if key < len(self._rows):
            return self._rows[key]
        raise KeyError(key)

    @property
    def rows(self) -> list[InlineCallSite]:
        return list(self._rows)

    @property
    def entries(self) -> list[InlineCallSite]:
        return self.rows

    @property
    def by_offset(self) -> dict[int, InlineCallSite]:
        return dict(self._by_offset)

    def row_at(self, offset: int) -> InlineCallSite | None:
        """Return the row whose concrete DIE has the given section offset."""
        return self._by_offset.get(offset)

    get_by_offset = row_at

    def by_name(self, name: str | bytes) -> list[InlineCallSite]:
        if isinstance(name, bytes):
            name = name.decode('latin-1')
        return list(self._by_name.get(name, []))

    def by_declaration(self, declaration: DIE | int) -> list[InlineCallSite]:
        offset = declaration if isinstance(declaration, int) else declaration.offset
        return list(self._by_declaration.get(offset, []))

    def by_origin(self, declaration: DIE | int) -> list[InlineCallSite]:
        return self.by_declaration(declaration)

    def children_of(self, caller: InlineCallSite | int) -> list[InlineCallSite]:
        """Rows whose nearest enclosing inline call-site is ``caller``."""
        offset = caller if isinstance(caller, int) else caller.offset
        return list(self._by_caller.get(offset, []))

    def in_cu(self, cu: CompileUnit | int) -> "InlineCallSiteTable":
        """Return a table containing only rows from one compilation unit."""
        cu_offset = cu if isinstance(cu, int) else cu.cu_offset
        return InlineCallSiteTable(
            [row for row in self._rows if row.cu_offset == cu_offset])

    def at_in_cu(self, address: int, cu: CompileUnit | int) -> InlineCallSite | None:
        return self.in_cu(cu).at(address)

    def all_at(self, address: int) -> list[InlineCallSite]:
        """Return every containing row, innermost/most specific first."""
        rows = [row for row in self._rows if row.contains(address)]
        return sorted(
            rows,
            key=lambda row: (
                row.depth,
                -(row.containing_span(address) or 0),
                row.offset),
            reverse=True)

    def at(self, address: int, cu: CompileUnit | int | None = None) -> InlineCallSite | None:
        """Return the innermost row whose actual ranges contain ``address``.

            An enclosing inline expansion is only selected when no nested
            instance actually contains the address.  Merely being inside the
            outer expansion's range is not enough to choose an inner row.
            Ties between unrelated expansions are broken by the most specific
            (smallest) containing range, then by DIE order.

            Pass ``cu`` when addresses may alias across compilation units,
            as they routinely do in shared objects.
        """
        rows = self._rows if cu is None else self.in_cu(cu)._rows
        candidates = [row for row in rows if row.contains(address)]
        if not candidates:
            return None

        def rank(row: InlineCallSite) -> tuple[int, int, int, int]:
            return (
                row.depth,
                -(row.containing_span(address) or 0),
                -row.cu_offset,
                row.offset)

        return max(candidates, key=rank)

    def lookup(self, address: int, cu: CompileUnit | int | None = None) -> InlineCallSite | None:
        return self.at(address, cu)

    def resolve(self, address: int, cu: CompileUnit | int | None = None) -> InlineCallSite | None:
        return self.at(address, cu)

    def get(self, address: int, default: Any = None,
            cu: CompileUnit | int | None = None) -> InlineCallSite | Any:
        row = self.at(address, cu)
        return default if row is None else row

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Return all rows as plain dictionaries, preserving DIE order."""
        return [row.asdict() for row in self._rows]

    def as_dicts(self) -> list[dict[str, Any]]:
        return self.to_dicts()

    @classmethod
    def from_dicts(cls, rows: Iterable[dict[str, Any]]) -> "InlineCallSiteTable":
        rebuilt = [_make_row_from_dict(data) for data in rows]
        by_offset = {row.offset: row for row in rebuilt}
        for data, row in zip(rows, rebuilt):
            caller_offset = data.get('caller_offset')
            if caller_offset is not None:
                row._caller = by_offset.get(int(caller_offset))
        return cls(rebuilt)

    def __repr__(self) -> str:
        return f'InlineCallSiteTable({len(self._rows)} rows)'


def _attr(die: DIE, name: str) -> Any:
    return die.attributes.get(name)


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return bytes2str(value)
    return str(value)


def _resolve_attribute(die: DIE, name: str) -> DIE | None:
    if name not in die.attributes:
        return None
    try:
        return die.get_DIE_from_attribute(name)
    except Exception:
        return None


def _reference_offset(die: DIE, name: str) -> int | None:
    """Return the section address a reference attribute points at.

    This is deliberately independent of successful DIE resolution so that a
    broken declaration reference still leaves a useful offset on the row.
    """
    attr = die.attributes.get(name)
    if attr is None:
        return None
    form = attr.form
    if form in ('DW_FORM_ref1', 'DW_FORM_ref2', 'DW_FORM_ref4',
                'DW_FORM_ref8', 'DW_FORM_ref', 'DW_FORM_ref_udata'):
        return die.cu.cu_offset + attr.raw_value
    if form == 'DW_FORM_ref_addr':
        return attr.raw_value
    return None


def _declaration_chain(die: DIE) -> list[DIE]:
    """Follow abstract origins and specifications without dropping the DIE.

        Broken references are skipped, but any data already found on the
        instance (or earlier links) remains available to the caller.
    """
    chain: list[DIE] = []
    seen: set[int] = set()

    def visit(current: DIE) -> None:
        if current.offset in seen:
            return
        seen.add(current.offset)
        chain.append(current)
        for attr_name in ('DW_AT_abstract_origin', 'DW_AT_specification'):
            referenced = _resolve_attribute(current, attr_name)
            if referenced is not None:
                visit(referenced)

    visit(die)
    return chain


def _first_attr(chain: list[DIE], name: str) -> Any:
    for die in chain:
        if name in die.attributes:
            return die.attributes[name].value
    return None


def _first_type_die(chain: list[DIE]) -> DIE | None:
    for die in chain:
        if 'DW_AT_type' in die.attributes:
            resolved = _resolve_attribute(die, 'DW_AT_type')
            if resolved is not None:
                return resolved
    return None


class _TypeWrapper:
    """Minimal DIE-shaped wrapper used to ask the C++ type formatter about a
    type DIE directly.  ``parse_cpp_datatype`` is written for variables and
    parameters, i.e. for DIEs that point at their type through DW_AT_type.
    """
    tag = None
    attributes = {'DW_AT_type': object()}

    def __init__(self, type_die: DIE) -> None:
        self._type_die = type_die

    def get_DIE_from_attribute(self, name: str) -> DIE:
        if name != 'DW_AT_type':
            raise KeyError(name)
        return self._type_die


def _type_name(type_die: DIE | None) -> str | None:
    if type_die is None:
        return None

    try:
        from .datatype_cpp import TypeDesc, parse_cpp_datatype
        description: TypeDesc = parse_cpp_datatype(cast('DIE', _TypeWrapper(type_die)))
        rendered = str(description)
        if rendered and rendered != 'None':
            return rendered
    except Exception:
        pass

    name_attr = _attr(type_die, 'DW_AT_name')
    if name_attr is not None:
        return _decode(name_attr.value)

    tag = type_die.tag
    if tag == 'DW_TAG_pointer_type':
        return 'void *' if 'DW_AT_type' not in type_die.attributes else None
    if tag == 'DW_TAG_reference_type':
        return 'void &' if 'DW_AT_type' not in type_die.attributes else None
    return None


def _cu_base(cu: CompileUnit | TypeUnit) -> int:
    top = cu.get_top_DIE()
    for name in ('DW_AT_low_pc', 'DW_AT_entry_pc'):
        attr = top.attributes.get(name)
        if attr is not None:
            return int(attr.value)

    ranges_attr = top.attributes.get('DW_AT_ranges')
    if ranges_attr is not None:
        try:
            range_lists = cu.dwarfinfo.range_lists()
            if range_lists is not None:
                entries = range_lists.get_range_list_at_offset(
                    ranges_attr.value, cast('CompileUnit', cu))
                candidates: list[int] = []
                for entry in entries:
                    if isinstance(entry, BaseAddressEntry):
                        candidates.append(entry.base_address)
                    elif isinstance(entry, RangeEntry) and entry.is_absolute:
                        candidates.append(entry.begin_offset)
                if candidates:
                    return min(candidates)
        except Exception:
            pass
    return 0


def _resolve_ranges(die: DIE) -> list[AddressRange]:
    cu = cast('CompileUnit', die.cu)
    ranges_attr = die.attributes.get('DW_AT_ranges')
    if ranges_attr is not None:
        try:
            range_lists = die.cu.dwarfinfo.range_lists()
            if range_lists is None:
                return []
            entries = range_lists.get_range_list_at_offset(ranges_attr.value, cu)
        except Exception:
            entries = []

        result: list[AddressRange] = []
        base = _cu_base(cu)
        for entry in entries:
            if isinstance(entry, BaseAddressEntry):
                base = entry.base_address
            elif isinstance(entry, RangeEntry):
                begin = entry.begin_offset if entry.is_absolute else base + entry.begin_offset
                end = entry.end_offset if entry.is_absolute else base + entry.end_offset
                if end > begin:
                    result.append(AddressRange(begin, end))
        if result:
            return result

    low_attr = die.attributes.get('DW_AT_low_pc')
    if low_attr is None:
        return []
    low = int(low_attr.value)
    high_attr = die.attributes.get('DW_AT_high_pc')
    if high_attr is None:
        return []
    try:
        high_class = describe_form_class(high_attr.form)
    except KeyError:
        high_class = 'constant'
    if high_class == 'address':
        high = int(high_attr.value)
    elif high_class == 'constant':
        high = low + int(high_attr.value)
    else:
        high = int(high_attr.value)
        if high < low:
            high = low + int(high_attr.value)
    return [AddressRange(low, high)] if high > low else []


def _file_name(cu: CompileUnit | TypeUnit, index: int | None) -> str | None:
    if index is None:
        return None
    try:
        line_program = cu.dwarfinfo.line_program_for_CU(cast('CompileUnit', cu))
        if line_program is None:
            return None
        if line_program.header.version >= 5:
            file_index = index
        else:
            if index == 0:
                return None
            file_index = index - 1
        entry = line_program.header.file_entry[file_index]
        return _decode(entry.name)
    except Exception:
        return None


def _make_row(die: DIE) -> InlineCallSite:
    chain = _declaration_chain(die)

    name_value = _first_attr(chain, 'DW_AT_name')
    if name_value is None:
        name_value = _first_attr(chain, 'DW_AT_linkage_name')
    name = _decode(name_value)
    name_raw = name_value if isinstance(name_value, bytes) else None

    linkage_name = _decode(_first_attr(chain, 'DW_AT_linkage_name'))
    type_die = _first_type_die(chain)
    type_name = _type_name(type_die)

    decl_file = _first_attr(chain, 'DW_AT_decl_file')
    decl_line = _first_attr(chain, 'DW_AT_decl_line')
    decl_column = _first_attr(chain, 'DW_AT_decl_column')

    call_file = _attr(die, 'DW_AT_call_file')
    call_line = _attr(die, 'DW_AT_call_line')
    call_column = _attr(die, 'DW_AT_call_column')
    call_pc = _attr(die, 'DW_AT_call_pc')
    call_return_pc = _attr(die, 'DW_AT_call_return_pc')

    declaration: DIE | None = None
    for attr_name in ('DW_AT_abstract_origin', 'DW_AT_specification'):
        declaration = _resolve_attribute(die, attr_name)
        if declaration is not None:
            break
    if declaration is None and len(chain) > 1:
        declaration = chain[1]

    declaration_offset = _reference_offset(die, 'DW_AT_abstract_origin')
    if declaration_offset is None:
        declaration_offset = _reference_offset(die, 'DW_AT_specification')

    parent: DIE | None
    try:
        parent = die.get_parent()
    except Exception:
        parent = None

    def _value(attr: Any) -> int | None:
        return int(attr.value) if attr is not None else None

    def _plain(value: Any) -> int | None:
        if value is None:
            return None
        return int(value.value) if hasattr(value, 'value') else int(value)

    return InlineCallSite(
        die,
        declaration=declaration,
        declaration_offset=declaration_offset,
        name=name,
        name_raw=name_raw,
        linkage_name=linkage_name,
        type_die=type_die,
        type_name=type_name,
        decl_file=_plain(decl_file),
        decl_line=_plain(decl_line),
        decl_column=_plain(decl_column),
        call_file=_value(call_file),
        call_line=_value(call_line),
        call_column=_value(call_column),
        call_pc=_value(call_pc),
        call_return_pc=_value(call_return_pc),
        ranges=_resolve_ranges(die),
        parent=parent,
        call_file_name=_file_name(die.cu, _value(call_file)),
        decl_file_name=_file_name(die.cu, _plain(decl_file)),
    )


def _nearest_inline_ancestor(
    die: DIE,
    by_offset: dict[int, InlineCallSite],
) -> tuple[InlineCallSite | None, int, DIE | None, DIE | None]:
    depth = 0
    caller: InlineCallSite | None = None
    caller_die: DIE | None = None
    subprogram: DIE | None = None
    try:
        parent = die.get_parent()
    except Exception:
        return None, 0, None, None
    while parent is not None:
        if parent.tag == 'DW_TAG_inlined_subroutine':
            depth += 1
            if caller is None:
                caller = by_offset.get(parent.offset)
                caller_die = parent
        elif parent.tag == 'DW_TAG_subprogram' and subprogram is None:
            subprogram = parent
        try:
            parent = parent.get_parent()
        except Exception:
            break
    return caller, depth, caller_die, subprogram


def _make_row_from_dict(data: dict[str, Any]) -> InlineCallSite:
    """Reconstruct a row from asdict()-style data without resolving DWARF."""
    name_value = data.get('name')
    if isinstance(name_value, bytes):
        name_value = name_value.decode('latin-1')
    type_value = data.get('type_name', data.get('type'))
    if isinstance(type_value, bytes):
        type_value = type_value.decode('latin-1')
    if data.get('type_name') is None and type_value is None and data.get('type') is not None:
        type_value = str(data['type'])
    ranges = [
        r if isinstance(r, AddressRange) else AddressRange(*r)
        for r in data.get('ranges', ())
    ]
    fake_die = _DictDIE(
        offset=int(data.get('offset', 0)),
        cu_offset=int(data.get('cu_offset', 0)))
    row = InlineCallSite(
        cast('DIE', fake_die),
        declaration=None,
        declaration_offset=data.get('declaration_offset'),
        name=str(name_value) if name_value is not None else None,
        name_raw=data.get('name_raw'),
        linkage_name=data.get('linkage_name'),
        type_die=None,
        type_name=str(type_value) if type_value is not None else None,
        decl_file=data.get('decl_file'),
        decl_line=data.get('decl_line'),
        decl_column=data.get('decl_column'),
        call_file=data.get('call_file'),
        call_line=data.get('call_line'),
        call_column=data.get('call_column'),
        call_pc=data.get('call_pc'),
        call_return_pc=data.get('call_return_pc'),
        ranges=ranges,
        parent=None,
        call_file_name=data.get('call_file_name'),
        decl_file_name=data.get('decl_file_name'),
    )
    row._depth = int(data.get('depth', 0))
    return row


class _DictCU:
    def __init__(self, cu_offset: int) -> None:
        self.cu_offset = cu_offset


class _DictDIE:
    def __init__(self, offset: int, cu_offset: int) -> None:
        self.offset = offset
        self.cu = _DictCU(cu_offset)
        self.attributes: dict[str, Any] = {}


def build_inline_call_site_table(
    dwarfinfo: DWARFInfo,
    cus: Iterable[CompileUnit] | None = None,
) -> InlineCallSiteTable:
    """Build one row per concrete DW_TAG_inlined_subroutine DIE.

    No de-duplication is performed: offsets, not names or abstract
    declarations, identify rows.
    """
    rows: list[InlineCallSite] = []
    for cu in (dwarfinfo.iter_CUs() if cus is None else cus):
        for die in cu.iter_DIEs():
            if die.tag == 'DW_TAG_inlined_subroutine':
                rows.append(_make_row(die))

    by_offset = {row.offset: row for row in rows}
    for row in rows:
        caller, depth, caller_die, subprogram = _nearest_inline_ancestor(row.die, by_offset)
        row._caller = caller
        row._depth = depth
        row.caller_die = caller_die
        row.caller_die_offset = caller_die.offset if caller_die is not None else None
        row.subprogram = subprogram
        row.subprogram_offset = subprogram.offset if subprogram is not None else None

    return InlineCallSiteTable(rows)


def collect_inline_call_sites(dwarfinfo: DWARFInfo) -> list[InlineCallSite]:
    """Return all concrete inline call-site rows in DIE order."""
    return build_inline_call_site_table(dwarfinfo).rows


def get_inline_call_site_table(dwarfinfo: DWARFInfo) -> InlineCallSiteTable:
    return build_inline_call_site_table(dwarfinfo)
