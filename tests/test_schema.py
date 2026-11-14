"""
Tests for olefile.schema: incremental column inference from OLE property types.
"""

from __future__ import print_function
import itertools
import json
import os
import sys
import unittest

_thismodule_dir = os.path.normpath(os.path.abspath(os.path.dirname(__file__)))
_parent_dir = os.path.normpath(os.path.join(_thismodule_dir, '..'))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import olefile
from olefile.schema import (
    Schema, Column, TypedValue, infer_schema, merge_schemas,
    infer_properties_schema, parse_numeric_text,
    VT_EMPTY, VT_NULL, VT_I2, VT_I4, VT_I8, VT_UI4, VT_R8, VT_BOOL,
    VT_LPSTR, VT_BSTR, VT_LPWSTR, VT_DATE, VT_FILETIME, VT_BLOB, VT_VECTOR,
    VT_VARIANT,
)


def column(schema, column_id):
    return schema[column_id].describe()


class TestNumericText(unittest.TestCase):

    def test_plain_integer(self):
        self.assertEqual(parse_numeric_text('42'), ('integer', 42))
        self.assertEqual(parse_numeric_text('-7'), ('integer', -7))
        self.assertEqual(parse_numeric_text(' +7 '), ('integer', 7))

    def test_plain_float(self):
        self.assertEqual(parse_numeric_text('3.5'), ('float', 3.5))
        self.assertEqual(parse_numeric_text('-.5'), ('float', -0.5))

    def test_rejects_non_numbers(self):
        for text in ['', ' ', 'abc', '1,000', '$5', '1e3', 'NaN', 'Inf',
                     '3.5.1', '.', '-', '5%']:
            self.assertIsNone(parse_numeric_text(text), text)


class TestIntegerWidening(unittest.TestCase):

    def test_same_column_id_when_width_widens(self):
        schema = Schema()
        schema.observe('num_pages', TypedValue(3, VT_I2))
        schema.observe('num_pages', TypedValue(70000, VT_I4))
        description = column(schema, 'num_pages')
        self.assertEqual(description['id'], 'num_pages')
        self.assertEqual(description['kind'], 'integer')
        self.assertEqual(description['vtypes'], ['VT_I2', 'VT_I4'])
        self.assertEqual(description['original_type'], 'VT_I2')

    def test_integer_then_float_widens_in_place(self):
        schema = Schema()
        schema.observe('ratio', TypedValue(1, VT_I4))
        schema.observe('ratio', TypedValue(1.5, VT_R8))
        description = column(schema, 'ratio')
        self.assertEqual(description['id'], 'ratio')
        self.assertEqual(description['kind'], 'float')
        self.assertEqual(description['conflicts'], 0)

    def test_integer_widths_do_not_change_value(self):
        schema = Schema()
        schema.observe('count', TypedValue(7, VT_I8))
        schema.observe('count', TypedValue(7, VT_I4))
        schema.observe('count', TypedValue(7, VT_UI4))
        description = column(schema, 'count')
        self.assertEqual(description['value'], 7)
        self.assertEqual(description['conflicts'], 0)
        self.assertEqual(description['original_type'], 'VT_I4')


class TestEmptyValues(unittest.TestCase):

    def test_empty_before_integer_does_not_pin_kind(self):
        schema = Schema()
        schema.observe('count', TypedValue(None, VT_EMPTY))
        schema.observe('count', TypedValue(5, VT_I4))
        description = column(schema, 'count')
        self.assertEqual(description['kind'], 'integer')
        self.assertEqual(description['null_count'], 1)
        self.assertEqual(description['conflicts'], 0)

    def test_null_and_empty_are_counted(self):
        schema = Schema()
        schema.observe('count', TypedValue(1, VT_I4))
        schema.observe('count', TypedValue(None, VT_EMPTY))
        schema.observe('count', TypedValue(None, VT_NULL))
        description = column(schema, 'count')
        self.assertEqual(description['null_count'], 2)
        self.assertEqual(description['count'], 3)


