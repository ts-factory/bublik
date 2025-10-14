# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import logging
import os
import re
import shutil
import tempfile
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
import pendulum

from bublik.core.argparse import (
    parser_type_date,
    parser_type_force,
    parser_type_project,
    parser_type_url,
)
from bublik.core.checks import check_run_file, modify_msg
from bublik.core.config.services import ConfigServices
from bublik.core.importruns import categorization, extract_logs_base
from bublik.core.importruns.source import incremental_import
from bublik.core.importruns.telog import JSONLog
from bublik.core.run.actions import prepare_cache_for_completed_run
from bublik.core.run.metadata import MetaData
from bublik.core.run.objects import add_import_id, add_run_log
from bublik.core.url import fetch_url, save_url_to_dir
from bublik.core.utils import Counter, create_event
from bublik.data.models import EventLog, GlobalConfigs, Project


logger = logging.getLogger('bublik.server')


def runtime(start_time):
    return (datetime.now() - start_time).total_seconds()


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
        html = fetch_url(url, quiet_404=True)
        if not html:
            logger.error(f'HTTP error occurred, ignoring: {url}')
            if self.start_time:
                msg = (
                    f'failed import {url} '
                    f'-- {self.task_msg} '
                    f'-- Error: HTTP error occurred '
                    f'-- runtime: {runtime(self.start_time)} sec'
                )
            else:
                msg = f'failed import {url} -- {self.task_msg} -- Error: HTTP error occurred'
            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.ERR,
                msg=msg,
            )
            return

        ast = BeautifulSoup(markup=html, features='html.parser')
        if ast.find(
            lambda t: t.name == 'a' and t.string.strip().lower() == 'trc_compromised.js',
        ):
            logger.info(f'run compromised, ignoring: {url}')
            return

        if check_run_file('meta_data.txt', url):
            yield url
            return

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
            '--id',
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
            type=parser_type_project,
            help='The name of the project',
        )

    def import_run(
        self,
        run_url,
        force,
        run_id=None,
        date_from=None,
        date_to=None,
        project_name=None,
    ):
        import_run_start_time = datetime.now()
        task_msg = f'Celery task ID {run_id}' if run_id else 'No Celery task ID'
        create_event(
            facility=EventLog.FacilityChoices.IMPORTRUNS,
            severity=EventLog.SeverityChoices.INFO,
            msg=f'started import {run_url} -- {task_msg}',
        )

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
                    logger.error(
                        'the --project_name import argument is required '
                        'when meta_data.json is not available',
                    )
                    return

                # Save to process dir available files for generating metadata
                files_to_try = ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'FILES_TO_GENERATE_METADATA',
                    project_id=project.id,
                    default=['meta_data.txt'],
                )
                for filename in files_to_try:
                    filename_saved = save_url_to_dir(run_url, process_dir, filename)
                    if filename_saved:
                        logger.info(f'Save {filename} for generating metadata')

                # Generate meta_data.json from available data
                meta_data = MetaData.generate(process_dir, project.name)

            project = meta_data.project if project is None else project
            logger.info(f'the project name is {project.name}')

            # Check whether the RUN_COMPLETE_FILE attribute exists
            try:
                ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'RUN_COMPLETE_FILE',
                    project.id,
                )
            except (ObjectDoesNotExist, KeyError) as e:
                msg = modify_msg(
                    str(e),
                    run_url,
                )
                logger.error(msg)
                return

            # Extract logs base
            logger.info('downloading run logs: %s', run_url)
            logs_base, suffix_url = extract_logs_base(run_url, project.id)
            if not suffix_url:
                logger.error(
                    f'run url doesn\'t matched project references, ignoring: {run_url}',
                )
                create_event(
                    facility=EventLog.FacilityChoices.IMPORTRUNS,
                    severity=EventLog.SeverityChoices.ERR,
                    msg=f'failed import {run_url} '
                    f'-- {task_msg} '
                    f'-- Error: URL doesn\'t match project references '
                    f'-- runtime: {runtime(import_run_start_time)} sec',
                )
                return

            # Filter out runs that don't fit the specified interval
            if not meta_data.check_run_period(date_from, date_to):
                logger.debug(
                    'run isn\'t satisfy '
                    f'the period {date_from.to_date_string()} - '
                    f'{date_to.to_date_string()}, ignoring: {run_url}',
                )
                create_event(
                    facility=EventLog.FacilityChoices.IMPORTRUNS,
                    severity=EventLog.SeverityChoices.WARNING,
                    msg=f'failed import {run_url} '
                    f'-- {task_msg} '
                    f'-- Error: run doesn\'t satisfy time period '
                    f'-- runtime: {runtime(import_run_start_time)} sec',
                )
                return

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
            logger.info('the process of incremental import of run logs is started')
            start_time = datetime.now()
            run = incremental_import(json_data, project.id, meta_data, run_completed, force)
            logger.info(
                f'the process of incremental import of run logs is completed in ['
                f'{datetime.now() - start_time}]',
            )

            if run:
                logger.info('the process of adding import id is started')
                start_time = datetime.now()
                add_import_id(run, run_id)
                logger.info(
                    f'the process of adding import id is completed in ['
                    f'{datetime.now() - start_time}]',
                )

                logger.info('the process of adding run log is started')
                start_time = datetime.now()
                add_run_log(run, suffix_url, logs_base)
                logger.info(
                    f'the process of adding run log is completed in ['
                    f'{datetime.now() - start_time}]',
                )

                categorization.categorize_metas(meta_data=meta_data, project_id=project.id)

                logger.info('the process of preparing cache for complited run is started')
                start_time = datetime.now()
                prepare_cache_for_completed_run(run)
                logger.info(
                    f'the process of preparing cache for complited run is completed in ['
                    f'{datetime.now() - start_time}]',
                )

                logger.info(f'run id is {run.id}')
                create_event(
                    facility=EventLog.FacilityChoices.IMPORTRUNS,
                    severity=EventLog.SeverityChoices.INFO,
                    msg=f'successful import {run_url} '
                    f'-- run_id={run.id} '
                    f'-- {task_msg} '
                    f'-- runtime: {runtime(import_run_start_time)} sec',
                )
            else:
                create_event(
                    facility=EventLog.FacilityChoices.IMPORTRUNS,
                    severity=EventLog.SeverityChoices.WARNING,
                    msg=f'session logs weren\'t processed {run_url} '
                    f'-- {task_msg} '
                    f'-- runtime: {runtime(import_run_start_time)} sec',
                )
                logger.info("run logs weren't processed")

        except Exception as e:
            create_event(
                facility=EventLog.FacilityChoices.IMPORTRUNS,
                severity=EventLog.SeverityChoices.ERR,
                msg=f'failed import {run_url} '
                f'-- {task_msg} '
                f'-- Error: {e!s} '
                f'-- runtime: {runtime(import_run_start_time)} sec',
            )
            logger.error(str(e))

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
        task_msg = f'Celery task ID {options["id"]}' if options['id'] else 'No Celery task ID'

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
                run_url,
                force,
                run_id=options['id'],
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
