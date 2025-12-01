# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
from functools import wraps
import os
import re
import shutil
import tempfile
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
import pendulum

from bublik.core.argparse import (
    parser_type_date,
    parser_type_force,
    parser_type_str_or_none,
    parser_type_url,
)
from bublik.core.checks import check_run_file
from bublik.core.config.services import ConfigServices
from bublik.core.exceptions import (
    ImportrunsError,
    RunAlreadyExistsError,
    RunCompromisedError,
    RunOutsidePeriodError,
    URLFetchError,
)
from bublik.core.importruns import categorization, extract_logs_base
from bublik.core.importruns.source import incremental_import
from bublik.core.importruns.telog import JSONLog
from bublik.core.logging import get_task_or_server_logger
from bublik.core.run.actions import prepare_cache_for_completed_run
from bublik.core.run.metadata import MetaData
from bublik.core.run.objects import add_import_id, add_run_log
from bublik.core.url import fetch_url, save_url_to_dir
from bublik.core.utils import Counter, create_event
from bublik.data.models import EventLog, GlobalConfigs, Project


logger = get_task_or_server_logger()


def runtime(start_time):
    return (datetime.now() - start_time).total_seconds()


def with_import_events(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        run_url = kwargs.get('run_url')
        task_id = kwargs.get('task_id')

        task_msg = f'Celery task ID {task_id}' if task_id else 'No Celery task ID'
        start_time = datetime.now()

        create_event(
            facility=EventLog.FacilityChoices.IMPORTRUNS,
            severity=EventLog.SeverityChoices.INFO,
            msg=f'started import {run_url} -- {task_msg}',
        )

        try:
            run = func(*args, **kwargs)
            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.INFO,
                msg=(
                    f'successful import {run_url} '
                    f'-- run_id={run.id} '
                    f'-- {task_msg} '
                    f'-- runtime: {runtime(start_time)} sec'
                ),
            )
            return run

        except (RunOutsidePeriodError, RunAlreadyExistsError) as re:
            logger.warning(f'{re.message}. Ignoring {run_url}')
            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.WARNING,
                msg=(
                    f'failed import {run_url} '
                    f'-- {task_msg} '
                    f'-- Error: {re.message} '
                    f'-- runtime: {runtime(start_time)} sec'
                ),
            )
            return None

        except Exception as e:
            error_data = getattr(e, 'message', type(e).__name__)
            logger.error(
                f'Importruns failed: {error_data}',
                exc_info=e,
            )
            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.ERR,
                msg=(
                    f'failed import {run_url} '
                    f'-- {task_msg} '
                    f'-- Error: {error_data} '
                    f'-- runtime: {runtime(start_time)} sec'
                ),
            )
            return None

    return wrapper


class HTTPDirectoryTraverser:
    def __init__(self, url, task_msg, start_time=None):
        super().__init__()

        self.url = url
        self.task_msg = task_msg
        self.start_time = start_time

    def __find_runs(
        self,
        url,
    ):
        try:
            html = fetch_url(url, quiet_404=True)
            if not html:
                raise URLFetchError

            ast = BeautifulSoup(markup=html, features='html.parser')
            if ast.find(
                lambda t: t.name == 'a' and t.string.strip().lower() == 'trc_compromised.js',
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
        except (URLFetchError, RunCompromisedError) as e:
            event_msg = f'failed import {url} -- {self.task_msg} -- Error: {e.message}'
            if self.start_time:
                event_msg += f' -- runtime: {runtime(self.start_time)} sec'

            severity = (
                EventLog.SeverityChoices.ERR
                if isinstance(e, URLFetchError)
                else EventLog.SeverityChoices.WARNING
            )
            logger_func = logger.error if isinstance(e, URLFetchError) else logger.warning
            logger_func(f'Importruns failed: {e.message}. Ignoring: {url}')

            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=severity,
                msg=event_msg,
            )

            return

    def find_runs(self):
        yield from self.__find_runs(self.url)