class TestNumericTextWidening(unittest.TestCase):

    def test_numeric_text_keeps_numeric_column(self):
        schema = Schema()
        schema.observe('count', TypedValue(5, VT_I4))
        schema.observe('count', TypedValue('42', VT_LPSTR))
        description = column(schema, 'count')
        self.assertEqual(description['id'], 'count')
        self.assertEqual(description['kind'], 'integer')
        self.assertEqual(description['coerced_text_count'], 1)
        self.assertEqual(description['text_count'], 1)
        self.assertEqual(description['text_samples'], ['42'])
        self.assertEqual(description['conflicts'], 0)
        # both the numeric value and the original spelling are queryable
        self.assertIn(description['value'], (5, 42))
        self.assertEqual(description['original_values'], [5, 42])

    def test_numeric_text_first_then_integer(self):
        schema = Schema()
        schema.observe('count', TypedValue('42', VT_LPSTR))
        schema.observe('count', TypedValue(5, VT_I4))
        description = column(schema, 'count')
        self.assertEqual(description['kind'], 'integer')
        self.assertEqual(description['text_count'], 1)
        self.assertIn(5, description['text_samples'] + [description['value']])


class TestRealTextWidening(unittest.TestCase):

    def test_true_text_widens_and_keeps_original(self):
        schema = Schema()
        schema.observe('count', TypedValue(5, VT_I4))
        schema.observe('count', TypedValue('unknown', VT_LPSTR))
        description = column(schema, 'count')
        self.assertEqual(description['id'], 'count')
        self.assertEqual(description['kind'], 'text')
        self.assertEqual(description['value'], 'unknown')
        self.assertEqual(description['original_type'], 'VT_I4')
        self.assertEqual(description['original_value'], 5)
        self.assertEqual(description['conflicts'], 1)
        kinds = [s['kind'] for s in description['conflict_samples']]
        self.assertIn('integer', kinds)
        self.assertIn('text', kinds)

    def test_text_first_then_integer_keeps_both(self):
        schema = Schema()
        schema.observe('count', TypedValue('unknown', VT_LPSTR))
        schema.observe('count', TypedValue(5, VT_I4))
        description = column(schema, 'count')
        self.assertEqual(description['kind'], 'text')
        self.assertEqual(description['original_type'], 'VT_I4')
        self.assertEqual(description['original_value'], 5)

    def test_text_widening_does_not_change_column_id(self):
        schema = Schema()
        schema.observe('count', TypedValue(5, VT_I4))
        before = column(schema, 'count')['id']
        schema.observe('count', TypedValue('unknown', VT_LPSTR))
        after = column(schema, 'count')['id']
        self.assertEqual(before, after)

    def test_text_and_datetime_conflict(self):
        schema = Schema()
        schema.observe('saved', TypedValue('yesterday', VT_LPSTR))
        schema.observe('saved', TypedValue(123456, VT_FILETIME))
        description = column(schema, 'saved')
        self.assertEqual(description['kind'], 'any')
        self.assertEqual(description['conflicts'], 1)
        self.assertEqual(len(description['conflict_samples']), 2)


class TestVectorScalarSeparation(unittest.TestCase):

    def test_scalar_and_vector_same_name_stay_separate(self):
        schema = Schema()
        schema.observe('title', TypedValue('single', VT_LPSTR))
        schema.observe('title', TypedValue(['a', 'b'], VT_VECTOR | VT_LPSTR))
        self.assertEqual(schema.column_ids(), ['title', 'title#vector'])
        scalar = column(schema, 'title')
        vector = column(schema, 'title#vector')
        self.assertEqual(scalar['value'], 'single')
        self.assertFalse(scalar['vector'])
        self.assertTrue(vector['vector'])
        self.assertEqual(vector['count'], 2)
        self.assertNotEqual(scalar['value'], vector['value'])

    def test_vector_only_has_vector_id(self):
        schema = Schema({'tags': TypedValue(['x', 'y'], VT_VECTOR | VT_LPSTR)})
        self.assertEqual(schema.column_ids(), ['tags#vector'])

    def test_vector_and_scalar_conflicts_do_not_mix(self):
        schema = Schema()
        schema.observe('tags', TypedValue(['a'], VT_VECTOR | VT_LPSTR))
        schema.observe('tags', TypedValue(5, VT_I4))
        self.assertEqual(column(schema, 'tags#vector')['conflicts'], 0)
        self.assertEqual(column(schema, 'tags')['kind'], 'integer')

    def test_vector_of_variants(self):
        schema = Schema()
        schema.observe('props', TypedValue(
            [TypedValue(1, VT_I4), TypedValue('x', VT_LPSTR)],
            VT_VECTOR | VT_VARIANT))
        description = column(schema, 'props#vector')
        self.assertTrue(description['vector'])
        self.assertEqual(description['kind'], 'text')
        self.assertEqual(description['conflicts'], 1)


