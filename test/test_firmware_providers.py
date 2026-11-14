#-------------------------------------------------------------------------------
# elftools tests
#
# Tests for the offline firmware provider selection module.
#
# This code is in the public domain.
#-------------------------------------------------------------------------------
import json
import os
import tempfile
import unittest

from elftools.elf.firmware import (
    FirmwareTarget,
    Module,
    Provider,
    load_catalog,
    load_config,
    read_dynamic_dependencies,
    select_providers,
)


def _provider(soname, version, size, satisfies, path=None):
    return Provider(soname=soname, version=version, archive_size=size,
                    satisfies=frozenset(satisfies), path=path)


class TestReadDynamicDependencies(unittest.TestCase):
    """Reading DT_SONAME / DT_NEEDED from real ELF fixtures."""

    def _path(self, name):
        return os.path.join(
            os.path.dirname(__file__), 'testfiles_for_unittests', name)

    def test_reads_soname_needed_and_size(self):
        path = self._path('android_dyntags.so.elf')
        info = read_dynamic_dependencies(path)
        self.assertEqual(info.soname, 'libsoundtrigger.so')
        self.assertEqual(info.needed[0], 'libcutils.so')
        self.assertIn('libbinder.so', info.needed)
        self.assertEqual(info.size, os.path.getsize(path))
        self.assertEqual(info.path, path)

    def test_reads_from_open_stream(self):
        path = self._path('lib_versioned64.so.elf')
        with open(path, 'rb') as stream:
            info = read_dynamic_dependencies(stream)
        self.assertEqual(info.soname, 'lib_versioned.so.1')
        self.assertEqual(info.needed, ('libz.so.1', 'libc.so.6'))
        self.assertEqual(info.size, os.path.getsize(path))

    def test_reads_without_section_headers(self):
        path = self._path('aarch64_super_stripped.elf')
        info = read_dynamic_dependencies(path)
        self.assertEqual(info.needed, ('libc.so.6',))

    def test_size_override_for_stream(self):
        path = self._path('lib_versioned64.so.elf')
        with open(path, 'rb') as stream:
            info = read_dynamic_dependencies(stream, size=1234)
        self.assertEqual(info.size, 1234)
        self.assertIsNone(info.path)


class TestProviderValidation(unittest.TestCase):
    def test_rejects_bad_provider_values(self):
        with self.assertRaises(ValueError):
            Provider(soname='', version='1', archive_size=1, satisfies=[])
        with self.assertRaises(ValueError):
            Provider(soname='lib', version='', archive_size=1, satisfies=[])
        with self.assertRaises(ValueError):
            Provider(soname='lib', version='1', archive_size=-1, satisfies=[])

    def test_from_elf_uses_soname_and_size(self):
        path = os.path.join(
            os.path.dirname(__file__), 'testfiles_for_unittests',
            'lib_versioned64.so.elf')
        provider = Provider.from_elf(path, version='1.0')
        self.assertEqual(provider.soname, 'lib_versioned.so.1')
        self.assertEqual(provider.archive_size, os.path.getsize(path))
        self.assertEqual(provider.satisfies, {'lib_versioned.so.1'})

    def test_from_elf_rejects_soname_mismatch(self):
        path = os.path.join(
            os.path.dirname(__file__), 'testfiles_for_unittests',
            'lib_versioned64.so.elf')
        with self.assertRaises(ValueError):
            Provider.from_elf(path, version='1.0', soname='libother.so')


