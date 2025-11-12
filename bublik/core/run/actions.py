# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.


from django.db import transaction

from bublik.core.logging import get_task_or_server_logger
from bublik.core.run.stats import get_run_stats_detailed


logger = get_task_or_server_logger()


@transaction.atomic
def prepare_cache_for_completed_run(run):
    if run.finish:
        try:
            warn_msg = 'unable to prepare run data'
            get_run_stats_detailed(run.id)
        except Exception as e:
            logger.warning(f'{warn_msg}: {e}')
