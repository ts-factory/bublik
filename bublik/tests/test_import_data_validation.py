# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import SimpleTestCase

from bublik.core.exceptions import InvalidImportDataError
from bublik.core.importruns.import_run import (
    MAX_REPORTED_FRAGMENT_LENGTH,
    MAX_REPORTED_SCHEMA_ERRORS,
    validate_import_data,
)
from bublik.data.schemas.services import load_schema


def make_iteration(iteration_type, name, test_id, **fields):
    return {
        'type': iteration_type,
        'name': name,
        'params': {},
        'hash': '',
        'tin': -1,
        'test_id': test_id,
        'objective': None,
        'reqs': [],
        'err': None,
        'obtained': {'result': {'status': 'PASSED'}},
        'start_ts': '2026.03.29 01:00:00.000',
        'end_ts': '2026.03.29 01:00:01.000',
        'iters': [],
        **fields,
    }


def make_run_log(entries):
    test = make_iteration(
        'test',
        'throughput',
        2,
        hash='0123456789abcdef0123456789abcdef',
        tin=42,
        params={'mtu': '1500'},
        measurements=[
            {
                'type': 'measurement',
                'version': 1,
                'results': [{'type': 'throughput', 'entries': entries}],
            },
        ],
    )
    return {'iters': [make_iteration('pkg', 'net-drv-ts', 1, iters=[test])]}


def make_entry(value):
    return {'aggr': 'mean', 'base_units': 'bps', 'multiplier': '1', 'value': value}


class ValidateImportDataTest(SimpleTestCase):
    def setUp(self):
        self.schema = load_schema('run_log')

    def get_debug_details(self, instance):
        with self.assertRaises(InvalidImportDataError) as cm:
            validate_import_data(instance, self.schema, 'invalid run log format')
        assert cm.exception.message == 'invalid run log format'
        return cm.exception.debug_details

    def get_fragment(self, details):
        start = next(i for i, line in enumerate(details) if line == 'Data fragment:')
        end = next(
            (i for i in range(start + 1, len(details)) if not details[i].startswith('  ')),
            len(details),
        )
        return details[start + 1 : end]

    def test_valid_data(self):
        validate_import_data(make_run_log([make_entry(1.5)]), self.schema, 'invalid')
        validate_import_data(None, self.schema, 'invalid')

    def test_violation_is_located(self):
        details = self.get_debug_details(make_run_log([make_entry(1.5), make_entry(None)]))
        assert details[0] == "None is not of type 'number', 'string'"
        assert details[1] == 'Iteration: net-drv-ts/throughput (TIN 42)'
        assert details[2] == (
            'Element: measurements #1 (measurement) > results #1 (throughput) '
            '> entries #2 > value'
        )

    def test_iteration_violation_has_no_element(self):
        run_log = make_run_log([make_entry(1.5)])
        del run_log['iters'][0]['params']
        details = self.get_debug_details(run_log)
        assert details[1] == 'Iteration: net-drv-ts (TIN -1)'
        assert not any(line.startswith('Element') for line in details)

    def test_fragment_marks_violation(self):
        details = self.get_debug_details(make_run_log([make_entry(1.5), make_entry(None)]))
        fragment = self.get_fragment(details)
        entry = '{"aggr": "mean", "base_units": "bps", "multiplier": "1", "value": %s}'
        assert '      "type": "measurement",' in fragment
        assert f'            {entry % "1.5"},' in fragment
        assert f'            {entry % "null"}  <-- violates the schema' in fragment
        assert sum('violates the schema' in line for line in fragment) == 1

    def test_fragment_marks_line_containing_violation(self):
        entry = make_entry(1.5)
        del entry['multiplier']
        details = self.get_debug_details(make_run_log([entry]))
        assert details[0] == "'multiplier' is a required property"
        fragment = self.get_fragment(details)
        assert (
            '        {"type": "throughput", "entries": '
            '[{"aggr": "mean", "base_units": "bps", "value": 1.5}]}  <-- violates the schema'
        ) in fragment

    def test_fragment_does_not_include_child_iterations(self):
        run_log = make_run_log([make_entry(1.5)])
        del run_log['iters'][0]['params']
        details = self.get_debug_details(run_log)
        fragment = self.get_fragment(details)
        assert '    "name": "net-drv-ts",' in fragment
        assert '    "iters": ["<1 child iterations>"]' in fragment
        assert not any('throughput' in line for line in fragment)

    def test_fragment_is_limited(self):
        entries = [make_entry(1.5) for _ in range(200)]
        entries[-1]['value'] = None
        details = self.get_debug_details(make_run_log(entries))
        fragment = self.get_fragment(details)
        assert sum(len(line) - 1 for line in fragment) <= MAX_REPORTED_FRAGMENT_LENGTH
        assert fragment == [
            (
                '  {"aggr": "mean", "base_units": "bps", "multiplier": "1", "value": null}'
                '  <-- violates the schema'
            ),
        ]

    def test_other_violations_are_limited(self):
        entries = [make_entry(None) for _ in range(MAX_REPORTED_SCHEMA_ERRORS + 3)]
        details = self.get_debug_details(make_run_log(entries))
        others = details.index(f'Other violations: {MAX_REPORTED_SCHEMA_ERRORS + 2}')
        location = (
            '  net-drv-ts/throughput (TIN 42), '
            'measurements #1 (measurement) > results #1 (throughput) > entries #'
        )
        message = " > value: None is not of type 'number', 'string'"
        for line in details[others + 1 : others + 1 + MAX_REPORTED_SCHEMA_ERRORS]:
            assert line.startswith(location)
            assert line.endswith(message)
        assert details[others + 1 + MAX_REPORTED_SCHEMA_ERRORS :] == ['  ...']

    def test_violation_outside_iterations(self):
        details = self.get_debug_details({'iters': 'not a list'})
        assert details[1] == 'Element: iters'
        assert not any(line.startswith('Iteration') for line in details)

    def test_violation_of_whole_data(self):
        details = self.get_debug_details([])
        assert details[1] == 'Element: the whole data'
