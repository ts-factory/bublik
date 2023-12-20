# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import logging

from django.db import transaction

from bublik.core.run.stats import get_run_stats_detailed


logger = logging.getLogger('bublik.server')


@transaction.atomic
def prepare_cache_for_completed_run(run):
    if run.finish:
        try:
            warn_msg = 'unable to prepare run data'
            get_run_stats_detailed(run.id)
        except Exception as e:
            logger.warning(f'{warn_msg}: {e}')
