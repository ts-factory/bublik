# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase
from django.utils import timezone

from bublik.core.run.data import get_comments_by_runs
from bublik.core.run.services import RunService
from bublik.core.run.stats import generate_runs_details
from bublik.data.models import Project, TestIterationResult
from bublik.interfaces.api_v2.run.serializers import serialize_run_summary_results


class RunListCommentTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='project')
        now = timezone.now()
        self.commented, self.plain = (
            TestIterationResult.objects.create(start=now, finish=now, project=self.project)
            for _ in range(2)
        )

    def test_comments_are_keyed_by_run(self):
        RunService.create_run_comment(self.commented.id, 'first\nsecond')

        runs = [self.commented, self.plain]

        assert get_comments_by_runs(runs) == {self.commented.id: 'first\nsecond'}
        assert get_comments_by_runs([run.id for run in runs]) == {
            self.commented.id: 'first\nsecond',
        }

    def test_edited_comment_replaces_the_listed_one(self):
        RunService.create_run_comment(self.commented.id, 'first')
        RunService.create_run_comment(self.commented.id, 'second')

        assert get_comments_by_runs([self.commented]) == {self.commented.id: 'second'}

    def test_runs_list_carries_the_comment(self):
        RunService.create_run_comment(self.commented.id, 'first')

        listed = {
            run['id']: run['comment']
            for run in serialize_run_summary_results(
                generate_runs_details([self.commented, self.plain]),
            )
        }

        assert listed == {self.commented.id: 'first', self.plain.id: None}
