# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view

from bublik.core.utils import get_local_log
from bublik.interfaces.celery import tasks


# TODO handle errors
@never_cache
@api_view(['GET', 'POST'])
def meta_categorization(request):
    task_id = tasks.meta_categorization.delay()
    return HttpResponse(f'\nYour task id: {task_id}\n')


def local_logs(request, task_id):
    filename = task_id

    try:
        logpath = get_local_log(filename)
        with open(logpath) as f:
            text = f.read()
        return TemplateResponse(request, 'bublik/import_log.html', {'file': text})

    except Exception as e:
        return TemplateResponse(
            request,
            'bublik/alert.html',
            {'detail': str(e), 'view': 'local_logs', 'alert_type': 'danger'},
        )


@never_cache
@api_view(['GET', 'POST'])
def clear_all_runs_stats_cache(request):
    task_id = tasks.clear_all_runs_stats_cache.delay()
    return HttpResponse(f'\nYour task id: {task_id}\n')
