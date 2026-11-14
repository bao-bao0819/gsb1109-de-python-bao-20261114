#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Audit user fields in OpenDocument packages.

The module distinguishes three things that are easy to confuse in an
ODF document:

* The field declaration (``text:user-field-decl``) which holds the
  authoritative name, value type and value of a user field.
* The field references (``text:user-field-get`` and
  ``text:user-field-input``) which are the *real* places where a field
  is used.  Plain text that merely happens to spell the same value is
  not a reference and is therefore never reported.
* The cached display text stored inside the reference element.  Office
  applications only refresh this text when the field is updated, so it
  can lag behind the value held by the declaration.

Each embedded object (the ``Object N`` folders in the package) is an
independent document: a field found inside it is resolved against the
declarations of that object only, never against the host document.
"""

import io
import sys
import zipfile
import xml.sax
from xml.sax import handler as saxhandler

from defusedxml import sax as defused_sax

from odf.namespaces import OFFICENS, STYLENS, TEXTNS

if sys.version_info[0] == 3:
    unicode = str

USER_FIELD_DECL = (TEXTNS, u'user-field-decl')
USER_FIELD_GET = (TEXTNS, u'user-field-get')
USER_FIELD_INPUT = (TEXTNS, u'user-field-input')
FIELD_ELEMENTS = (USER_FIELD_DECL, USER_FIELD_GET, USER_FIELD_INPUT)
REFERENCE_ELEMENTS = (USER_FIELD_GET, USER_FIELD_INPUT)

MASTER_PAGE = (STYLENS, u'master-page')
HEADER_FOOTER_ELEMENTS = (
    (STYLENS, u'header'),
    (STYLENS, u'header-left'),
    (STYLENS, u'footer'),
    (STYLENS, u'footer-left'),
)

# OpenDocument v1.2 section 7.7.4: the attribute carrying the value
# depends on the declared value type.
VALUE_ATTRIBUTES = {
    u'float': (OFFICENS, u'value'),
    u'percentage': (OFFICENS, u'value'),
    u'currency': (OFFICENS, u'value'),
    u'date': (OFFICENS, u'date-value'),
    u'time': (OFFICENS, u'time-value'),
    u'boolean': (OFFICENS, u'boolean-value'),
    u'string': (OFFICENS, u'string-value'),
}

MAIN_SCOPE = u''


class FieldDeclaration(object):
    """A ``text:user-field-decl`` element found in the package."""

    def __init__(self, scope, part, line, column, name,
                 value_type, value):
        self.scope = scope
        self.part = part
        self.line = line
        self.column = column
        self.name = name
        self.value_type = value_type
        self.value = value

    @property
    def location(self):
        return u"%s:%d:%d" % (self.part, self.line, self.column)

    def as_dict(self):
        return {
            u'kind': u'declaration',
            u'scope': self.scope,
            u'part': self.part,
            u'line': self.line,
            u'column': self.column,
            u'location': self.location,
            u'name': self.name,
            u'value_type': self.value_type,
            u'value': self.value,
        }


class FieldReference(object):
    """A ``text:user-field-get``/``text:user-field-input`` occurrence."""

    def __init__(self, scope, part, line, column, region, kind,
                 name, display_text):
        self.scope = scope
        self.part = part
        self.line = line
        self.column = column
        self.region = region
        self.kind = kind
        self.name = name
        self.display_text = display_text
        self.declaration = None

    @property
    def location(self):
        return u"%s:%d:%d" % (self.part, self.line, self.column)

    @property
    def declared(self):
        return self.declaration is not None

    @property
    def display_matches_value(self):
        """True when cached text equals the declared value."""
        if self.declaration is None or self.declaration.value is None:
            return False
        return self.display_text == self.declaration.value

    @property
    def status(self):
        """Freshness classification of the cached display text.

        ``undeclared``
            no declaration with this name exists in the same scope.
        ``no-stored-value``
            declaration carries no value attribute.
        ``matches``
            cached display text equals the declared value.
        ``stale``
            string field whose cached text differs from the declared
            value.  String values are displayed verbatim, so a
            difference reliably means the field has not been refreshed.
        ``display-differs``
            non-string field whose cached text differs.  Numbers,
            dates and booleans may be rendered with formatting
            (decimals, currency symbols, date patterns), so the
            difference is reported but not claimed to be stale.
        """
        if self.declaration is None:
            return u'undeclared'
        if self.declaration.value is None:
            return u'no-stored-value'
        if self.display_matches_value:
            return u'matches'
        if self.declaration.value_type == u'string':
            return u'stale'
        return u'display-differs'

    def as_dict(self):
        declaration = None
        if self.declaration is not None:
            declaration = {
                u'part': self.declaration.part,
                u'location': self.declaration.location,
                u'value_type': self.declaration.value_type,
                u'value': self.declaration.value,
            }
        return {
            u'kind': u'reference',
            u'scope': self.scope,
            u'part': self.part,
            u'line': self.line,
            u'column': self.column,
            u'location': self.location,
            u'region': self.region,
            u'element': self.kind,
            u'name': self.name,
            u'display_text': self.display_text,
            u'declared': self.declared,
            u'declaration': declaration,
            u'display_matches_value': self.display_matches_value,
            u'status': self.status,
        }


class AuditResult(object):
    """All declarations and references discovered in one package."""

    def __init__(self, source):
        self.source = source
        self.declarations = []
        self.references = []

    @property
    def undeclared_references(self):
        return [r for r in self.references if not r.declared]

    @property
    def stale_references(self):
        """String references whose cached text is provably outdated."""
        return [r for r in self.references if r.status == u'stale']

    @property
    def display_differ_references(self):
        """Non-string references whose cached text differs (formatting?)."""
        return [r for r in self.references
                if r.status == u'display-differs']

    def declarations_for(self, field_name):
        return [d for d in self.declarations if d.name == field_name]

    def references_for(self, field_name):
        return [r for r in self.references if r.name == field_name]

    def field_names(self):
        names = set()
        for item in self.declarations + self.references:
            names.add(item.name)
        return sorted(names)

    def filter(self, field_names):
        """Return a shallow copy restricted to the given field names."""
        filtered = AuditResult(self.source)
        filtered.declarations = [
            d for d in self.declarations if d.name in field_names]
        filtered.references = [
            r for r in self.references if r.name in field_names]
        return filtered

    def as_dict(self):
        return {
            u'source': self.source,
            u'declarations': [d.as_dict() for d in self.declarations],
            u'references': [r.as_dict() for r in self.references],
            u'undeclared_references': [
                r.location for r in self.undeclared_references],
            u'stale_references': [
                r.location for r in self.stale_references],
            u'display_differ_references': [
                r.location for r in self.display_differ_references],
        }


def scope_label(scope):
    """Human readable label for a package scope."""
    if scope == MAIN_SCOPE:
        return u'main document'
    return u'embedded object "%s"' % scope


def _scope_of_part(partname):
    """Return (scope id, part kind) for a package part, or None."""
    if partname in (u'content.xml', u'styles.xml'):
        return MAIN_SCOPE, partname[:-4]
    for tail in (u'/content.xml', u'/styles.xml'):
        if partname.endswith(tail):
            return partname[:-len(tail)], tail[1:-4]
    return None


class _PartHandler(saxhandler.ContentHandler):
    """SAX handler collecting declarations and references in one part."""

    def __init__(self, partname, scope, part_kind, raw_bytes):
        self.partname = partname
        self.scope = scope
        self.part_kind = part_kind
        self.declarations = []
        self.references = []

        self._locator = None
        self._parser = None
        self._master_pages = []
        self._regions = []
        self._open_fields = []

        self._raw_bytes = raw_bytes
        # Byte offset (from the start of the part) at which each line
        # begins.  Used to convert expat's absolute byte position into a
        # 1-based character column without multibyte ambiguities.
        self._line_start_bytes = [0]
        for chunk in raw_bytes.split(b'\n')[:-1]:
            self._line_start_bytes.append(
                self._line_start_bytes[-1] + len(chunk) + 1)

    def setDocumentLocator(self, locator):  # noqa: N802 (SAX API)
        self._locator = locator

    def _position(self):
        """Return the current (1-based line, 1-based character column).

        The column points at the opening ``<`` of the current element.
        """
        locator = self._locator or self._parser
        line = locator.getLineNumber()
        raw_expat = getattr(locator, u'_parser', None)
        if raw_expat is not None:
            absolute_byte = raw_expat.CurrentByteIndex
            line_start = self._line_start_bytes[line - 1]
            column_byte = absolute_byte - line_start
            column = len(
                self._raw_bytes[line_start:line_start + column_byte]
                .decode('utf-8')) + 1
            return line, column

        # Fallback for parsers that do not expose the raw expat object.
        expat_column = locator.getColumnNumber()
        return line, expat_column + 1

    def _current_region(self):
        if self._regions:
            master = self._master_pages[-1] if self._master_pages else u'?'
            return u'%s of master page "%s"' % (
                self._regions[-1], master)
        if self.part_kind == u'styles':
            return u'styles'
        return u'body'

    def startElementNS(self, name, qname, attrs):  # noqa: N802 (SAX API)
        if name == MASTER_PAGE:
            self._master_pages.append(
                attrs.get((STYLENS, u'name')) or u'?')
            return
        if name in HEADER_FOOTER_ELEMENTS:
            self._regions.append(name[1])
            return
        if name not in FIELD_ELEMENTS:
            return

        line, column = self._position()
        field_name = attrs.get((TEXTNS, u'name'))
        if name == USER_FIELD_DECL:
            value_type = attrs.get((OFFICENS, u'value-type'))
            value = self._extract_value(attrs, value_type)
            self.declarations.append(FieldDeclaration(
                self.scope, self.partname, line, column,
                field_name, value_type, value))
            return

        self._open_fields.append(FieldReference(
            self.scope, self.partname, line, column,
            self._current_region(), name[1], field_name, u''))

    def _extract_value(self, attrs, value_type):
        value_attr = VALUE_ATTRIBUTES.get(value_type)
        if value_attr is not None:
            return attrs.get(value_attr)
        # Unknown value type: report whatever office:*value attribute
        # the declaration carries.
        for (namespace, attr_name), attr_value in attrs.items():
            if namespace == OFFICENS and attr_name.endswith(u'value'):
                return attr_value
        return None

    def characters(self, content):
        if self._open_fields:
            current = self._open_fields[-1]
            current.display_text += content

    def endElementNS(self, name, qname):  # noqa: N802 (SAX API)
        if name == MASTER_PAGE and self._master_pages:
            self._master_pages.pop()
            return
        if name in HEADER_FOOTER_ELEMENTS and self._regions:
            self._regions.pop()
            return
        if name in REFERENCE_ELEMENTS and self._open_fields:
            self.references.append(self._open_fields.pop())


def _make_parser():
    parser = defused_sax.make_parser()
    parser.setFeature(saxhandler.feature_namespaces, True)
    return parser


def audit_package(src, field_names=None):
    """Audit an ODF package.

    @param src filename (unicode string), binary file object or
    BytesIO containing an ODF package.
    @param field_names optional iterable of field names to restrict the
    audit to.  All fields are audited when omitted.
    @return AuditResult
    """
    is_filename = isinstance(src, (bytes, unicode))
    if not zipfile.is_zipfile(src):
        if is_filename:
            raise TypeError(u"%s is no odf file." % src)
        raise TypeError(u"the given stream is no odf file.")
    archive = zipfile.ZipFile(src)
    owns_archive = is_filename
    result = AuditResult(src if is_filename else u'<stream>')
    wanted = set(field_names) if field_names is not None else None
    try:
        parts = []
        for partname in archive.namelist():
            scope_kind = _scope_of_part(partname)
            if scope_kind is not None:
                parts.append((partname,) + scope_kind)
        parts.sort()

        for partname, scope, part_kind in parts:
            raw_bytes = archive.read(partname)
            handler = _PartHandler(partname, scope, part_kind, raw_bytes)
            parser = _make_parser()
            handler._parser = parser
            parser.setContentHandler(handler)
            source = xml.sax.InputSource()
            source.setByteStream(io.BytesIO(raw_bytes))
            parser.parse(source)
            result.declarations.extend(handler.declarations)
            result.references.extend(handler.references)
    finally:
        if owns_archive:
            archive.close()

    declarations_by_scope = {}
    for declaration in result.declarations:
        declarations_by_scope.setdefault(
            declaration.scope, {}).setdefault(
            declaration.name, []).append(declaration)

    for reference in result.references:
        candidates = declarations_by_scope.get(
            reference.scope, {}).get(reference.name)
        if candidates:
            reference.declaration = candidates[0]

    result.declarations.sort(
        key=lambda d: (d.scope, d.part, d.line, d.column, d.name))
    result.references.sort(
        key=lambda r: (r.scope, r.part, r.line, r.column))

    if wanted is not None:
        result = result.filter(wanted)
    return result
