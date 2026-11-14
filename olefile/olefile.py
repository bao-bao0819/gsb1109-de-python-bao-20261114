"""
olefile (formerly OleFileIO_PL)

Module to read/write Microsoft OLE2 files (also called Structured Storage or
Microsoft Compound Document File Format), such as Microsoft Office 97-2003
documents, Image Composer and FlashPix files, Outlook messages, ...
This version is compatible with Python 2.7 and 3.5+

Project website: https://www.decalage.info/olefile

olefile is copyright (c) 2005-2023 Philippe Lagadec
(https://www.decalage.info)

olefile is based on the OleFileIO module from the PIL library v1.1.7
See: http://www.pythonware.com/products/pil/index.htm
and http://svn.effbot.org/public/tags/pil-1.1.7/PIL/OleFileIO.py

The Python Imaging Library (PIL) is
Copyright (c) 1997-2009 by Secret Labs AB
Copyright (c) 1995-2009 by Fredrik Lundh

See source code and LICENSE.txt for information on usage and redistribution.
"""

# Since olefile v0.47, only Python 2.7 and 3.5+ are supported
# This import enables print() as a function rather than a keyword
# (main requirement to be compatible with Python 3.x)
# The comment on the line below should be printed on Python 2.5 or older:
from __future__ import print_function   # This version of olefile requires Python 2.7 or 3.5+.


#--- LICENSE ------------------------------------------------------------------

# olefile (formerly OleFileIO_PL) is copyright (c) 2005-2023 Philippe Lagadec
# (https://www.decalage.info)
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# ----------
# PIL License:
#
# olefile is based on source code from the OleFileIO module of the Python
# Imaging Library (PIL) published by Fredrik Lundh under the following license:

# The Python Imaging Library (PIL) is
#    Copyright (c) 1997-2009 by Secret Labs AB
#    Copyright (c) 1995-2009 by Fredrik Lundh
#
# By obtaining, using, and/or copying this software and/or its associated
# documentation, you agree that you have read, understood, and will comply with
# the following terms and conditions:
#
# Permission to use, copy, modify, and distribute this software and its
# associated documentation for any purpose and without fee is hereby granted,
# provided that the above copyright notice appears in all copies, and that both
# that copyright notice and this permission notice appear in supporting
# documentation, and that the name of Secret Labs AB or the author(s) not be used
# in advertising or publicity pertaining to distribution of the software
# without specific, written prior permission.
#
# SECRET LABS AB AND THE AUTHORS DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
# SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS.
# IN NO EVENT SHALL SECRET LABS AB OR THE AUTHORS BE LIABLE FOR ANY SPECIAL,
# INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.

__date__    = "2023-12-01"
__version__ = '0.47'
__author__  = "Philippe Lagadec"

__all__ = ['isOleFile', 'OleFileIO', 'OleMetadata', 'enable_logging',
           'MAGIC', 'STGTY_EMPTY',
           'STGTY_STREAM', 'STGTY_STORAGE', 'STGTY_ROOT', 'STGTY_PROPERTY',
           'STGTY_LOCKBYTES', 'MINIMAL_OLEFILE_SIZE',
           'DEFECT_UNSURE', 'DEFECT_POTENTIAL', 'DEFECT_INCORRECT',
           'DEFECT_FATAL', 'DEFAULT_PATH_ENCODING',
           'MAXREGSECT', 'DIFSECT', 'FATSECT', 'ENDOFCHAIN', 'FREESECT',
           'MAXREGSID', 'NOSTREAM', 'UNKNOWN_SIZE', 'WORD_CLSID',
           'OleFileIONotClosed',
           'StreamReplacement', 'StreamReplacementResult',
           'StreamReplacementError', 'StreamPreconditionError',
           'StreamOverlapError', 'OutputStreamConflictError',
           'REPLACEMENT_APPLIED', 'REPLACEMENT_ALREADY_APPLIED',
           'stream_digest', 'replace_streams', 'write_streams'
]

import io
import os
import sys
import struct, array, os.path, datetime, logging, warnings, traceback
import hashlib
import tempfile
import errno
import binascii

#=== COMPATIBILITY WORKAROUNDS ================================================

# For Python 3.x, need to redefine long as int:
if str is not bytes:
    long = int

# Need to make sure we use xrange both on Python 2 and 3.x:
try:
    # on Python 2 we need xrange:
    iterrange = xrange
except Exception:
    # no xrange, for Python 3 it was renamed as range:
    iterrange = range

# [PL] workaround to fix an issue with array item size on 64 bits systems:
if array.array('L').itemsize == 4:
    # on 32 bits platforms, long integers in an array are 32 bits:
    UINT32 = 'L'
elif array.array('I').itemsize == 4:
    # on 64 bits platforms, integers in an array are 32 bits:
    UINT32 = 'I'
elif array.array('i').itemsize == 4:
    # On 64 bit Jython, signed integers ('i') are the only way to store our 32
    # bit values in an array in a *somewhat* reasonable way, as the otherwise
    # perfectly suited 'H' (unsigned int, 32 bits) results in a completely
    # unusable behaviour. This is most likely caused by the fact that Java
    # doesn't have unsigned values, and thus Jython's "array" implementation,
    # which is based on "jarray", doesn't have them either.
    # NOTE: to trick Jython into converting the values it would normally
    # interpret as "signed" into "unsigned", a binary-and operation with
    # 0xFFFFFFFF can be used. This way it is possible to use the same comparing
    # operations on all platforms / implementations. The corresponding code
    # lines are flagged with a 'JYTHON-WORKAROUND' tag below.
    UINT32 = 'i'
else:
    raise ValueError('Need to fix a bug with 32 bit arrays, please contact author...')


# [PL] These workarounds were inspired from the Path module
# (see http://www.jorendorff.com/articles/python/path/)
# TODO: remove the use of basestring, as it was removed in Python 3
try:
    basestring
except NameError:
    basestring = str

if sys.version_info[0] < 3:
    # On Python 2.x, the default encoding for path names is UTF-8:
    DEFAULT_PATH_ENCODING = 'utf-8'
else:
    # On Python 3.x, the default encoding for path names is Unicode (None):
    DEFAULT_PATH_ENCODING = None


# === LOGGING =================================================================

def get_logger(name, level=logging.CRITICAL+1):
    """
    Create a suitable logger object for this module.
    The goal is not to change settings of the root logger, to avoid getting
    other modules' logs on the screen.
    If a logger exists with same name, reuse it. (Else it would have duplicate
    handlers and messages would be doubled.)
    The level is set to CRITICAL+1 by default, to avoid any logging.
    """
    # First, test if there is already a logger with the same name, else it
    # will generate duplicate messages (due to duplicate handlers):
    if name in logging.Logger.manager.loggerDict:
        #NOTE: another less intrusive but more "hackish" solution would be to
        # use getLogger then test if its effective level is not default.
        logger = logging.getLogger(name)
        # make sure level is OK:
        logger.setLevel(level)
        return logger
    # get a new logger:
    logger = logging.getLogger(name)
    # only add a NullHandler for this logger, it is up to the application
    # to configure its own logging:
    logger.addHandler(logging.NullHandler())
    logger.setLevel(level)
    return logger


# a global logger object used for debugging:
log = get_logger('olefile')


def enable_logging():
    """
    Enable logging for this module (disabled by default).
    This will set the module-specific logger level to NOTSET, which
    means the main application controls the actual logging level.
    """
    log.setLevel(logging.NOTSET)


#=== CONSTANTS ===============================================================

#: magic bytes that should be at the beginning of every OLE file:
MAGIC = b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'

# [PL]: added constants for Sector IDs (from AAF specifications)
MAXREGSECT = 0xFFFFFFFA #: (-6) maximum SECT
DIFSECT    = 0xFFFFFFFC #: (-4) denotes a DIFAT sector in a FAT
FATSECT    = 0xFFFFFFFD #: (-3) denotes a FAT sector in a FAT
ENDOFCHAIN = 0xFFFFFFFE #: (-2) end of a virtual stream chain
FREESECT   = 0xFFFFFFFF #: (-1) unallocated sector

# [PL]: added constants for Directory Entry IDs (from AAF specifications)
MAXREGSID  = 0xFFFFFFFA #: (-6) maximum directory entry ID
NOSTREAM   = 0xFFFFFFFF #: (-1) unallocated directory entry

# [PL] object types in storage (from AAF specifications)
STGTY_EMPTY     = 0 #: empty directory entry
STGTY_STORAGE   = 1 #: element is a storage object
STGTY_STREAM    = 2 #: element is a stream object
STGTY_LOCKBYTES = 3 #: element is an ILockBytes object
STGTY_PROPERTY  = 4 #: element is an IPropertyStorage object
STGTY_ROOT      = 5 #: element is a root storage

# Unknown size for a stream (used by OleStream):
UNKNOWN_SIZE = 0x7FFFFFFF

#
# --------------------------------------------------------------------
# property types

VT_EMPTY=0; VT_NULL=1; VT_I2=2; VT_I4=3; VT_R4=4; VT_R8=5; VT_CY=6;
VT_DATE=7; VT_BSTR=8; VT_DISPATCH=9; VT_ERROR=10; VT_BOOL=11;
VT_VARIANT=12; VT_UNKNOWN=13; VT_DECIMAL=14; VT_I1=16; VT_UI1=17;
VT_UI2=18; VT_UI4=19; VT_I8=20; VT_UI8=21; VT_INT=22; VT_UINT=23;
VT_VOID=24; VT_HRESULT=25; VT_PTR=26; VT_SAFEARRAY=27; VT_CARRAY=28;
VT_USERDEFINED=29; VT_LPSTR=30; VT_LPWSTR=31; VT_FILETIME=64;
VT_BLOB=65; VT_STREAM=66; VT_STORAGE=67; VT_STREAMED_OBJECT=68;
VT_STORED_OBJECT=69; VT_BLOB_OBJECT=70; VT_CF=71; VT_CLSID=72;
VT_VECTOR=0x1000;

# map property id to name (for debugging purposes)
VT = {}
for keyword, var in list(vars().items()):
    if keyword[:3] == "VT_":
        VT[var] = keyword

#
# --------------------------------------------------------------------
# Some common document types (root.clsid fields)

WORD_CLSID = "00020900-0000-0000-C000-000000000046"
# TODO: check Excel, PPT, ...

# [PL]: Defect levels to classify parsing errors - see OleFileIO._raise_defect()
DEFECT_UNSURE =    10    # a case which looks weird, but not sure it's a defect
DEFECT_POTENTIAL = 20    # a potential defect
DEFECT_INCORRECT = 30    # an error according to specifications, but parsing
                         # can go on
DEFECT_FATAL =     40    # an error which cannot be ignored, parsing is
                         # impossible

# Minimal size of an empty OLE file, with 512-bytes sectors = 1536 bytes
# (this is used in isOleFile and OleFileIO.open)
MINIMAL_OLEFILE_SIZE = 1536

#=== FUNCTIONS ===============================================================

def isOleFile (filename=None, data=None):
    """
    Test if a file is an OLE container (according to the magic bytes in its header).

    .. note::
        This function only checks the first 8 bytes of the file, not the
        rest of the OLE structure.
        If data is provided, it also checks if the file size is above
        the minimal size of an OLE file (1536 bytes).
        If filename is provided with the path of the file on disk, the file is
        open only to read the first 8 bytes, then closed.

    .. versionadded:: 0.16

    :param filename: filename, contents or file-like object of the OLE file (string-like or file-like object)

        - if data is provided, filename is ignored.
        - if filename is a unicode string, it is used as path of the file to open on disk.
        - if filename is a bytes string smaller than 1536 bytes, it is used as path
          of the file to open on disk.
        - [deprecated] if filename is a bytes string longer than 1535 bytes, it is parsed
          as the content of an OLE file in memory. (bytes type only)
          Note that this use case is deprecated and should be replaced by the new data parameter
        - if filename is a file-like object (with read and seek methods),
          it is parsed as-is.
    :type filename: bytes, str, unicode or file-like object

    :param data: bytes string with the contents of the file to be checked, when the file is in memory
                 (added in olefile 0.47)
    :type data: bytes

    :returns: True if OLE, False otherwise.
    :rtype: bool
    """
    header = None
    # first check if data is provided and large enough
    if data is not None:
        if len(data) >= MINIMAL_OLEFILE_SIZE:
            header = data[:len(MAGIC)]
        else:
            # the file is too small, cannot be OLE
            return False
    # check if filename is a string-like or file-like object:
    elif hasattr(filename, 'read') and hasattr(filename, 'seek'):
        # file-like object: use it directly
        header = filename.read(len(MAGIC))
        # just in case, seek back to start of file:
        filename.seek(0)
    elif isinstance(filename, bytes) and len(filename) >= MINIMAL_OLEFILE_SIZE:
        # filename is a bytes string containing the OLE file to be parsed:
        header = filename[:len(MAGIC)]
    else:
        # string-like object: filename of file on disk
        with open(filename, 'rb') as fp:
            header = fp.read(len(MAGIC))
    if header == MAGIC:
        return True
    else:
        return False


if bytes is str:
    # version for Python 2.x
    def i8(c):
        return ord(c)
else:
    # version for Python 3.x
    def i8(c):
        return c if c.__class__ is int else c[0]


def i16(c, o = 0):
    """
    Converts a 2-bytes (16 bits) string to an integer.

    :param c: string containing bytes to convert
    :param o: offset of bytes to convert in string
    """
    return struct.unpack("<H", c[o:o+2])[0]


def i32(c, o = 0):
    """
    Converts a 4-bytes (32 bits) string to an integer.

    :param c: string containing bytes to convert
    :param o: offset of bytes to convert in string
    """
    return struct.unpack("<I", c[o:o+4])[0]


def _clsid(clsid):
    """
    Converts a CLSID to a human-readable string.

    :param clsid: string of length 16.
    """
    assert len(clsid) == 16
    # if clsid is only made of null bytes, return an empty string:
    # (PL: why not simply return the string with zeroes?)
    if not clsid.strip(b"\0"):
        return ""
    return (("%08X-%04X-%04X-%02X%02X-" + "%02X" * 6) %
            ((i32(clsid, 0), i16(clsid, 4), i16(clsid, 6)) +
            tuple(map(i8, clsid[8:16]))))