class TestSelectProviders(unittest.TestCase):
    def test_shared_file_counted_once(self):
        providers = [
            _provider('libssl.so.3', '1.1', 100, ['ssl']),
            _provider('libssl.so.3', '3.0', 140, ['ssl']),
            _provider('libcrypto.so.3', '1.1', 80, ['crypto']),
            _provider('libcrypto.so.3', '3.0', 90, ['crypto']),
            _provider('libc.so.6', 'v1', 50, ['c']),
        ]
        result = select_providers([
            FirmwareTarget('fw-a', {'ssl', 'crypto', 'c'}),
            FirmwareTarget('fw-b', {'ssl', 'crypto'}),
        ], providers)
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_size, 230)
        keys = [p.key for p in result.archive]
        self.assertEqual(keys, [
            ('libc.so.6', 'v1'),
            ('libcrypto.so.3', '1.1'),
            ('libssl.so.3', '1.1'),
        ])

    def test_single_version_per_soname_per_firmware(self):
        providers = [
            _provider('libA.so', '1', 10, ['a1', 'a2']),
            _provider('libA.so', '2', 5, ['a1', 'a2']),
        ]
        result = select_providers([
            FirmwareTarget('fw', {'a1', 'a2'})], providers)
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_size, 5)
        used = result.firmwares[0].providers
        self.assertEqual({p.key for p in used}, {('libA.so', '2')})

    def test_different_firmware_may_pick_different_versions(self):
        # No single version satisfies both targets, so the archive has to
        # carry both versions of libA.
        providers = [
            _provider('libA.so', 'old', 10, ['a1']),
            _provider('libA.so', 'new', 100, ['a3']),
        ]
        result = select_providers([
            FirmwareTarget('legacy', {'a1'}),
            FirmwareTarget('modern', {'a3'}),
        ], providers)
        self.assertTrue(result.feasible)
        versions = {
            firmware.firmware:
                {p.version for p in firmware.providers}
            for firmware in result.firmwares}
        self.assertEqual(versions['legacy'], {'old'})
        self.assertEqual(versions['modern'], {'new'})
        self.assertEqual(result.total_size, 110)

    def test_shared_provider_can_avoid_second_purchase(self):
        # fw-a could use libX v1 (10) + nothing else; fw-b needs c2 which
        # libC v2 (102) provides together with c1, avoiding libC v1 (100)
        # plus libD (50) for fw-c.
        providers = [
            _provider('libC.so', '1', 100, ['c1']),
            _provider('libC.so', '2', 102, ['c1', 'c2']),
            _provider('libD.so', '1', 50, ['c2']),
        ]
        result = select_providers([
            FirmwareTarget('a', {'c1'}),
            FirmwareTarget('c', {'c1', 'c2'}),
        ], providers)
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_size, 102)
        self.assertEqual([p.key for p in result.archive],
                         [('libC.so', '2')])

    def test_shared_file_is_counted_once_across_targets(self):
        providers = [
            _provider('libcommon.so', '1', 40, ['common']),
        ]
        result = select_providers([
            FirmwareTarget('one', {'common'}),
            FirmwareTarget('two', {'common'}),
            FirmwareTarget('three', {'common'}),
        ], providers)
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_size, 40)
        self.assertEqual(len(result.archive), 1)

    def test_reports_minimal_conflict(self):
        providers = [
            _provider('libx.so', '1', 10, ['x1']),
            _provider('libx.so', '2', 10, ['x2']),
            _provider('liby.so', '1', 10, ['y1']),
        ]
        result = select_providers([
            FirmwareTarget('good', {'x1', 'y1'}),
            FirmwareTarget('bad', {'x1', 'x2'}),
        ], providers)
        self.assertFalse(result.feasible)
        self.assertEqual(len(result.conflicts), 1)
        conflict = result.conflicts[0]
        self.assertEqual(conflict.firmware, 'bad')
        self.assertEqual(conflict.requirements, {'x1', 'x2'})
        self.assertIn('libx.so', conflict.detail)

    def test_conflict_is_subset_minimal(self):
        providers = [
            _provider('libx.so', '1', 10, ['x1']),
            _provider('libx.so', '2', 10, ['x2']),
            _provider('liby.so', '1', 10, ['y1']),
        ]
        result = select_providers([
            FirmwareTarget('bad', {'x1', 'x2', 'y1'}),
        ], providers)
        self.assertFalse(result.feasible)
        # y1 is satisfiable together with either x requirement, so only the
        # x1/x2 pair is minimal.
        self.assertEqual(result.conflicts[0].requirements, {'x1', 'x2'})

    def test_missing_provider_is_reported(self):
        result = select_providers([
            FirmwareTarget('bad', {'nodep'}),
        ], [])
        self.assertFalse(result.feasible)
        self.assertEqual(result.conflicts[0].requirements, {'nodep'})

    def test_requires_explicit_provider_for_each_requirement(self):
        providers = [_provider('liba.so', '1', 10, ['a'])]
        result = select_providers([FirmwareTarget('t', {'a', 'b'})],
                                  providers)
        self.assertFalse(result.feasible)
        self.assertEqual(result.conflicts[0].requirements, {'b'})

    def test_zero_sized_provider(self):
        providers = [_provider('libz.so', '1', 0, ['z'])]
        result = select_providers([FirmwareTarget('t', {'z'})], providers)
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_size, 0)

    def test_duplicate_provider_definitions_rejected(self):
        providers = [
            _provider('libsame.so', '1', 10, ['r']),
            _provider('libsame.so', '1', 11, ['r']),
        ]
        with self.assertRaises(ValueError):
            select_providers([FirmwareTarget('t', {'r'})], providers)

    def test_duplicate_firmware_names_rejected(self):
        with self.assertRaises(ValueError):
            select_providers([
                FirmwareTarget('t', {'a'}),
                FirmwareTarget('t', {'b'}),
            ], [])

    def test_capability_satisfies_multiple_requirements(self):
        providers = [
            _provider('liball.so', '1', 60, ['a', 'b', 'c']),
            _provider('liba.so', '1', 10, ['a']),
            _provider('libb.so', '1', 10, ['b']),
            _provider('libc.so', '1', 10, ['c']),
        ]
        result = select_providers([
            FirmwareTarget('t', {'a', 'b', 'c'})], providers)
        self.assertTrue(result.feasible)
        # Individual files total 30 and are cheaper than 60.
        self.assertEqual(result.total_size, 30)

    def test_empty_requirements_is_feasible_and_free(self):
        result = select_providers([FirmwareTarget('empty', set())], [])
        self.assertTrue(result.feasible)
        self.assertEqual(result.total_size, 0)
        self.assertEqual(result.archive, ())


