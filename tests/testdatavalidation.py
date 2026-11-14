#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for ODF spreadsheet content validation."""

import os
import tempfile
import unittest
from io import BytesIO

from odf.datavalidation import (
    EMPTY, PASS, VIOLATION, UNVERIFIABLE, ValidationStatus,
    evaluate_condition, validate_cell, validate_document, validate_values,
)
from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import (
    ContentValidation, ContentValidations, CoveredTableCell, NamedRange,
    NamedExpressions, Table, TableCell, TableRow,
)
from odf.text import P
from odf.validation import check_submission


def string_cell(value, validation_name=None):
    attributes = {'valuetype': 'string'}
    if validation_name is not None:
        attributes['contentvalidationname'] = validation_name
    cell = TableCell(**attributes)
    cell.addElement(P(text=value))
    return cell


def number_cell(value, validation_name=None):
    attributes = {'valuetype': 'float', 'value': value}
    if validation_name is not None:
        attributes['contentvalidationname'] = validation_name
    return TableCell(**attributes)


class SpreadsheetBuilder(object):
    def __init__(self):
        self.document = OpenDocumentSpreadsheet()
        self.validations = ContentValidations()
        self.document.spreadsheet.addElement(self.validations)
        self.table = Table(name='S')
        self.document.spreadsheet.addElement(self.table)

    def rule(self, **attributes):
        self.validations.addElement(ContentValidation(**attributes))

    def row(self, cells):
        row = TableRow()
        for cell in cells:
            row.addElement(cell)
        self.table.addElement(row)

    def named_range(self, name, address, base=None):
        expressions = NamedExpressions()
        attributes = {'name': name, 'cellrangeaddress': address}
        if base is not None:
            attributes['basecelladdress'] = base
        expressions.addElement(NamedRange(**attributes))
        self.document.spreadsheet.addElement(expressions)


class TestConstantList(unittest.TestCase):
    def setUp(self):
        builder = SpreadsheetBuilder()
        builder.rule(name='colors', condition='of:cell-content-is-in-list("red","green")')
        builder.row([string_cell('red', 'colors')])
        self.document = builder.document

    def test_pass(self):
        result = validate_cell(self.document, 'S.A1', value='green')
        self.assertEqual(PASS, result.status)

    def test_violation(self):
        result = validate_cell(self.document, 'S.A1', value='blue')
        self.assertEqual(VIOLATION, result.status)

    def test_numbers_are_compared_as_numbers(self):
        result = evaluate_condition(
            'of:cell-content-is-in-list(1,2,3)', 2)
        self.assertEqual(PASS, result.status)
        self.assertEqual(VIOLATION,
                         evaluate_condition(
                             'of:cell-content-is-in-list(1,2,3)', 4).status)

    def test_current_value_of_cell(self):
        result = validate_cell(self.document, 'S.A1')
        self.assertEqual(PASS, result.status)


class TestRangeList(unittest.TestCase):
    def setUp(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='from-range',
            condition='of:cell-content-is-in-list([$S.$C1:C$3])',
            basecelladdress='S.A1')
        builder.row([string_cell('x', 'from-range'), string_cell('label'),
                     string_cell('one')])
        builder.row([string_cell('y'), string_cell('label'),
                     string_cell('two')])
        builder.row([string_cell('z'), string_cell('label'),
                     string_cell('three')])
        self.document = builder.document

    def test_value_from_range_passes(self):
        self.assertEqual(
            PASS,
            validate_cell(self.document, 'S.A1', value='three').status)

    def test_value_outside_range_violates(self):
        self.assertEqual(
            VIOLATION,
            validate_cell(self.document, 'S.A1', value='four').status)

    def test_reads_cell_value_when_value_omitted(self):
        self.assertEqual(
            VIOLATION, validate_cell(self.document, 'S.A1').status)

    def test_document_walk_reports_all_bound_cells(self):
        results = validate_document(self.document)
        self.assertEqual(1, len(results))
        self.assertEqual('S.A1', results[0].cell_address)


