# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.shortcuts import get_object_or_404

from bublik.core.config.services import ConfigServices
from bublik.core.queries import get_or_none
from bublik.core.shortcuts import serialize
from bublik.data.models import GlobalConfigNames, MetaResult, TestIterationResult
from bublik.data.serializers import MetaResultSerializer
from bublik.interfaces.celery.tasks import meta_categorization


def is_run_compromised(run):
    if isinstance(run, (int, str)):
        run = get_or_none(TestIterationResult.objects, pk=run)
    if isinstance(run, TestIterationResult):
        return bool(get_or_none(run.meta_results, meta__name='compromised'))
    return None


def get_compromised_details(run):
    if isinstance(run, (int, str)):
        run = get_or_none(TestIterationResult.objects, pk=run)
    if isinstance(run, TestIterationResult):
        compromised_mr = get_or_none(run.meta_results, meta__name='compromised')
        if compromised_mr:
            compromised_meta = compromised_mr.meta
            bug_id = compromised_meta.value if compromised_meta.value else None
            reference_uri = compromised_mr.reference.uri if compromised_mr.reference else None
            bug_url = reference_uri + str(bug_id) if bug_id and reference_uri else None
            return {
                'status': True,
                'comment': compromised_meta.comment if compromised_meta.comment else None,
                'bug_id': bug_id,
                'bug_url': bug_url,
            }
        return {
            'status': False,
        }
    return None


def validate_compromised_request(run_id, comment, bug, reference):
    if not run_id:
        return 'Run id couldn\'t be None.'

    if not comment:
        return 'To mark runs as compromised comment is required.'

    if bool(bug) ^ bool(reference):
        return 'Bug ID and Reference are required together.'

    if reference and reference not in ConfigServices.getattr_from_global(
        GlobalConfigNames.REFERENCES,
        'ISSUES',
        default={},
    ):
        return f'Unknown reference key: {reference}.'

    if is_run_compromised(run_id):
        return 'Run is already marked as compromised.'

    return None


def mark_run_compromised(run_id, comment, bug_id, reference_key):
    run = get_object_or_404(TestIterationResult, pk=run_id)

    reference_data = None
    if reference_key:
        ref_source = ConfigServices.getattr_from_global(
            GlobalConfigNames.REFERENCES,
            'ISSUES',
            default={},
        )[reference_key]
        reference_data = {'name': ref_source['name'], 'uri': ref_source['uri'][0]}

    mr_serialize = serialize(
        MetaResultSerializer,
        {
            'meta': {
                'name': 'compromised',
                'type': 'note',
                'value': bug_id,
                'comment': comment,
            },
            'reference': reference_data,
            'result': run.pk,
        },
    )

    mr, _ = mr_serialize.get_or_create()

    meta_categorization.delay()

    return mr


def unmark_run_compromised(run_id):
    run = get_object_or_404(TestIterationResult, pk=run_id)
    MetaResult.objects.filter(result=run, meta__name='compromised', meta__type='note').delete()
