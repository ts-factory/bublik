# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.shortcuts import redirect

from bublik.core.run.tests_organization import get_run_root
from bublik.core.shortcuts import build_absolute_uri


def redirect_root(request):
    new_view_path = 'ui-v2-index'
    return redirect(new_view_path)


def redirect_dashboard(request):
    new_endpoint = f'{settings.UI_PREFIX}/dashboard'
    new_view_path = build_absolute_uri(request, new_endpoint)
    return redirect(new_view_path)


def redirect_runs_stats(request):
    new_endpoint = f'{settings.UI_PREFIX}/runs'
    new_view_path = build_absolute_uri(request, new_endpoint)
    return redirect(new_view_path)


def redirect_run_stats(request, run_id):
    new_endpoint = f'{settings.UI_PREFIX}/runs/{run_id}'
    new_view_path = build_absolute_uri(request, new_endpoint)
    return redirect(new_view_path)


def redirect_tests_run(request, run_id):
    result_id = request.GET.get('focus', '')
    new_endpoint = (
        f'{settings.UI_PREFIX}/log/{run_id}/treeAndinfoAndlog?focusId={result_id}'
    )
    new_view_path = build_absolute_uri(request, new_endpoint)
    return redirect(new_view_path)


def redirect_result_log(request, result_id):
    run = get_run_root(result_id)
    new_endpoint = f'{settings.UI_PREFIX}/log/{run.id}/infoAndlog?focusId={result_id}'
    new_view_path = build_absolute_uri(request, new_endpoint)
    return redirect(new_view_path)


def redirect_next(request):
    new_path = request.path.replace('next', settings.URL_PREFIX_BY_UI_VERSION[2])
    return redirect(new_path)


def redirect_flower(request):
    flower_url = (request, 'flower')
    return redirect(flower_url)