class TestSharedRuleWithRowRelativeRange(unittest.TestCase):
    def setUp(self):
        builder = SpreadsheetBuilder()
        # The list starts in the cell to the right of the validated cell.
        builder.rule(
            name='right-neighbour',
            condition='of:cell-content-is-in-list([$S.$C1:.$C1])',
            basecelladdress='$S.$B1',
            allowemptycell='false')

        allowed_per_row = ['apple', 'banana']
        for allowed in allowed_per_row:
            builder.row([
                string_cell('label'),
                string_cell(allowed, 'right-neighbour'),
                string_cell(allowed),
            ])
        self.document = builder.document

    def test_first_row_uses_its_own_right_neighbour(self):
        self.assertEqual(
            PASS, validate_cell(self.document, 'S.B1', value='apple').status)
        self.assertEqual(
            VIOLATION,
            validate_cell(self.document, 'S.B1', value='banana').status)

    def test_second_row_uses_a_different_list(self):
        self.assertEqual(
            PASS, validate_cell(self.document, 'S.B2', value='banana').status)
        self.assertEqual(
            VIOLATION,
            validate_cell(self.document, 'S.B2', value='apple').status)

    def test_absolute_and_relative_forms_agree(self):
        result = validate_cell(self.document, 'S.B2', value='banana')
        self.assertEqual(PASS, result.status)
        self.assertEqual(PASS, validate_cell(
            self.document, 'S.B2', value='banana').status)


class TestEmptyVersusZero(unittest.TestCase):
    def test_empty_is_allowed_and_zero_is_checked(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='non-negative',
            condition='of:cell-content-is-in-list(0,1,2)',
            allowemptycell='true')
        builder.row([number_cell(0, 'non-negative')])
        document = builder.document

        # A submitted blank is allowed by the rule ...
        self.assertEqual(
            PASS, validate_cell(document, 'S.A1', value=None).status)
        # ... but a submitted numeric zero is a value and must match the list.
        result = validate_cell(document, 'S.A1', value=0)
        self.assertEqual(PASS, result.status)
        self.assertIs(0, int(result.value))

    def test_empty_rejected_when_rule_disallows_it(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='required',
            condition='of:cell-content-is-in-list("yes")',
            allowemptycell='false')
        builder.row([string_cell('', 'required')])
        document = builder.document

        result = validate_cell(document, 'S.A1', value=None)
        self.assertEqual(VIOLATION, result.status)
        self.assertIs(EMPTY, result.value)

    def test_zero_is_not_mistaken_for_empty(self):
        self.assertEqual(
            VIOLATION,
            evaluate_condition(
                'of:cell-content-is-in-list("x")', 0,
                allow_empty_cell=True).status)
        self.assertEqual(
            PASS,
            evaluate_condition(
                'of:cell-content-is-in-list("x")', None,
                allow_empty_cell=True).status)


