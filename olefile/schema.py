"""
Incremental schema inference for OLE property sets.

OLE property streams store values as VARIANT types (``VT_*``).  The same
logical property may be stored differently in different documents: a counter
that used to be a 32-bit integer may become a string, old documents may leave
the property empty, and a vector property may share its name with a scalar
property.  This module turns parsed property values into *column
descriptions* that can be merged incrementally while keeping the column
identity already handed to downstream consumers.

Typical use::

    from olefile.schema import Schema, TypedValue, VT_I4, VT_LPSTR

    january = Schema({
        'num_pages': TypedValue(3, VT_I4),
        'author': TypedValue(b'Alice', VT_LPSTR),
    })
    february = Schema({
        'num_pages': TypedValue('N/A', VT_LPSTR),
        'author': TypedValue(None, VT_EMPTY),
    })
    year = january.merge(february)
    year.describe()

Guarantees:

- **Stable identity.**  A column id depends only on the property name and on
  the scalar/vector shape, never on the observed type.  Widening an integer
  column to text keeps the same id.
- **Monotone, order-independent merging.**  :meth:`Schema.merge` is
  commutative and associative, so schemas inferred per month can be merged in
  any order.  The columns, values, counters and conflict samples match a
  single pass over the year (each observation merged once), regardless of
  whether intermediate descriptions went through JSON.
- **Numeric widening.**  Integer widths and integer/float mixes widen within
  the same numeric column.  Numeric-looking text (for example ``"123"``) is
  absorbed by a numeric column; the original spelling is retained in
  ``text_samples`` and all numeric values in ``original_values``.
- **Real text is preserved.**  When a column that held numbers receives real
  text, the column widens to text and ``original_type`` / ``original_value``
  still expose the numeric content.  ``value`` carries the text.
- **Vector and scalar are separate.**  ``name`` and ``name#vector`` are
  distinct ids and are never merged into one value.
- **Empty values do not invent types.**  ``VT_EMPTY``, ``VT_NULL`` and
  ``None`` are counted as nulls and leave the column type unchanged.
- **Serializable descriptions.**  :meth:`Column.describe` contains the full
  merge state, so descriptions can be stored as JSON per month and merged
  later with the same result as merging in memory.
"""

from __future__ import print_function

import datetime
import json

__all__ = [
    'Schema', 'ColumnSchema', 'ColumnDescription', 'SchemaBuilder',
    'Column', 'TypedValue', 'normalize_vtype',
    'infer_schema', 'infer_column_schema', 'infer_columns',
    'merge_schemas', 'merge_schema', 'merge_columns', 'merge', 'describe',
    'infer_properties_schema', 'infer_ole_schema',
    'parse_numeric_text', 'join_kind', 'value_kind', 'vtype_kind', 'vt_name',
    # OLE variant types, also re-exported by olefile itself
    'VT_EMPTY', 'VT_NULL', 'VT_I2', 'VT_I4', 'VT_R4', 'VT_R8', 'VT_CY',
    'VT_DATE', 'VT_BSTR', 'VT_DISPATCH', 'VT_ERROR', 'VT_BOOL',
    'VT_VARIANT', 'VT_UNKNOWN', 'VT_DECIMAL', 'VT_I1', 'VT_UI1', 'VT_UI2',
    'VT_UI4', 'VT_I8', 'VT_UI8', 'VT_INT', 'VT_UINT', 'VT_VOID',
    'VT_HRESULT', 'VT_PTR', 'VT_SAFEARRAY', 'VT_CARRAY', 'VT_USERDEFINED',
    'VT_LPSTR', 'VT_LPWSTR', 'VT_FILETIME', 'VT_BLOB', 'VT_STREAM',
    'VT_STORAGE', 'VT_STREAMED_OBJECT', 'VT_STORED_OBJECT',
    'VT_BLOB_OBJECT', 'VT_CF', 'VT_CLSID', 'VT_VECTOR',
]

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)

try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)

try:
    from collections.abc import Mapping
except ImportError:
    from collections import Mapping


# -- OLE VARIANT types -------------------------------------------------------
# Values match the VT_* constants in olefile.py.  They are repeated here so
# this module can be imported and used on its own.

VT_EMPTY = 0
VT_NULL = 1
VT_I2 = 2
VT_I4 = 3
VT_R4 = 4
VT_R8 = 5
VT_CY = 6
VT_DATE = 7
VT_BSTR = 8
VT_DISPATCH = 9
VT_ERROR = 10
VT_BOOL = 11
VT_VARIANT = 12
VT_UNKNOWN = 13
VT_DECIMAL = 14
VT_I1 = 16
VT_UI1 = 17
VT_UI2 = 18
VT_UI4 = 19
VT_I8 = 20
VT_UI8 = 21
VT_INT = 22
VT_UINT = 23
VT_VOID = 24
VT_HRESULT = 25
VT_PTR = 26
VT_SAFEARRAY = 27
VT_CARRAY = 28
VT_USERDEFINED = 29
VT_LPSTR = 30
VT_LPWSTR = 31
VT_FILETIME = 64
VT_BLOB = 65
VT_STREAM = 66
VT_STORAGE = 67
VT_STREAMED_OBJECT = 68
VT_STORED_OBJECT = 69
VT_BLOB_OBJECT = 70
VT_CF = 71
VT_CLSID = 72
VT_VECTOR = 0x1000

