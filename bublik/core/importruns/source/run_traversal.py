# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import datetime
from functools import wraps
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from bublik.core.checks import check_run_file
from bublik.core.exceptions import (
    RunCompromisedError,
    URLFetchError,
)
from bublik.core.importruns.import_run import import_run
from bublik.core.importruns.utils import runtime
from bublik.core.logging import get_task_or_server_logger
from bublik.core.url import fetch_url
from bublik.core.utils import Counter, create_event
from bublik.data.models import EventLog


logger = get_task_or_server_logger()


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

        task_id = options['task_id']
        task_msg = f'Celery task ID {task_id}' if task_id else 'No Celery task ID'

        create_event(
            facility=EventLog.FacilityChoices.IMPORTRUNS,
            severity=EventLog.SeverityChoices.INFO,
            msg=f'started processing the path {init_url} -- {task_msg}',
        )

        counter = Counter()
        try:
            for _run_url in func(*args, **options):
                counter.increment()
        except (URLFetchError, RunCompromisedError) as e:
            # Update exception debug details with init url
            debug_details = getattr(e, 'debug_details', [])
            debug_details.append(f'Init URL: {init_url}')
            e.debug_details = debug_details

            event_msg = (
                f'failed import {init_url} '
                f'-- {task_msg} '
                f'-- Error: {e.message} '
                f'-- runtime: {runtime(start_time)} sec'
            )

            severity = (
                EventLog.SeverityChoices.ERR
                if isinstance(e, URLFetchError)
                else EventLog.SeverityChoices.WARNING
            )
            logger_func = logger.error if isinstance(e, URLFetchError) else logger.warning
            logger_func(f'Importruns failed: {e.message}. Ignoring: {init_url}', exc_info=e)

            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=severity,
                msg=event_msg,
            )
        finally:
            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.INFO,
                msg=(
                    f'finished processing the path {init_url} '
                    f'-- {task_msg} '
                    f'-- {counter.counter} sessions were processed '
                    f'-- runtime: {runtime(start_time)} sec'
                ),
            )
            logger.info(f'completed in [{datetime.now() - start_time}]')

    return wrapper


class HTTPDirectoryTraverser:
    def __init__(self, url):
        super().__init__()
        self.url = url

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
            yield url
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

            yield from self.__find_runs(url_next)

    def find_runs(self):
        yield from self.__find_runs(self.url)


@with_path_processing_events
def schedule_runs(
    task_id: str | None = None,
    **importruns_params,
):
    spear = HTTPDirectoryTraverser(importruns_params.pop('url'))
    for run_url in spear.find_runs():
        import_run(
            run_url=run_url,
            **importruns_params,
            task_id=task_id,
        )
        yield run_url