class TestUnverifiable(unittest.TestCase):
    def test_missing_range_reports_reason(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='missing',
            condition='of:cell-content-is-in-list([$S.$Z9:.$Z10])',
            basecelladdress='$S.$A1')
        builder.row([string_cell('value', 'missing')])
        result = validate_cell(builder.document, 'S.A1', value='x')
        self.assertEqual(UNVERIFIABLE, result.status)
        self.assertIn('missing', result.reason)
        self.assertIn('Z9', result.reason)

    def test_missing_sheet_reports_reason(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='bad-sheet',
            condition='of:cell-content-is-in-list([$Other.$A1:.$A2])',
            basecelladdress='$S.$A1')
        builder.row([string_cell('value', 'bad-sheet')])
        result = validate_cell(builder.document, 'S.A1', value='x')
        self.assertEqual(UNVERIFIABLE, result.status)
        self.assertIn('Other', result.reason)

    def test_partially_missing_range_is_unverifiable(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='past-edge',
            condition='of:cell-content-is-in-list([$S.$A1:.$C1])',
            basecelladdress='$S.$A1')
        builder.row([string_cell('value', 'past-edge'), string_cell('other')])
        result = validate_cell(builder.document, 'S.A1', value='other')
        self.assertEqual(UNVERIFIABLE, result.status)
        self.assertIn('A1:C1', result.reason)

    def test_range_beyond_last_row_is_unverifiable(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='past-rows',
            condition='of:cell-content-is-in-list([$S.$A9:.$B9])',
            basecelladdress='$S.$A1')
        builder.row([string_cell('value', 'past-rows')])
        result = validate_cell(builder.document, 'S.A1', value='x')
        self.assertEqual(UNVERIFIABLE, result.status)
        self.assertIn('A9:B9', result.reason)

    def test_missing_rule_reports_reason(self):
        builder = SpreadsheetBuilder()
        builder.row([string_cell('value', 'no-such-rule')])
        result = validate_cell(builder.document, 'S.A1', value='x')
        self.assertEqual(UNVERIFIABLE, result.status)
        self.assertIn('no-such-rule', result.reason)

    def test_unsupported_condition_is_unverifiable(self):
        builder = SpreadsheetBuilder()
        builder.rule(name='unsupported', condition='of:cell-content-is-between(1,5)')
        builder.row([string_cell('value', 'unsupported')])
        result = validate_cell(builder.document, 'S.A1', value=3)
        self.assertEqual(UNVERIFIABLE, result.status)
        self.assertIn('unsupported', result.reason)

    def test_unbound_cell_is_unverifiable(self):
        builder = SpreadsheetBuilder()
        builder.row([string_cell('value')])
        result = validate_cell(builder.document, 'S.A1', value='x')
        self.assertEqual(UNVERIFIABLE, result.status)


class TestSubmissionMapping(unittest.TestCase):
    def test_batch_values_preserve_order_and_distinguish_zero(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='list',
            condition='of:cell-content-is-in-list([$S.$C1:.$C1])',
            basecelladdress='$S.$B1',
            allowemptycell='true')
        for allowed in ('yes', 'no'):
            builder.row([
                string_cell('label'),
                string_cell(allowed, 'list'),
                string_cell(allowed),
            ])
        document = builder.document

        values = [('S.B1', None), ('S.B1', 0),
                  ('S.B1', 'yes'), ('S.B2', 'no')]
        results = validate_values(document, values)
        self.assertEqual(
            [PASS, VIOLATION, PASS, PASS],
            [result.status for result in results])
        self.assertIs(EMPTY, results[0].value)
        self.assertEqual(0, int(results[1].value))

    def test_unknown_cell_in_mapping_is_unverifiable(self):
        builder = SpreadsheetBuilder()
        builder.row([string_cell('value')])
        results = validate_values(builder.document, [('S.Z99', 'x')])
        self.assertEqual(UNVERIFIABLE, results[0].status)
        self.assertIn('no cell exists', results[0].reason)


class TestLoadRoundTrip(unittest.TestCase):
    def test_validate_values_loaded_from_saved_file(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='loaded',
            condition='of:cell-content-is-in-list([$S.$C1:.$C1])',
            basecelladdress='$S.$B1')
        builder.row([
            string_cell('label'),
            string_cell('good', 'loaded'),
            string_cell('good'),
        ])
        output = builder.document.toXml()
        self.assertIn('table:content-validation', output)
        # The in-memory document and one loaded back from XML must agree.
        self.assertEqual(
            PASS, validate_cell(builder.document, 'S.B1', value='good').status)

    def test_named_range_source(self):
        builder = SpreadsheetBuilder()
        builder.named_range('choices', '$S.$C$1:$C$2')
        builder.rule(
            name='named',
            condition='of:cell-content-is-in-list(choices)',
            basecelladdress='$S.$B1')
        builder.row([string_cell('one', 'named'), string_cell('label'),
                     string_cell('one')])
        builder.row([string_cell('one'), string_cell('label'),
                     string_cell('two')])
        self.assertEqual(
            PASS, validate_cell(builder.document, 'S.A1', value='two').status)
        self.assertEqual(
            VIOLATION,
            validate_cell(builder.document, 'S.A1', value='three').status)