VT_NAMES = {
    VT_EMPTY: 'VT_EMPTY', VT_NULL: 'VT_NULL',
    VT_I2: 'VT_I2', VT_I4: 'VT_I4', VT_R4: 'VT_R4', VT_R8: 'VT_R8',
    VT_CY: 'VT_CY', VT_DATE: 'VT_DATE', VT_BSTR: 'VT_BSTR',
    VT_DISPATCH: 'VT_DISPATCH', VT_ERROR: 'VT_ERROR', VT_BOOL: 'VT_BOOL',
    VT_VARIANT: 'VT_VARIANT', VT_UNKNOWN: 'VT_UNKNOWN',
    VT_DECIMAL: 'VT_DECIMAL', VT_I1: 'VT_I1', VT_UI1: 'VT_UI1',
    VT_UI2: 'VT_UI2', VT_UI4: 'VT_UI4', VT_I8: 'VT_I8', VT_UI8: 'VT_UI8',
    VT_INT: 'VT_INT', VT_UINT: 'VT_UINT', VT_VOID: 'VT_VOID',
    VT_HRESULT: 'VT_HRESULT', VT_PTR: 'VT_PTR', VT_SAFEARRAY: 'VT_SAFEARRAY',
    VT_CARRAY: 'VT_CARRAY', VT_USERDEFINED: 'VT_USERDEFINED',
    VT_LPSTR: 'VT_LPSTR', VT_LPWSTR: 'VT_LPWSTR', VT_FILETIME: 'VT_FILETIME',
    VT_BLOB: 'VT_BLOB', VT_STREAM: 'VT_STREAM', VT_STORAGE: 'VT_STORAGE',
    VT_STREAMED_OBJECT: 'VT_STREAMED_OBJECT',
    VT_STORED_OBJECT: 'VT_STORED_OBJECT', VT_BLOB_OBJECT: 'VT_BLOB_OBJECT',
    VT_CF: 'VT_CF', VT_CLSID: 'VT_CLSID', VT_VECTOR: 'VT_VECTOR',
}
VT_BY_NAME = dict((name, value) for value, name in VT_NAMES.items())


def normalize_vtype(vtype):
    """Return an integer OLE ``VT_*`` code from an int or a type name.

    Accepts ``VT_I4``, ``'VT_I4'``, ``'VT_VECTOR|VT_LPSTR'`` and plain
    integers.  Returns ``None`` for ``None``/unknown names.
    """
    if vtype is None or isinstance(vtype, integer_types):
        return vtype
    if not isinstance(vtype, string_types):
        return None
    name = vtype.strip().upper()
    if name in VT_BY_NAME:
        return VT_BY_NAME[name]
    if name.startswith('VT_VECTOR|'):
        base = normalize_vtype(name[len('VT_VECTOR|'):])
        return None if base is None else base | VT_VECTOR
    return None

INTEGER_VTYPES = frozenset([
    VT_I1, VT_UI1, VT_I2, VT_UI2, VT_I4, VT_UI4, VT_I8, VT_UI8,
    VT_INT, VT_UINT, VT_ERROR, VT_CY, VT_HRESULT,
])
FLOAT_VTYPES = frozenset([VT_R4, VT_R8, VT_DECIMAL])
TEXT_VTYPES = frozenset([VT_BSTR, VT_LPSTR, VT_LPWSTR])
DATETIME_VTYPES = frozenset([VT_DATE, VT_FILETIME])
NULL_VTYPES = frozenset([VT_EMPTY, VT_NULL])
BLOB_VTYPES = frozenset([
    VT_BLOB, VT_BLOB_OBJECT, VT_CF, VT_CLSID, VT_STREAM, VT_STORAGE,
    VT_STREAMED_OBJECT, VT_STORED_OBJECT, VT_UNKNOWN, VT_DISPATCH,
])

# Narrow-to-wide ordering used to report the original integer type after
# widening (VT_I2 before VT_I4, VT_I4 before VT_R8, ...).
INTEGER_WIDTH = {
    VT_I1: 1, VT_UI1: 1,
    VT_I2: 2, VT_UI2: 2,
    VT_I4: 4, VT_UI4: 4, VT_INT: 4, VT_UINT: 4, VT_ERROR: 4, VT_HRESULT: 4,
    VT_I8: 8, VT_UI8: 8, VT_CY: 8,
    VT_R4: 16, VT_R8: 16, VT_DECIMAL: 16,
}

# Bounded sample sets.  Bottom-K selection makes union/merge associative.
MAX_SAMPLES = 32
MAX_CONFLICT_SAMPLES = 16

# Conflict classes: kinds in the same class do not conflict with each other.
_CLASS_OF_KIND = {
    'integer': 'numeric',
    'float': 'numeric',
    'boolean': 'boolean',
    'datetime': 'datetime',
    'blob': 'blob',
    'text': 'text',
    'any': 'any',
}
_CLASS_ORDER = {
    'numeric': 0, 'boolean': 1, 'datetime': 2, 'blob': 3, 'text': 4,
    'any': 5,
}

_CANONICAL_VTYPE = {
    'integer': VT_I4,
    'float': VT_R8,
    'text': VT_LPSTR,
    'boolean': VT_BOOL,
    'datetime': VT_FILETIME,
    'blob': VT_BLOB,
}


def vt_name(vtype):
    """Return the canonical ``VT_*`` name for an OLE variant type."""
    vtype = normalize_vtype(vtype)
    if vtype is None:
        return None
    if vtype & VT_VECTOR:
        base = vtype & ~VT_VECTOR
        if base == VT_VARIANT:
            return 'VT_VECTOR|VT_VARIANT'
        return 'VT_VECTOR|%s' % vt_name(base)
    return VT_NAMES.get(vtype, 'UNKNOWN(0x%X)' % vtype)


def vtype_kind(vtype):
    """Return the schema kind implied by an OLE variant type, or ``None``."""
    vtype = normalize_vtype(vtype)
    if vtype is None or vtype & VT_VECTOR:
        return None
    if vtype in NULL_VTYPES:
        return 'null'
    if vtype in INTEGER_VTYPES:
        return 'integer'
    if vtype in FLOAT_VTYPES:
        return 'float'
    if vtype in TEXT_VTYPES:
        return 'text'
    if vtype in DATETIME_VTYPES:
        return 'datetime'
    if vtype == VT_BOOL:
        return 'boolean'
    if vtype in BLOB_VTYPES:
        return 'blob'
    return 'any'


