# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict, defaultdict
from unittest import mock

from django.test import SimpleTestCase

from bublik.core.history.services import HistoryService


RESULTS = [
    {'id': 1, 'run_id': 10, 'iteration_id': 100, 'has_error': False},
    {'id': 2, 'run_id': 10, 'iteration_id': 200, 'has_error': True},
    {'id': 3, 'run_id': 20, 'iteration_id': 100, 'has_error': True},
]

PARAMETERS = defaultdict(OrderedDict)
PARAMETERS[100].update({'env': 'VM', 'mtu': '1500'})
PARAMETERS[200].update({'env': 'IUT', 'mtu': '9000'})

VERDICTS = defaultdict(list)
VERDICTS[2].append('Packet was lost')
VERDICTS[3].append('Timeout expired')


@mock.patch.multiple(
    'bublik.core.history.services',
    get_tags_by_runs=mock.Mock(
        return_value=({10: ['linux'], 20: ['freebsd']}, {10: ['kernel=6.1'], 20: []}),
    ),
    get_results=mock.Mock(return_value={1: 'PASSED', 2: 'FAILED', 3: 'KILLED'}),
    get_verdicts=mock.Mock(return_value=VERDICTS),
    get_parameters_by_iterations=mock.Mock(return_value=PARAMETERS),
    get_metadata_by_runs=mock.Mock(return_value={10: ['branch=main'], 20: ['branch=dev']}),
)
class HistorySearchTestCase(SimpleTestCase):
    def _ids(self, search):
        data, counts, runs_ids, iterations_ids, results_ids = (
            HistoryService.prepare_results_data(RESULTS, search)
        )
        self.assertEqual({r['id'] for r in data['test_results']}, results_ids)
        self.assertEqual(counts['total_results'], len(results_ids))
        self.assertEqual(counts['runs'], len(runs_ids))
        self.assertEqual(counts['iterations'], len(iterations_ids))
        return results_ids

    def test_no_search_returns_all(self):
        assert self._ids('') == {1, 2, 3}
        assert self._ids(None) == {1, 2, 3}
        assert self._ids('   ') == {1, 2, 3}

    def test_search_by_parameter_key_value(self):
        assert self._ids('mtu=9000') == {2}
        assert self._ids('env=vm') == {1, 3}

    def test_search_by_verdict_case_insensitive(self):
        assert self._ids('LOST') == {2}

    def test_search_by_tags_and_metadata(self):
        assert self._ids('kernel=6') == {1, 2}
        assert self._ids('freebsd') == {3}
        assert self._ids('branch=dev') == {3}

    def test_search_by_result_type(self):
        assert self._ids('killed') == {3}

    def test_search_no_match(self):
        data, counts, *_ = HistoryService.prepare_results_data(RESULTS, 'nothing-like-this')
        assert data['test_results'] == []
        assert counts == {
            'runs': 0,
            'iterations': 0,
            'total_results': 0,
            'expected_results': 0,
            'unexpected_results': 0,
        }

    def test_counts_after_search(self):
        _, counts, *_ = HistoryService.prepare_results_data(RESULTS, 'env=vm')
        assert counts['expected_results'] == 1
        assert counts['unexpected_results'] == 1
