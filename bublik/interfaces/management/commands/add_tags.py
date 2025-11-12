# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
'''
This command can be called to add tags to:
- specified by id run;
- runs in a specific time range;
- runs with specific Meta in format <meta_name>=<meta_value>
    (Note! you can forward only meta_name if meta_value is None);

The log_base parameter is required because DB saves only the second part of the
path to the log. In order to get logs - the url base of log storage is needed.
'''

from datetime import datetime, timedelta
import os
import shutil
import tempfile

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
import pytz

from bublik.core.argparse import parser_type_date
from bublik.core.importruns.telog import JSONLog
from bublik.core.logging import get_task_or_server_logger
from bublik.core.run.objects import add_tags
from bublik.core.url import save_url_to_dir
from bublik.data.models import MetaResult, TestIterationResult


logger = get_task_or_server_logger()


def _get_json_data(url: str) -> dict:
    '''This methods tries to fetch logs from url.'''
    process_dir = None

    try:
        # Create a temporary dir for logs
        process_dir = tempfile.mkdtemp()
        logger.debug(f'Created a temporary dir {process_dir} to store logs.')

        # Fetch available logs
        if (
            not save_url_to_dir(url, process_dir, 'bublik.xml')
            and not save_url_to_dir(url, process_dir, 'log.json.xz')
            and not save_url_to_dir(url, process_dir, 'log.xml.xz')
        ):
            save_url_to_dir(url, process_dir, 'raw_log_bundle.tpxz')

        return JSONLog().convert_from_dir(process_dir)
    except AttributeError:
        logger.warning(f'An error occurred while parsing log {url}')
    finally:
        # Cleanup temporary dir
        if process_dir and os.path.isdir(process_dir):
            try:
                shutil.rmtree(process_dir)
                logger.debug('Removing a temporary dir created for log processing.')
            except OSError as e:
                logger.error(f'[Importruns] Failed to remove {process_dir}=({e.strerror})')


class Command(BaseCommand):
    help = 'Adds tags to the run. \nThe log_base is a required option!'

    def add_arguments(self, parser):
        parser.add_argument(
            'log_base',
            type=str,
            help='Required! The base url to get logs from.',
        )
        parser.add_argument(
            '-r',
            '--run_ids',
            action='store',
            nargs='+',
            type=int,
            default=None,
            help='The run_ids that tags will be updated to. You can forward '
            "several ids here like this '--run_ids 1 2 3'",
        )
        parser.add_argument(
            '--from',
            type=parser_type_date,
            default=datetime.now() - timedelta(days=30),
            help='Update runs at the given date or later. The format is YYYY.MM.DD',
        )
        parser.add_argument(
            '--to',
            type=parser_type_date,
            default=datetime.now(),
            help='Update runs at the given date or earlier. The format is YYYY.MM.DD',
        )
        parser.add_argument(
            '--meta',
            type=str,
            default=None,
            help='Update runs with the given meta. Example: --meta test=exp.'
            'Where test is Meta.name and exp is Meta.value.'
            'You can leave value empty if value is None.',
        )

    @staticmethod
    def process_log_and_update_tags(run: TestIterationResult, log_base_url: str) -> None:
        '''
        This method parses the log.json, gets tags from there and adds it to
        the given run.
        '''
        try:
            log_meta = MetaResult.objects.get(meta__type='log', result=run.pk).meta
        except ObjectDoesNotExist:
            logger.info(
                f'Logs for run {run.pk} does not exist. Probably this is not'
                "a run but one of the run's results.",
            )
            raise ObjectDoesNotExist from ObjectDoesNotExist

        log_url = log_base_url + log_meta.value

        # Get log.json
        json_data = _get_json_data(log_url)

        try:
            logger.info(f'Adding tags for run {run.pk}.')
            add_tags(run=run, tags=json_data.get('tags'))
        except AttributeError:
            logger.info('Something went wrong when trying to update tags.')
            return

    def handle(self, *args, **options) -> None:
        logger.info('Running a command to add tags.')
        start_time = datetime.now(tz=pytz.timezone(settings.TIME_ZONE))

        # Get the base url that is the same for all runs
        log_base_url = options['log_base']

        if options['run_ids']:
            # Process tags update by given run ids

            # Filter the run by input run ids
            runs = list(TestIterationResult.objects.filter(pk__in=options['run_ids']))

        else:
            # Process logs for several runs

            # Only filter the TestIterationResults that represent runs
            # (they do not have a test_run ForeignKey)
            runs = TestIterationResult.objects.filter(test_run=None)

            # Format dates considering timezone
            from_date = datetime.combine(
                date=options['from'].date(),
                time=options['from'].time(),
                tzinfo=pytz.timezone(settings.TIME_ZONE),
            )
            to_date = datetime.combine(
                date=options['to'].date(),
                time=options['to'].time(),
                tzinfo=pytz.timezone(settings.TIME_ZONE),
            )

            logger.debug(f'The starting date of updating tags is {from_date}.')
            logger.debug(f'The finishing date of updating tags is {to_date}.')

            runs = runs.filter_runs_by_date(from_d=from_date, to_d=to_date)

            if options['meta']:
                logger.info(
                    f"Updating tags for runs associated with Meta: {options['meta']}.",
                )
                runs = runs.filter_by_run_metas([options['meta']])

            if not runs:
                logger.warning('No runs were found with specified options.')
                return

        for run in runs:
            lp_start_time = datetime.now(tz=pytz.timezone(settings.TIME_ZONE))
            try:
                Command.process_log_and_update_tags(run=run, log_base_url=log_base_url)
            except ObjectDoesNotExist:
                continue
            logger.info(
                f'Adding tags for run {run.pk} is completed in '
                f'{datetime.now(tz=pytz.timezone(settings.TIME_ZONE)) - lp_start_time}',
            )

        logger.info(
            f'Completed in {datetime.now(tz=pytz.timezone(settings.TIME_ZONE)) - start_time}.',
        )