def value_kind(value):
    """Return the schema kind implied by a Python value."""
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, integer_types):
        return 'integer'
    if isinstance(value, float):
        return 'float'
    if isinstance(value, datetime.datetime) or isinstance(value, datetime.date):
        return 'datetime'
    if isinstance(value, bytearray):
        return 'blob'
    if isinstance(value, bytes):
        # getproperties() returns CodePageString values as bytes; without an
        # explicit VT type, bytes are text rather than an opaque blob.
        return 'text'
    if isinstance(value, string_types):
        return 'text'
    return 'any'


def _text_of(value):
    """Decode bytes/OLE strings to text, preserving byte order."""
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return value.decode('latin-1')
    if isinstance(value, bytearray):
        return bytes(value).decode('latin-1')
    if isinstance(value, string_types):
        return value
    return str(value)


def parse_numeric_text(value):
    """Interpret text as a plain decimal number.

    Returns ``(kind, number)`` where kind is ``'integer'`` or ``'float'``,
    or ``None`` when the text is not a plain decimal number.  Surrounding
    whitespace is ignored.  Thousands separators, currency symbols, exponents
    and NaN/Infinity are deliberately rejected so the result is predictable.
    """
    if not isinstance(value, string_types):
        return None
    text = value.strip()
    if not text:
        return None
    body = text[1:] if text[:1] in ('+', '-') else text
    if not body:
        return None
    if body.count('.') > 1:
        return None
    whole, dot, fraction = body.partition('.')
    if whole and not whole.isdigit():
        return None
    if fraction and not fraction.isdigit():
        return None
    if not whole and not fraction:
        return None
    if dot:
        try:
            return 'float', float(text)
        except (ValueError, OverflowError):
            return None
    try:
        return 'integer', int(text)
    except (ValueError, OverflowError):
        return None


