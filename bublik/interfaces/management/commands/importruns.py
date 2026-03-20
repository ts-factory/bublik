# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime

from django.core.management.base import BaseCommand

from bublik.core.argparse import (
    parser_type_date,
    parser_type_force,
    parser_type_str_or_none,
    parser_type_url,
)
from bublik.core.importruns.import_run import import_run
from bublik.core.importruns.source.run_traversal import (
    HTTPDirectoryTraverser,
    with_path_processing_events,
)
from bublik.data.models import Project


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

    @with_path_processing_events
    def handle(self, *args, **options):
        spear = HTTPDirectoryTraverser(options['url'])

        for run_url in spear.find_runs():
            import_run(
                run_url=run_url,
                project_name=options['project_name'],
                date_from=options['from'],
                date_to=options['to'],
                force=options['force'],
                task_id=options['task_id'],
            )
            yield run_url
