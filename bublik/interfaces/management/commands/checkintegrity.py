# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import defaultdict
from datetime import datetime
from functools import reduce
import os
import re
from tempfile import mkstemp

from django.core.management.base import BaseCommand

from bublik.core.argparse import parser_type_date, parser_type_url
from bublik.core.logging import get_task_or_server_logger
from bublik.core.run.stats import get_run_stats_detailed
from bublik.core.url import fetch_url, save_url_to_fd
from bublik.data.models import Meta, TestIterationResult

from .importruns import ConverterError, ConverterLogJSON, HTTPDirectoryTraverser


logger = get_task_or_server_logger()


class HandlerLogGist(ConverterLogJSON):
    def __init__(self, path_raw_log):
        super().__init__(path_raw_log)

        self.date_start = self.__rundate2date(self.log_json['start_ts'])
        self.date_finish = self.__rundate2date(self.log_json['end_ts'])
        self.tags = self.log_json['tags']

    def __rundate2date(self, str_date):
        try:
            return datetime.strptime(str_date, '%Y.%m.%d %H:%M:%S %f ms')
        except ValueError:
            pass

        return None

    def find_node(self, predicate):
        def spear(iterations, predicate):
            for iteration in iterations:
                if predicate(iteration):
                    yield iteration

                if 'iters' in iteration:
                    yield from spear(iteration['iters'], predicate)

        yield from spear(self.log_json['iters'], predicate)

    def __del__(self):
        super().__del__()


class TRCStats:
    class Node(dict):
        def __init__(self, tree, total):
            super().__init__(tree)

            self.total = total

    KEYS_MAP = {
        'run': (
            'Run (total)',
            {
                'passed_expected': 'Passed, as expected',
                'failed_expected': 'Failed, as expected',
                'passed_unexpected': 'Passed unexpectedly',
                'failed_unexpected': 'Failed unexpectedly',
                'aborted': 'Aborted (no useful result)',
                'new': 'New (expected result is not known)',
            },
        ),
        'not_run': (
            'Not Run (total)',
            {
                'skipped_expected': 'Skipped, as expected',
                'skipped_unexpected': 'Skipped unexpectedly',
            },
        ),
    }

    def __init__(self):
        super().__init__()

        self.statistics = defaultdict(list)

    def __getitem__(self, key_root):
        key_root_text, subtree_keys = self.KEYS_MAP.get(key_root, (None, []))
        if not key_root_text:
            return {}

        subtree = next(
            ((k, v) for k, v in self.statistics.items() if k[0] == key_root_text),
            None,
        )
        if not subtree:
            return {}
        subtree_key, subtree = subtree[0], subtree[1]

        subtree = {[i for i, j in subtree_keys.items() if j == k][0]: v for k, v in subtree}

        return TRCStats.Node(subtree, int(subtree_key[1]))


class ParserTRCStats:
    def __init__(self, data):
        super().__init__()

        self.data = data
        self.statistics = TRCStats()

    def parse(self):
        self.statistics = TRCStats()
        key_current = None
        for line in self.data.splitlines():
            line = line.rstrip()

            m = re.search(r'^(?P<spaces>\s*)(?P<hint>.+?)\s{2,}(?P<data>\d+)$', line)
            if not m:
                continue

            hint, data, spaces = (
                m.group('hint'),
                int(m.group('data')),
                m.group('spaces'),
            )

            if not spaces:
                key_current = (hint, data)
            else:
                self.statistics.statistics[key_current].append((hint, data))

        return self.statistics