def _stable_value(value):
    """Return a deterministic, JSON-serializable representation of a value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, integer_types):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return _text_of(value)
    if isinstance(value, string_types):
        return value
    return repr(value)


def _value_order(value):
    """Total ordering key for heterogeneous values (never raises)."""
    if value is None:
        return (0, '')
    return (1, value_kind(value), repr(value))


def join_kind(left, right):
    """Join two schema kinds in a widening lattice.

    ``null`` is the identity, ``integer`` widens to ``float``, and ``text``
    absorbs numeric kinds (an integer counter may become a string).  Other
    incompatible combinations widen to ``any``, which is the top element.
    The operation is commutative and associative.
    """
    if left == right:
        return left
    if left == 'null':
        return right
    if right == 'null':
        return left
    if left == 'any' or right == 'any':
        return 'any'
    numeric = ('integer', 'float')
    if left in numeric and right in numeric:
        return 'float'
    if left in numeric and right == 'text':
        return 'text'
    if right in numeric and left == 'text':
        return 'text'
    return 'any'


def _class_of(kind):
    return _CLASS_OF_KIND.get(kind, 'any')


# -- typed input -------------------------------------------------------------

class TypedValue(object):
    """A parsed value together with its OLE variant type.

    :param value: parsed Python value
    :param vtype: OLE ``VT_*`` type, or ``None`` to infer it from the value
    :param vector: True for a vector value.  Also set automatically when
        ``vtype`` contains ``VT_VECTOR``.

    A plain Python value cannot distinguish a numeric ``VT_I4`` from a
    numeric-looking ``VT_LPSTR``; pass :class:`TypedValue` when that matters.
    """

    __slots__ = ('value', 'vtype', 'vector')

    def __init__(self, value, vtype=None, vector=False):
        vtype = normalize_vtype(vtype)
        self.value = value
        self.vtype = vtype
        self.vector = bool(vector) or bool(
            vtype is not None and vtype & VT_VECTOR)

    def __repr__(self):
        return 'TypedValue(%r, vtype=%r, vector=%r)' % (
            self.value, self.vtype, self.vector)

    def __eq__(self, other):
        return (isinstance(other, TypedValue) and
                self.value == other.value and
                self.vtype == other.vtype and
                self.vector == other.vector)

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash((repr(self.value), self.vtype, self.vector))

    @classmethod
    def from_ole(cls, value, vtype=None, vector=None):
        """Build a typed value from a parsed value and an OLE ``VT_*`` type.

        ``vector`` defaults to the ``VT_VECTOR`` bit in ``vtype``.
        """
        if vector is None:
            vector = bool(vtype is not None and vtype & VT_VECTOR)
        return cls(value, vtype, vector)


def _as_typed(value):
    """Recognize an explicit ``(value, vtype)`` pair.

    Returns a :class:`TypedValue` or ``None``.  A pair is only treated as
    typed when the second element is an OLE ``VT_*`` integer; ordinary
    two-element vectors remain vectors.
    """
    if isinstance(value, Mapping):
        keys = set(value)
        type_key = None
        for candidate in ('vtype', 'type', 'vt', 'ole_type'):
            if candidate in keys:
                type_key = candidate
                break
        if type_key is not None and 'value' in keys:
            return TypedValue(value.get('value'), value[type_key],
                              value.get('vector', False))
        return None
    if isinstance(value, tuple) and len(value) == 2:
        first, second = value
        # ``(value, VT_TYPE)`` and ``(VT_TYPE, value)`` are both accepted.
        # The type may be an integer code or a name such as 'VT_LPSTR'.  A
        # tuple like ``('a', 'b')`` is therefore still a vector.
        if isinstance(second, (integer_types, string_types)) and \
                not isinstance(second, bool):
            normalized = normalize_vtype(second)
            if normalized is not None:
                return TypedValue(first, normalized)
        if isinstance(first, (integer_types, string_types)) and \
                not isinstance(first, bool):
            normalized = normalize_vtype(first)
            if normalized is not None:
                return TypedValue(second, normalized)
    return None


def _strip_vector(vtype):
    """Return the base type of a vector type (``None`` for VT_VECTOR|VT_VARIANT)."""
    vtype = normalize_vtype(vtype)
    if vtype is None:
        return None
    if not vtype & VT_VECTOR:
        return vtype
    base = vtype & ~VT_VECTOR
    return None if base == VT_VARIANT else base


# -- observation -------------------------------------------------------------

class _Observation(object):
    """One normalized observation retained in a bounded sample set."""

    __slots__ = ('kind', 'vtype', 'value', 'text')

    def __init__(self, kind, vtype, value, text=None):
        self.kind = kind
        self.vtype = vtype
        self.value = value
        # original text spelling when the value arrived as text
        self.text = text

    def key(self):
        # _stable_value() normalizes bytes to text, so identical observations
        # deduplicate even when one arrives as bytes and another as str.
        return (_class_of(self.kind), self.kind, vt_name(self.vtype) or '',
                repr(_stable_value(self.value)),
                repr(_stable_value(self.text)))

    def to_state(self):
        return {
            'kind': self.kind,
            'vtype': vt_name(self.vtype),
            'value': _stable_value(self.value),
            'text': _stable_value(self.text),
        }

    @classmethod
    def from_state(cls, state):
        return cls(state.get('kind') or 'any',
                   VT_BY_NAME.get(state.get('vtype')),
                   state.get('value'),
                   state.get('text'))

    def to_conflict_sample(self):
        return {
            'kind': self.kind,
            'vtype': vt_name(self.vtype),
            'value': _stable_value(self.value),
            'original_value': _stable_value(
                self.text if self.text is not None else self.value),
        }

    def to_text_sample(self):
        return {
            'kind': 'text',
            'vtype': vt_name(self.vtype),
            'value': _stable_value(self.value),
            'text': _stable_value(self.text),
        }


def _bottom_k(observations, limit=MAX_SAMPLES):
    """Deduplicate and keep the deterministic bottom-K observations."""
    unique = {}
    for observation in observations:
        unique[observation.key()] = observation
    ordered = sorted(unique.values(), key=lambda item: item.key())
    return ordered[:limit]


class Column(object):
    """Description of one logical column.

    :attr:`id` is the stable identity consumers receive.  It is built from
    the property name and the scalar/vector shape only, so widening the type
    never changes the id.
    """

    __slots__ = ('name', 'vector', '_kind', 'vtypes', 'count', 'null_count',
                 'text_count', 'coerced_text_count', 'real_text_count',
                 '_kind_counts', '_samples', '_text_samples')

    def __init__(self, name, vector=False):
        self.name = _normalize_name(name)
        self.vector = bool(vector)
        self._kind = 'null'
        self.vtypes = set()
        self.count = 0
        self.null_count = 0
        # observations that arrived as text (parsed numeric or real text)
        self.text_count = 0
        # observations that arrived as text but were parsed as numbers
        self.coerced_text_count = 0
        # observations that arrived as text and could not be parsed as a
        # number (the "real text" that widens a numeric column)
        self.real_text_count = 0
        # exact observation count per non-null kind (used for conflicts)
        self._kind_counts = {}
        # bounded samples per kind
        self._samples = {}
        # bounded original spellings of numeric text
        self._text_samples = []

    # -- identity ---------------------------------------------------------

    @property
    def id(self):
        """Stable column id: ``name`` for scalars, ``name#vector`` for vectors."""
        return '%s#vector' % (self.name,) if self.vector else str(self.name)

    @property
    def column_id(self):
        """Alias for :attr:`id`."""
        return self.id

    @property
    def scalar(self):
        """True for a scalar column."""
        return not self.vector

    @property
    def kind(self):
        """Current (widened) column kind."""
        return self._kind

    def __repr__(self):
        return 'Column(%r, kind=%r, vector=%r)' % (
            self.id, self._kind, self.vector)

    @classmethod
    def from_dict(cls, description):
        """Build a column from :meth:`describe` output."""
        column = cls(description['name'], description.get('vector', False))
        schema = Schema.from_dict({'columns': [description]})
        return schema.columns[column.id]

    # -- observation ------------------------------------------------------

    def observe(self, kind, value, vtype=None, is_text=False, coerced=False,
                text=None):
        """Record one normalized observation."""
        self.count += 1
        if vtype is not None:
            self.vtypes.add(vtype & ~VT_VECTOR)
        if kind == 'null':
            self.null_count += 1
            return
        self._kind = join_kind(self._kind, kind)
        self._kind_counts[kind] = self._kind_counts.get(kind, 0) + 1
        if is_text:
            self.text_count += 1
            if not coerced:
                self.real_text_count += 1
        observation = _Observation(kind, vtype, value,
                                   text if coerced else None)
        if coerced:
            self.coerced_text_count += 1
            self._text_samples = _bottom_k(
                self._text_samples + [_Observation('text', vtype, text, text)])
        bucket = self._samples.setdefault(kind, [])
        bucket[:] = _bottom_k(bucket + [observation])

    # -- merging ----------------------------------------------------------

    def merge(self, other):
        """Merge another column with the same id into this one."""
        self.vtypes = set(self.vtypes) | set(other.vtypes)
        self.count += other.count
        self.null_count += other.null_count
        self.text_count += other.text_count
        self.coerced_text_count += other.coerced_text_count
        self.real_text_count += other.real_text_count
        self._kind = join_kind(self._kind, other._kind)
        for kind, count in other._kind_counts.items():
            self._kind_counts[kind] = self._kind_counts.get(kind, 0) + count
        for kind, samples in other._samples.items():
            bucket = self._samples.setdefault(kind, [])
            bucket[:] = _bottom_k(bucket + samples)
        self._text_samples = _bottom_k(self._text_samples +
                                       other._text_samples)
        return self

    def copy(self):
        other = Column(self.name, self.vector)
        other._kind = self._kind
        other.vtypes = set(self.vtypes)
        other.count = self.count
        other.null_count = self.null_count
        other.text_count = self.text_count
        other.coerced_text_count = self.coerced_text_count
        other.real_text_count = self.real_text_count
        other._kind_counts = dict(self._kind_counts)
        other._samples = dict((kind, list(samples))
                              for kind, samples in self._samples.items())
        other._text_samples = list(self._text_samples)
        return other

    # -- derived description ---------------------------------------------

    def _all_observations(self):
        result = []
        for kind in sorted(self._samples):
            result.extend(self._samples[kind])
        return result

    def _observations_of_class(self, conflict_class):
        result = [observation for observation in self._all_observations()
                  if _class_of(observation.kind) == conflict_class]
        result.sort(key=lambda item: item.key())
        return result

    def _class_counts(self):
        counts = {}
        for kind, count in self._kind_counts.items():
            conflict_class = _class_of(kind)
            counts[conflict_class] = counts.get(conflict_class, 0) + count
        return counts

    def _primary_class(self):
        counts = self._class_counts()
        if not counts:
            return None
        # Highest count wins; ties are broken by class name so the choice is
        # total and independent of observation or merge order.
        return sorted(counts, key=lambda name: (-counts[name],
                                                name))[0]

    def _classes(self):
        return sorted(self._class_counts(),
                      key=lambda name: _CLASS_ORDER.get(name, 99))

    def conflict_count(self):
        """Number of non-null observations outside the primary class.

        A column mixing integer and float has no conflicts.  A column that
        holds numbers and real text counts the less frequent side.
        """
        counts = self._class_counts()
        if not counts:
            return 0
        primary = self._primary_class()
        return sum(counts.values()) - counts.get(primary, 0)

    def conflict_samples(self):
        """Return deterministic samples of conflicting observations.

        Samples come from every class except the primary one, plus a single
        representative of the primary class.  Selection depends only on the
        merged state, never on insertion order, which makes month-by-month
        merges equal to a single pass.
        """
        classes = self._classes()
        if len(classes) < 2:
            return []
        primary = self._primary_class()
        selected = []
        for conflict_class in classes:
            observations = self._observations_of_class(conflict_class)
            if conflict_class == primary:
                selected.extend(observations[:1])
            else:
                selected.extend(observations[:MAX_CONFLICT_SAMPLES])
        selected.sort(key=lambda item: item.key())
        return [observation.to_conflict_sample()
                for observation in selected[:MAX_CONFLICT_SAMPLES]]

    def _kind_observations(self, kinds):
        result = []
        for kind in kinds:
            result.extend(self._samples.get(kind, []))
        result.sort(key=lambda item: item.key())
        return result

    def _numeric_type(self):
        """Return the narrowest observed numeric variant type, or ``None``."""
        numeric = [vtype for vtype in self.vtypes if vtype in INTEGER_WIDTH]
        if not numeric:
            return None
        numeric.sort(key=lambda vtype: (INTEGER_WIDTH.get(vtype, 99),
                                        vt_name(vtype)))
        return numeric[0]

    def _numeric_value(self):
        numeric = self._kind_observations(['integer', 'float'])
        if not numeric:
            return None
        numeric.sort(key=lambda item: (
            INTEGER_WIDTH.get(item.vtype, 99),
            _value_order(item.value)))
        return numeric[0].value

    @property
    def value(self):
        """The canonical column value, chosen deterministically."""
        if self._kind == 'null':
            return None
        if self._kind == 'text':
            texts = self._kind_observations(['text'])
            if texts:
                return texts[0].value
            return self._numeric_value()
        if _class_of(self._kind) == 'numeric':
            numeric = self._numeric_value()
            if numeric is not None:
                return numeric
        observations = self._all_observations()
        if not observations:
            return None
        observations.sort(key=lambda item: _value_order(item.value))
        return observations[0].value

    @property
    def original_type(self):
        """Original type before widening, as a ``VT_*`` name, or ``None``.

        When real text widened a numeric column, this is the narrowest numeric
        type observed.  When several numeric widths were observed it is the
        narrowest one, so the original column can continue to carry the
        values.  Pure, single-type columns return ``None``.
        """
        numeric_type = self._numeric_type()
        classes = self._class_counts()
        if numeric_type is not None:
            if self._kind == 'text':
                # real text widened the column: the numeric interpretation
                # remains queryable through original_type/original_value
                return vt_name(numeric_type)
            numeric_types = set(vtype for vtype in self.vtypes
                                if vtype in INTEGER_WIDTH)
            if len(numeric_types) > 1:
                return vt_name(numeric_type)
        if len(classes) >= 2:
            primary = self._primary_class()
            if primary == 'numeric':
                return vt_name(self._numeric_type())
            observations = self._observations_of_class(primary)
            if observations:
                return vt_name(observations[0].vtype)
        return None

    @property
    def original_vtype(self):
        """Original numeric ``VT_*`` value before widening, or ``None``.

        Numeric counterpart of :attr:`original_type`; it is only set when a
        widening actually occurred.
        """
        if self.original_type is None:
            return None
        return self._numeric_type()

    @property
    def original_value(self):
        """Original value before widening, or ``None``.

        This is the value paired with :meth:`original_type`, so a column
        widened from integer to text can be queried both ways.
        """
        numeric_type = self._numeric_type()
        if numeric_type is not None and (
                self._kind == 'text' or
                len(self.vtypes & set(INTEGER_WIDTH)) > 1):
            return _stable_value(self._numeric_value())
        if len(self._class_counts()) >= 2:
            primary = self._primary_class()
            observations = self._observations_of_class(primary)
            if observations:
                return _stable_value(observations[0].value)
        return None

    def text_samples(self):
        """Return original spellings of text values that parsed as numbers.

        These are the exact strings found in the property stream (for
        example ``'0042'``), independent of the parsed numeric value.
        """
        return [_stable_value(observation.text)
                for observation in self._text_samples]

    @property
    def original_values(self):
        """Numeric values retained for look-back after a type widening.

        Returns a deterministic, bounded list of the numeric observations that
        made up the column before it was widened.  Empty when the column was
        only ever text.
        """
        numeric = self._kind_observations(['integer', 'float'])
        numeric.sort(key=lambda item: (
            INTEGER_WIDTH.get(item.vtype, 99),
            _value_order(item.value)))
        return [_stable_value(item.value) for item in numeric]

    def describe(self):
        """Return a JSON-serializable description including merge state."""
        conflicts = self.conflict_samples()
        return {
            'id': self.id,
            'column_id': self.id,
            'name': self.name,
            'vector': self.vector,
            'kind': self._kind,
            'type': self._kind,
            'vtypes': sorted(vt_name(vtype) for vtype in self.vtypes),
            'vtype_codes': sorted(self.vtypes),
            'count': self.count,
            'null_count': self.null_count,
            'text_count': self.text_count,
            'coerced_text_count': self.coerced_text_count,
            'real_text_count': self.real_text_count,
            'value': _stable_value(self.value),
            'original_type': self.original_type,
            'original_vtype': self.original_vtype,
            'original_value': self.original_value,
            'original_values': self.original_values,
            'text_samples': self.text_samples(),
            'conflicts': self.conflict_count(),
            'conflict_samples': conflicts,
            # exact state, so a description can be merged later
            'state': {
                'kind_counts': dict(self._kind_counts),
                'samples': dict(
                    (kind, [observation.to_state()
                            for observation in samples])
                    for kind, samples in self._samples.items()),
                'text_samples': [observation.to_text_sample()
                                 for observation in self._text_samples],
            },
        }

    def fingerprint(self):
        """Return a hashable summary for equivalence checks."""
        return (
            self.id,
            self._kind,
            tuple(sorted(vt_name(vtype) for vtype in self.vtypes)),
            self.count,
            self.null_count,
            self.text_count,
            self.coerced_text_count,
            tuple(sorted(self._kind_counts.items())),
            tuple(tuple(sorted(observation.key()))
                  for observation in self._all_observations()),
            tuple(tuple(sorted(observation.key()))
                  for observation in self._text_samples),
            repr(_stable_value(self.value)),
            self.original_type,
            repr(_stable_value(self.original_value)),
            tuple(tuple(sorted(sample.items()))
                  for sample in self.conflict_samples()),
        )


