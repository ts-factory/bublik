# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import logging
import sys

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from bublik.core.argparse import parser_type_date
from bublik.core.cache import RunCache
from bublik.core.run.actions import prepare_cache_for_completed_run
from bublik.core.utils import get_difference
from bublik.data import models


logger = logging.getLogger('bublik.server')


class Command(BaseCommand):
    help = '''DELETE, CREATE or UPDATE cached data for runs satisfying the given parameters.
              Cached data for managing can be controlled by --data option.
              By default cache action is applyied to all runs.'''

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['delete', 'create', 'update'],
            type=str,
            help='Apply specified action to runs',
        )
        parser.add_argument(
            '-f',
            '--from',
            type=parser_type_date,
            help='Fetch runs at the given date or later',
        )
        parser.add_argument(
            '-t',
            '--to',
            type=parser_type_date,
            help='Fetch runs at the given date or prior',
        )
        parser.add_argument(
            '-i',
            '--id',
            type=int,
            action='append',
            default=[],
            help='Run id or ids separated by a semicolon',
        )
        parser.add_argument(
            '-d',
            '--data',
            type=str,
            action='append',
            choices=RunCache.KEY_DATA_CHOICES,
            default=[],
            help='Key of cached data to delete',
        )
        parser.add_argument(
            '--logger_out',
            type=bool,
            default=False,
            help='Enable logging formatting',
        )

    def handle(self, *args, **options):
        try:
            query = Q()
            run_start = options['from']
            run_finish = options['to']
            run_ids = options['id']
            data = options['data']
            action = options['action']
            logger_out = options['logger_out']

            if run_start:
                query &= Q(start__date__gte=run_start)
            if run_finish:
                query &= Q(finish__date__lte=run_finish)
            if run_ids:
                query &= Q(id__in=run_ids)

            if not query:
                msg = 'No parameters specified.'
                raise CommandError(msg)

            query &= Q(test_run=None)
            runs = models.TestIterationResult.objects.filter(query)

            if not runs:
                msg = 'Runs by specified parameters were not found!'
                if logger_out:
                    logger.error(msg)
                else:
                    self.stdout.write(self.style.ERROR(msg))
                sys.exit(1)

            run_ids_found = list(runs.values_list('id', flat=True))
            diff = get_difference(run_ids, run_ids_found)
            if diff:
                msg = f'Runs with the following ids: {diff} were not found!'
                if logger_out:
                    logger.warning(msg)
                else:
                    self.stdout.write(self.style.WARNING(msg))

            if not data:
                data = list(RunCache.KEY_DATA_CHOICES)

            kwargs = {'data_keys': data}
            for run in runs:
                if action == 'delete':
                    kwargs.update({'run': run})
                    RunCache.delete_data_for_obj(**kwargs)
                elif action == 'create':
                    prepare_cache_for_completed_run(run)
                elif action == 'update':
                    kwargs.update({'run': run})
                    RunCache.delete_data_for_obj(**kwargs)
                    prepare_cache_for_completed_run(run)

            msg = (
                f'Caches for {data} were successfully {action}d '
                f'for runs by ID: {run_ids_found}!'
            )
            if logger_out:
                logger.info(msg)
            else:
                self.stdout.write(self.style.SUCCESS(msg))

        except Exception as e:
            raise CommandError(e) from CommandError
