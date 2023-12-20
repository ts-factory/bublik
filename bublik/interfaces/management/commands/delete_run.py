# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from bublik.core.argparse import parser_type_date
from bublik.data import models


class Command(BaseCommand):
    help = '''COMPLETELY DELETE all runs satisfying the given parameters.
              This action CANNOT be UNDONE. Related cache will be also deleted.'''

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
            type=str,
            help='Run id or ids separated by a semicolon',
        )

    def handle(self, *args, **options):
        '''Delete only runs, not any test iteration result.'''

        try:
            query = Q()
            run_start = options['from']
            run_finish = options['to']
            run_ids = options['id']

            if run_start:
                query &= Q(start__date__gte=run_start)
            if run_finish:
                query &= Q(finish__date__lte=run_finish)
            if run_ids:
                run_ids = run_ids.split(settings.QUERY_DELIMITER)
                query &= Q(id__in=run_ids)

            if not query:
                msg = 'No parameters specified.'
                raise CommandError(msg)

            query &= Q(test_run=None)
            runs = models.TestIterationResult.objects.filter(query)
            runs_deleted = runs.count()
            res = runs.delete()

            if res[0] == 0:
                msg = self.style.WARNING('Runs by specified parameters were not found.')
            else:
                msg = self.style.SUCCESS(f'Successfully deleted! ({runs_deleted})\n')
                msg += f'Details: {res}'

            self.stdout.write(msg)

        except Exception as e:
            raise CommandError(e) from CommandError
