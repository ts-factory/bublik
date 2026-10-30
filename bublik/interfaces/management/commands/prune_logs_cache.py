# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand

from bublik.core.log.s3_logs import cache_root, cache_ttl, prune_cache


class Command(BaseCommand):
    help = (
        'Delete the local cache folders of S3-stored runs (downloaded bundles and '
        'the log pages generated from them) unused for LOGS_S3_CACHE_TTL seconds. '
        'Requests already do this in the background every few minutes; this is '
        'for cron, or to clear the cache now with --ttl 0. Nothing in S3 is touched.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--ttl',
            type=int,
            default=None,
            help='Seconds of disuse after which a folder goes (default: LOGS_S3_CACHE_TTL)',
        )

    def handle(self, *args, **options):
        ttl = cache_ttl() if options['ttl'] is None else options['ttl']
        pruned = prune_cache(ttl=ttl)
        self.stdout.write(f'Pruned {pruned} run folder(s) from {cache_root()}')
