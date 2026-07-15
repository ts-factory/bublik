# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase
from django.utils import timezone

from bublik.core.exceptions import NotFoundError
from bublik.core.run.services import RunService
from bublik.data import models


class RunLookupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        project = models.Project.objects.create(name='proj')
        cls.run_row = models.TestIterationResult.objects.create(
            start=now,
            finish=now,
            project=project,
        )
        cls.result = models.TestIterationResult.objects.create(
            start=now,
            finish=now,
            project=project,
            test_run=cls.run_row,
        )

    def test_run_is_found(self):
        assert RunService.get_run(self.run_row.id) == self.run_row

    def test_result_is_not_a_run(self):
        with self.assertRaises(NotFoundError):
            RunService.get_run(self.result.id)