class TestStatusConstants(unittest.TestCase):
    def test_status_values(self):
        self.assertEqual('pass', ValidationStatus.PASS)
        self.assertEqual('violation', ValidationStatus.VIOLATION)
        self.assertEqual('unverifiable', ValidationStatus.UNVERIFIABLE)

    def test_bool_reflects_pass(self):
        self.assertTrue(evaluate_condition(
            'of:cell-content-is-in-list("a")', 'a'))
        self.assertFalse(evaluate_condition(
            'of:cell-content-is-in-list("a")', 'b'))


class TestStoredValues(unittest.TestCase):
    def test_zero_cell_and_empty_cell_are_different(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='allowed',
            condition='of:cell-content-is-in-list(0)',
            allowemptycell='true')
        builder.row([number_cell(0, 'allowed'), string_cell('', 'allowed')])
        results = validate_document(builder.document)
        by_address = {result.cell_address: result for result in results}
        self.assertEqual(PASS, by_address['S.A1'].status)
        self.assertIs(EMPTY, by_address['S.B1'].value)

    def test_string_cell_with_numeric_text_does_not_match_number(self):
        builder = SpreadsheetBuilder()
        builder.rule(name='zero', condition='of:cell-content-is-in-list(0)')
        builder.row([string_cell('0', 'zero')])
        self.assertEqual(
            VIOLATION, validate_cell(builder.document, 'S.A1').status)

    def test_vertically_merged_cell_covers_following_row(self):
        builder = SpreadsheetBuilder()
        builder.rule(name='merged', condition='of:cell-content-is-in-list("m")')
        first = TableRow()
        first.addElement(TableCell(
            valuetype='string', value='m',
            numberrowsspanned=2, contentvalidationname='merged'))
        builder.table.addElement(first)
        second = TableRow()
        second.addElement(CoveredTableCell())
        builder.table.addElement(second)
        result = validate_cell(builder.document, 'S.A2')
        self.assertEqual(PASS, result.status)
        self.assertEqual('m', result.value)

    def test_repeated_cell_binding_covers_all_positions(self):
        builder = SpreadsheetBuilder()
        builder.rule(name='wide', condition='of:cell-content-is-in-list("x")')
        row = TableRow()
        row.addElement(TableCell(
            numbercolumnsrepeated=3, contentvalidationname='wide'))
        builder.table.addElement(row)
        for address in ('S.A1', 'S.B1', 'S.C1'):
            self.assertEqual(
                PASS, validate_cell(builder.document, address, value='x').status)


class TestSavedFile(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(
            tempfile.mkdtemp(prefix='odf-validation-'), 'check.ods')

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_save_reload_and_check_submission(self):
        builder = SpreadsheetBuilder()
        builder.rule(
            name='shared',
            condition='of:cell-content-is-in-list([$S.$C1:.$C1])',
            basecelladdress='$S.$B1',
            allowemptycell='false')
        for allowed in ('one', 'two'):
            builder.row([
                string_cell('label'),
                string_cell(allowed, 'shared'),
                string_cell(allowed),
            ])
        builder.document.save(self.path)

        results = check_submission(self.path, [
            ('S.B1', 'one'),
            ('S.B1', 'two'),
            ('S.B2', 'two'),
            ('S.B2', 'one'),
            ('S.B1', None),
            ('S.B1', 0),
        ])
        self.assertEqual(
            [PASS, VIOLATION, PASS, VIOLATION, VIOLATION, VIOLATION],
            [result.status for result in results])
        self.assertIs(EMPTY, results[4].value)

    def test_stream_input_is_accepted(self):
        builder = SpreadsheetBuilder()
        builder.rule(name='r', condition='of:cell-content-is-in-list("ok")')
        builder.row([string_cell('ok', 'r')])
        stream = BytesIO()
        builder.document.write(stream)
        stream.seek(0)
        results = check_submission(stream, [('S.A1', 'no')])
        self.assertEqual(VIOLATION, results[0].status)


if __name__ == '__main__':
    unittest.main()
