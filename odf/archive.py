# -*- coding: utf-8 -*-
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Contributor(s):
#

__doc__ = """
Archive the embedded objects and pictures an ODF document really uses.

The archive walker follows the references found in the document bodies:
``xlink:href`` attributes on ``draw:image``, ``draw:object``,
``draw:object-ole`` and ``draw:floating-frame`` elements. Relative
references are resolved against the (sub)document that contains them, so
identically named pictures in two different subdocuments can resolve to
different bytes.

Only in-package references are followed. References pointing outside the
package (absolute URLs, parent-path traversal, unknown schemes) and
references entering a cycle are recorded with the reason the walk stopped;
the import never leaves the package. One physical entry is archived once
even when several reference chains lead to it, and every reference chain
is kept.
"""

import hashlib
import json
import os
from posixpath import normpath as posix_normpath

from odf.element import Node
from odf.namespaces import DRAWNS, XLINKNS, nsdict
from odf.opendocument import IS_IMAGE

# Elements carrying an xlink:href that points at embedded content.
_LINKTAGS = (
    (DRAWNS, u'image'),
    (DRAWNS, u'object'),
    (DRAWNS, u'object-ole'),
    (DRAWNS, u'floating-frame'),
)

# Elements that represent a page/slide a referencing element appears on.
_PAGETAG = (DRAWNS, u'page')

# Stop reasons recorded on references the walk must not follow.
STOP_EXTERNAL = u'external-reference'
STOP_CYCLE = u'cycle'
STOP_MISSING = u'missing-entry'

_KIND_OBJECT = u'object'
_KIND_IMAGE = u'image'
_KIND_FILE = u'file'


class ArchivedResource:
    """
    One physical package entry saved for the retrieval service.

    @ivar path package-relative path of the entry (unicode), e.g.
        u'Pictures/a.png' or u'Object 1/Object 1/'
    @ivar mediatype media type declared in the manifest (unicode)
    @ivar content entry bytes, or None for folders and entries not in memory
    @ivar kind u'object', u'image' or u'file'
    @ivar references list of ReferenceInfo instances leading to the entry
    @ivar fingerprint sha1 hex of the content, or None when no content
    """

    def __init__(self, path, mediatype, content, kind):
        self.path = path
        self.mediatype = mediatype
        self.content = content
        self.kind = kind
        self.references = []
        self.fingerprint = hashlib.sha1(content).hexdigest() \
            if content else None

    def as_dict(self):
        return {
            u'path': self.path,
            u'mediatype': self.mediatype,
            u'kind': self.kind,
            u'fingerprint': self.fingerprint,
            u'references': [r.as_dict() for r in self.references],
        }

    def __repr__(self):
        return u"ArchivedResource(%r, %r, %d reference(s))" % (
            self.path, self.kind, len(self.references))


class ReferenceInfo:
    """
    One reference chain to an embedded entry.

    @ivar chain list of unicode steps from the package root; the last step
        describes the referencing element and its raw xlink:href
    @ivar container package-relative folder of the document holding the
        reference (u'' for the top-level document)
    @ivar page nearest enclosing draw:page name (unicode), or u'' when the
        reference is not on a draw:page
    @ivar href raw xlink:href value as found in the XML
    @ivar target resolved package-relative target path, or None when the
        reference could not be resolved inside the package
    @ivar stop reason the walk stopped, or None when the target was reached
    """

    def __init__(self, chain, container, page, href, target, stop):
        self.chain = chain
        self.container = container
        self.page = page
        self.href = href
        self.target = target
        self.stop = stop

    def as_dict(self):
        return {
            u'chain': list(self.chain),
            u'container': self.container,
            u'page': self.page,
            u'href': self.href,
            u'target': self.target,
            u'stop': self.stop,
        }

    def __repr__(self):
        return u"ReferenceInfo(%r -> %r, stop=%r)" % (
            self.href, self.target, self.stop)