# -- schema ------------------------------------------------------------------

def _normalize_name(name):
    if isinstance(name, bytes):
        return _text_of(name)
    return name if isinstance(name, string_types) else str(name)


def _column_id(name, vector):
    name = _normalize_name(name)
    return '%s#vector' % name if vector else name


def _coerce_schema(value):
    """Convert schema descriptions/mappings/records to a :class:`Schema`.

    Returns ``None`` for values that are not schemas/descriptions/records.
    """
    if value is None:
        return None
    if isinstance(value, Schema):
        return value
    if isinstance(value, Mapping):
        columns = value.get('columns')
        if isinstance(columns, (list, tuple)) and columns and \
                isinstance(columns[0], Mapping) and 'id' in columns[0]:
            return Schema.from_dict(value)
        if columns is not None and isinstance(columns, (list, tuple)):
            return Schema.from_dict({'columns': list(columns)})
        values = list(value.values())
        if values and isinstance(values[0], Mapping) and 'id' in values[0]:
            return Schema.from_dict({'columns': values})
        return Schema(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return Schema()
        if isinstance(value[0], Mapping) and 'id' in value[0]:
            return Schema.from_dict({'columns': value})
        if isinstance(value[0], Mapping):
            return Schema(value)
    return None


class Schema(object):
    """An incrementally mergeable set of :class:`Column` descriptions.

    ``Schema`` is mutable; :meth:`merge` updates ``self`` and returns it so it
    can be used in a reduce.  Columns are keyed by their stable id, so merging
    schemas that describe the same property widens the existing column instead
    of creating a duplicate.

    Merging is commutative and associative over a partition of the input
    observations: each monthly schema must be merged exactly once.  Merging the
    same schema twice would count its observations twice (like adding the same
    month twice), so callers tracking cumulative state should keep their
    schemas disjoint; use :meth:`merged` when both operands must be preserved.

    :param properties: optional mapping or iterable of ``(name, value)``
        pairs.  Values may be plain values, :class:`TypedValue` instances, or
        lists/tuples representing vectors.
    :param name: optional label for the schema (month, file, ...).
    """

    def __init__(self, properties=None, name=None):
        self.name = name
        self.columns = {}
        if properties is not None:
            self.update(properties)

    def __bool__(self):
        return bool(self.columns)

    # Python 2 name for __bool__.
    __nonzero__ = __bool__

    # -- input ------------------------------------------------------------

    def observe(self, name, values):
        """Observe one property.

        ``values`` may be a scalar, a :class:`TypedValue`, or a vector
        (list/tuple).  A list/tuple is always treated as a vector; wrap a
        scalar container in ``TypedValue(container, vector=False)``.
        """
        if not isinstance(values, TypedValue):
            typed = _as_typed(values)
            if typed is not None:
                values = typed
        if isinstance(values, TypedValue):
            payload = values.value
            base_vtype = _strip_vector(values.vtype)
            is_vector = values.vector or bool(
                values.vtype is not None and values.vtype & VT_VECTOR)
            if is_vector:
                self._observe_vector(name, payload, base_vtype)
            else:
                self._observe_one(name, payload, base_vtype)
            return self
        if isinstance(values, (list, tuple)):
            self._observe_vector(name, values, None)
            return self
        self._observe_one(name, values, None)
        return self

    def _observe_vector(self, name, payload, base_vtype):
        column = self._get_or_create(name, True)
        base_vtype = normalize_vtype(base_vtype)
        if not isinstance(payload, (list, tuple)):
            self._observe_in(column, payload, base_vtype)
            return
        for item in payload:
            if isinstance(item, TypedValue):
                self._observe_in(column, item.value,
                                 _strip_vector(item.vtype))
            else:
                self._observe_in(column, item, base_vtype)

    def _observe_one(self, name, value, vtype):
        self._observe_in(self._get_or_create(name, False), value, vtype)

    def _observe_in(self, column, value, vtype):
        kind, normalized, vtype, is_text, coerced, text = _classify(value, vtype)
        column.observe(kind, normalized, vtype=vtype, is_text=is_text,
                       coerced=coerced, text=text)
        return column

    def _get_or_create(self, name, vector):
        column_id = _column_id(name, vector)
        column = self.columns.get(column_id)
        if column is None:
            column = Column(name, vector)
            self.columns[column_id] = column
        return column

    def update(self, properties):
        """Observe properties from a mapping, a record, or records.

        Accepted forms:

        - ``{'name': value, ...}``: one record (or one column mapping)
        - ``[{'name': value, ...}, ...]``: several documents/records
        - ``[('name', value), ...]``: explicit column/value pairs
        """
        if isinstance(properties, Schema):
            return self.merge(properties)
        if isinstance(properties, Mapping):
            for name, values in properties.items():
                self.observe(name, values)
            return self
        items = list(properties)
        if not items:
            return self
        if all(isinstance(item, Mapping) for item in items):
            # a list of records: each mapping is one document
            for record in items:
                for name, values in record.items():
                    self.observe(name, values)
            return self
        for name, values in items:
            self.observe(name, values)
        return self

    @classmethod
    def from_records(cls, records, name=None):
        """Infer a schema from an iterable of property mappings."""
        return cls(records, name=name)

    # -- merging ----------------------------------------------------------

    def merge(self, other):
        """Merge ``other`` into this schema and return ``self``.

        This updates ``self`` in place.  Over disjoint inputs the merge is
        commutative and associative, so monthly schemas can be combined in any
        order and still match a single pass.  Merging the same schema twice
        counts its observations twice.  Use :meth:`merged` for a
        non-destructive version.
        """
        schema = _coerce_schema(other)
        if schema is None:
            return self
        for column_id, other_column in schema.columns.items():
            column = self.columns.get(column_id)
            if column is None:
                self.columns[column_id] = other_column.copy()
            else:
                column.merge(other_column)
        return self

    def merged(self, other):
        """Return a new schema with ``other`` merged in, leaving both
        originals unchanged."""
        return self.copy().merge(other)

    def __add__(self, other):
        return self.merged(other)

    def copy(self):
        """Return an independent copy of this schema."""
        other = Schema(name=self.name)
        for column_id, column in self.columns.items():
            other.columns[column_id] = column.copy()
        return other

    # -- output -----------------------------------------------------------

    def __len__(self):
        return len(self.columns)

    def __iter__(self):
        return iter(self.describe())

    def __contains__(self, column_id):
        return _normalize_name(column_id) in self.columns

    def __getitem__(self, column_id):
        return self.columns[_normalize_name(column_id)]

    def __repr__(self):
        return 'Schema(%r)' % (sorted(self.columns),)

    def describe(self):
        """Return the column descriptions as a JSON-serializable list."""
        return [self.columns[column_id].describe()
                for column_id in sorted(self.columns)]

    def as_dict(self):
        """Return column descriptions keyed by stable column id."""
        return dict((column_id, self.columns[column_id].describe())
                    for column_id in sorted(self.columns))

    def column_ids(self):
        """Return the stable column ids, sorted."""
        return sorted(self.columns)

    def to_dict(self):
        """Return a fully JSON-serializable representation."""
        return {'name': self.name, 'columns': self.describe()}

    def to_json(self, **kwargs):
        """Serialize to JSON (deterministic key order by default)."""
        kwargs.setdefault('sort_keys', True)
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data):
        """Rebuild a schema from :meth:`to_dict` output."""
        schema = cls(name=data.get('name'))
        for description in data.get('columns', []):
            column = Column(description['name'],
                            description.get('vector', False))
            column._kind = description.get('kind', 'null')
            column.count = description.get('count', 0)
            column.null_count = description.get('null_count', 0)
            column.text_count = description.get('text_count', 0)
            column.coerced_text_count = description.get(
                'coerced_text_count', 0)
            column.real_text_count = description.get('real_text_count', 0)
            for name in description.get('vtypes', []):
                vtype = VT_BY_NAME.get(name)
                if vtype is not None:
                    column.vtypes.add(vtype)
            state = description.get('state')
            if state:
                column._kind_counts = dict(state.get('kind_counts', {}))
                for kind, samples in state.get('samples', {}).items():
                    column._samples[kind] = [
                        _Observation.from_state(sample) for sample in samples]
                column._text_samples = [
                    _Observation('text', VT_BY_NAME.get(sample.get('vtype')),
                                 sample.get('value'), sample.get('text'))
                    for sample in state.get('text_samples', [])]
            else:
                # Fall back to the samples visible in the description.
                for sample in description.get('conflict_samples', []):
                    kind = sample.get('kind') or 'any'
                    observation = _Observation(
                        kind, VT_BY_NAME.get(sample.get('vtype')),
                        sample.get('value'), sample.get('original_value'))
                    column._samples.setdefault(kind, []).append(observation)
                    column._kind_counts[kind] = (
                        column._kind_counts.get(kind, 0) + 1)
            schema.columns[column.id] = column
        return schema

    @classmethod
    def from_json(cls, text):
        """Rebuild a schema from :meth:`to_json` output."""
        return cls.from_dict(json.loads(text))

    @property
    def conflict_samples(self):
        """Conflict samples of all columns, flattened and sorted."""
        return self.conflicts()

    def conflicts(self):
        """Return conflict samples of all columns, flattened and sorted."""
        result = []
        for column_id in sorted(self.columns):
            for sample in self.columns[column_id].conflict_samples():
                item = dict(sample)
                item['column'] = column_id
                result.append(item)
        return result

    def columns_by_id(self):
        """Return a shallow copy of ``{column_id: Column}``."""
        return dict(self.columns)

    @classmethod
    def from_properties(cls, properties, name=None):
        """Alias for constructing a schema from property values."""
        return cls(properties, name=name)

    @classmethod
    def merge_all(cls, schemas, name=None):
        """Merge an iterable of schemas/descriptions into a new schema."""
        result = cls(name=name)
        for schema in schemas:
            result.merge(schema)
        return result

    @classmethod
    def from_schemas(cls, schemas, name=None):
        """Alias for :meth:`merge_all`."""
        return cls.merge_all(schemas, name=name)

    def fingerprints(self):
        """Return ``{column_id: fingerprint}`` for equivalence checks."""
        return dict((column_id, column.fingerprint())
                    for column_id, column in self.columns.items())