class TestModuleAndConfig(unittest.TestCase):
    def _path(self, name):
        return os.path.join(
            os.path.dirname(__file__), 'testfiles_for_unittests', name)

    def test_module_from_elf(self):
        module = Module.from_elf('m', self._path('lib_versioned64.so.elf'))
        self.assertEqual(module.dependencies, {'libz.so.1', 'libc.so.6'})

    def test_module_extra_requirements(self):
        module = Module.from_elf(
            'm', self._path('lib_versioned64.so.elf'),
            extra_requirements=['libextra.so'])
        self.assertIn('libextra.so', module.dependencies)

    def test_firmware_target_from_modules(self):
        target = FirmwareTarget.from_modules(
            'fw',
            [Module('a', {'x'}), Module('b', {'y'})],
            extra_requirements=['z'])
        self.assertEqual(target.requirements, {'x', 'y', 'z'})


class TestLoaders(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='pyelftools-firmware-')

    def tearDown(self):
        for root, _, files in os.walk(self._dir, topdown=False):
            for name in files:
                os.unlink(os.path.join(root, name))
            os.rmdir(root)

    def _write(self, name, data):
        path = os.path.join(self._dir, name)
        with open(path, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        return path

    def test_load_catalog_with_size_and_satisfies(self):
        path = self._write('catalog.json', {
            'providers': [
                {'soname': 'libssl.so.3', 'version': '1.1',
                 'size': 100, 'satisfies': ['ssl', 'tls']},
                {'soname': 'libc.so.6', 'version': 'v1', 'size': 50},
            ]
        })
        providers = load_catalog(path)
        self.assertEqual(len(providers), 2)
        self.assertEqual(providers[0].satisfies, {'ssl', 'tls'})
        self.assertEqual(providers[1].satisfies, {'libc.so.6'})

    def test_load_catalog_from_elf_path(self):
        elf = os.path.join(
            os.path.dirname(__file__), 'testfiles_for_unittests',
            'lib_versioned64.so.elf')
        path = self._write('catalog.json', [
            {'version': '1', 'path': elf},
        ])
        providers = load_catalog(path)
        self.assertEqual(providers[0].soname, 'lib_versioned.so.1')
        self.assertEqual(providers[0].archive_size, os.path.getsize(elf))

    def test_load_catalog_rejects_soname_mismatch(self):
        elf = os.path.join(
            os.path.dirname(__file__), 'testfiles_for_unittests',
            'lib_versioned64.so.elf')
        path = self._write('catalog.json', [
            {'version': '1', 'path': elf, 'soname': 'libwrong.so'},
        ])
        with self.assertRaises(ValueError):
            load_catalog(path)

    def test_load_config_from_modules(self):
        elf = os.path.join(
            os.path.dirname(__file__), 'testfiles_for_unittests',
            'lib_versioned64.so.elf')
        path = self._write('config.json', {
            'modules': {
                'acquire': {'elf': elf, 'dependencies': ['libextra.so']},
                'plain': {'dependencies': ['libplain.so']},
            },
            'firmwares': [
                {'name': 'v1', 'modules': ['acquire', 'plain'],
                 'requirements': ['libfinal.so']},
            ],
        })
        targets = load_config(path)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].requirements, {
            'libz.so.1', 'libc.so.6', 'libextra.so',
            'libplain.so', 'libfinal.so',
        })

    def test_load_config_rejects_unknown_module(self):
        path = self._write('config.json', {
            'modules': {},
            'firmwares': [{'name': 'v1', 'modules': ['missing']}],
        })
        with self.assertRaises(ValueError):
            load_config(path)

    def test_load_config_with_enabled_flags(self):
        path = self._write('config.json', {
            'modules': {
                'a': {'dependencies': ['ra']},
                'b': {'dependencies': ['rb']},
            },
            'targets': [
                {'name': 'v1', 'modules': {'a': True, 'b': False}},
            ],
        })
        targets = load_config(path)
        self.assertEqual(targets[0].requirements, {'ra'})

    def test_load_catalog_accepts_libraries_key(self):
        path = self._write('catalog.json', {
            'libraries': [
                {'soname': 'liba.so', 'version': '1', 'size': 10},
            ]
        })
        providers = load_catalog(path)
        self.assertEqual(providers[0].satisfies, {'liba.so'})


if __name__ == '__main__':
    unittest.main()