class ArchiveReport:
    """
    Result of an archive run.

    @ivar resources dict mapping package-relative paths to ArchivedResource
    @ivar references flat list of every ReferenceInfo discovered, including
        references that could not be followed
    """

    def __init__(self):
        self.resources = {}
        self.references = []

    @property
    def unresolved(self):
        """ References whose target could not be reached inside the package. """
        return [r for r in self.references if r.stop is not None]

    def unique_resources(self):
        """
        The physical entities to hand over. Byte-identical entries are
        merged while building the report, so this is simply the archived
        resources, each carrying every reference chain that reaches it.
        """
        return list(self.resources.values())

    def as_dict(self):
        return {
            u'resources': [r.as_dict() for r in self.resources.values()],
            u'unresolved': [r.as_dict() for r in self.unresolved],
        }

    def get(self, path):
        return self.resources.get(path)

    def __iter__(self):
        return iter(self.resources.values())

    def __len__(self):
        return len(self.resources)


def _document_folder(document):
    """ Package-relative folder of a (sub)document without leading slash. """
    folder = document.folder or u""
    if folder.startswith(u"/"):
        folder = folder[1:]
    return folder


def _qualified_name(qname):
    prefix = nsdict.get(qname[0], u'')
    if prefix:
        return u'%s:%s' % (prefix, qname[1])
    return qname[1]


def _looks_like_scheme(href):
    # RFC 3986 scheme: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ) ":"
    if u':' not in href:
        return False
    head = href.split(u':', 1)[0]
    if not head or not head[0].isalpha():
        return False
    for char in head[1:]:
        if not (char.isalnum() or char in u'+-.'):
            return False
    return True


