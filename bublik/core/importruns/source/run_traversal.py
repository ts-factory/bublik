# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import datetime
from functools import wraps
import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from bublik.core.checks import check_run_file
from bublik.core.exceptions import (
    RunCompromisedError,
    URLFetchError,
)
from bublik.core.importruns.utils import indicate_collision, runtime
from bublik.core.shortcuts import build_absolute_uri
from bublik.core.url import fetch_url
from bublik.core.utils import create_event, get_import_job_task
from bublik.data.models import EventLog
from bublik.interfaces.celery import tasks


if TYPE_CHECKING:
    from rest_framework.request import Request


def with_path_processing_events(func):
    @wraps(func)
    def wrapper(*args, **options):
        start_time = datetime.now()

        # Log URLs without a training slash are valid, but they don't pass
        # Kerberos authorization check (bug 11109, bug 11168).
        init_url = options['url']
        if not init_url.endswith('/'):
            init_url += '/'
            options['url'] = init_url

        job_id = options['job_id']
        job_task_execution = get_import_job_task(job_id)

        create_event(
            facility=EventLog.FacilityChoices.IMPORTRUNS,
            severity=EventLog.SeverityChoices.INFO,
            msg=f'started processing the path {init_url}',
            job_task_execution=job_task_execution,
        )

        try:
            result = func(*args, **options)
            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.INFO,
                msg=(
                    f'finished processing the path {init_url} '
                    f'-- runtime: {runtime(start_time)} sec'
                ),
                job_task_execution=job_task_execution,
            )
            return result
        except Exception as e:
            # Update exception debug details with init url
            debug_details = getattr(e, 'debug_details', [])
            debug_details.append(f'Init URL: {init_url}')
            e.debug_details = debug_details

            error_data = getattr(e, 'message', type(e).__name__)
            event_msg = (
                f'failed processing the path {init_url} '
                f'-- Error: {error_data} '
                f'-- runtime: {runtime(start_time)} sec'
            )

            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.ERR,
                msg=event_msg,
                job_task_execution=job_task_execution,
            )

            raise

    return wrapper


def with_run_events(gen_func):
    @wraps(gen_func)
    def wrapper(self, *args, **kwargs):
        for run_source_url, error in gen_func(self, *args, **kwargs):
            if error is None:
                create_event(
                    facility=EventLog.FacilityChoices.IMPORTRUNS,
                    severity=EventLog.SeverityChoices.INFO,
                    msg=f'discovered run {run_source_url}',
                    job_task_execution=self.job_task_execution,
                )
            else:
                severity = (
                    EventLog.SeverityChoices.ERR
                    if isinstance(error, URLFetchError)
                    else EventLog.SeverityChoices.WARNING
                )
                error_data = getattr(error, 'message', type(error).__name__)
                create_event(
                    facility=EventLog.FacilityChoices.IMPORTRUNS,
                    severity=severity,
                    msg=f'skipped subpath {run_source_url} -- Error: {error_data}',
                    job_task_execution=self.job_task_execution,
                )
            yield run_source_url, error

    return wrapper


class HTTPDirectoryTraverser:
    def __init__(self, url, job_id):
        super().__init__()
        self.url = url
        self.job_task_execution = get_import_job_task(job_id)

    def __find_runs(self, url):
        html = fetch_url(url, quiet_404=True)

        ast = BeautifulSoup(markup=html, features='html.parser')
        if ast.find(
            lambda t: t.name == 'a'
            and t.string
            and t.string.strip().lower() == 'trc_compromised.js',
        ):
            raise RunCompromisedError

        if check_run_file('meta_data.json', url):
            yield url, None
            return

        # NOTE: the parser relies on links to directories ending with a slash "/"
        for node in ast.find_all(
            lambda t: t.name == 'a'
            and hasattr(t, 'href')
            and not re.match(r'(\./)?\.\./?', t['href'])
            and t['href'].endswith('/'),
        ):
            a_href = node['href'].strip()
            url_next = urljoin(url + '/', a_href)

            try:
                yield from self.__find_runs(url_next)
            except Exception as e:
                yield url_next, e

    @with_run_events
    def find_runs(self):
        yield from self.__find_runs(self.url)


@with_path_processing_events
def schedule_runs(
    request: Request,
    requesting_host: str,
    job_id: int,
    **importruns_params,
):
    tasks_data = []
    spear = HTTPDirectoryTraverser(importruns_params.pop('url'), job_id)

    for run_source_url, error in spear.find_runs():
        if error is not None:
            tasks_data.append(
                {
                    'run_source_url': run_source_url,
                    'celery_task_id': None,
                    'flower': None,
                    'import_log': None,
                },
            )
            continue

        task_id = tasks.importruns.delay(
            requesting_host,
            job_id,
            run_source_url,
            **importruns_params,
        )
        if indicate_collision(str(task_id), run_source_url):
            task_id = tasks.importruns.delay(
                requesting_host,
                job_id,
                run_source_url,
                **importruns_params,
            )
        tasks_data.append(
            {
                'run_source_url': run_source_url,
                'celery_task_id': str(task_id),
                'flower': build_absolute_uri(request, f'flower/task/{task_id}'),
                'import_log': build_absolute_uri(request, f'importlog/{task_id}'),
            },
        )

    return tasks_data
