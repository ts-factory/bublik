# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from bublik.core.argparse import parser_type_date
from bublik.core.meta.categorization import get_metas_by_category
from bublik.core.run.objects import set_run_status
from bublik.core.utils import get_difference
from bublik.data import models


def define_run_status(run):
    '''Function to define existing run status'''

    category_names = ['DL', 'DU', 'Status', 'Notes']
    run_meta_results = run.meta_results.select_related('meta')
    metas_by_category = get_metas_by_category(
        run_meta_results,
        category_names,
        run.project.id,
    )

    meta_values_by_category = {}
    for category, metas in metas_by_category.items():
        # These categories have by one meta only
        meta_value = metas[0]['value'] if metas else None
        meta_values_by_category[category] = meta_value

    status = meta_values_by_category['Status']
    driver_load = meta_values_by_category['DL']
    driver_unload = meta_values_by_category['DU']
    meta_values_by_category['Notes']

    def run_running():
        return status == 'RUNNING'

    def run_warning():
        return status == 'STOPPED'

    def run_error():
        status_norm = ['STOPPED', 'RUNNING', 'OK', 'DONE']
        driver_norm = ['OK', 'REUSE', '-', None]

        return (
            status not in status_norm
            or driver_load not in driver_norm
            or driver_unload not in driver_norm
        )

    # Regarding to runs statuses specification in run_status_default()
    # from bublik/core/run/objects.py

    if run_running():
        return 'RUN_STATUS_RUNNING'

    if run_warning():
        return 'RUN_STATUS_WARNING'

    if run_error():
        return 'RUN_STATUS_ERROR'

    return 'RUN_STATUS_DONE'


class Command(BaseCommand):
    help = '''Reset existing run statuses to the one of the following values:
              ['DONE', 'ERROR', 'WARNING', 'RUNNING']
              for runs satisfying the given parameters.'''

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        query = Q()
        run_start = options['from']
        run_finish = options['to']
        run_ids = options['id']

        # Runs filtering
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
        runs = models.TestIterationResult.objects.select_related('project').filter(query)
        runs = runs.filter_by_run_classification(['notcompromised'])

        if not runs:
            msg = 'Runs by specified parameters were not found.'
            raise CommandError(msg)

        run_ids_found = list(runs.values_list('id', flat=True))
        diff = get_difference(run_ids, run_ids_found)
        if diff:
            msg = f'Runs with the following ids: {diff} were not found.'
            raise CommandError(msg)

        # Update status metas for non compromised runs only
        for run in runs:
            status_key = define_run_status(run)
            set_run_status(run, status_key)

        msg = self.style.SUCCESS(
            f'Run statuses were successfully updated for runs by ID: {run_ids_found}!',
        )
        self.stdout.write(msg)