class _Archiver(object):
    """
    Holds the walk state for one loaded document tree.
    """

    def __init__(self, rootdoc, zipentries):
        self.rootdoc = rootdoc
        self.zipentries = zipentries or {}
        self.report = ArchiveReport()
        # folder path (u'' for root) -> OpenDocument instance
        self.docindex = {}
        # folder path -> folder path with trailing slash, for object targets
        self.objectfolders = set()
        self._index(rootdoc)
        self.manifest = rootdoc._package_manifest or {}
        # Folders whose bodies have already been walked.
        self.walked = set()
        # Folders on the current DFS stack: a link back into this set is a
        # genuine cycle, whereas a link to an already walked folder outside
        # the stack is a diamond reference and must keep both chains.
        self.active = set()

    def _index(self, document):
        folder = _document_folder(document)
        self.docindex[folder] = document
        for child in document.childobjects:
            childfolder = _document_folder(child)
            self.objectfolders.add(childfolder + u"/")
            self._index(child)

    def run(self):
        self._walk_document(self.rootdoc, [u"/"])
        return self.report

    def _walk_document(self, document, chain):
        folder = _document_folder(document)
        if folder in self.walked:
            return
        self.walked.add(folder)
        self.active.add(folder)
        if folder:
            self._register_object(folder + u"/", document)
        self._walk_element(document.body, folder, chain, [])
        self.active.discard(folder)

    def _register_object(self, path, document):
        resource = self.report.resources.get(path)
        if resource is None:
            mediatype = self._manifest_mediatype(path) or document.mimetype
            resource = ArchivedResource(path, mediatype, None, _KIND_OBJECT)
            self.report.resources[path] = resource
        return resource

    def _walk_element(self, node, folder, chain, pagestack):
        is_page = node.nodeType == Node.ELEMENT_NODE and \
            node.qname == _PAGETAG
        if is_page:
            pagestack = pagestack + [
                node.getAttrNS(DRAWNS, u'name') or u'']
        for child in node.childNodes:
            if child.nodeType != Node.ELEMENT_NODE:
                continue
            if child.qname in _LINKTAGS:
                href = child.getAttrNS(XLINKNS, u'href')
                if href:
                    self._handle_link(
                        folder, child, href, chain,
                        pagestack[-1] if pagestack else u'')
            # Links live inside frames, tables, pages and other containers.
            self._walk_element(child, folder, chain, pagestack)

    def _handle_link(self, folder, element, href, chain, page):
        stripped = href.strip()
        # A bare fragment points at an element inside the same XML part,
        # not at a package entry: nothing to archive or report.
        if not stripped or stripped.startswith(u'#'):
            return
        target, stop = self._resolve(folder, href)
        step = u'%s[@xlink:href="%s"]' % (
            _qualified_name(element.qname), href)
        refchain = chain + [step]
        reference = ReferenceInfo(
            chain=refchain, container=folder, page=page,
            href=href, target=target, stop=stop)
        self.report.references.append(reference)
        if stop is not None:
            return

        # A link back to the package root document is a back edge: the
        # root is always being walked while any reference is processed.
        if target == u"/":
            reference.stop = STOP_CYCLE
            return

        resource = self.report.resources.get(target)
        if resource is None:
            resource = self._create_resource(target)
            if resource is None:
                reference.stop = STOP_MISSING
                reference.target = None
                return
            # Byte-identical entries are delivered as one physical entity.
            # The reference keeps its own target, so both chains survive.
            twin = self._find_content_twin(resource)
            if twin is not None:
                twin.references.append(reference)
                return
            self.report.resources[target] = resource
        resource.references.append(reference)

        if resource.kind != _KIND_OBJECT:
            return
        targetfolder = target[:-1]
        subdoc = self.docindex.get(targetfolder)
        if subdoc is None:
            reference.stop = STOP_MISSING
            return
        if targetfolder in self.active:
            # Back edge: record where the cycle stops and do not re-enter.
            reference.stop = STOP_CYCLE
            return
        if targetfolder in self.walked:
            # Diamond: same document reached from another chain; it is only
            # walked once, but this reference chain is already preserved.
            return
        self._walk_document(subdoc, refchain)

    def _resolve(self, folder, href):
        """
        Resolves an href relative to folder without leaving the package.
        @return (target path or None, stop reason or None)
        """
        stripped = href.strip()
        # A path may carry a fragment (e.g. an object anchor); the entry
        # is addressed by the part before it.
        if u'#' in stripped:
            stripped = stripped.split(u'#', 1)[0]
        if not stripped:
            return None, STOP_MISSING
        if stripped.startswith(u'//') or _looks_like_scheme(stripped):
            return None, STOP_EXTERNAL
        if stripped.startswith(u'/'):
            # Absolute paths are not relative to the current subdocument;
            # stop instead of guessing a base outside it.
            return None, STOP_EXTERNAL

        base = folder + u"/" if folder else u""
        normalized = posix_normpath(base + stripped)
        if normalized == u".":
            normalized = u""
        # normpath keeps a leading '..' only when the reference escapes
        # the package root; references staying inside (e.g.
        # Object 1/../Pictures/a.png) resolve normally.
        if normalized.startswith(u".."):
            return None, STOP_EXTERNAL

        # Object folders are referenced without a trailing slash in
        # draw:object hrefs (e.g. "./Object 1"); normalise to the folder form.
        if normalized and normalized + u"/" in self.objectfolders:
            normalized = normalized + u"/"
        # A reference resolving to the package root addresses the top-level
        # document itself; canonicalise to its manifest folder entry.
        if not normalized:
            normalized = u"/"
        if self._entry_exists(normalized):
            return normalized, None
        return None, STOP_MISSING

    def _entry_exists(self, path):
        if path in self.objectfolders:
            return True
        if path in self.manifest or (path + u"/") in self.manifest:
            return True
        if path in self.zipentries:
            return True
        owner, arcname = self._locate_picture(path)
        return owner is not None

    def _create_resource(self, target):
        if target in self.objectfolders:
            document = self.docindex.get(target[:-1])
            mediatype = document.mimetype if document else \
                self._manifest_mediatype(target)
            return ArchivedResource(target, mediatype, None, _KIND_OBJECT)
        mediatype = self._manifest_mediatype(target)
        content = self._read_content(target)
        if content is None and target not in self.manifest \
                and (target + u"/") not in self.manifest \
                and target not in self.zipentries:
            owner, arcname = self._locate_picture(target)
            if owner is None:
                return None
        kind = _KIND_IMAGE if u"Pictures/" in target else _KIND_FILE
        return ArchivedResource(target, mediatype, content, kind)

    def _find_content_twin(self, resource):
        """ An already archived entry with the same content fingerprint. """
        if resource.fingerprint is None:
            return None
        for existing in self.report.resources.values():
            if existing is not resource and \
                    existing.fingerprint == resource.fingerprint:
                return existing
        return None

    def _manifest_mediatype(self, target):
        entry = self.manifest.get(target) or \
            self.manifest.get(target + u"/")
        if entry is not None:
            return entry.get('media-type', u'') or u''
        return u''

    def _read_content(self, target):
        if target in self.zipentries:
            return self.zipentries[target]()
        owner, arcname = self._locate_picture(target)
        if owner is None:
            return None
        record = owner.Pictures.get(arcname)
        if record is None:
            return None
        what_it_is, payload = record[0], record[1]
        # IS_FILENAME records only carry a path, no bytes.
        if what_it_is == IS_IMAGE:
            return payload
        return None

    def _locate_picture(self, path):
        """ Finds the (sub)document owning a package-relative picture path. """
        best = None
        best_length = -1
        for folder, document in self.docindex.items():
            prefix = folder + u"/" if folder else u""
            if not path.startswith(prefix):
                continue
            arcname = path[len(prefix):]
            if arcname in document.Pictures and len(prefix) > best_length:
                best = (document, arcname)
                best_length = len(prefix)
        return best if best is not None else (None, None)