def _classify(value, vtype):
    """Normalize one observation.

    Returns ``(kind, normalized_value, vtype, is_text, coerced, text)``.
    Text is always examined for a numeric spelling; if it parses, the logical
    kind is numeric and the original spelling is retained.  Otherwise the
    logical kind is text.
    """
    declared = vtype_kind(vtype)
    if declared == 'null' or value is None:
        return 'null', None, vtype, False, False, None
    if declared is None:
        declared = value_kind(value)

    if declared == 'text':
        text = _text_of(value)
        parsed = parse_numeric_text(text)
        if parsed is not None:
            return parsed[0], parsed[1], vtype, True, True, text
        return 'text', text, vtype, True, False, text

    if declared == 'integer':
        if isinstance(value, bool):
            return 'integer', int(value), vtype, False, False, None
        try:
            return 'integer', int(value), vtype, False, False, None
        except (TypeError, ValueError):
            # The caller declared an integer type but the parsed value does
            # not look like one (for example a truncated string).  Keep the
            # declared kind so type information from the stream wins.
            return 'integer', value, vtype, False, False, None

    if declared == 'float':
        try:
            return 'float', float(value), vtype, False, False, None
        except (TypeError, ValueError):
            return 'float', value, vtype, False, False, None

    if declared == 'boolean':
        return 'boolean', bool(value), vtype, False, False, None

    if declared == 'blob':
        return 'blob', value, vtype, False, False, None

    if declared == 'datetime':
        return 'datetime', value, vtype, False, False, None

    return 'any', value, vtype, False, False, None


