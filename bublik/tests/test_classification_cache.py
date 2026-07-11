# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.core.cache import RunCache
from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class InvalidateClassificationAffectedTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        project = models.Project.objects.create(name='proj')
        cls.run_row = data.run(project)
        cls.other_run = data.run(project)

    def _fill(self, run):
        for data_key in RunCache.KEY_DATA_CHOICES:
            RunCache.by_obj(run, data_key).data = data_key

    def _cached(self, run):
        return {
            data_key
            for data_key in RunCache.KEY_DATA_CHOICES
            if RunCache.by_obj(run, data_key).data is not None
        }

    def test_drops_only_what_classification_affects_of_the_given_runs(self):
        self._fill(self.run_row)
        self._fill(self.other_run)
        RunCache.invalidate_classification_affected([self.run_row.id])
        assert self._cached(self.run_row) == RunCache.KEY_DATA_CHOICES - (
            RunCache.KEYS_CLASSIFICATION_AFFECTED
        )
        assert self._cached(self.other_run) == RunCache.KEY_DATA_CHOICES