def build_archive(document, zipentries=None):
    """
    Walks a loaded ODF document and builds the archive of the embedded
    objects and pictures actually referenced from its bodies.

    @param document an OpenDocument instance returned by load()
    @param zipentries optional mapping of package-relative entry names to
        zero-argument callables returning the entry bytes (typically a
        bound zipfile.ZipFile.read). When omitted, only content already held
        in memory by the loader is available.
    @return an ArchiveReport
    """
    return _Archiver(document, zipentries).run()


def save_archive(document, outputdir, zipfile_handle=None):
    """
    Builds the archive, writes each referenced entry below outputdir
    (preserving package-relative paths) and emits archive-index.json so the
    retrieval service learns which page objects reference each entity.

    Referenced pictures and other binary entries are always written when
    their bytes are available. When zipfile_handle is given, the XML parts
    (content.xml, styles.xml, ...) of referenced embedded objects are
    extracted as well; without it, objects are still listed in the index
    with their reference chains.

    @param document an OpenDocument instance returned by load()
    @param outputdir unicode directory to write into; created if missing
    @param zipfile_handle optional open zipfile.ZipFile of the source package
    @return the ArchiveReport describing the written entries and every stop
        point (external references, cycles, missing entries)
    """
    zipentries = None
    if zipfile_handle is not None:
        zipentries = dict(
            (name, (lambda n: lambda: zipfile_handle.read(n))(name))
            for name in zipfile_handle.namelist())
    report = build_archive(document, zipentries)
    if not os.path.isdir(outputdir):
        os.makedirs(outputdir)
    for resource in report.resources.values():
        if resource.kind == _KIND_OBJECT:
            _write_object_parts(resource, zipentries, outputdir)
        elif resource.content is not None:
            _write_file(outputdir, resource.path, resource.content)
    index_path = os.path.join(outputdir, u'archive-index.json')
    with open(index_path, u'w') as fd:
        json.dump(report.as_dict(), fd, ensure_ascii=False, indent=2)
    return report


def _write_file(outputdir, path, content):
    destination = os.path.join(outputdir, *path.split(u"/"))
    parent = os.path.dirname(destination)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(destination, u'wb') as fd:
        fd.write(content)


def _write_object_parts(resource, zipentries, outputdir):
    if not zipentries:
        return
    prefix = resource.path
    for name, reader in zipentries.items():
        if not name.startswith(prefix) or not name.endswith(u'.xml'):
            continue
        # Only the object's own XML parts; nested objects are separate
        # archived resources and handle their folders themselves.
        remainder = name[len(prefix):]
        if u"/" in remainder:
            continue
        _write_file(outputdir, name, reader())