class TestMergeEquivalence(unittest.TestCase):
    """Monthly inference merged must equal a single full-year pass."""

    MONTHS = [
        {'num_pages': TypedValue(3, VT_I2),
         'title': TypedValue('one', VT_LPSTR)},
        {'num_pages': TypedValue(70000, VT_I4)},
        {'num_pages': TypedValue(None, VT_EMPTY)},
        {'num_pages': TypedValue('unknown', VT_LPSTR),
         'title': TypedValue(['a', 'b'], VT_VECTOR | VT_LPSTR)},
        {'num_pages': TypedValue('44', VT_LPSTR),
         'title': TypedValue('one', VT_LPSTR)},
        {'num_pages': TypedValue(9, VT_I4),
         'title': TypedValue(None, VT_NULL)},
    ]

    def _one_pass(self):
        schema = Schema()
        for month in self.MONTHS:
            for name, value in month.items():
                schema.observe(name, value)
        return schema

    def _monthly(self, order=None):
        months = self.MONTHS if order is None else [self.MONTHS[i]
                                                    for i in order]
        schemas = [Schema(month, name='month-%d' % index)
                   for index, month in enumerate(months)]
        return merge_schemas(*schemas)

    def test_all_orders_match_single_pass(self):
        expected = self._one_pass().fingerprints()
        for order in itertools.permutations(range(len(self.MONTHS))):
            merged = self._monthly(order)
            self.assertEqual(merged.fingerprints(), expected, order)

    def test_conflict_samples_match_single_pass(self):
        expected = self._one_pass().conflicts()
        for order in itertools.permutations(range(len(self.MONTHS))):
            merged = self._monthly(order)
            self.assertEqual(merged.conflicts(), expected, order)

    def test_merge_is_associative(self):
        a, b, c, d, e, f = (Schema(month) for month in self.MONTHS)
        left = merge_schemas(merge_schemas(a, b), merge_schemas(c, d),
                             merge_schemas(e, f))
        right = merge_schemas(a, merge_schemas(b, merge_schemas(c, d)),
                              merge_schemas(e, f))
        self.assertEqual(left.fingerprints(), right.fingerprints())

    def test_merge_is_commutative(self):
        a = Schema(self.MONTHS[0])
        b = Schema(self.MONTHS[3])
        self.assertEqual(merge_schemas(a, b).fingerprints(),
                         merge_schemas(b, a).fingerprints())


class TestCounterMigrationScenario(unittest.TestCase):
    """The prompt's scenario: a counter moves from integer to string."""

    MONTHS = [
        {'doc_count': TypedValue(100, VT_I4)},
        {'doc_count': TypedValue(200, VT_I4), 'title': TypedValue('a', VT_LPSTR)},
        {'doc_count': TypedValue(None, VT_EMPTY)},
        {'doc_count': TypedValue(None, VT_NULL)},
        {'doc_count': TypedValue('n/a', VT_LPSTR)},
        {'doc_count': TypedValue('n/a', VT_LPSTR), 'title': TypedValue('b', VT_LPSTR)},
    ]

    def _monthly(self):
        schemas = []
        for index, record in enumerate(self.MONTHS):
            schema = Schema(record, name='2026-%02d' % (index + 1))
            # round-trip each month through JSON, as an incremental pipeline
            # would persist it
            schemas.append(Schema.from_json(schema.to_json()))
        return merge_schemas(*schemas)

    def _annual(self):
        schema = Schema()
        for record in self.MONTHS:
            for name, value in record.items():
                schema.observe(name, value)
        return schema

    def test_column_identity_survives_type_change(self):
        monthly = self._monthly()
        self.assertEqual(monthly.column_ids(), ['doc_count', 'title'])

    def test_monthly_equals_annual(self):
        monthly = self._monthly()
        annual = self._annual()
        self.assertEqual(monthly.fingerprints(), annual.fingerprints())
        self.assertEqual(monthly.conflicts(), annual.conflicts())

    def test_lookback_fields(self):
        description = column(self._monthly(), 'doc_count')
        self.assertEqual(description['kind'], 'text')
        self.assertEqual(description['value'], 'n/a')
        self.assertEqual(description['original_type'], 'VT_I4')
        self.assertEqual(description['original_value'], 100)
        self.assertEqual(description['original_values'], [100, 200])
        self.assertEqual(description['null_count'], 2)
        self.assertEqual(description['conflicts'], 2)

    def test_conflict_samples_are_stable_across_orders(self):
        expected = self._annual().conflicts()
        for order in itertools.permutations(range(len(self.MONTHS))):
            months = [self.MONTHS[index] for index in order]
            schemas = [Schema(record) for record in months]
            merged = merge_schemas(*schemas)
            self.assertEqual(merged.conflicts(), expected, order)