# -- convenience functions --------------------------------------------------

def infer_schema(properties, name=None):
    """Infer a :class:`Schema` from a mapping/iterable of property values."""
    return Schema(properties, name=name)


def merge_schemas(*schemas):
    """Merge any number of schemas/descriptions into a new schema."""
    result = Schema()
    for schema in schemas:
        result.merge(schema)
    return result


def merge(*schemas):
    """Alias for :func:`merge_schemas`."""
    return merge_schemas(*schemas)


def describe(properties, name=None):
    """Infer a schema and return its JSON-serializable column list."""
    return infer_schema(properties, name=name).describe()


def infer_properties_schema(ole_file, stream_name, convert_time=False,
                            no_conversion=None, name=None):
    """Infer a schema from one property stream of an ``OleFileIO`` object.

    OLE variant types are preserved through
    ``getproperties(..., with_types=True)`` so integer widths, text types and
    vector shapes are known rather than guessed from Python values.
    """
    properties = ole_file.getproperties(
        stream_name, convert_time=convert_time,
        no_conversion=no_conversion, with_types=True)
    return infer_schema(properties, name=name)


def infer_ole_schema(ole_file, streams=None, prefix=True,
                     convert_time=False, no_conversion=None):
    """Infer one schema from all property streams of an ``OleFileIO``.

    :param ole_file: an open :class:`olefile.OleFileIO`
    :param streams: optional iterable of stream names; by default every
        stream whose last path component starts with ``\\x05``
    :param prefix: if True, prefix property ids with the stream name so equal
        property ids from different streams stay in separate columns.
    """
    schema = Schema()
    if streams is None:
        streams = ole_file.listdir()
    for stream_name in streams:
        if isinstance(stream_name, (list, tuple)):
            if not (stream_name and _is_property_stream_name(stream_name[-1])):
                continue
        elif not _is_property_stream_name(stream_name):
            continue
        try:
            properties = ole_file.getproperties(
                stream_name, convert_time=convert_time,
                no_conversion=no_conversion, with_types=True)
        except Exception:
            continue
        if isinstance(stream_name, (list, tuple)):
            stream_label = _text_of(stream_name[-1])
        else:
            stream_label = _text_of(stream_name)
        for property_id, value in properties.items():
            column_name = ('%s:%s' % (stream_label, property_id)
                           if prefix else property_id)
            schema.observe(column_name, value)
    return schema


def _is_property_stream_name(name):
    """True for standard OLE property stream names (starting with 0x05)."""
    if isinstance(name, bytes):
        return name[:1] == b'\x05'
    if isinstance(name, string_types):
        return name[:1] == '\x05'
    return False


# Explicit aliases for discoverability.
ColumnSchema = Schema
ColumnDescription = Column
SchemaBuilder = Schema
infer_column_schema = infer_schema
infer_columns = infer_schema
merge_columns = merge_schemas
merge_schema = merge_schemas
