# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import logging

from django.db.models import F

from bublik.core.config.services import ConfigServices
from bublik.core.queries import get_or_none
from bublik.core.run.tests_organization import get_run_root
from bublik.core.utils import get_local_log
from bublik.data.models import GlobalConfigs, Meta, MetaResult


logger = logging.getLogger('bublik.server')


class ImportMode:
    SOURCE = 'source'
    LIVE = 'live'


def extract_logs_base(run_url):
    logs_bases = ConfigServices.getattr_from_global(
        GlobalConfigs.REFERENCES.name,
        'LOGS_BASES',
    )
    for logs_base in logs_bases:
        for uri in logs_base['uri']:
            if run_url.startswith(uri):
                return logs_base, run_url.replace(uri, '')
    return None, None


def identify_run(key_metas):
    run_id_sets = []

    for key_meta in key_metas:
        meta = get_or_none(Meta.objects, **key_meta)
        if not meta:
            return None

        run_id_sets.append(
            set(
                MetaResult.objects.filter(result__test_run=None, meta=meta).values_list(
                    F('result__id'),
                    flat=True,
                ),
            ),
        )

    if not run_id_sets:
        return None

    run = run_id_sets.pop()
    for run_id_set in run_id_sets:
        run &= run_id_set

    if not run:
        return None
    return run.pop()


def get_import_log_for_run(run_id):
    """
    Finds import log of a certain run.

    Returns absolute path to the log or None if run was imported directly
    on the server via manage.py, so has no logfile.

    Raises RunTimeError if:
    - run by ID passed wasn't found,
    - it has no meta representing import log or
    """

    run = get_run_root(run_id)
    if not run:
        msg = f"Run by id {run_id} wasn't found"
        raise RuntimeError(msg)

    import_meta_result = get_or_none(
        run.meta_results,
        meta__name='import_id',
        meta__type='import',
    )
    if not import_meta_result:
        msg = f'Run by id {run_id} has no import meta'
        raise RuntimeError(msg)

    if not import_meta_result.meta.value:
        return None

    filename = import_meta_result.meta.value
    return get_local_log(filename)
