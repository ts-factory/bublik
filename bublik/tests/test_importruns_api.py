# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import json
from pathlib import Path
import tempfile
from unittest import mock

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.core.cache import RunCache
from bublik.data import models
from bublik.tests.fake_generator import gen_test_iteration_result_simple


VIEWS = 'bublik.interfaces.api_v2.importruns.views'
LOGS_BASE = 'https://logs.example.com/'


class ImportFromSourceTest(APITestCase):
    def setUp(self):
        models.Project.objects.create(name='test')
        patcher = mock.patch('bublik.interfaces.api_v2.importruns.serializers.ConfigServices')
        patcher.start().getattr_from_global.return_value = [{'uri': [LOGS_BASE]}]
        self.addCleanup(patcher.stop)

    def source(self, url):
        return self.client.get(reverse('api-v2:importruns-source'), {'url': url})

    def test_url_outside_logs_bases_is_rejected(self):
        response = self.source('https://elsewhere.example.com/run/')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'url' in response.data['messages']

    def test_import_is_scheduled_as_a_job(self):
        url = f'{LOGS_BASE}run/'
        with mock.patch(f'{VIEWS}.schedule_runs', return_value=[]) as schedule_runs:
            response = self.source(url)
        assert response.status_code == status.HTTP_200_OK
        job = models.ImportJob.objects.get(id=response.data['job_id'])
        assert job.url == url
        assert job.finished_at is not None
        assert schedule_runs.call_args.kwargs['url'] == url


class LiveImportTest(APITestCase):
    def post(self, name, body, **params):
        return self.client.post(
            reverse(f'api-v2:importruns-{name}'),
            body,
            content_type='application/json',
            query_params=params,
        )

    def test_init_rejects_malformed_json(self):
        response = self.post('init', '{')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {'message': 'malformed JSON'}

    def test_init_requires_heartbeat_interval(self):
        response = self.post('init', '{}')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {'message': 'heartbeat interval is required'}

    def test_feed_of_unknown_session_is_rejected(self):
        run = gen_test_iteration_result_simple('2026.01.01')
        del RunCache.by_id(run.id, 'livelog').data
        response = self.post('feed', '[]', run=run.id)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {'message': 'unknown session'}


class ImportLogTest(APITestCase):
    def test_log_lines_are_returned_as_json(self):
        line = {'asctime': '2026-01-01 00:00:00,000', 'levelname': 'INFO'}
        line.update({'module': 'importruns', 'message': 'imported'})
        with tempfile.TemporaryDirectory() as tmp:
            logpath = Path(tmp, 'task')
            logpath.write_text(f'{json.dumps(line)}\nnot json\n')
            with mock.patch(f'{VIEWS}.get_local_log', return_value=logpath) as get_local_log:
                response = self.client.get(
                    reverse('api-v2:importruns-log'), {'task_id': 'task'}
                )
        get_local_log.assert_called_once_with('task')
        assert response.status_code == status.HTTP_200_OK
        assert response.data[0] == line
        assert response.data[1]['message'] == 'not json'
