# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Interpretica, Unipessoal Lda. All rights reserved.

import re

from django.test import TestCase
from rest_framework import status

from bublik.core.project import ProjectBadgeService
from bublik.data.models import Project


class BadgeSVGTest(TestCase):
    def test_svg_structure(self):
        svg = ProjectBadgeService.render('label', 'value', '#4c1')
        assert '<svg' in svg
        assert '</svg>' in svg
        assert 'label' in svg
        assert 'value' in svg
        assert '#4c1' in svg

    def test_width_grows_with_text(self):
        short = ProjectBadgeService.render('a', 'b', '#4c1')
        long = ProjectBadgeService.render('long label', 'long value', '#4c1')

        def w(s):
            return int(re.search(r'width="(\d+)"', s).group(1))

        assert w(long) > w(short)

    def test_text_width_nonempty(self):
        assert ProjectBadgeService._text_width('hello') > 0

    def test_unknown_char_fallback(self):
        svg = ProjectBadgeService.render('тест', '★', '#555')
        assert '<svg' in svg


class BadgeEndpointTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='test-project')

    def test_badge_returns_svg(self):
        url = f'/api/v2/projects/{self.project.id}/badge/'
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response['Content-Type'] == 'image/svg+xml'

    def test_badge_unknown_project(self):
        response = self.client.get('/api/v2/projects/99999/badge/')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_badge_no_runs(self):
        url = f'/api/v2/projects/{self.project.id}/badge/'
        response = self.client.get(url)
        assert b'no runs' in response.content