class Command(BaseCommand):
    help = 'Match TRC statistics against the contents of the database'

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
            'url',
            type=parser_type_url,
            help='URL of the logs storage directory',
        )

    def __log_on_diff(self, total_first, total_second, method, category, verb):
        if total_first != total_second:
            method(
                'discrepency detected in the total amount of tests %s (%s / TRC stats): '
                '%d != %d',
                verb,
                category,
                total_first,
                total_second,
            )
            return False

        return True

    def __verify_parsed_log(self, stats_trc, log_gist):
        nb_total, nb_passed, nb_failed = [0] * 3
        for testrun in log_gist.find_node(lambda i: not i.get('package', None)):
            nb_total += 1

            if not testrun.get('start_ts', None) and not testrun.get('end_ts', None):
                continue

            if 'result' in testrun:
                result = testrun['result'].lower()

                if result == 'passed':
                    nb_passed += 1
                elif result == 'failed':
                    nb_failed += 1

        return (
            self.__log_on_diff(
                nb_total,
                stats_trc['run'].total,
                logger.warning,
                'log stats',
                'run',
            )
            and self.__log_on_diff(
                nb_passed,
                stats_trc['run']['passed_expected'] + stats_trc['run']['passed_unexpected'],
                logger.warning,
                'logs stats',
                'passed',
            )
            and self.__log_on_diff(
                nb_failed,
                stats_trc['run']['failed_expected'] + stats_trc['run']['failed_unexpected'],
                logger.warning,
                'logs stats',
                'failed',
            )
        )

    def __verify_database(self, stats_trc, log_gist):
        testruns = TestIterationResult.objects.filter(
            test_run=None,
            start__date__exact=log_gist.date_start,
            finish__date__exact=log_gist.date_finish,
        )

        testrun = None
        for run in testruns:
            tags = Meta.objects.filter(metaresult__result=run, type='tag')

            if len(log_gist.tags) != len(tags):
                continue

            if reduce(
                lambda acc, t: acc
                and t.name in log_gist.tags
                and log_gist.tags[t.name] == t.value,
                tags,
                True,
            ):
                testrun = run
                break

        if not testrun:
            logger.error('unable to find the downloaded log in the database')
            return False

        stats_run = get_run_stats_detailed(testrun.id)

        nb = {
            'total': 0,
            'passed': 0,
            'failed': 0,
            'passed_unexpected': 0,
            'failed_unexpected': 0,
        }
        for package in stats_run['results']:
            for field in nb:
                nb[field] += package['tests_run'][field]

        rc = self.__log_on_diff(
            nb['total'],
            stats_trc['run'].total,
            logger.error,
            'database stats',
            'total',
        )
        rc &= self.__log_on_diff(
            nb['passed'],
            stats_trc['run']['passed_expected'],
            logger.error,
            'database stats',
            'passed',
        )
        rc &= self.__log_on_diff(
            nb['passed_unexpected'],
            stats_trc['run']['passed_unexpected'],
            logger.error,
            'database stats',
            'passed unexpected',
        )
        rc &= self.__log_on_diff(
            nb['failed'],
            stats_trc['run']['failed_expected'],
            logger.error,
            'database stats',
            'failed',
        )
        rc &= self.__log_on_diff(
            nb['failed_unexpected'],
            stats_trc['run']['failed_unexpected'],
            logger.error,
            'database stats',
            'failed unexpected',
        )
        return rc

    def handle(self, *args, **options):
        spear = HTTPDirectoryTraverser(options['url'])

        # Max out the given date to make it inclusive
        options['to'] = datetime.combine(options['to'], datetime.max.time())

        for ast, _node, url_node in spear.find_files(
            options['from'],
            options['to'],
            ['trc-stats.txt'],
        ):
            run_url = os.path.split(url_node)[0]

            if ast.find(
                lambda t: t.name == 'a' and t.string.strip().lower() == 'trc_compromised.js',
            ):
                continue

            logger.info('downloading run: %s', run_url)

            data_trc_stats = fetch_url(url_node)
            parser_trc_stats = ParserTRCStats(data_trc_stats)

            stats_trc = parser_trc_stats.parse()
            logger.debug('Run: %d', stats_trc['run'].total)
            logger.debug('Passed expected: %d', stats_trc['run']['passed_expected'])
            logger.debug('Failed expected: %d', stats_trc['run']['failed_expected'])
            logger.debug('Passed unexpected: %d', stats_trc['run']['passed_unexpected'])
            logger.debug('Failed unexpected: %d', stats_trc['run']['failed_unexpected'])
            logger.debug('Aborted: %d', stats_trc['run']['aborted'])
            logger.debug('New: %d', stats_trc['run']['new'])
            logger.debug('Not run: %d', stats_trc['not_run'].total)
            logger.debug('Skipped expected: %d', stats_trc['not_run']['skipped_expected'])
            logger.debug('Skipped unexpected: %d', stats_trc['not_run']['skipped_unexpected'])

            url_log_gist = os.path.join(run_url, 'log_gist.raw')

            fd_raw_log, path_raw_log = mkstemp()
            save_url_to_fd(url_log_gist, fd_raw_log)
            os.close(fd_raw_log)

            try:
                log_gist = HandlerLogGist(path_raw_log)
            except ConverterError as e:
                logger.error('%s (file %s)', e, path_raw_log)
                break

            os.unlink(path_raw_log)

            if not log_gist.date_start or not log_gist.date_finish:
                logger.error(
                    'invalid dates detected, start_ts=%s; end_ts=%s',
                    log_gist.date_start,
                    log_gist.date_finish,
                )
                break

            if not self.__verify_parsed_log(stats_trc, log_gist):
                logger.warning(
                    'parsing the run log and reading the TRC stats file yielded '
                    'different results',
                )
            if not self.__verify_database(stats_trc, log_gist):
                logger.error('the database and the TRC stats file hold different values')
