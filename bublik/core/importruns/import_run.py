# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import datetime
from functools import wraps
import os
import shutil
import tempfile

import pendulum

from bublik.core.checks import check_run_file
from bublik.core.config.services import ConfigServices
from bublik.core.exceptions import (
    ImportrunsError,
    RunAlreadyExistsError,
    RunOutsidePeriodError,
)
from bublik.core.importruns import categorization, extract_logs_base
from bublik.core.importruns.source import incremental_import
from bublik.core.importruns.telog import JSONLog
from bublik.core.importruns.utils import runtime
from bublik.core.logging import get_task_or_server_logger
from bublik.core.run.actions import prepare_cache_for_completed_run
from bublik.core.run.metadata import MetaData
from bublik.core.run.objects import add_import_id, add_run_log
from bublik.core.url import save_url_to_dir
from bublik.core.utils import create_event
from bublik.data.models import EventLog, GlobalConfigs, Project


logger = get_task_or_server_logger()


def with_import_events(func):
    @wraps(func)
    def wrapper(*args, **kwargs):

        task_id = kwargs.get('task_id')
        run_url = kwargs.get('run_url')

        task_msg = f'Celery task ID {task_id}'
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
            # Update exception debug details with run source url
            debug_details = getattr(e, 'debug_details', [])
            debug_details.append(f'Run source URL: {run_url}')
            e.debug_details = debug_details

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


@with_import_events
def import_run(
    task_id: str,
    run_url: str,
    project_name: str | None = None,
    date_from: datetime = datetime.min,
    date_to: datetime = datetime.max,
    force: bool = False,
):
    date_from = pendulum.instance(datetime.combine(date_from, datetime.min.time()))
    date_to = pendulum.instance(datetime.combine(date_to, datetime.max.time()))

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
            logs_bases = ConfigServices.getattr_from_global(
                GlobalConfigs.REFERENCES.name,
                'LOGS_BASES',
                project.id,
            )
            allowed_uris = {uri for logs_base in logs_bases for uri in logs_base.get('uri', [])}
            debug_details = [f'Allowed URIs: {allowed_uris}']
            raise ImportrunsError(
                message=error_msg,
                debug_details=debug_details,
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
