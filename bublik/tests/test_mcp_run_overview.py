# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""The run overview names everything a comment tool needs."""

from django.test import SimpleTestCase

from bublik.core.run.dto import (
    RunCompromisedDetails,
    RunDetailsResult,
    RunStatsComment,
    RunStatsResult,
    RunStatsValues,
)
from bublik.mcp.run.markdown import render_run_overview


def _stats(**overrides):
    values = dict.fromkeys(
        (
            'passed',
            'failed',
            'passed_unexpected',
            'failed_unexpected',
            'skipped',
            'skipped_unexpected',
            'abnormal',
        ),
        0,
    )
    values.update(overrides)
    return RunStatsValues(**values)


def _node(result_id, test_id, name, node_type='test', comments=(), children=()):
    return RunStatsResult(
        result_id=result_id,
        exec_seqno=result_id,
        parent_id=None,
        type=node_type,
        test_id=test_id,
        test_name=name,
        period='',
        path=['suite', name],
        objective='',
        children=list(children),
        stats=_stats(passed=1),
        comments=list(comments),
    )


def _details():
    return RunDetailsResult(
        project_id=7,
        project_name='proj',
        id=42,
        start=None,
        finish=None,
        duration=None,
        main_package='suite',
        status='DONE',
        status_by_nok='ok',
        compromised=RunCompromisedDetails(
            status=False, comment=None, bug_id=None, bug_url=None
        ),
        conclusion='OK',
        conclusion_reason=None,
        important_tags=[],
        relevant_tags=[],
        branches=[],
        revisions=[],
        labels=[],
        configuration=[],
        special_categories={},
    )


class RunOverviewCommentsTest(SimpleTestCase):
    def test_project_test_and_comment_ids_are_visible(self):
        comment = RunStatsComment(comment_id='910', updated='', serial='0', comment='flaky')
        leaf = _node(200, 55, 'leaf', comments=[comment])
        root = _node(100, 50, 'suite', node_type='pkg', children=[leaf])

        text = render_run_overview(_details(), None, root, None)

        assert '| Project ID | 7 |' in text
        assert '| Result ID | Test ID | Type |' in text
        assert '| 200 | 55 | test | suite / leaf |' in text
        assert '[910] flaky' in text
        assert '`edit_test_comment`' in text

    def test_an_empty_run_keeps_the_column_count(self):
        text = render_run_overview(_details(), None, None, None)

        header = next(line for line in text.splitlines() if line.startswith('| Result ID'))
        empty = next(line for line in text.splitlines() if line.startswith('| - |'))
        assert header.count('|') == empty.count('|')
