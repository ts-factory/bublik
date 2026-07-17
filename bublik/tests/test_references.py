# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from unittest import mock

from django.test import SimpleTestCase

from bublik.core.config.services import ConfigServices
from bublik.core.references import REF_PATTERN, resolve_ref


TRACKERS = {'JIRA': {'uri': 'https://jira.example.com/browse/'}}


class RefPatternTest(SimpleTestCase):
    def _refs(self, text):
        return [match.groups() for match in REF_PATTERN.finditer(text)]

    def test_tracker_ends_at_the_first_slash(self):
        assert self._refs('ref://JIRA/PROJ/123') == [('JIRA', 'PROJ/123')]

    def test_key_keeps_url_characters(self):
        assert self._refs('ref://GH/org/repo#12') == [('GH', 'org/repo#12')]
        assert self._refs('ref://BZ/123?comment=3') == [('BZ', '123?comment=3')]

    def test_refs_are_separated_by_spaces_and_commas(self):
        assert self._refs('see ref://JIRA/A-1, ref://BZ/2 and text') == [
            ('JIRA', 'A-1'),
            ('BZ', '2'),
        ]


class ResolveRefTest(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.object(
            ConfigServices, 'getattr_from_global', return_value=TRACKERS
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_known_tracker_resolves_to_url(self):
        assert resolve_ref('ref://JIRA/PROJ/123') == (
            'JIRA',
            'PROJ/123',
            'https://jira.example.com/browse/PROJ/123',
        )

    def test_unknown_tracker_has_no_url(self):
        assert resolve_ref('ref://BZ/1') == ('BZ', '1', None)

    def test_not_a_ref(self):
        assert resolve_ref('JIRA-1') is None
        assert resolve_ref('ref://JIRA/A-1 and more') is None