class TestInputForms(unittest.TestCase):

    def test_record_list(self):
        schema = Schema([
            {'count': (5, VT_I4), 'title': 'one'},
            {'count': ('unknown', VT_LPSTR)},
        ])
        description = column(schema, 'count')
        self.assertEqual(description['kind'], 'text')
        self.assertEqual(description['original_value'], 5)

    def test_explicit_typed_pair(self):
        schema = Schema({'count': (5, VT_I8)})
        description = column(schema, 'count')
        self.assertEqual(description['vtypes'], ['VT_I8'])

    def test_two_element_tuple_is_still_a_vector(self):
        schema = Schema({'tags': ('a', 'b')})
        self.assertEqual(schema.column_ids(), ['tags#vector'])
        self.assertEqual(column(schema, 'tags#vector')['count'], 2)

    def test_typed_value_from_ole(self):
        typed = TypedValue.from_ole(5, VT_VECTOR | VT_I4)
        self.assertTrue(typed.vector)
        self.assertEqual(typed.vtype, VT_VECTOR | VT_I4)

    def test_string_type_names(self):
        schema = Schema({
            'a': ('5', 'VT_I2'),
            'b': {'value': 6, 'type': 'VT_I4'},
            'c': ('hello', 'VT_LPSTR'),
        })
        self.assertEqual(column(schema, 'a')['kind'], 'integer')
        self.assertEqual(column(schema, 'a')['vtypes'], ['VT_I2'])
        self.assertEqual(column(schema, 'b')['kind'], 'integer')
        self.assertEqual(column(schema, 'c')['kind'], 'text')
        self.assertEqual(schema.column_ids(), ['a', 'b', 'c'])

    def test_string_vector_type_name(self):
        schema = Schema({'tags': (['a', 'b'], 'VT_VECTOR|VT_LPSTR')})
        description = column(schema, 'tags#vector')
        self.assertTrue(description['vector'])
        self.assertEqual(description['vtypes'], ['VT_LPSTR'])


class TestSerialization(unittest.TestCase):

    def test_describe_is_json_serializable(self):
        schema = Schema()
        schema.observe('count', TypedValue(5, VT_I4))
        schema.observe('count', TypedValue('unknown', VT_LPSTR))
        schema.observe('when', TypedValue(123456789, VT_FILETIME))
        schema.observe('tags', TypedValue(['a'], VT_VECTOR | VT_LPSTR))
        text = schema.to_json()
        self.assertEqual(json.loads(text)['columns'], schema.describe())

    def test_descriptions_can_be_merged_later(self):
        a = Schema({'count': TypedValue(5, VT_I4)})
        b = Schema({'count': TypedValue('x', VT_LPSTR)})
        expected = merge_schemas(a, b).fingerprints()
        restored = Schema.from_dict(json.loads(a.to_json()))
        restored.merge(json.loads(b.to_json()))
        self.assertEqual(restored.fingerprints(), expected)

    def test_from_dict_round_trip(self):
        schema = Schema()
        schema.observe('count', TypedValue(5, VT_I4))
        schema.observe('count', TypedValue('6', VT_LPSTR))
        schema.observe('tags', TypedValue(['a'], VT_VECTOR | VT_LPSTR))
        restored = Schema.from_dict(schema.to_dict())
        self.assertEqual(restored.fingerprints(), schema.fingerprints())

    def test_conflict_samples_are_bounded(self):
        schema = Schema()
        for value in range(100):
            schema.observe('count', TypedValue(value, VT_I4))
        for value in range(100):
            schema.observe('count', TypedValue('text-%d' % value, VT_LPSTR))
        description = column(schema, 'count')
        self.assertLessEqual(len(description['conflict_samples']), 16)
        self.assertEqual(description['conflicts'], 100)


