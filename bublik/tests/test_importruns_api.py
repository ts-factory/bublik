# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import logging
import os
from pprint import pformat

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.core.shortcuts import serialize
from bublik.data import models
from bublik.data.serializers import MetaSerializer
from bublik.interfaces.celery.tasks import get_or_create_task_logger


logger = logging.getLogger()


class ImportrunsTest(APITestCase):
    def test_import_from_source(self):
        """Call run import from source."""

        endpoint = reverse('importruns-source')
        logger.info('TEST: (GET) /importruns/source/?url=')
        response = self.client.get(endpoint, {'url': ''})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        logger.info('PASSED')

        logger.info('TEST: (POST) /importruns/source/?url=')
        response = self.client.post(f'{endpoint}?url=')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        logger.info('PASSED')

    def test_life_import(self):
        """Perform 'init' and 'feed' stages of life import."""

        endpoint = reverse('importruns-init')
        logger.info('TEST: (POST) /importruns/init/')
        response = self.client.post(endpoint, {})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        logger.info('PASSED')

        endpoint = reverse('importruns-feed')
        logger.info('TEST: (POST) /importruns/feed/?run=1')
        response = self.client.post(f'{endpoint}?run=1')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        logger.info('PASSED')

    def test_getting_import_logs(self):
        """Get importruns log for a certain run."""

        import_id = 'test'
        logger, logpath = get_or_create_task_logger(import_id)
        logger.info(f'Create test log: {logpath}')
        open(logpath, 'w+').close()

        try:
            run = models.TestIterationResult.objects.create(start=datetime.now(), finish=None)
            logger.info(f'Create run (id={run.id})')

            m_data = dict(name='import_id', type='import', value=import_id)
            m_serializer = serialize(MetaSerializer, m_data, logger)
            meta, _ = m_serializer.get_or_create()
            logger.info(f'Create import meta: {pformat(m_data)}')

            models.MetaResult.objects.get_or_create(result=run, meta=meta)
            logger.info('Create meta result with these run and meta')

            endpoint = reverse('importruns-logs')
            logger.info(f'TEST: (GET) /importruns/logs/?run={run.id}')
            response = self.client.get(endpoint, {'run': run.id})

        finally:
            os.remove(logpath)
            logger.info(f'Remove test log: {logpath}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        logger.info('PASSED')
