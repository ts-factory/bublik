# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import SimpleTestCase

from bublik.core.report.dto import RunReportConfigDTO
from bublik.core.run.dto import RunDetailsResult, RunStatsResult, RunStatsValues
from bublik.mcp.run.markdown import render_run_overview


def _run_details() -> RunDetailsResult:
    return RunDetailsResult(
        project_id=1,
        project_name='proj',
        id=15079,
        start=None,
        finish=None,
        duration=None,
        main_package='pkg',
        status='DONE',
        status_by_nok='NOK',
        compromised=None,
        conclusion='run-ok',
        conclusion_reason=None,
        important_tags=[],
        relevant_tags=[],
        branches=[],
        revisions=[],
        labels=[],
        special_categories=[],
        configuration=None,
    )


def _run_stats() -> RunStatsResult:
    return RunStatsResult(
        result_id=10,
        exec_seqno=1,
        parent_id=None,
        type='pkg',
        test_id=1,
        test_name='pkg',
        period='',
        path=['pkg'],
        objective='',
        children=[],
        stats=RunStatsValues(
            passed=1,
            failed=0,
            passed_unexpected=0,
            failed_unexpected=0,
            skipped=0,
            skipped_unexpected=0,
            abnormal=0,
        ),
        comments=[],
    )


class RenderRunOverviewReportConfigsTests(SimpleTestCase):
    def test_renders_report_config_dtos(self):
        """
        The report service returns RunReportConfigDTO dataclasses; the overview
        must render them by attribute rather than treating them as dicts.
        """
        report_configs = [
            RunReportConfigDTO(
                id=7,
                name='perf',
                version=2,
                project=1,
                description='Perf report',
            ),
            RunReportConfigDTO(id=9, name='func', version=1, project=1, description=''),
        ]

        output = render_run_overview(
            _run_details(),
            'src',
            _run_stats(),
            None,
            report_configs=report_configs,
        )

        self.assertIn('## Report Configurations', output)
        self.assertIn('| 7 | perf | Perf report |', output)
        self.assertIn('| 9 | func | - |', output)

    def test_omits_section_without_report_configs(self):
        for report_configs in (None, []):
            with self.subTest(report_configs=report_configs):
                output = render_run_overview(
                    _run_details(),
                    'src',
                    _run_stats(),
                    None,
                    report_configs=report_configs,
                )

                self.assertNotIn('## Report Configurations', output)
