# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from django.shortcuts import get_object_or_404

from bublik.core.config.services import ConfigServices
from bublik.core.queries import get_or_none
from bublik.core.run.tests_organization import get_run_root
from bublik.data import models
from bublik.data.models import GlobalConfigs


def get_sources(result, source=None):
    """
    Param @result can be either TestIterationResult object or ID
    of a run itself or any test result.

    Returns run's source link.
    """

    try:
        run = get_run_root(result)
        if not run:
            return None

        log = run.meta_results.filter(meta__type='log').order_by('id').last()

        if not log:
            # Backward compatibility for bug 11190.
            main_package = get_or_none(
                models.TestIterationResult.objects,
                test_run=run,
                parent_package__isnull=True,
            )
            if main_package:
                log = main_package.meta_results.filter(meta__type='log').order_by('id').last()

        if log and log.reference:
            project_id = run.project.id
            references_logs_bases = ConfigServices.getattr_from_global(
                GlobalConfigs.REFERENCES.name,
                'LOGS_BASES',
                project_id,
            )
            log_base = next(
                (
                    ref_lb['uri'][-1]
                    for ref_lb in references_logs_bases
                    if ref_lb['name'] == log.reference.name and ref_lb['uri']
                ),
                log.reference.uri,
            )
            source_tail = log.meta.value
            if log_base.endswith('/') or source_tail.startswith('/'):
                return f'{log_base.rstrip("/")}/{source_tail.lstrip("/")}'
            return log_base + source_tail

        return None

    except Exception:
        # TODO: Should be writen to a runtime debug logger
        return None


def get_result_log(result):
    '''
    Returns result source link to log.

    Param @result can be either TestIterationResult object or ID
    of a run itself or any test result.
    '''

    try:
        run_source_link = get_sources(result)
        if not run_source_link:
            return None

        if not isinstance(result, models.TestIterationResult):
            result = get_object_or_404(models.TestIterationResult, id=result)

        if not result.test_run:
            # The root TIR's link is a link to all logs of its run
            html_tail = 'html/node_1_0.html'
        else:
            html_tail = f'html/node_id{result.exec_seqno!s}.html'

        return os.path.join(run_source_link, html_tail)

    except Exception:
        # TODO: Should be writen to a runtime debug logger
        return None


def get_trc_brief(result):
    '''
    Returns result source link to trc-brief.
    '''

    try:
        run_source_link = get_sources(result)
        if not run_source_link:
            return None
        return os.path.join(run_source_link, 'trc-brief.html')
    except Exception:
        # TODO: Should be writen to a runtime debug logger
        return None