class Command(BaseCommand):
    help = 'Import test runs stored on a remote server to the local database'

    def add_arguments(self, parser):
        parser.add_argument(
            '-f',
            '--from',
            type=parser_type_date,
            default=datetime.min,
            help='Fetch logs created at the given date or later',
        )
        parser.add_argument(
            '-t',
            '--to',
            type=parser_type_date,
            default=datetime.max,
            help='Fetch logs created at the given date or prior',
        )
        parser.add_argument(
            '--task_id',
            type=str,
            help='Log id determing the server logfile',
        )
        parser.add_argument(
            'url',
            type=parser_type_url,
            help='URL of the logs storage directory',
        )
        parser.add_argument(
            '--force',
            type=parser_type_force,
            default=False,
            help='Re-import the run over the existing one',
        )
        parser.add_argument(
            '--project_name',
            type=parser_type_str_or_none,
            default=None,
            choices=[*Project.objects.values_list('name', flat=True), None],
            help='The name of the project or None (default)',
        )

    @with_import_events
    def import_run(
        self,
        run_url,
        force,
        task_id=None,
        date_from=None,
        date_to=None,
        project_name=None,
    ):
        project = Project.objects.get(name=project_name) if project_name is not None else None
        process_dir = None

        try:
            # Create temp dir for logs processing
            process_dir = tempfile.mkdtemp()

            logger.info(f'downloading and parsing meta_data at {process_dir=}')

            # Fetch meta_data.json if available
            meta_data_saved = save_url_to_dir(run_url, process_dir, 'meta_data.json')

            # Fetch available logs, convert and load JSON log
            log_files = [
                'bublik.json',
                'bublik.xml',
                'log.json.xz',
                'log.xml.xz',
                'raw_log_bundle.tpxz',
            ]
            log_file = next(
                (f for f in log_files if save_url_to_dir(run_url, process_dir, f)),
                None,
            )
            if log_file:
                args = (process_dir, log_file) if log_file == 'bublik.json' else (process_dir,)
                json_data = JSONLog().convert_from_dir(*args)
                logger.info(f'run logs were downloaded from {os.path.join(run_url, log_file)}')
            else:
                json_data = None
                logger.warning('no logs were downloaded')

            if meta_data_saved:
                # Load meta_data.json
                meta_data = MetaData.load(os.path.join(process_dir, 'meta_data.json'), project)
            else:
                if project is None:
                    error_msg = (
                        'meta_data.json not found. You must either add meta_data.json '
                        'to the run source directory or specify the project so that '
                        'meta_data.json can be generated using the FILES_TO_GENERATE_METADATA '
                        'from the corresponding main project configuration.'
                    )
                    raise ImportrunsError(
                        message=error_msg,
                    )

                # Save to process dir available files for generating metadata
                files_to_try = ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'FILES_TO_GENERATE_METADATA',
                    project_id=project.id,
                )
                for filename in files_to_try:
                    filename_saved = save_url_to_dir(run_url, process_dir, filename)
                    if filename_saved:
                        logger.info(f'Save {filename} for generating metadata')

                # Generate meta_data.json from available data
                meta_data = MetaData.generate(process_dir, project.name)

            project = meta_data.project if project is None else project
            logger.info(f'the project name is {project.name}')

            # Extract logs base
            logger.info('downloading run logs: %s', run_url)
            logs_base, suffix_url = extract_logs_base(run_url, project.id)
            if not suffix_url:
                error_msg = (
                    'run URL doesn\'t match any of the logs bases URIs specified '
                    'in the project\'s references configuration'
                )
                raise ImportrunsError(
                    message=error_msg,
                )

            # Filter out runs that don't fit the specified interval
            if not meta_data.check_run_period(date_from, date_to):
                msg = (
                    'run isn\'t satisfy '
                    f'the period {date_from.to_date_string()} - '
                    f'{date_to.to_date_string()}'
                )
                raise RunOutsidePeriodError(
                    message=msg,
                )

            if meta_data_saved:
                run_completed = check_run_file(
                    ConfigServices.getattr_from_global(
                        GlobalConfigs.PER_CONF.name,
                        'RUN_COMPLETE_FILE',
                        project.id,
                    ),
                    run_url,
                    logger,
                )
            else:
                # Always count Logs without meta_data.json as completed
                run_completed = True

            # Import run incrementally
            run, created = incremental_import(
                json_data,
                project.id,
                meta_data,
                run_completed,
                force,
            )

            if not created:
                msg = f'run already exists -- run_id={run.id}'
                raise RunAlreadyExistsError(
                    message=msg,
                )

            add_import_id(run, task_id)
            add_run_log(run, suffix_url, logs_base)
            categorization.categorize_metas(meta_data=meta_data, project_id=project.id)
            prepare_cache_for_completed_run(run)

            logger.info(f'run id is {run.id}')

            return run

        finally:
            # Cleanup temporary dir
            if process_dir and os.path.isdir(process_dir):
                try:
                    shutil.rmtree(process_dir)
                except OSError as e:
                    logger.error(
                        f'[Importruns] Failed to remove {process_dir=} ({e.strerror})',
                    )

    def handle(self, *args, **options):
        start_time = datetime.now()
        counter = Counter()

        # Log URLs without a training slash are valid, but they don't pass
        # Kerberos authorization check (bug 11109, bug 11168).
        init_url = options['url']
        if not init_url.endswith('/'):
            init_url += '/'

        force = options['force']
        task_id = options['task_id']
        task_msg = f'Celery task ID {task_id}' if task_id else 'No Celery task ID'

        spear = HTTPDirectoryTraverser(init_url, task_msg=task_msg, start_time=start_time)

        create_event(
            facility=EventLog.FacilityChoices.IMPORTRUNS,
            severity=EventLog.SeverityChoices.INFO,
            msg=f'started processing the path {init_url} -- {task_msg}',
        )

        # Max out the given dates to make them inclusive
        date_from = pendulum.instance(datetime.combine(options['from'], datetime.min.time()))
        date_to = pendulum.instance(datetime.combine(options['to'], datetime.max.time()))

        for run_url in spear.find_runs():
            self.import_run(
                run_url=run_url,
                force=force,
                task_id=options['task_id'],
                date_from=date_from,
                date_to=date_to,
                project_name=options['project_name'],
            )
            counter.increment()
        create_event(
            facility=EventLog.FacilityChoices.IMPORTRUNS,
            severity=EventLog.SeverityChoices.INFO,
            msg=f'finished processing the path {init_url} '
            f'-- {task_msg} '
            f'-- {counter.counter} sessions were processed '
            f'-- runtime: {runtime(start_time)} sec',
        )
        logger.info(f'completed in [{datetime.now() - start_time}]')
