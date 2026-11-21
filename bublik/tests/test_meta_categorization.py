# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase
from django.utils import timezone

from bublik.core.meta.categorization import categorize_meta
from bublik.core.run.services import RunService
from bublik.data.models import (
    Meta,
    MetaCategory,
    MetaPattern,
    MetaResult,
    Project,
    TestIterationResult,
)


class CategorizeMetaTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='project')
        now = timezone.now()
        self.run = TestIterationResult.objects.create(
            start=now,
            finish=now,
            project=self.project,
        )
        self.notes = MetaCategory.objects.create(
            name='Notes',
            type='comment',
            project=self.project,
        )
        self.ts = MetaCategory.objects.create(
            name='Test Suite',
            type='label',
            project=self.project,
        )
        MetaPattern.objects.create(pattern='^TS_NAME$', category=self.ts)

    def comment_categories(self):
        mr = MetaResult.objects.get(result=self.run, meta__type='comment')
        return list(mr.meta.category.values_list('name', flat=True))

    def test_new_run_comment_is_categorized(self):
        RunService.create_run_comment(self.run.id, 'first')

        assert self.comment_categories() == ['Notes']

    def test_updated_run_comment_is_categorized(self):
        RunService.create_run_comment(self.run.id, 'first')
        RunService.create_run_comment(self.run.id, 'second')

        assert self.comment_categories() == ['Notes']

    def test_patterns_are_matched_against_name(self):
        metas = [
            Meta.objects.create(type='label', name=name, value='v', hash=name)
            for name in ('TS_NAME', 'OTHER')
        ]
        unnamed = Meta.objects.create(type='label', name=None, value='v', hash='unnamed')
        for meta in [*metas, unnamed]:
            categorize_meta(meta)

        assert list(self.ts.metas.values_list('name', flat=True)) == ['TS_NAME']