def filetime2datetime(filetime):
    """
    convert FILETIME (64 bits int) to Python datetime.datetime
    """
    # TODO: manage exception when microseconds is too large
    # inspired from https://code.activestate.com/recipes/511425-filetime-to-datetime/
    _FILETIME_null_date = datetime.datetime(1601, 1, 1, 0, 0, 0)
    # log.debug('timedelta days=%d' % (filetime//(10*1000000*3600*24)))
    return _FILETIME_null_date + datetime.timedelta(microseconds=filetime//10)



#=== CLASSES ==================================================================

class OleFileError(IOError):
    """
    Generic base error for this module.
    """
    pass

class NotOleFileError(OleFileError):
    """
    Error raised when the opened file is not an OLE file.
    """
    pass


class StreamReplacementError(OleFileError, ValueError):
    """
    Error raised when a group of equal-length stream replacements cannot be
    applied. This covers invalid replacement records and stream-level
    preconditions, and is the base class of the more specific errors below.

    It inherits from ValueError as well, so callers that validate replacement
    arguments can keep catching ValueError.
    """
    pass


class StreamPreconditionError(StreamReplacementError):
    """
    Error raised when the current contents of a stream do not match the digest
    the caller supplied for the old contents (stale or missing stream, or an
    existing output that was changed by someone else).

    Attributes:

        - stream_name: path of the stream that failed the check
        - stream_path: tuple of path components
        - expected_digest: digest of the old contents supplied by the caller
        - actual_digest: digest computed from the stream, None if missing
        - digest_algorithm: algorithm used for this check
    """
    def __init__(self, stream_name, expected_digest, actual_digest, message=None,
                 digest_algorithm='sha256'):
        self.stream_name = stream_name
        self.stream_path = _normalize_stream_path(stream_name) \
            if not isinstance(stream_name, tuple) else stream_name
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest
        self.digest_algorithm = digest_algorithm
        if message is None:
            message = "current contents of stream %r do not match the old digest" \
                % ('/'.join(self.stream_path),)
        StreamReplacementError.__init__(self, message)


class StreamOverlapError(StreamReplacementError):
    """
    Error raised when two replacements in the same group resolve to storage
    regions that overlap, or when the same stream is listed twice.

    Attributes:

        - stream_names: the two conflicting stream paths, as tuples
    """
    def __init__(self, first, second, message=None):
        self.stream_names = (tuple(first), tuple(second))
        if message is None:
            message = "streams %r and %r cannot be replaced together: " \
                "their storage regions overlap" \
                % ('/'.join(self.stream_names[0]), '/'.join(self.stream_names[1]))
        StreamReplacementError.__init__(self, message)


class OutputStreamConflictError(StreamReplacementError):
    """
    Error raised when the output path already exists but does not hold either
    the original file or the expected result of this exact group. Refusing to
    overwrite it protects concurrent or unrelated output.

    Attributes:

        - output_path: the path that was refused
    """
    def __init__(self, output_path, message=None):
        self.output_path = output_path
        if message is None:
            message = "output file %r already exists and does not match this " \
                "replacement group" % (output_path,)
        StreamReplacementError.__init__(self, message)


#: status of a replacement that was published by this call
REPLACEMENT_APPLIED = 'applied'
#: status of a replacement that had already been published by an earlier
#: identical call (the caller is retrying after losing the receipt)
REPLACEMENT_ALREADY_APPLIED = 'already_applied'


class StreamReplacement(object):
    """
    One equal-length stream replacement in a :meth:`OleFileIO.replace_streams`
    group.

    :param stream_name: path of the stream, as a string using Unix path syntax
        ('storage/stream') or a list of path components
    :param data: bytes, new contents, exactly as long as the current stream
    :param old_digest: digest of the current stream contents that the caller
        expects; either a hexadecimal string, an object with a ``hexdigest()``
        method, or raw digest bytes. The replacement is only applied if the
        stream still has that digest. The algorithm is inferred from the
        digest length: SHA-256 (64 hex characters), SHA-384 (96) or SHA-512
        (128) are recommended; MD5 (32) and SHA-1 (40) are accepted for
        callers that already have legacy digests.
    :param digest_algorithm: optional name of the hashlib algorithm, used to
        confirm the algorithm inferred from ``old_digest``.
    """
    def __init__(self, stream_name=None, data=None, old_digest=None,
                 digest_algorithm=None, **kwargs):
        if stream_name is None:
            stream_name = kwargs.pop('stream', kwargs.pop('path',
                                    kwargs.pop('name', None)))
        if data is None:
            data = kwargs.pop('new_data', kwargs.pop('contents', None))
        if old_digest is None:
            old_digest = kwargs.pop('expected_digest',
                           kwargs.pop('digest',
                           kwargs.pop('old_sha256', None)))
        if digest_algorithm is None:
            digest_algorithm = kwargs.pop('algorithm', None)
        if kwargs:
            raise TypeError("StreamReplacement: unexpected arguments %r"
                            % (sorted(kwargs),))
        if stream_name is None or data is None or old_digest is None:
            raise TypeError("StreamReplacement requires stream_name, data and "
                            "old_digest")
        if not isinstance(data, bytes):
            raise TypeError("StreamReplacement: data must be a bytes string")
        self.stream_name = stream_name
        self.stream_path = _normalize_stream_path(stream_name)
        self.data = data
        self.old_digest = _digest_hex(old_digest)
        inferred = _digest_algorithm(self.old_digest)
        if digest_algorithm is not None:
            if not isinstance(digest_algorithm, basestring):
                raise TypeError("StreamReplacement: digest_algorithm must be a string")
            if digest_algorithm.lower() != inferred:
                raise ValueError("StreamReplacement: digest_algorithm does not "
                                 "match old_digest length")
            inferred = digest_algorithm.lower()
        self.digest_algorithm = inferred
        #: SHA-256 digest of data, used in the result receipt and in the
        #: logical fingerprint of the output
        self.new_digest = hashlib.sha256(data).hexdigest()

    @property
    def expected_digest(self):
        """Alias for :attr:`old_digest`."""
        return self.old_digest

    @property
    def new_contents(self):
        """Alias for :attr:`data`."""
        return self.data

    @property
    def size(self):
        """Length of the replacement data in bytes."""
        return len(self.data)

    def __repr__(self):
        return 'StreamReplacement(%r, size=%d, old_digest=%r)' % (
            '/'.join(self.stream_path), self.size, self.old_digest)


class StreamReplacementResult(object):
    """
    Receipt returned by :meth:`OleFileIO.replace_streams` once a group has been
    published (or recognized as already published).

    The version identifier (``group_digest``) is stable for one replacement
    group and does not depend on the output path or on the current state of
    the source file: a caller that lost its receipt can resubmit the same
    stream names, old digests and new contents and recognize that the
    published file is the same version.

    Attributes:

        - output_path: path of the newly published file
        - source_digest: SHA-256 of the original OLE file this call read
        - group_digest: SHA-256 identifying the replacement group (version)
        - output_digest: SHA-256 of the published file
        - status: REPLACEMENT_APPLIED or REPLACEMENT_ALREADY_APPLIED
        - applied: True if this call created the published file
        - already_applied: True if an identical group was already published
        - replaced: tuple of (stream_path, old_digest, new_digest, size) tuples
    """
    def __init__(self, output_path, source_digest, group_digest, output_digest,
                 status, replaced):
        self.output_path = output_path
        self.source_digest = source_digest
        self.group_digest = group_digest
        self.output_digest = output_digest
        self.status = status
        self.replaced = tuple(replaced)
        #: True if this call created the published file
        self.applied = status == REPLACEMENT_APPLIED
        #: True if an identical group had already been published
        self.already_applied = status == REPLACEMENT_ALREADY_APPLIED

    def __eq__(self, other):
        if isinstance(other, StreamReplacementResult):
            return self.as_dict() == other.as_dict()
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    @property
    def replayed(self):
        """Alias for :attr:`already_applied`."""
        return self.already_applied

    @property
    def digest(self):
        """Alias for :attr:`output_digest`."""
        return self.output_digest

    @property
    def version(self):
        """Alias for :attr:`group_digest`, the identity of this version."""
        return self.group_digest

    def as_dict(self):
        """Return the receipt as a plain dictionary."""
        return {
            'output_path': self.output_path,
            'source_digest': self.source_digest,
            'group_digest': self.group_digest,
            'version': self.group_digest,
            'output_digest': self.output_digest,
            'status': self.status,
            'applied': self.applied,
            'already_applied': self.already_applied,
            'replaced': self.replaced,
        }

    def __getitem__(self, key):
        return self.as_dict()[key]

    def keys(self):
        return self.as_dict().keys()

    def items(self):
        return self.as_dict().items()

    def __repr__(self):
        return 'StreamReplacementResult(%r, status=%r, group_digest=%r)' % (
            self.output_path, self.status, self.group_digest)


#: digest lengths supported for old-content preconditions, by hexadecimal size.
#: SHA-256 and stronger are recommended; MD5/SHA-1 are accepted for callers
#: that already store legacy digests.
_DIGEST_ALGORITHMS_BY_LENGTH = {32: 'md5', 40: 'sha1', 64: 'sha256',
                                96: 'sha384', 128: 'sha512'}


def _digest_hex(digest):
    """
    Normalize a caller-provided digest to a lowercase hexadecimal string.

    Accepts a hexadecimal string, raw digest bytes, or an object with a
    ``hexdigest()`` method (such as hashlib objects). The algorithm is
    inferred from the digest length: md5 (32), sha1 (40), sha256 (64),
    sha384 (96) or sha512 (128 hex characters).
    """
    if hasattr(digest, 'hexdigest'):
        digest = digest.hexdigest()
    if isinstance(digest, bytes) and not isinstance(digest, str):
        # raw digest bytes, as returned by hashlib.digest()
        digest = binascii.hexlify(digest).decode('ascii')
    if not isinstance(digest, basestring):
        raise TypeError("stream digest must be a hexadecimal string or digest object")
    digest = digest.strip().lower()
    if len(digest) not in _DIGEST_ALGORITHMS_BY_LENGTH:
        raise ValueError("stream digest must be a hexadecimal digest string")
    try:
        int(digest, 16)
    except ValueError:
        raise ValueError("stream digest must be a hexadecimal string")
    return digest


def _digest_algorithm(digest):
    """Return the algorithm name matching a normalized hexadecimal digest."""
    return _DIGEST_ALGORITHMS_BY_LENGTH[len(digest)]


def _normalize_stream_path(stream_name, path_encoding=None):
    """
    Convert a stream name to a tuple of text path components.

    Accepts a '/'-separated string or a list/tuple of components. In Python 3,
    bytes names are decoded with ``path_encoding`` (UTF-8 by default) to match
    the names returned by the directory parser.
    """
    if isinstance(stream_name, bytes) and str is not bytes:
        stream_name = stream_name.decode(path_encoding or 'utf-8', 'replace')
    if isinstance(stream_name, basestring):
        parts = stream_name.split('/')
    else:
        parts = list(stream_name)
        if str is not bytes:
            parts = [part.decode(path_encoding or 'utf-8', 'replace')
                     if isinstance(part, bytes) else part for part in parts]
    if not parts or any(not part for part in parts):
        raise ValueError("invalid stream name %r" % (stream_name,))
    return tuple(parts)


def _stream_path_key(stream_path):
    """Case-insensitive key for a stream path, as OLE names are case-insensitive."""
    return tuple(part.lower() for part in stream_path)


def stream_digest(filename=None, stream_name=None, data=None):
    """
    Compute a stream digest as used by :class:`StreamReplacement`
    preconditions and the receipts returned by
    :meth:`OleFileIO.replace_streams`.

    Two calling styles are supported::

        stream_digest('report.doc', 'WordDocument')   # stream contents
        stream_digest(data=contents)                  # bytes directly

    :param filename: path or file-like object of the OLE file to read from,
        or the contents themselves when it is a bytes string and neither
        ``stream_name`` nor ``data`` is given.
    :param stream_name: path of the stream in the OLE file (see
        :meth:`OleFileIO.openstream` for the syntax)
    :param data: bytes, contents to digest directly. If provided, no file is
        opened.
    :returns: lowercase hexadecimal SHA-256 digest
    """
    if data is not None:
        return hashlib.sha256(data).hexdigest()
    if stream_name is None:
        if isinstance(filename, bytes):
            return hashlib.sha256(filename).hexdigest()
        raise TypeError("stream_digest: stream_name is required")
    with OleFileIO(filename) as ole:
        stream = ole.openstream(stream_name)
        try:
            data = stream.read()
        finally:
            stream.close()
    return hashlib.sha256(data).hexdigest()


def _looks_like_digest(value):
    """True if value is a strong hexadecimal digest or a digest object."""
    if hasattr(value, 'hexdigest'):
        return True
    if isinstance(value, bytes) and not isinstance(value, str):
        return len(value) in (16, 20, 32, 48, 64)
    if isinstance(value, basestring):
        return len(value.strip()) in (32, 40, 64, 96, 128)
    return False


def _coerce_replacement(item):
    """Accept a StreamReplacement, a (name, data, old_digest) tuple, or a dict."""
    if isinstance(item, StreamReplacement):
        return item
    if isinstance(item, dict):
        name = item.get('stream_name', item.get('stream_path', item.get('name')))
        if name is None:
            name = item.get('stream', item.get('path'))
        data = item.get('data', item.get('new_data', item.get('contents')))
        old_digest = item.get('old_digest', item.get('old_sha256'))
        if old_digest is None:
            old_digest = item.get('expected_digest', item.get('digest'))
        if name is None or data is None or old_digest is None:
            raise TypeError("replacement mapping needs stream_name, data and old_digest")
        return StreamReplacement(name, data, old_digest)
    if isinstance(item, (tuple, list)) and len(item) == 3:
        name, second, third = item
        # Accept both (name, data, old_digest) and (name, old_digest, data).
        if not isinstance(second, bytes) and isinstance(third, bytes) \
                and _looks_like_digest(second):
            return StreamReplacement(name, third, second)
        return StreamReplacement(name, second, third)
    raise TypeError("replacements must be StreamReplacement objects, "
                    "(stream_name, data, old_digest) tuples or mappings")


def _coerce_replacements(replacements):
    """Normalize the group argument to a list of StreamReplacement objects."""
    if isinstance(replacements, StreamReplacement):
        return [replacements]
    if isinstance(replacements, dict):
        if any(key in replacements for key in ('data', 'new_data')):
            # a single replacement described as a mapping
            return [_coerce_replacement(replacements)]
        # mapping of stream name -> replacement record / (data, old_digest)
        result = []
        for name, value in replacements.items():
            if isinstance(value, StreamReplacement):
                result.append(StreamReplacement(name, value.data, value.old_digest))
            elif isinstance(value, dict):
                record = dict(value)
                record['stream_name'] = name
                result.append(_coerce_replacement(record))
            elif isinstance(value, (tuple, list)) and len(value) == 2:
                result.append(StreamReplacement(name, value[0], value[1]))
            else:
                raise TypeError("replacement for %r must be a mapping or "
                                "(data, old_digest) tuple" % (name,))
        return result
    result = []
    for item in replacements:
        result.append(_coerce_replacement(item))
    return result


def _stream_snapshot(ole):
    """
    Build a logical fingerprint of an OLE file: every stream path, size and
    SHA-256 digest, plus storages, root entry name and CLSID.

    Two files with the same fingerprint expose the same logical contents,
    which is what makes an already-published file recognizable on retry.

    :param ole: OleFileIO instance
    :returns: a hashable tuple
    """
    streams = []
    for path in ole.listdir(streams=True, storages=False):
        stream_path = _normalize_stream_path(path)
        sid = ole._find(path)
        entry = ole.direntries[sid]
        if entry.entry_type != STGTY_STREAM:
            continue
        stream = ole.openstream(path)
        try:
            data = stream.read()
        finally:
            stream.close()
        streams.append((stream_path, entry.size,
                        hashlib.sha256(data).hexdigest()))
    storages = [tuple(path) for path in ole.listdir(streams=False, storages=True)]
    root_clsid = getattr(ole.root, 'clsid', '')
    return (tuple(sorted(streams)), tuple(sorted(storages)),
            ole.get_rootentry_name(), root_clsid)


def _entry_byte_regions(ole, entry):
    """
    Return the physical byte ranges a stream occupies in the OLE container.

    Regions are computed from the actual FAT or MiniFAT sector chain, so two
    different stream names that resolve to the same or overlapping sectors
    are detected even if the directory looks consistent. The walk is bounded
    by the table size and detects cycles, so a malformed chain is rejected
    instead of looping forever.

    :param ole: OleFileIO instance
    :param entry: OleDirectoryEntry of the stream
    :returns: list of (start, end) byte offsets
    """
    if entry.entry_type != STGTY_STREAM or entry.size == 0:
        return []
    if entry.is_minifat:
        table = ole.minifat
        if table is None:
            ole.loadminifat()
            table = ole.minifat
        # The MiniFAT writer walks the whole chain, including any trailing
        # sectors beyond the declared size, so they are part of the write.
        chain = _walk_sector_chain(entry.isectStart, table, entry.size,
                                   ole.mini_sector_size, 'MiniFAT',
                                   stop_at_size=False)
        if not ole.root.sect_chain:
            ole.root.build_sect_chain(ole)
        block_size = ole.sector_size // ole.mini_sector_size
        regions = []
        for mini_sect in chain:
            base = mini_sect // block_size
            offset = mini_sect % block_size
            if base < 0 or base >= len(ole.root.sect_chain):
                raise StreamReplacementError(
                    "malformed MiniFAT: mini sector %d is outside the mini stream"
                    % (mini_sect,))
            start = (ole.root.sect_chain[base] + 1) * ole.sector_size \
                + offset * ole.mini_sector_size
            regions.append((start, start + ole.mini_sector_size))
    else:
        chain = _walk_sector_chain(entry.isectStart, ole.fat, entry.size,
                                   ole.sector_size, 'FAT')
        regions = []
        for sect in chain:
            if sect < 0 or sect >= ole.nb_sect:
                raise StreamReplacementError(
                    "malformed FAT: sector %d is outside the file" % (sect,))
            start = (sect + 1) * ole.sector_size
            regions.append((start, start + ole.sector_size))
    return regions


def _walk_sector_chain(start, table, size, sector_size, table_name,
                       stop_at_size=True):
    """
    Walk a FAT/MiniFAT chain and return the sector indexes it covers.

    :param start: first sector index
    :param table: FAT or MiniFAT sequence
    :param size: declared stream size in bytes
    :param sector_size: size of one sector of this table
    :param table_name: 'FAT' or 'MiniFAT', used in error messages
    :param stop_at_size: if True, stop after the number of sectors needed for
        ``size``; if False, walk to the end of the chain
    :returns: list of sector indexes
    """
    expected = (size + sector_size - 1) // sector_size
    chain = []
    seen = set()
    sect = start
    limit = len(table) + 1
    while sect != ENDOFCHAIN and (not stop_at_size or len(chain) < expected):
        if sect < 0 or sect >= len(table):
            raise StreamReplacementError(
                "malformed %s: sector index %r is out of range"
                % (table_name, sect))
        if sect in seen:
            raise StreamReplacementError(
                "malformed %s: cycle in sector chain at sector %d"
                % (table_name, sect))
        seen.add(sect)
        chain.append(sect)
        sect = table[sect] & 0xFFFFFFFF
        if len(chain) > limit:  # defensive; the size bound should hit first
            raise StreamReplacementError(
                "malformed %s: sector chain is longer than the table"
                % (table_name,))
    if stop_at_size and len(chain) != expected:
        raise StreamReplacementError(
            "malformed %s: stream chain has %d sectors, expected %d"
            % (table_name, len(chain), expected))
    return chain


def _check_no_overlap(regions_by_key, path_by_key):
    """
    Reject a group whose streams resolve to overlapping physical regions.

    :param regions_by_key: dict keyed by stream path key, values are lists of
        (start, end) byte ranges
    :param path_by_key: dict keyed by stream path key, values are display paths
    """
    spans = []
    for key, regions in regions_by_key.items():
        for start, end in regions:
            spans.append((start, end, key))
    spans.sort()
    max_end = None
    max_key = None
    max_start = None
    for start, end, key in spans:
        if max_end is not None and max_end > start:
            first = path_by_key[max_key]
            second = path_by_key[key]
            raise StreamOverlapError(first, second, message=(
                "streams %r and %r resolve to overlapping storage regions "
                "(bytes %d-%d and %d-%d)"
                % ('/'.join(first), '/'.join(second), max_start, max_end, start, end)))
        if max_end is None or end > max_end:
            max_end, max_key, max_start = end, key, start


def _file_digest(path):
    """Return the SHA-256 digest of a file on disk."""
    digest = hashlib.sha256()
    with open(path, 'rb') as fp:
        while True:
            chunk = fp.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _publish_new_file(temp_path, output_path):
    """
    Publish a fully written temporary file without overwriting any existing
    file at ``output_path``. The hard link makes the final name appear
    atomically and raises EEXIST if a concurrent writer won the race.
    """
    link = getattr(os, 'link', None)
    if link is None:
        # platform without hard links (old Python on Windows)
        if os.path.exists(output_path):
            raise OSError(errno.EEXIST, 'output already exists', output_path)
        os.rename(temp_path, output_path)
        return
    try:
        link(temp_path, output_path)
    except OSError as exc:
        unsupported = set(getattr(errno, name, None) for name in
                          ('EPERM', 'ENOTSUP', 'EOPNOTSUPP', 'ENOSYS'))
        unsupported.discard(None)
        if exc.errno not in unsupported:
            raise
        # Filesystem without hard links: fall back to a checked rename.
        if os.path.exists(output_path):
            raise
        os.rename(temp_path, output_path)
        return
    os.unlink(temp_path)


def _text_path(path):
    """Convert an os.PathLike or path-like object to a plain string path."""
    if hasattr(os, 'fspath'):
        return os.fspath(path)
    method = getattr(path, '__fspath__', None)
    if method is not None:
        return method()
    if isinstance(path, bytes):
        return path
    return str(path)


def _copy_file_permissions(temp_path, reference_path):
    """Give a published file the same access bits as its reference file."""
    if reference_path is None or not os.path.exists(reference_path):
        return
    try:
        mode = os.stat(reference_path).st_mode & 0o7777
        os.chmod(temp_path, mode)
    except OSError:
        # permissions are advisory; never fail a completed publish for this
        pass


def _resolve_output_alias(output_path, kwargs):
    """Accept output/destination/path keywords as aliases for output_path."""
    for alias in ('output', 'destination', 'dest', 'new_path', 'file_path'):
        if alias in kwargs:
            if output_path is not None:
                raise TypeError("output_path and %s cannot both be given" % (alias,))
            output_path = kwargs.pop(alias)
    if kwargs:
        raise TypeError("unexpected arguments: %s" % (sorted(kwargs),))
    return output_path


def replace_streams(filename, replacements=None, output_path=None, **kwargs):
    """
    Module-level wrapper for :meth:`OleFileIO.replace_streams`.

    :param filename: path or file-like object of the original OLE file. It is
        opened read-only and left unchanged.
    :param replacements: iterable of :class:`StreamReplacement` objects
    :param output_path: path of the new file to publish. If None, a path is
        derived from ``filename`` by appending '.replaced' before the extension.
    :returns: :class:`StreamReplacementResult`
    """
    if replacements is None:
        replacements = kwargs.pop('replacement_group',
                                  kwargs.pop('stream_replacements', None))
    output_path = _resolve_output_alias(output_path, kwargs)
    with OleFileIO(filename) as ole:
        return ole.replace_streams(replacements, output_path)


def write_streams(filename, replacements=None, output_path=None, **kwargs):
    """Alias for :func:`replace_streams`."""
    return replace_streams(filename, replacements, output_path, **kwargs)


class OleMetadata:
    """
    Class to parse and store metadata from standard properties of OLE files.

    Available attributes:
    codepage, title, subject, author, keywords, comments, template,
    last_saved_by, revision_number, total_edit_time, last_printed, create_time,
    last_saved_time, num_pages, num_words, num_chars, thumbnail,
    creating_application, security, codepage_doc, category, presentation_target,
    bytes, lines, paragraphs, slides, notes, hidden_slides, mm_clips,
    scale_crop, heading_pairs, titles_of_parts, manager, company, links_dirty,
    chars_with_spaces, unused, shared_doc, link_base, hlinks, hlinks_changed,
    version, dig_sig, content_type, content_status, language, doc_version

    Note: an attribute is set to None when not present in the properties of the
    OLE file.

    References for SummaryInformation stream:

    - https://msdn.microsoft.com/en-us/library/dd942545.aspx
    - https://msdn.microsoft.com/en-us/library/dd925819%28v=office.12%29.aspx
    - https://msdn.microsoft.com/en-us/library/windows/desktop/aa380376%28v=vs.85%29.aspx
    - https://msdn.microsoft.com/en-us/library/aa372045.aspx
    - http://sedna-soft.de/articles/summary-information-stream/
    - https://poi.apache.org/apidocs/org/apache/poi/hpsf/SummaryInformation.html

    References for DocumentSummaryInformation stream:

    - https://msdn.microsoft.com/en-us/library/dd945671%28v=office.12%29.aspx
    - https://msdn.microsoft.com/en-us/library/windows/desktop/aa380374%28v=vs.85%29.aspx
    - https://poi.apache.org/apidocs/org/apache/poi/hpsf/DocumentSummaryInformation.html

    New in version 0.25
    """

    # attribute names for SummaryInformation stream properties:
    # (ordered by property id, starting at 1)
    SUMMARY_ATTRIBS = ['codepage', 'title', 'subject', 'author', 'keywords', 'comments',
        'template', 'last_saved_by', 'revision_number', 'total_edit_time',
        'last_printed', 'create_time', 'last_saved_time', 'num_pages',
        'num_words', 'num_chars', 'thumbnail', 'creating_application',
        'security']

    # attribute names for DocumentSummaryInformation stream properties:
    # (ordered by property id, starting at 1)
    DOCSUM_ATTRIBS = ['codepage_doc', 'category', 'presentation_target', 'bytes', 'lines', 'paragraphs',
        'slides', 'notes', 'hidden_slides', 'mm_clips',
        'scale_crop', 'heading_pairs', 'titles_of_parts', 'manager',
        'company', 'links_dirty', 'chars_with_spaces', 'unused', 'shared_doc',
        'link_base', 'hlinks', 'hlinks_changed', 'version', 'dig_sig',
        'content_type', 'content_status', 'language', 'doc_version']

    def __init__(self):
        """
        Constructor for OleMetadata
        All attributes are set to None by default
        """
        # properties from SummaryInformation stream
        self.codepage = None
        self.title = None
        self.subject = None
        self.author = None
        self.keywords = None
        self.comments = None
        self.template = None
        self.last_saved_by = None
        self.revision_number = None
        self.total_edit_time = None
        self.last_printed = None
        self.create_time = None
        self.last_saved_time = None
        self.num_pages = None
        self.num_words = None
        self.num_chars = None
        self.thumbnail = None
        self.creating_application = None
        self.security = None
        # properties from DocumentSummaryInformation stream
        self.codepage_doc = None
        self.category = None
        self.presentation_target = None
        self.bytes = None
        self.lines = None
        self.paragraphs = None
        self.slides = None
        self.notes = None
        self.hidden_slides = None
        self.mm_clips = None
        self.scale_crop = None
        self.heading_pairs = None
        self.titles_of_parts = None
        self.manager = None
        self.company = None
        self.links_dirty = None
        self.chars_with_spaces = None
        self.unused = None
        self.shared_doc = None
        self.link_base = None
        self.hlinks = None
        self.hlinks_changed = None
        self.version = None
        self.dig_sig = None
        self.content_type = None
        self.content_status = None
        self.language = None
        self.doc_version = None

    def parse_properties(self, ole_file):
        """
        Parse standard properties of an OLE file, from the streams
        ``\\x05SummaryInformation`` and ``\\x05DocumentSummaryInformation``,
        if present.
        Properties are converted to strings, integers or python datetime objects.
        If a property is not present, its value is set to None.

        :param ole_file: OleFileIO object from which to parse properties
        """
        # first set all attributes to None:
        for attrib in (self.SUMMARY_ATTRIBS + self.DOCSUM_ATTRIBS):
            setattr(self, attrib, None)
        if ole_file.exists("\x05SummaryInformation"):
            # get properties from the stream:
            # (converting timestamps to python datetime, except total_edit_time,
            # which is property #10)
            props = ole_file.getproperties("\x05SummaryInformation",
                                           convert_time=True, no_conversion=[10])
            # store them into this object's attributes:
            for i in range(len(self.SUMMARY_ATTRIBS)):
                # ids for standards properties start at 0x01, until 0x13
                value = props.get(i+1, None)
                setattr(self, self.SUMMARY_ATTRIBS[i], value)
        if ole_file.exists("\x05DocumentSummaryInformation"):
            # get properties from the stream:
            props = ole_file.getproperties("\x05DocumentSummaryInformation",
                                           convert_time=True)
            # store them into this object's attributes:
            for i in range(len(self.DOCSUM_ATTRIBS)):
                # ids for standards properties start at 0x01, until 0x13
                value = props.get(i+1, None)
                setattr(self, self.DOCSUM_ATTRIBS[i], value)

    def dump(self):
        """
        Dump all metadata, for debugging purposes.
        """
        print('Properties from SummaryInformation stream:')
        for prop in self.SUMMARY_ATTRIBS:
            value = getattr(self, prop)
            print('- {}: {}'.format(prop, repr(value)))
        print('Properties from DocumentSummaryInformation stream:')
        for prop in self.DOCSUM_ATTRIBS:
            value = getattr(self, prop)
            print('- {}: {}'.format(prop, repr(value)))

class OleFileIONotClosed(RuntimeWarning):
    """
    Warning type used when OleFileIO is destructed but has open file handle.
    """
    def __init__(self, stack_of_open=None):
        super(OleFileIONotClosed, self).__init__()
        self.stack_of_open = stack_of_open

    def __str__(self):
        msg = 'Deleting OleFileIO instance with open file handle. ' \
              'You should ensure that OleFileIO is never deleted ' \
              'without calling close() first. Consider using '\
              '"with OleFileIO(...) as ole: ...".'
        if self.stack_of_open:
            return ''.join([msg, '\n', 'Stacktrace of open() call:\n'] +
                           self.stack_of_open.format())
        else:
            return msg


# --- OleStream ---------------------------------------------------------------

class OleStream(io.BytesIO):
    """
    OLE2 Stream

    Returns a read-only file object which can be used to read
    the contents of a OLE stream (instance of the BytesIO class).
    To open a stream, use the openstream method in the OleFileIO class.

    This function can be used with either ordinary streams,
    or ministreams, depending on the offset, sectorsize, and
    fat table arguments.

    Attributes:

        - size: actual size of data stream, after it was opened.
    """
    # FIXME: should store the list of sects obtained by following
    # the fat chain, and load new sectors on demand instead of
    # loading it all in one go.

    def __init__(self, fp, sect, size, offset, sectorsize, fat, filesize, olefileio):
        """
        Constructor for OleStream class.

        :param fp: file object, the OLE container or the MiniFAT stream
        :param sect: sector index of first sector in the stream
        :param size: total size of the stream
        :param offset: offset in bytes for the first FAT or MiniFAT sector
        :param sectorsize: size of one sector
        :param fat: array/list of sector indexes (FAT or MiniFAT)
        :param filesize: size of OLE file (for debugging)
        :param olefileio: OleFileIO object containing this stream
        :returns: a BytesIO instance containing the OLE stream
        """
        log.debug('OleStream.__init__:')
        log.debug('  sect=%d (%X), size=%d, offset=%d, sectorsize=%d, len(fat)=%d, fp=%s'
            %(sect,sect,size,offset,sectorsize,len(fat), repr(fp)))
        self.ole = olefileio
        # this check is necessary, otherwise when attempting to open a stream
        # from a closed OleFileIO, a stream of size zero is returned without
        # raising an exception. (see issue #81)
        if self.ole.fp.closed:
            raise OSError('Attempting to open a stream from a closed OLE File')
        # [PL] To detect malformed documents with FAT loops, we compute the
        # expected number of sectors in the stream:
        unknown_size = False
        if size == UNKNOWN_SIZE:
            # this is the case when called from OleFileIO._open(), and stream
            # size is not known in advance (for example when reading the
            # Directory stream). Then we can only guess maximum size:
            size = len(fat)*sectorsize
            # and we keep a record that size was unknown:
            unknown_size = True
            log.debug('  stream with UNKNOWN SIZE')
        nb_sectors = (size + (sectorsize-1)) // sectorsize
        log.debug('nb_sectors = %d' % nb_sectors)
        # This number should (at least) be less than the total number of
        # sectors in the given FAT:
        if nb_sectors > len(fat):
            self.ole._raise_defect(DEFECT_INCORRECT, 'malformed OLE document, stream too large')
        # optimization(?): data is first a list of strings, and join() is called
        # at the end to concatenate all in one string.
        # (this may not be really useful with recent Python versions)
        data = []
        # if size is zero, then first sector index should be ENDOFCHAIN:
        if size == 0 and sect != ENDOFCHAIN:
            log.debug('size == 0 and sect != ENDOFCHAIN:')
            self.ole._raise_defect(DEFECT_INCORRECT, 'incorrect OLE sector index for empty stream')
        # [PL] A fixed-length for loop is used instead of an undefined while
        # loop to avoid DoS attacks:
        for i in range(nb_sectors):
            log.debug('Reading stream sector[%d] = %Xh' % (i, sect))
            # Sector index may be ENDOFCHAIN, but only if size was unknown
            if sect == ENDOFCHAIN:
                if unknown_size:
                    log.debug('Reached ENDOFCHAIN sector for stream with unknown size')
                    break
                else:
                    # else this means that the stream is smaller than declared:
                    log.debug('sect=ENDOFCHAIN before expected size')
                    self.ole._raise_defect(DEFECT_INCORRECT, 'incomplete OLE stream')
            # sector index should be within FAT:
            if sect<0 or sect>=len(fat):
                log.debug('sect=%d (%X) / len(fat)=%d' % (sect, sect, len(fat)))
                log.debug('i=%d / nb_sectors=%d' %(i, nb_sectors))
##                tmp_data = b"".join(data)
##                f = open('test_debug.bin', 'wb')
##                f.write(tmp_data)
##                f.close()
##                log.debug('data read so far: %d bytes' % len(tmp_data))
                self.ole._raise_defect(DEFECT_INCORRECT, 'incorrect OLE FAT, sector index out of range')
                # stop reading here if the exception is ignored:
                break
            # TODO: merge this code with OleFileIO.getsect() ?
            # TODO: check if this works with 4K sectors:
            try:
                fp.seek(offset + sectorsize * sect)
            except Exception:
                log.debug('sect=%d, seek=%d, filesize=%d' %
                    (sect, offset+sectorsize*sect, filesize))
                self.ole._raise_defect(DEFECT_INCORRECT, 'OLE sector index out of range')
                # stop reading here if the exception is ignored:
                break
            sector_data = fp.read(sectorsize)
            # [PL] check if there was enough data:
            # Note: if sector is the last of the file, sometimes it is not a
            # complete sector (of 512 or 4K), so we may read less than
            # sectorsize.
            if len(sector_data)!=sectorsize and sect!=(len(fat)-1):
                log.debug('sect=%d / len(fat)=%d, seek=%d / filesize=%d, len read=%d' %
                    (sect, len(fat), offset+sectorsize*sect, filesize, len(sector_data)))
                log.debug('seek+len(read)=%d' % (offset+sectorsize*sect+len(sector_data)))
                self.ole._raise_defect(DEFECT_INCORRECT, 'incomplete OLE sector')
            data.append(sector_data)
            # jump to next sector in the FAT:
            try:
                sect = fat[sect] & 0xFFFFFFFF  # JYTHON-WORKAROUND
            except IndexError:
                # [PL] if pointer is out of the FAT an exception is raised
                self.ole._raise_defect(DEFECT_INCORRECT, 'incorrect OLE FAT, sector index out of range')
                # stop reading here if the exception is ignored:
                break
        # [PL] Last sector should be a "end of chain" marker:
        # if sect != ENDOFCHAIN:
        #     raise IOError('incorrect last sector index in OLE stream')
        data = b"".join(data)
        # Data is truncated to the actual stream size:
        if len(data) >= size:
            log.debug('Read data of length %d, truncated to stream size %d' % (len(data), size))
            data = data[:size]
            # actual stream size is stored for future use:
            self.size = size
        elif unknown_size:
            # actual stream size was not known, now we know the size of read
            # data:
            log.debug('Read data of length %d, the stream size was unknown' % len(data))
            self.size = len(data)
        else:
            # read data is less than expected:
            log.debug('Read data of length %d, less than expected stream size %d' % (len(data), size))
            # TODO: provide details in exception message
            self.size = len(data)
            self.ole._raise_defect(DEFECT_INCORRECT, 'OLE stream size is less than declared')
        # when all data is read in memory, BytesIO constructor is called
        io.BytesIO.__init__(self, data)
        # Then the OleStream object can be used as a read-only file object.


# --- OleDirectoryEntry -------------------------------------------------------

class OleDirectoryEntry:
    """
    OLE2 Directory Entry pointing to a stream or a storage
    """
    # struct to parse directory entries:
    # <: little-endian byte order, standard sizes
    #    (note: this should guarantee that Q returns a 64 bits int)
    # 64s: string containing entry name in unicode UTF-16 (max 31 chars) + null char = 64 bytes
    # H: uint16, number of bytes used in name buffer, including null = (len+1)*2
    # B: uint8, dir entry type (between 0 and 5)
    # B: uint8, color: 0=black, 1=red
    # I: uint32, index of left child node in the red-black tree, NOSTREAM if none
    # I: uint32, index of right child node in the red-black tree, NOSTREAM if none
    # I: uint32, index of child root node if it is a storage, else NOSTREAM
    # 16s: CLSID, unique identifier (only used if it is a storage)
    # I: uint32, user flags
    # Q (was 8s): uint64, creation timestamp or zero
    # Q (was 8s): uint64, modification timestamp or zero
    # I: uint32, SID of first sector if stream or ministream, SID of 1st sector
    #    of stream containing ministreams if root entry, 0 otherwise
    # I: uint32, total stream size in bytes if stream (low 32 bits), 0 otherwise
    # I: uint32, total stream size in bytes if stream (high 32 bits), 0 otherwise
    STRUCT_DIRENTRY = '<64sHBBIII16sIQQIII'
    # size of a directory entry: 128 bytes
    DIRENTRY_SIZE = 128
    assert struct.calcsize(STRUCT_DIRENTRY) == DIRENTRY_SIZE

    def __init__(self, entry, sid, ole_file):
        """
        Constructor for an OleDirectoryEntry object.
        Parses a 128-bytes entry from the OLE Directory stream.

        :param bytes entry: bytes string (must be 128 bytes long)
        :param int sid: index of this directory entry in the OLE file directory
        :param OleFileIO ole_file: OleFileIO object containing this directory entry
        """
        self.sid = sid
        # ref to ole_file is stored for future use
        self.olefile = ole_file
        # kids is a list of children entries, if this entry is a storage:
        # (list of OleDirectoryEntry objects)
        self.kids = []
        # kids_dict is a dictionary of children entries, indexed by their
        # name in lowercase: used to quickly find an entry, and to detect
        # duplicates
        self.kids_dict = {}
        # flag used to detect if the entry is referenced more than once in
        # directory:
        self.used = False
        # decode DirEntry
        (
            self.name_raw, # 64s: string containing entry name in unicode UTF-16 (max 31 chars) + null char = 64 bytes
            self.namelength, # H: uint16, number of bytes used in name buffer, including null = (len+1)*2
            self.entry_type,
            self.color,
            self.sid_left,
            self.sid_right,
            self.sid_child,
            clsid,
            self.dwUserFlags,
            self.createTime,
            self.modifyTime,
            self.isectStart,
            self.sizeLow,
            self.sizeHigh
        ) = struct.unpack(OleDirectoryEntry.STRUCT_DIRENTRY, entry)
        if self.entry_type not in [STGTY_ROOT, STGTY_STORAGE, STGTY_STREAM, STGTY_EMPTY]:
            ole_file._raise_defect(DEFECT_INCORRECT, 'unhandled OLE storage type')
        # only first directory entry can (and should) be root:
        if self.entry_type == STGTY_ROOT and sid != 0:
            ole_file._raise_defect(DEFECT_INCORRECT, 'duplicate OLE root entry')
        if sid == 0 and self.entry_type != STGTY_ROOT:
            ole_file._raise_defect(DEFECT_INCORRECT, 'incorrect OLE root entry')
        # log.debug(struct.unpack(fmt_entry, entry[:len_entry]))
        # name should be at most 31 unicode characters + null character,
        # so 64 bytes in total (31*2 + 2):
        if self.namelength > 64:
            ole_file._raise_defect(DEFECT_INCORRECT, 'incorrect DirEntry name length >64 bytes')
            # if exception not raised, namelength is set to the maximum value:
            self.namelength = 64
        # only characters without ending null char are kept:
        self.name_utf16 = self.name_raw[:(self.namelength-2)]
        # TODO: check if the name is actually followed by a null unicode character ([MS-CFB] 2.6.1)
        # TODO: check if the name does not contain forbidden characters:
        # [MS-CFB] 2.6.1: "The following characters are illegal and MUST NOT be part of the name: '/', '\', ':', '!'."
        # name is converted from UTF-16LE to the path encoding specified in the OleFileIO:
        self.name = ole_file._decode_utf16_str(self.name_utf16)

        log.debug('DirEntry SID=%d: %s' % (self.sid, repr(self.name)))
        log.debug(' - type: %d' % self.entry_type)
        log.debug(' - sect: %Xh' % self.isectStart)
        log.debug(' - SID left: %d, right: %d, child: %d' % (self.sid_left,
            self.sid_right, self.sid_child))

        # sizeHigh is only used for 4K sectors, it should be zero for 512 bytes
        # sectors, BUT apparently some implementations set it as 0xFFFFFFFF, 1
        # or some other value so it cannot be raised as a defect in general:
        if ole_file.sectorsize == 512:
            if self.sizeHigh != 0 and self.sizeHigh != 0xFFFFFFFF:
                log.debug('sectorsize=%d, sizeLow=%d, sizeHigh=%d (%X)' %
                          (ole_file.sectorsize, self.sizeLow, self.sizeHigh, self.sizeHigh))
                ole_file._raise_defect(DEFECT_UNSURE, 'incorrect OLE stream size')
            self.size = self.sizeLow
        else:
            self.size = self.sizeLow + (long(self.sizeHigh)<<32)
        log.debug(' - size: %d (sizeLow=%d, sizeHigh=%d)' % (self.size, self.sizeLow, self.sizeHigh))

        self.clsid = _clsid(clsid)
        # a storage should have a null size, BUT some implementations such as
        # Word 8 for Mac seem to allow non-null values => Potential defect:
        if self.entry_type == STGTY_STORAGE and self.size != 0:
            ole_file._raise_defect(DEFECT_POTENTIAL, 'OLE storage with size>0')
        # check if stream is not already referenced elsewhere:
        self.is_minifat = False
        if self.entry_type in (STGTY_ROOT, STGTY_STREAM) and self.size>0:
            if self.size < ole_file.minisectorcutoff \
            and self.entry_type==STGTY_STREAM: # only streams can be in MiniFAT
                # ministream object
                self.is_minifat = True
            else:
                self.is_minifat = False
            ole_file._check_duplicate_stream(self.isectStart, self.is_minifat)
        self.sect_chain = None

    def build_sect_chain(self, ole_file):
        """
        Build the sector chain for a stream (from the FAT or the MiniFAT)

        :param OleFileIO ole_file: OleFileIO object containing this directory entry
        :return: nothing
        """
        # TODO: seems to be used only from _write_mini_stream, is it useful?
        # TODO: use self.olefile instead of ole_file
        if self.sect_chain:
            return
        if self.entry_type not in (STGTY_ROOT, STGTY_STREAM) or self.size == 0:
            return

        self.sect_chain = list()

        if self.is_minifat and not ole_file.minifat:
            ole_file.loadminifat()

        next_sect = self.isectStart
        while next_sect != ENDOFCHAIN:
            self.sect_chain.append(next_sect)
            if self.is_minifat:
                next_sect = ole_file.minifat[next_sect]
            else:
                next_sect = ole_file.fat[next_sect]

    def build_storage_tree(self):
        """
        Read and build the red-black tree attached to this OleDirectoryEntry
        object, if it is a storage.
        Note that this method builds a tree of all subentries, so it should
        only be called for the root object once.
        """
        log.debug('build_storage_tree: SID=%d - %s - sid_child=%d'
            % (self.sid, repr(self.name), self.sid_child))
        if self.sid_child != NOSTREAM:
            # if child SID is not NOSTREAM, then this entry is a storage.
            # Let's walk through the tree of children to fill the kids list:
            self.append_kids(self.sid_child)

            # Note from OpenOffice documentation: the safest way is to
            # recreate the tree because some implementations may store broken
            # red-black trees...

            # in the OLE file, entries are sorted on (length, name).
            # for convenience, we sort them on name instead:
            # (see rich comparison methods in this class)
            self.kids.sort()

    def append_kids(self, child_sid):
        """
        Walk through red-black tree of children of this directory entry to add
        all of them to the kids list. (recursive method)

        :param child_sid: index of child directory entry to use, or None when called
            first time for the root. (only used during recursion)
        """
        log.debug('append_kids: child_sid=%d' % child_sid)
        # [PL] this method was added to use simple recursion instead of a complex
        # algorithm.
        # if this is not a storage or a leaf of the tree, nothing to do:
        if child_sid == NOSTREAM:
            return
        # check if child SID is in the proper range:
        if child_sid<0 or child_sid>=len(self.olefile.direntries):
            self.olefile._raise_defect(DEFECT_INCORRECT, 'OLE DirEntry index out of range')
        else:
            # get child direntry:
            child = self.olefile._load_direntry(child_sid) #direntries[child_sid]
            log.debug('append_kids: child_sid=%d - %s - sid_left=%d, sid_right=%d, sid_child=%d'
                % (child.sid, repr(child.name), child.sid_left, child.sid_right, child.sid_child))
            # Check if kid was not already referenced in a storage:
            if child.used:
                self.olefile._raise_defect(DEFECT_INCORRECT,
                    'OLE Entry referenced more than once')
                return
            child.used = True
            # the directory entries are organized as a red-black tree.
            # (cf. Wikipedia for details)
            # First walk through left side of the tree:
            self.append_kids(child.sid_left)
            # Check if its name is not already used (case-insensitive):
            name_lower = child.name.lower()
            if name_lower in self.kids_dict:
                self.olefile._raise_defect(DEFECT_INCORRECT,
                    "Duplicate filename in OLE storage")
            # Then the child_sid OleDirectoryEntry object is appended to the
            # kids list and dictionary:
            self.kids.append(child)
            self.kids_dict[name_lower] = child
            # Finally walk through right side of the tree:
            self.append_kids(child.sid_right)
            # Afterwards build kid's own tree if it's also a storage:
            child.build_storage_tree()

    def __eq__(self, other):
        "Compare entries by name"
        return self.name == other.name

    def __lt__(self, other):
        "Compare entries by name"
        return self.name < other.name

    def __ne__(self, other):
        return not self.__eq__(other)

    def __le__(self, other):
        return self.__eq__(other) or self.__lt__(other)

    # Reflected __lt__() and __le__() will be used for __gt__() and __ge__()

    # TODO: replace by the same function as MS implementation ?
    # (order by name length first, then case-insensitive order)

    def dump(self, tab = 0):
        "Dump this entry, and all its subentries (for debug purposes only)"
        TYPES = ["(invalid)", "(storage)", "(stream)", "(lockbytes)",
                 "(property)", "(root)"]
        try:
            type_name = TYPES[self.entry_type]
        except IndexError:
            type_name = '(UNKNOWN)'
        print(" "*tab + repr(self.name), type_name, end=' ')
        if self.entry_type in (STGTY_STREAM, STGTY_ROOT):
            print(self.size, "bytes", end=' ')
        print()
        if self.entry_type in (STGTY_STORAGE, STGTY_ROOT) and self.clsid:
            print(" "*tab + "{%s}" % self.clsid)

        for kid in self.kids:
            kid.dump(tab + 2)

    def getmtime(self):
        """
        Return modification time of a directory entry.

        :returns: None if modification time is null, a python datetime object
            otherwise (UTC timezone)

        new in version 0.26
        """
        if self.modifyTime == 0:
            return None
        return filetime2datetime(self.modifyTime)


    def getctime(self):
        """
        Return creation time of a directory entry.

        :returns: None if modification time is null, a python datetime object
            otherwise (UTC timezone)

        new in version 0.26
        """
        if self.createTime == 0:
            return None
        return filetime2datetime(self.createTime)


#--- OleFileIO ----------------------------------------------------------------

class OleFileIO:
    """
    OLE container object

    This class encapsulates the interface to an OLE 2 structured
    storage file.  Use the listdir and openstream methods to
    access the contents of this file.

    Object names are given as a list of strings, one for each subentry
    level.  The root entry should be omitted.  For example, the following
    code extracts all image streams from a Microsoft Image Composer file::

        with OleFileIO("fan.mic") as ole:

            for entry in ole.listdir():
                if entry[1:2] == "Image":
                    fin = ole.openstream(entry)
                    fout = open(entry[0:1], "wb")
                    while True:
                        s = fin.read(8192)
                        if not s:
                            break
                        fout.write(s)

    You can use the viewer application provided with the Python Imaging
    Library to view the resulting files (which happens to be standard
    TIFF files).
    """

    def __init__(self, filename=None, raise_defects=DEFECT_FATAL,
                 write_mode=False, debug=False, path_encoding=DEFAULT_PATH_ENCODING):
        """
        Constructor for the OleFileIO class.

        :param filename: file to open.

            - if filename is a string smaller than 1536 bytes, it is the path
              of the file to open. (bytes or unicode string)
            - if filename is a string longer than 1535 bytes, it is parsed
              as the content of an OLE file in memory. (bytes type only)
            - if filename is a file-like object (with read, seek and tell methods),
              it is parsed as-is. The caller is responsible for closing it when done.

        :param raise_defects: minimal level for defects to be raised as exceptions.
            (use DEFECT_FATAL for a typical application, DEFECT_INCORRECT for a
            security-oriented application, see source code for details)

        :param write_mode: bool, if True the file is opened in read/write mode instead
            of read-only by default.

        :param debug: bool, set debug mode (deprecated, not used anymore)

        :param path_encoding: None or str, name of the codec to use for path
            names (streams and storages), or None for Unicode.
            Unicode by default on Python 3+, UTF-8 on Python 2.x.
            (new in olefile 0.42, was hardcoded to Latin-1 until olefile v0.41)
        """
        # minimal level for defects to be raised as exceptions:
        self._raise_defects_level = raise_defects
        #: list of defects/issues not raised as exceptions:
        #: tuples of (exception type, message)
        self.parsing_issues = []
        self.write_mode = write_mode
        self.path_encoding = path_encoding
        # initialize all attributes to default values:
        self._filesize = None
        self.ministream = None
        self._used_streams_fat = []
        self._used_streams_minifat = []
        self.byte_order = None
        self.directory_fp = None
        self.direntries = None
        self.dll_version = None
        self.fat = None
        self.first_difat_sector = None
        self.first_dir_sector = None
        self.first_mini_fat_sector = None
        self.fp = None
        self.header_clsid = None
        self.header_signature = None
        self.metadata = None
        self.mini_sector_shift = None
        self.mini_sector_size = None
        self.mini_stream_cutoff_size = None
        self.minifat = None
        self.minifatsect = None
        # TODO: duplicates?
        self.minisectorcutoff = None
        self.minisectorsize = None
        self.ministream = None
        self.minor_version = None
        self.nb_sect = None
        self.num_difat_sectors = None
        self.num_dir_sectors = None
        self.num_fat_sectors = None
        self.num_mini_fat_sectors = None
        self.reserved1 = None
        self.reserved2 = None
        self.root = None
        self.sector_shift = None
        self.sector_size = None
        self.transaction_signature_number = None
        self.warn_if_not_closed = False
        self._we_opened_fp = False
        self._open_stack = None
        #: path this object was opened from, when it was a path on disk
        self._opened_path = filename \
            if isinstance(filename, basestring) and not (
                isinstance(filename, bytes) and len(filename) >= MINIMAL_OLEFILE_SIZE) \
            else None
        if filename:
            # try opening, ensure fp is closed if that fails
            try:
                self.open(filename, write_mode=write_mode)
            except Exception:
                # caller has no chance of calling close() now
                self._close(warn=False)
                raise

    def __del__(self):
        """Destructor, ensures all file handles are closed that we opened."""
        self._close(warn=True)
        # super(OleFileIO, self).__del__()  # there's no super-class destructor


    def __enter__(self):
        return self


    def __exit__(self, *args):
        self._close(warn=False)


    def _raise_defect(self, defect_level, message, exception_type=OleFileError):
        """
        This method should be called for any defect found during file parsing.
        It may raise an OleFileError exception according to the minimal level chosen
        for the OleFileIO object.

        :param defect_level: defect level, possible values are:

            - DEFECT_UNSURE    : a case which looks weird, but not sure it's a defect
            - DEFECT_POTENTIAL : a potential defect
            - DEFECT_INCORRECT : an error according to specifications, but parsing can go on
            - DEFECT_FATAL     : an error which cannot be ignored, parsing is impossible

        :param message: string describing the defect, used with raised exception.
        :param exception_type: exception class to be raised, OleFileError by default
        """
        # added by [PL]
        if defect_level >= self._raise_defects_level:
            log.error(message)
            raise exception_type(message)
        else:
            # just record the issue, no exception raised:
            self.parsing_issues.append((exception_type, message))
            log.warning(message)


    def _decode_utf16_str(self, utf16_str, errors='replace'):
        """
        Decode a string encoded in UTF-16 LE format, as found in the OLE
        directory or in property streams. Return a string encoded
        according to the path_encoding specified for the OleFileIO object.

        :param bytes utf16_str: bytes string encoded in UTF-16 LE format
        :param str errors: str, see python documentation for str.decode()
        :return: str, encoded according to path_encoding
        :rtype: str
        """
        unicode_str = utf16_str.decode('UTF-16LE', errors)
        if self.path_encoding:
            # an encoding has been specified for path names:
            return unicode_str.encode(self.path_encoding, errors)
        else:
            # path_encoding=None, return the Unicode string as-is:
            return unicode_str


    def open(self, filename, write_mode=False):
        """
        Open an OLE2 file in read-only or read/write mode.
        Read and parse the header, FAT and directory.

        :param filename: string-like or file-like object, OLE file to parse

            - if filename is a string smaller than 1536 bytes, it is the path
              of the file to open. (bytes or unicode string)
            - if filename is a string longer than 1535 bytes, it is parsed
              as the content of an OLE file in memory. (bytes type only)
            - if filename is a file-like object (with read, seek and tell methods),
              it is parsed as-is. The caller is responsible for closing it when done

        :param write_mode: bool, if True the file is opened in read/write mode instead
            of read-only by default. (ignored if filename is not a path)
        """
        self.write_mode = write_mode
        # [PL] check if filename is a string-like or file-like object:
        # (it is better to check for a read() method)
        if hasattr(filename, 'read'):
            # TODO: also check seek and tell methods?
            # file-like object: use it directly
            self.fp = filename
        elif isinstance(filename, bytes) and len(filename) >= MINIMAL_OLEFILE_SIZE:
            # filename is a bytes string containing the OLE file to be parsed:
            # convert it to BytesIO
            self.fp = io.BytesIO(filename)
        else:
            # string-like object: filename of file on disk
            if self.write_mode:
                # open file in mode 'read with update, binary'
                # According to https://docs.python.org/library/functions.html#open
                # 'w' would truncate the file, 'a' may only append on some Unixes
                mode = 'r+b'
            else:
                # read-only mode by default
                mode = 'rb'
            self.fp = open(filename, mode)
            self._we_opened_fp = True
            self._opened_path = filename
            self._open_stack = traceback.extract_stack()   # remember for warning
        # obtain the filesize by using seek and tell, which should work on most
        # file-like objects:
        # TODO: do it above, using getsize with filename when possible?
        # TODO: fix code to fail with clear exception when filesize cannot be obtained
        filesize = 0
        self.fp.seek(0, os.SEEK_END)
        try:
            filesize = self.fp.tell()
        finally:
            self.fp.seek(0)
        self._filesize = filesize
        log.debug('File size: %d bytes (%Xh)' % (self._filesize, self._filesize))

        # lists of streams in FAT and MiniFAT, to detect duplicate references
        # (list of indexes of first sectors of each stream)
        self._used_streams_fat = []
        self._used_streams_minifat = []

        header = self.fp.read(512)

        if len(header) != 512 or header[:8] != MAGIC:
            log.debug('Magic = {!r} instead of {!r}'.format(header[:8], MAGIC))
            self._raise_defect(DEFECT_FATAL, "not an OLE2 structured storage file", NotOleFileError)

        # [PL] header structure according to AAF specifications:
        ##Header
        ##struct StructuredStorageHeader { // [offset from start (bytes), length (bytes)]
        ##BYTE _abSig[8]; // [00H,08] {0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1,
        ##                // 0x1a, 0xe1} for current version
        ##CLSID _clsid;   // [08H,16] reserved must be zero (WriteClassStg/
        ##                // GetClassFile uses root directory class id)
        ##USHORT _uMinorVersion; // [18H,02] minor version of the format: 33 is
        ##                       // written by reference implementation
        ##USHORT _uDllVersion;   // [1AH,02] major version of the dll/format: 3 for
        ##                       // 512-byte sectors, 4 for 4 KB sectors
        ##USHORT _uByteOrder;    // [1CH,02] 0xFFFE: indicates Intel byte-ordering
        ##USHORT _uSectorShift;  // [1EH,02] size of sectors in power-of-two;
        ##                       // typically 9 indicating 512-byte sectors
        ##USHORT _uMiniSectorShift; // [20H,02] size of mini-sectors in power-of-two;
        ##                          // typically 6 indicating 64-byte mini-sectors
        ##USHORT _usReserved; // [22H,02] reserved, must be zero
        ##ULONG _ulReserved1; // [24H,04] reserved, must be zero
        ##FSINDEX _csectDir; // [28H,04] must be zero for 512-byte sectors,
        ##                   // number of SECTs in directory chain for 4 KB
        ##                   // sectors
        ##FSINDEX _csectFat; // [2CH,04] number of SECTs in the FAT chain
        ##SECT _sectDirStart; // [30H,04] first SECT in the directory chain
        ##DFSIGNATURE _signature; // [34H,04] signature used for transactions; must
        ##                        // be zero. The reference implementation
        ##                        // does not support transactions
        ##ULONG _ulMiniSectorCutoff; // [38H,04] maximum size for a mini stream;
        ##                           // typically 4096 bytes
        ##SECT _sectMiniFatStart; // [3CH,04] first SECT in the MiniFAT chain
        ##FSINDEX _csectMiniFat; // [40H,04] number of SECTs in the MiniFAT chain
        ##SECT _sectDifStart; // [44H,04] first SECT in the DIFAT chain
        ##FSINDEX _csectDif; // [48H,04] number of SECTs in the DIFAT chain
        ##SECT _sectFat[109]; // [4CH,436] the SECTs of first 109 FAT sectors
        ##};

        # [PL] header decoding:
        # '<' indicates little-endian byte ordering for Intel (cf. struct module help)
        fmt_header = '<8s16sHHHHHHLLLLLLLLLL'
        header_size = struct.calcsize(fmt_header)
        log.debug( "fmt_header size = %d, +FAT = %d" % (header_size, header_size + 109*4) )
        header1 = header[:header_size]
        (
            self.header_signature,
            self.header_clsid,
            self.minor_version,
            self.dll_version,
            self.byte_order,
            self.sector_shift,
            self.mini_sector_shift,
            self.reserved1,
            self.reserved2,
            self.num_dir_sectors,
            self.num_fat_sectors,
            self.first_dir_sector,
            self.transaction_signature_number,
            self.mini_stream_cutoff_size,
            self.first_mini_fat_sector,
            self.num_mini_fat_sectors,
            self.first_difat_sector,
            self.num_difat_sectors
        ) = struct.unpack(fmt_header, header1)
        log.debug( struct.unpack(fmt_header,    header1))

        if self.header_signature != MAGIC:
            # OLE signature should always be present
            self._raise_defect(DEFECT_FATAL, "incorrect OLE signature")
        if self.header_clsid != bytearray(16):
            # according to AAF specs, CLSID should always be zero
            self._raise_defect(DEFECT_INCORRECT, "incorrect CLSID in OLE header")
        log.debug( "Minor Version = %d" % self.minor_version )
        # TODO: according to MS-CFB, minor version should be 0x003E
        log.debug( "DLL Version   = %d (expected: 3 or 4)" % self.dll_version )
        if self.dll_version not in [3, 4]:
            # version 3: usual format, 512 bytes per sector
            # version 4: large format, 4K per sector
            self._raise_defect(DEFECT_INCORRECT, "incorrect DllVersion in OLE header")
        log.debug( "Byte Order    = %X (expected: FFFE)" % self.byte_order )
        if self.byte_order != 0xFFFE:
            # For now only common little-endian documents are handled correctly
            self._raise_defect(DEFECT_INCORRECT, "incorrect ByteOrder in OLE header")
            # TODO: add big-endian support for documents created on Mac ?
            # But according to [MS-CFB] ? v20140502, ByteOrder MUST be 0xFFFE.
        self.sector_size = 2**self.sector_shift
        log.debug( "Sector Size   = %d bytes (expected: 512 or 4096)" % self.sector_size )
        if self.sector_size not in [512, 4096]:
            self._raise_defect(DEFECT_INCORRECT, "incorrect sector_size in OLE header")
        if (self.dll_version==3 and self.sector_size!=512) \
        or (self.dll_version==4 and self.sector_size!=4096):
            self._raise_defect(DEFECT_INCORRECT, "sector_size does not match DllVersion in OLE header")
        self.mini_sector_size = 2**self.mini_sector_shift
        log.debug( "MiniFAT Sector Size   = %d bytes (expected: 64)" % self.mini_sector_size )
        if self.mini_sector_size not in [64]:
            self._raise_defect(DEFECT_INCORRECT, "incorrect mini_sector_size in OLE header")
        if self.reserved1 != 0 or self.reserved2 != 0:
            self._raise_defect(DEFECT_INCORRECT, "incorrect OLE header (non-null reserved bytes)")
        log.debug( "Number of Directory sectors = %d" % self.num_dir_sectors )
        # Number of directory sectors (only allowed if DllVersion != 3)
        if self.sector_size==512 and self.num_dir_sectors!=0:
            self._raise_defect(DEFECT_INCORRECT, "incorrect number of directory sectors in OLE header")
        log.debug( "Number of FAT sectors = %d" % self.num_fat_sectors )
        # num_fat_sectors = number of FAT sectors in the file
        log.debug( "First Directory sector  = %Xh" % self.first_dir_sector )
        # first_dir_sector = 1st sector containing the directory
        log.debug( "Transaction Signature Number    = %d" % self.transaction_signature_number )
        # Signature should be zero, BUT some implementations do not follow this
        # rule => only a potential defect:
        # (according to MS-CFB, may be != 0 for applications supporting file
        # transactions)
        if self.transaction_signature_number != 0:
            self._raise_defect(DEFECT_POTENTIAL, "incorrect OLE header (transaction_signature_number>0)")
        log.debug( "Mini Stream cutoff size = %Xh (expected: 1000h)" % self.mini_stream_cutoff_size )
        # MS-CFB: This integer field MUST be set to 0x00001000. This field
        # specifies the maximum size of a user-defined data stream allocated
        # from the mini FAT and mini stream, and that cutoff is 4096 bytes.
        # Any user-defined data stream larger than or equal to this cutoff size
        # must be allocated as normal sectors from the FAT.
        if self.mini_stream_cutoff_size != 0x1000:
            self._raise_defect(DEFECT_INCORRECT, "incorrect mini_stream_cutoff_size in OLE header")
            # if no exception is raised, the cutoff size is fixed to 0x1000
            log.warning('Fixing the mini_stream_cutoff_size to 4096 (mandatory value) instead of %d' %
                        self.mini_stream_cutoff_size)
            self.mini_stream_cutoff_size = 0x1000
        # TODO: check if these values are OK
        log.debug( "First MiniFAT sector      = %Xh" % self.first_mini_fat_sector )
        log.debug( "Number of MiniFAT sectors = %d" % self.num_mini_fat_sectors )
        log.debug( "First DIFAT sector        = %Xh" % self.first_difat_sector )
        log.debug( "Number of DIFAT sectors   = %d" % self.num_difat_sectors )

        # calculate the number of sectors in the file
        # (-1 because header doesn't count)
        self.nb_sect = ( (filesize + self.sector_size-1) // self.sector_size) - 1
        log.debug( "Maximum number of sectors in the file: %d (%Xh)" % (self.nb_sect, self.nb_sect))
        # TODO: change this test, because an OLE file MAY contain other data
        # after the last sector.

        # file clsid
        self.header_clsid = _clsid(header[8:24])

        # TODO: remove redundant attributes, and fix the code which uses them?
        self.sectorsize = self.sector_size #1 << i16(header, 30)
        self.minisectorsize = self.mini_sector_size  #1 << i16(header, 32)
        self.minisectorcutoff = self.mini_stream_cutoff_size # i32(header, 56)

        # check known streams for duplicate references (these are always in FAT,
        # never in MiniFAT):
        self._check_duplicate_stream(self.first_dir_sector)
        # check MiniFAT only if it is not empty:
        if self.num_mini_fat_sectors:
            self._check_duplicate_stream(self.first_mini_fat_sector)
        # check DIFAT only if it is not empty:
        if self.num_difat_sectors:
            self._check_duplicate_stream(self.first_difat_sector)

        # Load file allocation tables
        self.loadfat(header)
        # Load directory.  This sets both the direntries list (ordered by sid)
        # and the root (ordered by hierarchy) members.
        self.loaddirectory(self.first_dir_sector)
        self.minifatsect = self.first_mini_fat_sector

    def close(self):
        """
        close the OLE file, release the file object if we created it ourselves.

        Leaves the file handle open if it was provided by the caller.
        """
        self._close(warn=False)

    def _close(self, warn=False):
        """Implementation of close() with internal arg `warn`."""
        if self._we_opened_fp:
            if warn and self.warn_if_not_closed:
                # we only raise a warning if the file was not explicitly closed,
                # and if the option warn_if_not_closed is enabled
                warnings.warn(OleFileIONotClosed(self._open_stack))
            self.fp.close()
            self._we_opened_fp = False

    def _check_duplicate_stream(self, first_sect, minifat=False):
        """
        Checks if a stream has not been already referenced elsewhere.
        This method should only be called once for each known stream, and only
        if stream size is not null.

        :param first_sect: int, index of first sector of the stream in FAT
        :param minifat: bool, if True, stream is located in the MiniFAT, else in the FAT
        """
        if minifat:
            log.debug('_check_duplicate_stream: sect=%Xh in MiniFAT' % first_sect)
            used_streams = self._used_streams_minifat
        else:
            log.debug('_check_duplicate_stream: sect=%Xh in FAT' % first_sect)
            # some values can be safely ignored (not a real stream):
            if first_sect in (DIFSECT,FATSECT,ENDOFCHAIN,FREESECT):
                return
            used_streams = self._used_streams_fat
        # TODO: would it be more efficient using a dict or hash values, instead
        #      of a list of long ?
        if first_sect in used_streams:
            self._raise_defect(DEFECT_INCORRECT, 'Stream referenced twice')
        else:
            used_streams.append(first_sect)

    def dumpfat(self, fat, firstindex=0):
        """
        Display a part of FAT in human-readable form for debugging purposes
        """
        # dictionary to convert special FAT values in human-readable strings
        VPL = 8 # values per line (8+1 * 8+1 = 81)
        fatnames = {
            FREESECT:   "..free..",
            ENDOFCHAIN: "[ END. ]",
            FATSECT:    "FATSECT ",
            DIFSECT:    "DIFSECT "
            }
        nbsect = len(fat)
        nlines = (nbsect+VPL-1)//VPL
        print("index", end=" ")
        for i in range(VPL):
            print("%8X" % i, end=" ")
        print()
        for l in range(nlines):
            index = l*VPL
            print("%6X:" % (firstindex+index), end=" ")
            for i in range(index, index+VPL):
                if i>=nbsect:
                    break
                sect = fat[i]
                aux = sect & 0xFFFFFFFF  # JYTHON-WORKAROUND
                if aux in fatnames:
                    name = fatnames[aux]
                else:
                    if sect == i+1:
                        name = "    --->"
                    else:
                        name = "%8X" % sect
                print(name, end=" ")
            print()

    def dumpsect(self, sector, firstindex=0):
        """
        Display a sector in a human-readable form, for debugging purposes
        """
        VPL=8 # number of values per line (8+1 * 8+1 = 81)
        tab = array.array(UINT32, sector)
        if sys.byteorder == 'big':
            tab.byteswap()
        nbsect = len(tab)
        nlines = (nbsect+VPL-1)//VPL
        print("index", end=" ")
        for i in range(VPL):
            print("%8X" % i, end=" ")
        print()
        for l in range(nlines):
            index = l*VPL
            print("%6X:" % (firstindex+index), end=" ")
            for i in range(index, index+VPL):
                if i>=nbsect:
                    break
                sect = tab[i]
                name = "%8X" % sect
                print(name, end=" ")
            print()

    def sect2array(self, sect):
        """
        convert a sector to an array of 32 bits unsigned integers,
        swapping bytes on big endian CPUs such as PowerPC (old Macs)
        """
        # TODO: make this a static function
        a = array.array(UINT32, sect)
        # if CPU is big endian, swap bytes:
        if sys.byteorder == 'big':
            a.byteswap()
        return a

    def loadfat_sect(self, sect):
        """
        Adds the indexes of the given sector to the FAT

        :param sect: string containing the first FAT sector, or array of long integers
        :returns: index of last FAT sector.
        """
        # a FAT sector is an array of ulong integers.
        if isinstance(sect, array.array):
            # if sect is already an array it is directly used
            fat1 = sect
        else:
            # if it's a raw sector, it is parsed in an array
            fat1 = self.sect2array(sect)
            # Display the sector contents only if the logging level is debug:
            if log.isEnabledFor(logging.DEBUG):
                self.dumpsect(sect)
        # The FAT is a sector chain starting at the first index of itself.
        # initialize isect, just in case:
        isect = None
        for isect in fat1:
            isect = isect & 0xFFFFFFFF  # JYTHON-WORKAROUND
            log.debug("isect = %X" % isect)
            if isect == ENDOFCHAIN or isect == FREESECT:
                # the end of the sector chain has been reached
                log.debug("found end of sector chain")
                break
            # read the FAT sector
            s = self.getsect(isect)
            # parse it as an array of 32 bits integers, and add it to the
            # global FAT array
            nextfat = self.sect2array(s)
            self.fat = self.fat + nextfat
        return isect

    def loadfat(self, header):
        """
        Load the FAT table.
        """
        # The 1st sector of the file contains sector numbers for the first 109
        # FAT sectors, right after the header which is 76 bytes long.
        # (always 109, whatever the sector size: 512 bytes = 76+4*109)
        # Additional sectors are described by DIF blocks

        log.debug('Loading the FAT table, starting with the 1st sector after the header')
        sect = header[76:512]
        log.debug( "len(sect)=%d, so %d integers" % (len(sect), len(sect)//4) )
        # fat    = []
        # FAT is an array of 32 bits unsigned ints, it's more effective
        # to use an array than a list in Python.
        # It's initialized as empty first:
        self.fat = array.array(UINT32)
        self.loadfat_sect(sect)
        # self.dumpfat(self.fat)
        # for i in range(0, len(sect), 4):
        #     ix = i32(sect, i)
        #     # [PL] if ix == -2 or ix == -1: # ix == 0xFFFFFFFE or ix == 0xFFFFFFFF:
        #     if ix == 0xFFFFFFFE or ix == 0xFFFFFFFF:
        #         break
        #     s = self.getsect(ix)
        #     # fat    = fat + [i32(s, i) for i in range(0, len(s), 4)]
        #     fat = fat + array.array(UINT32, s)
        if self.num_difat_sectors != 0:
            log.debug('DIFAT is used, because file size > 6.8MB.')
            # [PL] There's a DIFAT because file is larger than 6.8MB
            # some checks just in case:
            if self.num_fat_sectors <= 109:
                # there must be at least 109 blocks in header and the rest in
                # DIFAT, so number of sectors must be >109.
                self._raise_defect(DEFECT_INCORRECT, 'incorrect DIFAT, not enough sectors')
            if self.first_difat_sector >= self.nb_sect:
                # initial DIFAT block index must be valid
                self._raise_defect(DEFECT_FATAL, 'incorrect DIFAT, first index out of range')
            log.debug( "DIFAT analysis..." )
            # We compute the necessary number of DIFAT sectors :
            # Number of pointers per DIFAT sector = (sectorsize/4)-1
            # (-1 because the last pointer is the next DIFAT sector number)
            nb_difat_sectors = (self.sectorsize//4)-1
            # (if 512 bytes: each DIFAT sector = 127 pointers + 1 towards next DIFAT sector)
            nb_difat = (self.num_fat_sectors-109 + nb_difat_sectors-1)//nb_difat_sectors
            log.debug( "nb_difat = %d" % nb_difat )
            if self.num_difat_sectors != nb_difat:
                raise IOError('incorrect DIFAT')
            isect_difat = self.first_difat_sector
            for i in iterrange(nb_difat):
                log.debug( "DIFAT block %d, sector %X" % (i, isect_difat) )
                # TODO: check if corresponding FAT SID = DIFSECT
                sector_difat = self.getsect(isect_difat)
                difat = self.sect2array(sector_difat)
                # Display the sector contents only if the logging level is debug:
                if log.isEnabledFor(logging.DEBUG):
                    self.dumpsect(sector_difat)
                self.loadfat_sect(difat[:nb_difat_sectors])
                # last DIFAT pointer is next DIFAT sector:
                isect_difat = difat[nb_difat_sectors]
                log.debug( "next DIFAT sector: %X" % isect_difat )
            # checks:
            if isect_difat not in [ENDOFCHAIN, FREESECT]:
                # last DIFAT pointer value must be ENDOFCHAIN or FREESECT
                raise IOError('incorrect end of DIFAT')
            # if len(self.fat) != self.num_fat_sectors:
            #     # FAT should contain num_fat_sectors blocks
            #     print("FAT length: %d instead of %d" % (len(self.fat), self.num_fat_sectors))
            #     raise IOError('incorrect DIFAT')
        else:
            log.debug('No DIFAT, because file size < 6.8MB.')
        # since FAT is read from fixed-size sectors, it may contain more values
        # than the actual number of sectors in the file.
        # Keep only the relevant sector indexes:
        if len(self.fat) > self.nb_sect:
            log.debug('len(fat)=%d, shrunk to nb_sect=%d' % (len(self.fat), self.nb_sect))
            self.fat = self.fat[:self.nb_sect]
        log.debug('FAT references %d sectors / Maximum %d sectors in file' % (len(self.fat), self.nb_sect))
        # Display the FAT contents only if the logging level is debug:
        if log.isEnabledFor(logging.DEBUG):
            log.debug('\nFAT:')
            self.dumpfat(self.fat)

    def loadminifat(self):
        """
        Load the MiniFAT table.
        """
        # MiniFAT is stored in a standard  sub-stream, pointed to by a header
        # field.
        # NOTE: there are two sizes to take into account for this stream:
        # 1) Stream size is calculated according to the number of sectors
        #    declared in the OLE header. This allocated stream may be more than
        #    needed to store the actual sector indexes.
        # (self.num_mini_fat_sectors is the number of sectors of size self.sector_size)
        stream_size = self.num_mini_fat_sectors * self.sector_size
        # 2) Actually used size is calculated by dividing the MiniStream size
        #    (given by root entry size) by the size of mini sectors, *4 for
        #    32 bits indexes:
        nb_minisectors = (self.root.size + self.mini_sector_size-1) // self.mini_sector_size
        used_size = nb_minisectors * 4
        log.debug('loadminifat(): minifatsect=%d, nb FAT sectors=%d, used_size=%d, stream_size=%d, nb MiniSectors=%d' %
            (self.minifatsect, self.num_mini_fat_sectors, used_size, stream_size, nb_minisectors))
        if used_size > stream_size:
            # This is not really a problem, but may indicate a wrong implementation:
            self._raise_defect(DEFECT_INCORRECT, 'OLE MiniStream is larger than MiniFAT')
        # In any case, first read stream_size:
        s = self._open(self.minifatsect, stream_size, force_FAT=True).read()
        # [PL] Old code replaced by an array:
        #self.minifat = [i32(s, i) for i in range(0, len(s), 4)]
        self.minifat = self.sect2array(s)
        # Then shrink the array to used size, to avoid indexes out of MiniStream:
        log.debug('MiniFAT shrunk from %d to %d sectors' % (len(self.minifat), nb_minisectors))
        self.minifat = self.minifat[:nb_minisectors]
        log.debug('loadminifat(): len=%d' % len(self.minifat))
        # Display the FAT contents only if the logging level is debug:
        if log.isEnabledFor(logging.DEBUG):
            log.debug('\nMiniFAT:')
            self.dumpfat(self.minifat)

    def getsect(self, sect):
        """
        Read given sector from file on disk.

        :param sect: int, sector index
        :returns: a string containing the sector data.
        """
        # From [MS-CFB]: A sector number can be converted into a byte offset
        # into the file by using the following formula:
        # (sector number + 1) x Sector Size.
        # This implies that sector #0 of the file begins at byte offset Sector
        # Size, not at 0.

        # [PL] the original code in PIL was wrong when sectors are 4KB instead of
        # 512 bytes:
        #self.fp.seek(512 + self.sectorsize * sect)
        # [PL]: added safety checks:
        #print("getsect(%X)" % sect)
        try:
            self.fp.seek(self.sectorsize * (sect+1))
        except Exception:
            log.debug('getsect(): sect=%X, seek=%d, filesize=%d' %
                (sect, self.sectorsize*(sect+1), self._filesize))
            self._raise_defect(DEFECT_FATAL, 'OLE sector index out of range')
        sector = self.fp.read(self.sectorsize)
        if len(sector) != self.sectorsize:
            log.debug('getsect(): sect=%X, read=%d, sectorsize=%d' %
                (sect, len(sector), self.sectorsize))
            self._raise_defect(DEFECT_FATAL, 'incomplete OLE sector')
        return sector

    def write_sect(self, sect, data, padding=b'\x00'):
        """
        Write given sector to file on disk.

        :param sect: int, sector index
        :param data: bytes, sector data
        :param padding: single byte, padding character if data < sector size
        """
        if not isinstance(data, bytes):
            raise TypeError("write_sect: data must be a bytes string")
        if not isinstance(padding, bytes) or len(padding)!=1:
            raise TypeError("write_sect: padding must be a bytes string of 1 char")
        # TODO: we could allow padding=None for no padding at all
        try:
            self.fp.seek(self.sectorsize * (sect+1))
        except Exception:
            log.debug('write_sect(): sect=%X, seek=%d, filesize=%d' %
                (sect, self.sectorsize*(sect+1), self._filesize))
            self._raise_defect(DEFECT_FATAL, 'OLE sector index out of range')
        if len(data) < self.sectorsize:
            # add padding
            data += padding * (self.sectorsize - len(data))
        elif len(data) > self.sectorsize:
            raise ValueError("Data is larger than sector size")
        self.fp.write(data)

    def _write_mini_sect(self, fp_pos, data, padding = b'\x00'):
        """
        Write given sector to file on disk.

        :param fp_pos: int, file position
        :param data: bytes, sector data
        :param padding: single byte, padding character if data < sector size
        """
        if not isinstance(data, bytes):
            raise TypeError("write_mini_sect: data must be a bytes string")
        if not isinstance(padding, bytes) or len(padding) != 1:
            raise TypeError("write_mini_sect: padding must be a bytes string of 1 char")

        try:
            self.fp.seek(fp_pos)
        except Exception:
            log.debug('write_mini_sect(): fp_pos=%d, filesize=%d' %
                      (fp_pos, self._filesize))
            self._raise_defect(DEFECT_FATAL, 'OLE sector index out of range')
        len_data = len(data)
        if len_data < self.mini_sector_size:
            data += padding * (self.mini_sector_size - len_data)
        if self.mini_sector_size < len_data:
            raise ValueError("Data is larger than sector size")
        self.fp.write(data)

    def loaddirectory(self, sect):
        """
        Load the directory.

        :param sect: sector index of directory stream.
        """
        log.debug('Loading the Directory:')
        # The directory is  stored in a standard
        # substream, independent of its size.

        # open directory stream as a read-only file:
        # (stream size is not known in advance)
        self.directory_fp = self._open(sect, force_FAT=True)

        # [PL] to detect malformed documents and avoid DoS attacks, the maximum
        # number of directory entries can be calculated:
        max_entries = self.directory_fp.size // 128
        log.debug('loaddirectory: size=%d, max_entries=%d' %
            (self.directory_fp.size, max_entries))

        # Create list of directory entries
        # self.direntries = []
        # We start with a list of "None" object
        self.direntries = [None] * max_entries
        # for sid in iterrange(max_entries):
        #     entry = fp.read(128)
        #     if not entry:
        #         break
        #     self.direntries.append(OleDirectoryEntry(entry, sid, self))
        # load root entry:
        root_entry = self._load_direntry(0)
        # Root entry is the first entry:
        self.root = self.direntries[0]
        # TODO: read ALL directory entries (ignore bad entries?)
        # TODO: adapt build_storage_tree to avoid duplicate reads
        # for i in range(1, max_entries):
        #     self._load_direntry(i)
        # read and build all storage trees, starting from the root:
        self.root.build_storage_tree()

    def _load_direntry (self, sid):
        """
        Load a directory entry from the directory.
        This method should only be called once for each storage/stream when
        loading the directory.

        :param sid: index of storage/stream in the directory.
        :returns: a OleDirectoryEntry object

        :exception OleFileError: if the entry has always been referenced.
        """
        # check if SID is OK:
        if sid<0 or sid>=len(self.direntries):
            self._raise_defect(DEFECT_FATAL, "OLE directory index out of range")
        # check if entry was already referenced:
        if self.direntries[sid] is not None:
            self._raise_defect(DEFECT_INCORRECT,
                "double reference for OLE stream/storage")
            # if exception not raised, return the object
            return self.direntries[sid]
        self.directory_fp.seek(sid * 128)
        entry = self.directory_fp.read(128)
        self.direntries[sid] = OleDirectoryEntry(entry, sid, self)
        return self.direntries[sid]

    def dumpdirectory(self):
        """
        Dump directory (for debugging only)
        """
        self.root.dump()

    def _open(self, start, size = UNKNOWN_SIZE, force_FAT=False):
        """
        Open a stream, either in FAT or MiniFAT according to its size.
        (openstream helper)

        :param start: index of first sector
        :param size: size of stream (or nothing if size is unknown)
        :param force_FAT: if False (default), stream will be opened in FAT or MiniFAT
            according to size. If True, it will always be opened in FAT.
        """
        log.debug('OleFileIO.open(): sect=%Xh, size=%d, force_FAT=%s' %
            (start, size, str(force_FAT)))
        # stream size is compared to the mini_stream_cutoff_size threshold:
        if size < self.minisectorcutoff and not force_FAT:
            # ministream object
            if not self.ministream:
                # load MiniFAT if it wasn't already done:
                self.loadminifat()
                # The first sector index of the miniFAT stream is stored in the
                # root directory entry:
                size_ministream = self.root.size
                log.debug('Opening MiniStream: sect=%Xh, size=%d' %
                    (self.root.isectStart, size_ministream))
                self.ministream = self._open(self.root.isectStart,
                    size_ministream, force_FAT=True)
            return OleStream(fp=self.ministream, sect=start, size=size,
                             offset=0, sectorsize=self.minisectorsize,
                             fat=self.minifat, filesize=self.ministream.size,
                             olefileio=self)
        else:
            # standard stream
            return OleStream(fp=self.fp, sect=start, size=size,
                             offset=self.sectorsize,
                             sectorsize=self.sectorsize, fat=self.fat,
                             filesize=self._filesize,
                             olefileio=self)

    def _list(self, files, prefix, node, streams=True, storages=False):
        """
        listdir helper

        :param files: list of files to fill in
        :param prefix: current location in storage tree (list of names)
        :param node: current node (OleDirectoryEntry object)
        :param streams: bool, include streams if True (True by default) - new in v0.26
        :param storages: bool, include storages if True (False by default) - new in v0.26
            (note: the root storage is never included)
        """
        prefix = prefix + [node.name]
        for entry in node.kids:
            if entry.entry_type == STGTY_STORAGE:
                # this is a storage
                if storages:
                    # add it to the list
                    files.append(prefix[1:] + [entry.name])
                # check its kids
                self._list(files, prefix, entry, streams, storages)
            elif entry.entry_type == STGTY_STREAM:
                # this is a stream
                if streams:
                    # add it to the list
                    files.append(prefix[1:] + [entry.name])
            else:
                self._raise_defect(DEFECT_INCORRECT, 'The directory tree contains an entry which is not a stream nor a storage.')

    def listdir(self, streams=True, storages=False):
        """
        Return a list of streams and/or storages stored in this file

        :param streams: bool, include streams if True (True by default) - new in v0.26
        :param storages: bool, include storages if True (False by default) - new in v0.26
            (note: the root storage is never included)
        :returns: list of stream and/or storage paths
        """
        files = []
        self._list(files, [], self.root, streams, storages)
        return files

    def _find(self, filename):
        """
        Returns directory entry of given filename. (openstream helper)
        Note: this method is case-insensitive.

        :param filename: path of stream in storage tree (except root entry), either:

            - a string using Unix path syntax, for example:
              'storage_1/storage_1.2/stream'
            - or a list of storage filenames, path to the desired stream/storage.
              Example: ['storage_1', 'storage_1.2', 'stream']

        :returns: sid of requested filename
        :exception IOError: if file not found
        """

        # if filename is a string instead of a list, split it on slashes to
        # convert to a list:
        if isinstance(filename, basestring):
            filename = filename.split('/')
        # walk across storage tree, following given path:
        node = self.root
        for name in filename:
            for kid in node.kids:
                if kid.name.lower() == name.lower():
                    break
            else:
                raise IOError("file not found")
            node = kid
        return node.sid

    def openstream(self, filename):
        """
        Open a stream as a read-only file object (BytesIO).
        Note: filename is case-insensitive.

        :param filename: path of stream in storage tree (except root entry), either:

            - a string using Unix path syntax, for example:
              'storage_1/storage_1.2/stream'
            - or a list of storage filenames, path to the desired stream/storage.
              Example: ['storage_1', 'storage_1.2', 'stream']

        :returns: file object (read-only)
        :exception IOError: if filename not found, or if this is not a stream.
        """
        sid = self._find(filename)
        entry = self.direntries[sid]
        if entry.entry_type != STGTY_STREAM:
            raise IOError("this file is not a stream")
        return self._open(entry.isectStart, entry.size)

    def _write_mini_stream(self, entry, data_to_write):
        if not entry.sect_chain:
            entry.build_sect_chain(self)
        nb_sectors = len(entry.sect_chain)

        if not self.root.sect_chain:
            self.root.build_sect_chain(self)
        block_size = self.sector_size // self.mini_sector_size
        for idx, sect in enumerate(entry.sect_chain):
            sect_base = sect // block_size
            sect_offset = sect % block_size
            fp_pos = (self.root.sect_chain[sect_base] + 1)*self.sector_size + sect_offset*self.mini_sector_size
            if idx < (nb_sectors - 1):
                data_per_sector = data_to_write[idx * self.mini_sector_size: (idx + 1) * self.mini_sector_size]
            else:
                data_per_sector = data_to_write[idx * self.mini_sector_size:]
            self._write_mini_sect(fp_pos, data_per_sector)

    def write_stream(self, stream_name, data):
        """
        Write a stream to disk. For now, it is only possible to replace an
        existing stream by data of the same size.

        :param stream_name: path of stream in storage tree (except root entry), either:

            - a string using Unix path syntax, for example:
              'storage_1/storage_1.2/stream'
            - or a list of storage filenames, path to the desired stream/storage.
              Example: ['storage_1', 'storage_1.2', 'stream']

        :param data: bytes, data to be written, must be the same size as the original
            stream.
        """
        if not isinstance(data, bytes):
            raise TypeError("write_stream: data must be a bytes string")
        sid = self._find(stream_name)
        entry = self.direntries[sid]
        if entry.entry_type != STGTY_STREAM:
            raise IOError("this is not a stream")
        size = entry.size
        if size != len(data):
            raise ValueError("write_stream: data must be the same size as the existing stream")
        if size < self.minisectorcutoff and entry.entry_type != STGTY_ROOT:
            return self._write_mini_stream(entry = entry, data_to_write = data)

        sect = entry.isectStart
        # number of sectors to write
        nb_sectors = (size + (self.sectorsize-1)) // self.sectorsize
        log.debug('nb_sectors = %d' % nb_sectors)
        for i in range(nb_sectors):
            # try:
            #     self.fp.seek(offset + self.sectorsize * sect)
            # except Exception:
            #     log.debug('sect=%d, seek=%d' %
            #         (sect, offset+self.sectorsize*sect))
            #     raise IOError('OLE sector index out of range')
            # extract one sector from data, the last one being smaller:
            if i<(nb_sectors-1):
                data_sector = data [i*self.sectorsize : (i+1)*self.sectorsize]
                # TODO: comment this if it works
                assert(len(data_sector)==self.sectorsize)
            else:
                data_sector = data [i*self.sectorsize:]
                # TODO: comment this if it works
                log.debug('write_stream: size=%d sectorsize=%d data_sector=%Xh size%%sectorsize=%d'
                    % (size, self.sectorsize, len(data_sector), size % self.sectorsize))
                assert(len(data_sector) % self.sectorsize==size % self.sectorsize)
            self.write_sect(sect, data_sector)
            # self.fp.write(data_sector)
            # jump to next sector in the FAT:
            try:
                sect = self.fat[sect]
            except IndexError:
                # [PL] if pointer is out of the FAT an exception is raised
                raise IOError('incorrect OLE FAT, sector index out of range')
        # [PL] Last sector should be a "end of chain" marker:
        if sect != ENDOFCHAIN:
            raise IOError('incorrect last sector index in OLE stream')

    def replace_streams(self, replacements=None, output_path=None, **kwargs):
        """
        Apply a group of equal-length stream replacements and publish the
        result as a new file, leaving the original file untouched.

        This is the batch, precondition-checked counterpart of
        :meth:`write_stream`. Each :class:`StreamReplacement` carries the
        digest of the stream contents the caller expects to replace, so the
        group is only published if every precondition still holds when the
        new file is built. The new file is written completely before it is
        published under its final name; if anything fails, no new file
        appears.

        The original file is never modified. If ``output_path`` already holds
        the exact expected result of this same group, the call reports
        ``status == REPLACEMENT_ALREADY_APPLIED`` (the receipt from an earlier
        attempt was presumably lost) instead of treating it as a conflict.
        This still works when the original file was corrected after the
        publish, as long as the published file is byte-for-byte the result of
        this group. If the output holds anything else, and the group is not
        replayable, :class:`OutputStreamConflictError` is raised.

        :param replacements: iterable of :class:`StreamReplacement` objects.
            For convenience, (stream_name, data, old_digest) tuples and
            mappings with the same keys are accepted as well.
        :param output_path: path of the new file to publish. Must differ from
            the original file. If None, a path is derived from the original
            path by inserting '.replaced' before the extension.
        :returns: :class:`StreamReplacementResult`, the receipt for this group
        :exception StreamPreconditionError: if a stream is missing, has a
            different size, or its contents no longer match ``old_digest``
        :exception StreamOverlapError: if two replacements resolve to
            overlapping storage regions (including the same stream twice)
        :exception OutputStreamConflictError: if ``output_path`` already exists
            and is neither the original file nor the expected result
        """
        if self.fp.closed:
            raise ValueError("replace_streams: the OLE file is closed")
        if replacements is None:
            replacements = kwargs.pop('replacement_group',
                                      kwargs.pop('stream_replacements', None))
        output_path = _resolve_output_alias(output_path, kwargs)
        if replacements is None:
            raise TypeError("replace_streams: replacements are required")
        source_path = self._opened_path
        source_bytes = self._replacement_source_bytes()
        source_digest = hashlib.sha256(source_bytes).hexdigest()

        records_by_key, old_digests = self._prepare_replacements(
            source_bytes, replacements)
        group_digest = self._group_digest(records_by_key)

        if output_path is None:
            if source_path is None:
                raise ValueError("replace_streams: output_path is required when "
                                 "the OLE file was not opened from a path")
            root, extension = os.path.splitext(source_path)
            output_path = root + '.replaced' + extension
        output_path = _text_path(output_path)
        if source_path is not None and os.path.exists(output_path):
            try:
                same_file = os.path.samefile(source_path, output_path)
            except OSError:
                same_file = os.path.abspath(source_path) == \
                    os.path.abspath(output_path)
            if same_file:
                raise ValueError("replace_streams: output_path must differ from "
                                 "the original file, which is left unchanged")

        # Build the complete expected output in memory from the same snapshot
        # the preconditions are checked against, then verify it logically
        # before writing anything.
        expected_output = self._build_replaced_bytes(source_bytes, records_by_key)
        expected_output_digest = hashlib.sha256(expected_output).hexdigest()
        expected_fingerprint = self._replaced_fingerprint(expected_output)

        if os.path.exists(output_path):
            # The published file may be the result of an earlier submission of
            # this same group (the caller lost its receipt). That is recognized
            # from the bytes it would have, even if the original file no longer
            # matches the old digests, as long as the group still applies
            # structurally.
            if self._output_matches(output_path, expected_output_digest,
                                    expected_fingerprint):
                return self._replacement_result(
                    output_path, source_digest, group_digest,
                    expected_output_digest, REPLACEMENT_ALREADY_APPLIED,
                    records_by_key)
            # The output is not this group's result. If the group itself is
            # stale, report that first, as it is the actual caller error.
            self._check_old_digests(records_by_key, old_digests)
            raise OutputStreamConflictError(output_path)

        # No output yet: every old-content precondition must hold before the
        # new file is built or published.
        self._check_old_digests(records_by_key, old_digests)

        output_dir = os.path.dirname(os.path.abspath(output_path))
        temp_fd, temp_path = tempfile.mkstemp(prefix='.olefile-replace-',
                                              suffix='.tmp', dir=output_dir)
        try:
            with os.fdopen(temp_fd, 'wb') as temp_fp:
                temp_fp.write(expected_output)
                temp_fp.flush()
                os.fsync(temp_fp.fileno())
            # Verify the written file before it becomes visible under its
            # final name, both as a whole and stream by stream:
            if _file_digest(temp_path) != expected_output_digest:
                raise StreamReplacementError(
                    "replace_streams: written file does not match the expected "
                    "result, nothing was published")
            with OleFileIO(temp_path) as written:
                actual_fingerprint = _stream_snapshot(written)
            if actual_fingerprint != expected_fingerprint:
                raise StreamReplacementError(
                    "replace_streams: written streams do not match the expected "
                    "result, nothing was published")
            if source_path is not None and _file_digest(source_path) != source_digest:
                # The original changed after the snapshot: the preconditions
                # no longer describe the file this group was built from.
                raise StreamPreconditionError(
                    records_by_key[sorted(records_by_key)[0]].stream_path,
                    None, None,
                    message="the original file changed while the replacement "
                            "group was being prepared, nothing was published")
            _copy_file_permissions(temp_path, source_path)
            try:
                _publish_new_file(temp_path, output_path)
            except OSError:
                if os.path.exists(output_path) and self._output_matches(
                        output_path, expected_output_digest, expected_fingerprint):
                    # A concurrent identical call published the same result:
                    return self._replacement_result(
                        output_path, source_digest, group_digest,
                        expected_output_digest, REPLACEMENT_ALREADY_APPLIED,
                        records_by_key)
                raise
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        return self._replacement_result(
            output_path, source_digest, group_digest, expected_output_digest,
            REPLACEMENT_APPLIED, records_by_key)

    def _replacement_source_bytes(self):
        """
        Read the whole original OLE file as bytes, without relying on the file
        position or mode it was opened in.
        """
        if self._opened_path is not None:
            with open(self._opened_path, 'rb') as fp:
                return fp.read()
        position = self.fp.tell()
        try:
            self.fp.seek(0)
            return self.fp.read()
        finally:
            self.fp.seek(position)

    def _prepare_replacements(self, source, replacements):
        """
        Validate a replacement group against one immutable snapshot of the
        source file.

        Checks performed here, against the exact bytes that will be copied to
        the output: records must be well formed, streams must exist, must be
        streams, must have the same length as the new data, and no two
        replacements may resolve to overlapping storage regions. The current
        digest of each stream is also computed, but compared separately by
        :meth:`_check_old_digests` so that an already-published output can be
        recognized before raising a precondition error.

        :param source: bytes of the original OLE file
        :param replacements: iterable of StreamReplacement objects, tuples or
            mappings (see :meth:`replace_streams`)
        :returns: (records_by_key, old_digests) where records_by_key maps a
            stream path key to a StreamReplacement, and old_digests maps the
            same key to the digest of the current stream contents computed
            with that record's algorithm
        """
        records = _coerce_replacements(replacements)
        if not records:
            raise ValueError("replace_streams: at least one replacement is required")
        records_by_key = {}
        path_by_key = {}
        for record in records:
            key = _stream_path_key(record.stream_path)
            if key in records_by_key:
                raise StreamOverlapError(records_by_key[key].stream_path,
                                         record.stream_path,
                                         message="stream %r is listed more than once "
                                         "in the same replacement group"
                                         % ('/'.join(record.stream_path),))
            records_by_key[key] = record
            path_by_key[key] = record.stream_path
        old_digests = {}
        with OleFileIO(source) as snapshot:
            for key, record in records_by_key.items():
                try:
                    sid = snapshot._find(list(record.stream_path))
                except IOError:
                    raise StreamPreconditionError(record.stream_path,
                                                  record.old_digest, None,
                        message="stream %r does not exist in this OLE file"
                        % ('/'.join(record.stream_path),))
                entry = snapshot.direntries[sid]
                if entry.entry_type != STGTY_STREAM:
                    raise StreamPreconditionError(record.stream_path,
                                                  record.old_digest, None,
                        message="%r is not a stream" % ('/'.join(record.stream_path),))
                if entry.size != record.size:
                    raise StreamReplacementError(
                        "stream %r is %d bytes, replacement data is %d bytes: "
                        "only equal-length replacements are allowed"
                        % ('/'.join(record.stream_path), entry.size, record.size))
                stream = snapshot.openstream(list(record.stream_path))
                try:
                    data = stream.read()
                finally:
                    stream.close()
                old_digests[key] = hashlib.new(record.digest_algorithm,
                                               data).hexdigest()
            # Reject groups whose streams physically share or overlap sectors,
            # even when the directory tree looks valid:
            regions_by_key = {}
            for key, record in records_by_key.items():
                sid = snapshot._find(list(record.stream_path))
                regions = _entry_byte_regions(snapshot, snapshot.direntries[sid])
                if regions:
                    regions_by_key[key] = regions
            _check_no_overlap(regions_by_key, path_by_key)
        return records_by_key, old_digests

    @staticmethod
    def _check_old_digests(records_by_key, old_digests):
        """
        Verify every old-content digest, raising StreamPreconditionError for
        the first stream whose contents no longer match.
        """
        for key in sorted(records_by_key):
            record = records_by_key[key]
            actual_digest = old_digests[key]
            if actual_digest != record.old_digest:
                raise StreamPreconditionError(
                    record.stream_path, record.old_digest, actual_digest,
                    digest_algorithm=record.digest_algorithm)

    def _group_digest(self, records_by_key):
        """
        Compute a stable digest identifying one replacement group.

        The digest covers each stream path (case-insensitively, as OLE names
        are case-insensitive), old digest, new digest and size, sorted by
        path. It deliberately does not cover the source file, the output path
        or any timing information, so a caller that lost its receipt and
        resubmits the same group gets the same version identifier.
        """
        digest = hashlib.sha256()
        digest.update(b'olefile.replace_streams\x00')
        for key in sorted(records_by_key):
            record = records_by_key[key]
            digest.update(b'\x00')
            digest.update('/'.join(key).encode('utf-8', 'replace'))
            digest.update(b'\x00')
            digest.update(record.old_digest.encode('ascii'))
            digest.update(b'\x00')
            digest.update(record.new_digest.encode('ascii'))
            digest.update(struct.pack('<Q', record.size))
        return digest.hexdigest()

    def _replaced_fingerprint(self, output_bytes):
        """
        Return the logical fingerprint of the expected output bytes.

        :param output_bytes: bytes of the built output file
        """
        with OleFileIO(output_bytes) as output:
            return _stream_snapshot(output)

    @staticmethod
    def _output_matches(output_path, expected_digest, expected_fingerprint):
        """
        Check whether an existing output file is exactly the expected result.

        Both the byte digest and the logical stream fingerprint must match.
        The fingerprint guards against a file that happens to have the same
        bytes on disk but a different logical layout, and lets a retry after a
        lost receipt identify the same published version.
        """
        if _file_digest(output_path) != expected_digest:
            return False
        try:
            with OleFileIO(output_path) as other:
                return _stream_snapshot(other) == expected_fingerprint
        except (IOError, OleFileError):
            return False

    def _build_replaced_bytes(self, source_bytes, records_by_key):
        """
        Build the complete bytes of the output file in memory.

        The snapshot bytes are copied and each replacement is written through
        a private in-memory OleFileIO over that copy, so the original file and
        this object are never modified.

        :param source_bytes: bytes of the original OLE file
        :param records_by_key: dict keyed by stream path key
        :returns: bytes of the new OLE file
        """
        # BytesIO gives the writer its own mutable buffer over a copy of the
        # original bytes. write_stream() writes through this buffer, never
        # through the original file or through this object's file handle.
        buffer = io.BytesIO(source_bytes)
        writer = OleFileIO(buffer, write_mode=True)
        try:
            for key, record in records_by_key.items():
                if record.size == 0:
                    # Equal-length replacement by empty data is a no-op; the
                    # writer cannot address a zero-length chain.
                    continue
                writer.write_stream(list(record.stream_path), record.data)
            result = buffer.getvalue()
        finally:
            writer.close()
        return result

    def _replacement_result(self, output_path, source_digest, group_digest,
                            output_digest, status, records_by_key):
        """Build the receipt returned by replace_streams."""
        replaced = []
        for key in sorted(records_by_key):
            record = records_by_key[key]
            replaced.append((record.stream_path, record.old_digest,
                             record.new_digest, record.size))
        return StreamReplacementResult(output_path, source_digest, group_digest,
                                       output_digest, status, replaced)

    def write_streams(self, replacements=None, output_path=None, **kwargs):
        """Alias for :meth:`replace_streams`."""
        return self.replace_streams(replacements, output_path, **kwargs)

    def get_type(self, filename):
        """
        Test if given filename exists as a stream or a storage in the OLE
        container, and return its type.

        :param filename: path of stream in storage tree. (see openstream for syntax)
        :returns: False if object does not exist, its entry type (>0) otherwise:

            - STGTY_STREAM: a stream
            - STGTY_STORAGE: a storage
            - STGTY_ROOT: the root entry
        """
        try:
            sid = self._find(filename)
            entry = self.direntries[sid]
            return entry.entry_type
        except Exception:
            return False

    def getclsid(self, filename):
        """
        Return clsid of a stream/storage.

        :param filename: path of stream/storage in storage tree. (see openstream for
            syntax)
        :returns: Empty string if clsid is null, a printable representation of the clsid otherwise

        new in version 0.44
        """
        sid = self._find(filename)
        entry = self.direntries[sid]
        return entry.clsid

    def getmtime(self, filename):
        """
        Return modification time of a stream/storage.

        :param filename: path of stream/storage in storage tree. (see openstream for
            syntax)
        :returns: None if modification time is null, a python datetime object
            otherwise (UTC timezone)

        new in version 0.26
        """
        sid = self._find(filename)
        entry = self.direntries[sid]
        return entry.getmtime()

    def getctime(self, filename):
        """
        Return creation time of a stream/storage.

        :param filename: path of stream/storage in storage tree. (see openstream for
            syntax)
        :returns: None if creation time is null, a python datetime object
            otherwise (UTC timezone)

        new in version 0.26
        """
        sid = self._find(filename)
        entry = self.direntries[sid]
        return entry.getctime()

    def exists(self, filename):
        """
        Test if given filename exists as a stream or a storage in the OLE
        container.
        Note: filename is case-insensitive.

        :param filename: path of stream in storage tree. (see openstream for syntax)
        :returns: True if object exist, else False.
        """
        try:
            sid = self._find(filename)
            return True
        except Exception:
            return False

    def get_size(self, filename):
        """
        Return size of a stream in the OLE container, in bytes.

        :param filename: path of stream in storage tree (see openstream for syntax)
        :returns: size in bytes (long integer)
        :exception IOError: if file not found
        :exception TypeError: if this is not a stream.
        """
        sid = self._find(filename)
        entry = self.direntries[sid]
        if entry.entry_type != STGTY_STREAM:
            # TODO: Should it return zero instead of raising an exception ?
            raise TypeError('object is not an OLE stream')
        return entry.size

    def get_rootentry_name(self):
        """
        Return root entry name. Should usually be 'Root Entry' or 'R' in most
        implementations.
        """
        return self.root.name

    def getproperties(self, filename, convert_time=False, no_conversion=None):
        """
        Return properties described in substream.

        :param filename: path of stream in storage tree (see openstream for syntax)
        :param convert_time: bool, if True timestamps will be converted to Python datetime
        :param no_conversion: None or list of int, timestamps not to be converted
            (for example total editing time is not a real timestamp)

        :returns: a dictionary of values indexed by id (integer)
        """
        #REFERENCE: [MS-OLEPS] https://msdn.microsoft.com/en-us/library/dd942421.aspx
        # make sure no_conversion is a list, just to simplify code below:
        if no_conversion == None:
            no_conversion = []
        # stream path as a string to report exceptions:
        streampath = filename
        if not isinstance(streampath, str):
            streampath = '/'.join(streampath)
        fp = self.openstream(filename)
        data = {}
        try:
            # header
            s = fp.read(28)
            clsid = _clsid(s[8:24])
            # format id
            s = fp.read(20)
            fmtid = _clsid(s[:16])
            fp.seek(i32(s, 16))
            # get section
            s = b"****" + fp.read(i32(fp.read(4))-4)
            # number of properties:
            num_props = i32(s, 4)
        except BaseException as exc:
            # catch exception while parsing property header, and only raise
            # a DEFECT_INCORRECT then return an empty dict, because this is not
            # a fatal error when parsing the whole file
            msg = 'Error while parsing properties header in stream {}: {}'.format(
                repr(streampath), exc)
            self._raise_defect(DEFECT_INCORRECT, msg, type(exc))
            return data
        # clamp num_props based on the data length
        num_props = min(num_props, int(len(s) / 8))
        for i in iterrange(num_props):
            property_id = 0 # just in case of an exception
            try:
                property_id = i32(s, 8+i*8)
                offset = i32(s, 12+i*8)
                property_type = i32(s, offset)

                vt_name = VT.get(property_type, 'UNKNOWN')
                log.debug('property id=%d: type=%d/%s offset=%X' % (property_id, property_type, vt_name, offset))

                value = self._parse_property(s, offset+4, property_id, property_type, convert_time, no_conversion)
                data[property_id] = value
            except BaseException as exc:
                # catch exception while parsing each property, and only raise
                # a DEFECT_INCORRECT, because parsing can go on
                msg = 'Error while parsing property id %d in stream %s: %s' % (
                    property_id, repr(streampath), exc)
                self._raise_defect(DEFECT_INCORRECT, msg, type(exc))

        return data

    def _parse_property(self, s, offset, property_id, property_type, convert_time, no_conversion):
        v = None
        if property_type <= VT_BLOB or property_type in (VT_CLSID, VT_CF):
            v, _ = self._parse_property_basic(s, offset, property_id, property_type, convert_time, no_conversion)
        elif property_type == VT_VECTOR | VT_VARIANT:
            log.debug('property_type == VT_VECTOR | VT_VARIANT')
            off = 4
            count = i32(s, offset)
            values = []
            for _ in range(count):
                property_type = i32(s, offset + off)
                v, sz  = self._parse_property_basic(s, offset + off + 4, property_id, property_type, convert_time, no_conversion)
                values.append(v)
                off += sz + 4
            v = values

        elif property_type & VT_VECTOR:
            property_type_base = property_type & ~VT_VECTOR
            log.debug('property_type == VT_VECTOR | %s' % VT.get(property_type_base, 'UNKNOWN'))
            off = 4
            count = i32(s, offset)
            values = []
            for _ in range(count):
                v, sz = self._parse_property_basic(s, offset + off, property_id, property_type & ~VT_VECTOR, convert_time, no_conversion)
                values.append(v)
                off += sz
            v = values
        else:
            log.debug('property id=%d: type=%d not implemented in parser yet' % (property_id, property_type))
        return v

    def _parse_property_basic(self, s, offset, property_id, property_type, convert_time, no_conversion):
            value = None
            size = 0
            # test for common types first (should perhaps use
            # a dictionary instead?)

            if property_type == VT_I2: # 16-bit signed integer
                value = i16(s, offset)
                if value >= 32768:
                    value = value - 65536
                size = 2
            elif property_type == VT_UI2: # 2-byte unsigned integer
                value = i16(s, offset)
                size = 2
            elif property_type in (VT_I4, VT_INT, VT_ERROR):
                # VT_I4: 32-bit signed integer
                # VT_ERROR: HRESULT, similar to 32-bit signed integer,
                # see https://msdn.microsoft.com/en-us/library/cc230330.aspx
                value = i32(s, offset)
                size = 4
            elif property_type in (VT_UI4, VT_UINT): # 4-byte unsigned integer
                value = i32(s, offset) # FIXME
                size = 4
            elif property_type in (VT_BSTR, VT_LPSTR):
                # CodePageString, see https://msdn.microsoft.com/en-us/library/dd942354.aspx
                # size is a 32 bits integer, including the null terminator, and
                # possibly trailing or embedded null chars
                #TODO: if codepage is unicode, the string should be converted as such
                count = i32(s, offset)
                value = s[offset+4:offset+4+count-1]
                # remove all null chars:
                value = value.replace(b'\x00', b'')
                size = 4 + count
            elif property_type == VT_BLOB:
                # binary large object (BLOB)
                # see https://msdn.microsoft.com/en-us/library/dd942282.aspx
                count = i32(s, offset)
                value = s[offset+4:offset+4+count]
                size = 4 + count
            elif property_type == VT_LPWSTR:
                # UnicodeString
                # see https://msdn.microsoft.com/en-us/library/dd942313.aspx
                # "the string should NOT contain embedded or additional trailing
                # null characters."
                count = i32(s, offset+4)
                value = self._decode_utf16_str(s[offset+4:offset+4+count*2])
                size = 4 + count * 2
            elif property_type == VT_FILETIME:
                value = long(i32(s, offset)) + (long(i32(s, offset+4))<<32)
                # FILETIME is a 64-bit int: "number of 100ns periods
                # since Jan 1,1601".
                if convert_time and property_id not in no_conversion:
                    log.debug('Converting property #%d to python datetime, value=%d=%fs'
                            %(property_id, value, float(value)/10000000))
                    # convert FILETIME to Python datetime.datetime
                    # inspired from https://code.activestate.com/recipes/511425-filetime-to-datetime/
                    _FILETIME_null_date = datetime.datetime(1601, 1, 1, 0, 0, 0)
                    log.debug('timedelta days=%d' % (value//(10*1000000*3600*24)))
                    value = _FILETIME_null_date + datetime.timedelta(microseconds=value//10)
                else:
                    # legacy code kept for backward compatibility: returns a
                    # number of seconds since Jan 1,1601
                    value = value // 10000000 # seconds
                size = 8
            elif property_type == VT_UI1: # 1-byte unsigned integer
                value = i8(s[offset])
                size = 1
            elif property_type == VT_CLSID:
                value = _clsid(s[offset:offset+16])
                size = 16
            elif property_type == VT_CF:
                # PropertyIdentifier or ClipboardData??
                # see https://msdn.microsoft.com/en-us/library/dd941945.aspx
                count = i32(s, offset)
                value = s[offset+4:offset+4+count]
                size = 4 + count
            elif property_type == VT_BOOL:
                # VARIANT_BOOL, 16 bits bool, 0x0000=Fals, 0xFFFF=True
                # see https://msdn.microsoft.com/en-us/library/cc237864.aspx
                value = bool(i16(s, offset))
                size = 2
            else:
                value = None # everything else yields "None"
                log.debug('property id=%d: type=%d not implemented in parser yet' % (property_id, property_type))

                # missing: VT_EMPTY, VT_NULL, VT_R4, VT_R8, VT_CY, VT_DATE,
                # VT_DECIMAL, VT_I1, VT_I8, VT_UI8,
                # see https://msdn.microsoft.com/en-us/library/dd942033.aspx

                #print("%08x" % property_id, repr(value), end=" ")
                #print("(%s)" % VT[i32(s, offset) & 0xFFF])
            return value, size


    def get_metadata(self):
        """
        Parse standard properties streams, return an OleMetadata object
        containing all the available metadata.
        (also stored in the metadata attribute of the OleFileIO object)

        new in version 0.25
        """
        self.metadata = OleMetadata()
        self.metadata.parse_properties(self)
        return self.metadata

    def get_userdefined_properties(self, filename, convert_time=False, no_conversion=None):
        """
        Return properties described in substream.

        :param filename: path of stream in storage tree (see openstream for syntax)
        :param convert_time: bool, if True timestamps will be converted to Python datetime
        :param no_conversion: None or list of int, timestamps not to be converted
            (for example total editing time is not a real timestamp)

        :returns: a dictionary of values indexed by id (integer)
        """
        # REFERENCE: [MS-OLEPS] https://msdn.microsoft.com/en-us/library/dd942421.aspx
        # REFERENCE: https://docs.microsoft.com/en-us/openspecs/office_file_formats/ms-oshared/2ea8be67-a4a0-4e2e-b42f-49a182645562
        #'D5CDD502-2E9C-101B-9397-08002B2CF9AE'
        # TODO: testing the code more rigorously
        # TODO: adding exception handeling
        FMTID_USERDEFINED_PROPERTIES = _clsid(b'\x05\xD5\xCD\xD5\x9C\x2E\x1B\x10\x93\x97\x08\x00\x2B\x2C\xF9\xAE')

        # make sure no_conversion is a list, just to simplify code below:
        if no_conversion == None:
            no_conversion = []
        # stream path as a string to report exceptions:
        streampath = filename
        if not isinstance(streampath, str):
            streampath = '/'.join(streampath)

        fp = self.openstream(filename)

        data = []

        # header
        s = fp.read(28)
        clsid = _clsid(s[8:24])

        # PropertySetStream.cSections (4 bytes starts at 1c): number of property sets in this stream
        sections_count = i32(s, 24)

        section_file_pointers = []

        try:
            for i in range(sections_count):
                # format id
                s = fp.read(20)
                fmtid = _clsid(s[:16])

                if fmtid == FMTID_USERDEFINED_PROPERTIES:
                    file_pointer = i32(s, 16)
                    fp.seek(file_pointer)
                    # read saved sections
                    s = b"****" + fp.read(i32(fp.read(4)) - 4)
                    # number of properties:
                    num_props = i32(s, 4)

                    PropertyIdentifierAndOffset = s[8: 8+8*num_props]

                    # property names (dictionary)
                    # ref: https://docs.microsoft.com/en-us/openspecs/windows_protocols/MS-OLEPS/99127b7f-c440-4697-91a4-c853086d6b33
                    index = 8+8*num_props
                    entry_count = i32(s[index: index+4])
                    index += 4
                    for i in range(entry_count):
                        identifier = s[index: index +4]
                        str_size = i32(s[index+4: index + 8])
                        string = s[index+8: index+8+str_size].decode('utf_8').strip('\0')
                        data.append({'property_name':string, 'value':None})
                        index = index+8+str_size
                    # clamp num_props based on the data length
                    num_props = min(num_props, int(len(s) / 8))

                    # property values
                    # ref: https://docs.microsoft.com/en-us/openspecs/windows_protocols/MS-OLEPS/f122b9d7-e5cf-4484-8466-83f6fd94b3cc
                    for i in iterrange(2, num_props):
                        property_id = 0  # just in case of an exception
                        try:
                            property_id = i32(s, 8 + i * 8)
                            offset = i32(s, 12 + i * 8)
                            property_type = i32(s, offset)

                            vt_name = VT.get(property_type, 'UNKNOWN')
                            log.debug('property id=%d: type=%d/%s offset=%X' % (property_id, property_type, vt_name, offset))

                            # test for common types first (should perhaps use
                            # a dictionary instead?)

                            if property_type == VT_I2:  # 16-bit signed integer
                                value = i16(s, offset + 4)
                                if value >= 32768:
                                    value = value - 65536
                            elif property_type == 1:
                                # supposed to be VT_NULL but seems it is not NULL
                                str_size = i32(s, offset + 8)
                                value = s[offset + 12:offset + 12 + str_size - 1]

                            elif property_type == VT_UI2:  # 2-byte unsigned integer
                                value = i16(s, offset + 4)
                            elif property_type in (VT_I4, VT_INT, VT_ERROR):
                                # VT_I4: 32-bit signed integer
                                # VT_ERROR: HRESULT, similar to 32-bit signed integer,
                                # see https://msdn.microsoft.com/en-us/library/cc230330.aspx
                                value = i32(s, offset + 4)
                            elif property_type in (VT_UI4, VT_UINT):  # 4-byte unsigned integer
                                value = i32(s, offset + 4)  # FIXME
                            elif property_type in (VT_BSTR, VT_LPSTR):
                                # CodePageString, see https://msdn.microsoft.com/en-us/library/dd942354.aspx
                                # size is a 32 bits integer, including the null terminator, and
                                # possibly trailing or embedded null chars
                                # TODO: if codepage is unicode, the string should be converted as such
                                count = i32(s, offset + 4)
                                value = s[offset + 8:offset + 8 + count - 1]
                                # remove all null chars:
                                value = value.replace(b'\x00', b'')
                            elif property_type == VT_BLOB:
                                # binary large object (BLOB)
                                # see https://msdn.microsoft.com/en-us/library/dd942282.aspx
                                count = i32(s, offset + 4)
                                value = s[offset + 8:offset + 8 + count]
                            elif property_type == VT_LPWSTR:
                                # UnicodeString
                                # see https://msdn.microsoft.com/en-us/library/dd942313.aspx
                                # "the string should NOT contain embedded or additional trailing
                                # null characters."
                                count = i32(s, offset + 4)
                                value = self._decode_utf16_str(s[offset + 8:offset + 8 + count * 2])
                            elif property_type == VT_FILETIME:
                                value = long(i32(s, offset + 4)) + (long(i32(s, offset + 8)) << 32)
                                # FILETIME is a 64-bit int: "number of 100ns periods
                                # since Jan 1,1601".
                                if convert_time and property_id not in no_conversion:
                                    log.debug('Converting property #%d to python datetime, value=%d=%fs'
                                              % (property_id, value, float(value) / 10000000))
                                    # convert FILETIME to Python datetime.datetime
                                    # inspired from https://code.activestate.com/recipes/511425-filetime-to-datetime/
                                    _FILETIME_null_date = datetime.datetime(1601, 1, 1, 0, 0, 0)
                                    log.debug('timedelta days=%d' % (value // (10 * 1000000 * 3600 * 24)))
                                    value = _FILETIME_null_date + datetime.timedelta(microseconds=value // 10)
                                else:
                                    # legacy code kept for backward compatibility: returns a
                                    # number of seconds since Jan 1,1601
                                    value = value // 10000000  # seconds
                            elif property_type == VT_UI1:  # 1-byte unsigned integer
                                value = i8(s[offset + 4])
                            elif property_type == VT_CLSID:
                                value = _clsid(s[offset + 4:offset + 20])
                            elif property_type == VT_CF:
                                # PropertyIdentifier or ClipboardData??
                                # see https://msdn.microsoft.com/en-us/library/dd941945.aspx
                                count = i32(s, offset + 4)
                                value = s[offset + 8:offset + 8 + count]
                            elif property_type == VT_BOOL:
                                # VARIANT_BOOL, 16 bits bool, 0x0000=Fals, 0xFFFF=True
                                # see https://msdn.microsoft.com/en-us/library/cc237864.aspx
                                value = bool(i16(s, offset + 4))
                            else:
                                value = None  # everything else yields "None"
                                log.debug(
                                    'property id=%d: type=%d not implemented in parser yet' % (property_id, property_type))

                            # missing: VT_EMPTY, VT_NULL, VT_R4, VT_R8, VT_CY, VT_DATE,
                            # VT_DECIMAL, VT_I1, VT_I8, VT_UI8,
                            # see https://msdn.microsoft.com/en-us/library/dd942033.aspx

                            # FIXME: add support for VT_VECTOR
                            # VT_VECTOR is a 32 uint giving the number of items, followed by
                            # the items in sequence. The VT_VECTOR value is combined with the
                            # type of items, e.g. VT_VECTOR|VT_BSTR
                            # see https://msdn.microsoft.com/en-us/library/dd942011.aspx

                            # print("%08x" % property_id, repr(value), end=" ")
                            # print("(%s)" % VT[i32(s, offset) & 0xFFF])

                            data[i-2]['value']=value
                        except BaseException as exc:
                            # catch exception while parsing each property, and only raise
                            # a DEFECT_INCORRECT, because parsing can go on
                            msg = 'Error while parsing property id %d in stream %s: %s' % (
                                property_id, repr(streampath), exc)
                            self._raise_defect(DEFECT_INCORRECT, msg, type(exc))

        except BaseException as exc:
            # catch exception while parsing property header, and only raise
            # a DEFECT_INCORRECT then return an empty dict, because this is not
            # a fatal error when parsing the whole file
            msg = 'Error while parsing properties header in stream %s: %s' % (
                repr(streampath), exc)
            self._raise_defect(DEFECT_INCORRECT, msg, type(exc))
            return data

        return data


# --------------------------------------------------------------------
# This script can be used to dump the directory of any OLE2 structured
# storage file.

def main():
    """
    Main function when olefile is runs as a script from the command line.
    This will open an OLE2 file and display its structure and properties
    :return: nothing
    """
    import sys, optparse

    DEFAULT_LOG_LEVEL = "warning" # Default log level
    LOG_LEVELS = {
        'debug':    logging.DEBUG,
        'info':     logging.INFO,
        'warning':  logging.WARNING,
        'error':    logging.ERROR,
        'critical': logging.CRITICAL
        }

    usage = 'usage: %prog [options] <filename> [filename2 ...]'
    parser = optparse.OptionParser(usage=usage)

    parser.add_option("-c", action="store_true", dest="check_streams",
        help='check all streams (for debugging purposes)')
    parser.add_option("-p", action="store_true", dest="extract_customprop",
                      help='extract all user-defined propertires')
    parser.add_option("-d", action="store_true", dest="debug_mode",
        help='debug mode, shortcut for -l debug (displays a lot of debug information, for developers only)')
    parser.add_option('-l', '--loglevel', dest="loglevel", action="store", default=DEFAULT_LOG_LEVEL,
                            help="logging level debug/info/warning/error/critical (default=%default)")

    (options, args) = parser.parse_args()

    print('olefile version {} {} - https://www.decalage.info/en/olefile\n'.format(__version__, __date__))

    # Print help if no arguments are passed
    if len(args) == 0:
        print(__doc__)
        parser.print_help()
        sys.exit()

    if options.debug_mode:
        options.loglevel = 'debug'

    # setup logging to the console
    logging.basicConfig(level=LOG_LEVELS[options.loglevel], format='%(levelname)-8s %(message)s')

    # also enable the module's logger:
    enable_logging()

    for filename in args:
        try:
            ole = OleFileIO(filename)#, raise_defects=DEFECT_INCORRECT)
            print("-" * 68)
            print(filename)
            print("-" * 68)
            ole.dumpdirectory()
            for streamname in ole.listdir():
                if streamname[-1][0] == "\005":
                    print("%r: properties" % streamname)
                    try:
                        props = ole.getproperties(streamname, convert_time=True)
                        props = sorted(props.items())
                        for k, v in props:
                            # [PL]: avoid to display too large or binary values:
                            if isinstance(v, (basestring, bytes)):
                                if len(v) > 50:
                                    v = v[:50]
                            if isinstance(v, bytes):
                                # quick and dirty binary check:
                                for c in (1,2,3,4,5,6,7,11,12,14,15,16,17,18,19,20,
                                          21,22,23,24,25,26,27,28,29,30,31):
                                    if c in bytearray(v):
                                        v = '(binary data)'
                                        break
                            print("   ", k, v)
                    except Exception:
                        log.exception('Error while parsing property stream %r' % streamname)

                    try:
                        if options.extract_customprop:
                            variables = ole.get_userdefined_properties(streamname, convert_time=True)
                            if len(variables):
                                print("%r: user-defined properties" % streamname)
                                for index, variable in enumerate(variables):
                                    print('\t{} {}: {}'.format(index, variable['property_name'],variable['value']))

                    except:
                        log.exception('Error while parsing user-defined property stream %r' % streamname)


            if options.check_streams:
                # Read all streams to check if there are errors:
                print('\nChecking streams...')
                for streamname in ole.listdir():
                    # print name using repr() to convert binary chars to \xNN:
                    print('-', repr('/'.join(streamname)),'-', end=' ')
                    st_type = ole.get_type(streamname)
                    if st_type == STGTY_STREAM:
                        print('size %d' % ole.get_size(streamname))
                        # just try to read stream in memory:
                        ole.openstream(streamname)
                    else:
                        print('NOT a stream : type=%d' % st_type)
                print()

            # for streamname in ole.listdir():
            #     # print name using repr() to convert binary chars to \xNN:
            #     print('-', repr('/'.join(streamname)),'-', end=' ')
            #     print(ole.getmtime(streamname))
            # print()

            print('Modification/Creation times of all directory entries:')
            for entry in ole.direntries:
                if entry is not None:
                    print('- {}: mtime={} ctime={}'.format(entry.name,
                        entry.getmtime(), entry.getctime()))
            print()

            # parse and display metadata:
            try:
                meta = ole.get_metadata()
                meta.dump()
            except Exception:
                log.exception('Error while parsing metadata')
            print()
            # [PL] Test a few new methods:
            root = ole.get_rootentry_name()
            print('Root entry name: "%s"' % root)
            if ole.exists('worddocument'):
                print("This is a Word document.")
                print("type of stream 'WordDocument':", ole.get_type('worddocument'))
                print("size :", ole.get_size('worddocument'))
                if ole.exists('macros/vba'):
                    print("This document may contain VBA macros.")

            # print parsing issues:
            print('\nNon-fatal issues raised during parsing:')
            if ole.parsing_issues:
                for exctype, msg in ole.parsing_issues:
                    print('- {}: {}'.format(exctype.__name__, msg))
            else:
                print('None')
            ole.close()
        except Exception:
            log.exception('Error while parsing file %r' % filename)


if __name__ == "__main__":
    main()

# this code was developed while listening to The Wedding Present "Sea Monsters"