class TestOleIntegration(unittest.TestCase):

    def test_typed_properties_from_real_file(self):
        ole_file = os.path.join(os.path.dirname(__file__), 'images',
                                'test-ole-file.doc')
        with olefile.OleFileIO(ole_file) as ole:
            properties = ole.getproperties('\x05SummaryInformation',
                                           with_types=True)
            self.assertIsInstance(properties[1], TypedValue)
            self.assertEqual(properties[1].vtype, VT_I2)
            self.assertEqual(properties[1].value, 1252)
            self.assertEqual(properties[4].vtype, VT_LPSTR)
            self.assertEqual(properties[4].value, b'Laurence Ipsum')

            schema = infer_properties_schema(ole, '\x05SummaryInformation')
            description = column(schema, '4')
            self.assertEqual(description['kind'], 'text')
            self.assertEqual(description['value'], 'Laurence Ipsum')
            # property 9 is a CodePageString containing a number
            description = column(schema, '9')
            self.assertEqual(description['kind'], 'integer')
            self.assertEqual(description['text_samples'], ['2'])

    def test_default_getproperties_is_unchanged(self):
        ole_file = os.path.join(os.path.dirname(__file__), 'images',
                                'test-ole-file.doc')
        with olefile.OleFileIO(ole_file) as ole:
            properties = ole.getproperties('\x05SummaryInformation')
            self.assertEqual(properties[1], 1252)
            self.assertEqual(properties[4], b'Laurence Ipsum')

    def test_olefile_get_schema(self):
        ole_file = os.path.join(os.path.dirname(__file__), 'images',
                                'test-ole-file.doc')
        with olefile.OleFileIO(ole_file) as ole:
            schema = ole.get_schema('\x05SummaryInformation')
            self.assertEqual(len(schema), 13)
            self.assertEqual(column(schema, '1')['kind'], 'integer')
            everything = ole.get_schema()
            self.assertGreater(len(everything), len(schema))

    def test_ole_schema_keeps_streams_apart(self):
        ole_file = os.path.join(os.path.dirname(__file__), 'images',
                                'test-ole-file.doc')
        with olefile.OleFileIO(ole_file) as ole:
            schema = olefile.infer_ole_schema(ole)
            ids = schema.column_ids()
            self.assertTrue(any(i.startswith('\x05SummaryInformation:')
                                for i in ids))
            self.assertTrue(any(
                i.startswith('\x05DocumentSummaryInformation:') for i in ids))


class TestOrderIndependenceFuzz(unittest.TestCase):
    """Randomized check that merge order cannot affect the result."""

    def test_random_months_match_single_pass(self):
        import random
        rng = random.Random(20261114)

        def observation():
            vtype = rng.choice([VT_I2, VT_I4, VT_I8, VT_LPSTR, VT_EMPTY,
                                VT_R8, VT_FILETIME, VT_BOOL])
            if vtype == VT_EMPTY:
                return TypedValue(None, vtype)
            if vtype in (VT_I2, VT_I4, VT_I8):
                return TypedValue(rng.randint(-5, 100), vtype)
            if vtype == VT_R8:
                return TypedValue(rng.random() * 5, vtype)
            if vtype == VT_FILETIME:
                return TypedValue(rng.randint(0, 10 ** 6), vtype)
            if vtype == VT_BOOL:
                return TypedValue(rng.choice([True, False]), vtype)
            return TypedValue(rng.choice(['x', '42', '-3', '1.5', '007',
                                          'N/A']), vtype)

        for _ in range(25):
            months = []
            for _ in range(rng.randint(2, 7)):
                record = {}
                for _ in range(rng.randint(1, 4)):
                    name = rng.choice(['a', 'b', 'c'])
                    if rng.random() < 0.3:
                        record[name] = TypedValue(
                            [observation() for _ in range(rng.randint(0, 3))],
                            VT_VECTOR | rng.choice([VT_I4, VT_LPSTR,
                                                    VT_VARIANT]))
                    else:
                        record[name] = observation()
                months.append(record)

            annual = Schema()
            for record in months:
                for name, value in record.items():
                    annual.observe(name, value)

            order = list(range(len(months)))
            rng.shuffle(order)
            monthly = merge_schemas(*[
                Schema.from_json(Schema(months[index]).to_json())
                for index in order])
            self.assertEqual(monthly.fingerprints(), annual.fingerprints())
            self.assertEqual(monthly.conflicts(), annual.conflicts())


if __name__ == '__main__':
    unittest.main()
