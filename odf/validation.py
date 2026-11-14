# -*- coding: utf-8 -*-
# Copyright (C) 2026
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#

"""Convenience entry points for data validation.

``odf.datavalidation`` contains the evaluator itself.  This module adds a
small helper that accepts an ODS file name or an already loaded document, so
a data import can be guarded with one call::

    from odf.validation import check_submission

    results = check_submission('book.ods', {'Sheet1.B2': 'online'})
    for result in results:
        if not result.passed:
            print(result.cell_address, result.status, result.reason)
"""

from odf.datavalidation import (
    EMPTY, PASS, VIOLATION, UNVERIFIABLE, ValidationResult,
    ValidationStatus, evaluate_condition, validate_cell, validate_document,
    validate_submission, validate_values,
)

__all__ = [
    'EMPTY', 'PASS', 'VIOLATION', 'UNVERIFIABLE',
    'ValidationResult', 'ValidationStatus', 'check_submission',
    'evaluate_condition', 'load_document', 'validate_cell',
    'validate_document', 'validate_submission', 'validate_values',
]


def load_document(source):
    """Return *source* if it is already a document, else load it as ODS.

    ``source`` may be a file name, a readable stream, or an object that
    implements ``getElementsByType`` (an already loaded document).
    """
    if hasattr(source, 'getElementsByType'):
        return source
    from odf.opendocument import load
    return load(source)


def check_submission(source, values, sheet=None):
    """Validate submitted cell values against an ODS file's rules.

    ``source`` is a file name, stream or loaded document.  ``values`` is a
    mapping or iterable of ``(address, value)`` pairs.  Uses
    :func:`odf.datavalidation.validate_values`; see that function for the
    meaning of each result.
    """
    return validate_values(load_document(source), values, sheet=sheet)
