# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from argparse import ArgumentTypeError
from datetime import datetime

from django.core.management.base import BaseCommand

from bublik.core.argparse import parser_type_date, parser_type_tags
from bublik.core.logging import get_task_or_server_logger
from bublik.core.run.filter_expression import TestRunMetasGroup
from bublik.core.testing_coverage import TestingCoverage


logger = get_task_or_server_logger()


class Command(BaseCommand):
    help = 'Utility to generate testing coverage report'

    def __init__(self):
        super().__init__()

        self.tag_groups = []
        self.package_ignore = []

    def add_arguments(self, parser):
        def parser_package_ignore(package):
            self.package_ignore.append(package)
            return self.package_ignore

        def parser_tags_group(tags):
            self.tag_groups.append(TestRunMetasGroup(parser_type_tags(tags)))
            return self.tag_groups

        parser.add_argument(
            '-f',
            '--from',
            type=parser_type_date,
            default=None,
            help='Process test runs at the given date or later',
        )
        parser.add_argument(
            '-t',
            '--to',
            type=parser_type_date,
            default=datetime.today(),
            help='Process test runs at the given date or prior',
        )
        parser.add_argument(
            '-T',
            '--tags',
            type=parser_type_tags,
            default=[],
            help='Tags which differentiate test runs',
        )
        parser.add_argument(
            '-g',
            '--tags-group',
            type=parser_tags_group,
            default=None,
            help='Tags group which considered to be a one '
            'meta tag in relation to test runs '
            'differentiation',
        )
        parser.add_argument(
            '-s',
            '--package-strip',
            default=None,
            help='Drop the substring from test packages path',
        )
        parser.add_argument(
            '-i',
            '--package-ignore',
            type=parser_package_ignore,
            default=None,
            help='Ignore test packages which begin with the line',
        )
        parser.add_argument(
            '-l',
            '--pdf-table-lines',
            type=int,
            default=20,
            help='PDF report option: maximum lines number in table on a page.',
        )
        parser.add_argument(
            '-w',
            '--pdf-table-first-column-width',
            type=int,
            default=7,
            help='PDF report option: width if the first column in centimeters.',
        )
        parser.add_argument(
            '-I',
            '--run-ignore-label-substring',
            type=str,
            default=None,
            help='Ignore a test run if it has `label` with the specified substring',
        )
        parser.add_argument(
            '-d',
            '--debug',
            action='store_true',
            default=False,
            help='Dump links to trc-brief.html for all test runs sorted by tags.',
        )

    def handle(self, *args, **options):
        logger.info('Begin')

        if not options['from']:
            msg = 'Date `from` is not specified!'
            raise ArgumentTypeError(msg)

        # Add tags from groups to the tags list to get complete list.
        tags = options['tags']
        for group in self.tag_groups:
            for tag in group.tags:
                if tag not in tags:
                    tags.append(tag)

        coverage = TestingCoverage(
            tags,
            self.tag_groups,
            options['package_strip'],
            self.package_ignore,
            options['debug'],
        )
        coverage.get(options['from'], options['to'], 5, options['run_ignore_label_substring'])
        coverage.generate_pdf(
            options['pdf_table_lines'],
            options['pdf_table_first_column_width'],
        )

        logger.info('The end')
