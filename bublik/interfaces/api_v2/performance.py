# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import logging

from urllib.parse import urlencode

from django.conf import settings
import per_conf

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from bublik.core.shortcuts import build_absolute_uri


logger = logging.getLogger()


class PerformanceCheckView(APIView):
    def get(self, request, *args, **kwargs):
        '''
        Return labels, URLs and timeouts for basic views.
        Route: /performance_check.
        '''
        views_to_check = [
            'dashboard',
            'runs_list',
            'runs_charts',
            'history_list_base',
            'history_list_intense',
        ]

        # Check settings timeouts
        if set(settings.VIEWS_TIMEOUTS.keys()) != set(views_to_check):
            msg = f'Incorrect views timeouts list! Expected "{views_to_check}". Check settings.'
            raise KeyError(msg)

        # Build history URLs
        hist_args_common = {
            'runProperties': 'notcompromised',
            'results': 'PASSED;FAILED;KILLED;CORED;SKIPPED;FAKED;INCOMPLETE',
            'resultProperties': 'expected;unexpected',
        }
        hist_args_common = urlencode(hist_args_common)

        try:
            hist_args_per_conf = per_conf.HISTORY_SEARCH_EXAMPLE
            hist_args_intense = urlencode(hist_args_per_conf) + f'&{hist_args_common}'

            hist_args_base = {
                arg: hist_args_per_conf[arg] for arg in ['testName', 'startDate', 'finishDate']
            }
            hist_args_base = urlencode(hist_args_base) + f'&{hist_args_common}'

            history_base_url = build_absolute_uri(
                request,
                f'v2/history/?{hist_args_base}',
            )
            history_intense_url = build_absolute_uri(
                request,
                f'v2/history/?{hist_args_intense}',
            )
        except (AttributeError, KeyError):
            logger.warning('No or incorrect history search example in project config!')
            history_base_url = history_intense_url = None

        # Collect data
        data = []
        for view in views_to_check:
            if view == 'dashboard':
                view_data = {
                    'label': 'Dashboard',
                    'url': build_absolute_uri(request, 'v2/dashboard/'),
                    'timeout': settings.VIEWS_TIMEOUTS[view],
                }
            elif view == 'runs_list':
                view_data = {
                    'label': 'Runs List',
                    'url': build_absolute_uri(request, 'v2/runs/'),
                    'timeout': settings.VIEWS_TIMEOUTS[view],
                }
            elif view == 'runs_charts':
                view_data = {
                    'label': 'Runs Charts',
                    'url': build_absolute_uri(request, 'v2/runs?mode=charts'),
                    'timeout': settings.VIEWS_TIMEOUTS[view],
                }
            elif view == 'history_list_base':
                view_data = {
                    'label': 'History List Base Filtering',
                    'url': history_base_url,
                    'timeout': settings.VIEWS_TIMEOUTS[view],
                }
            elif view == 'history_list_intense':
                view_data = {
                    'label': 'History List Intense Filtering',
                    'url': history_intense_url,
                    'timeout': settings.VIEWS_TIMEOUTS[view],
                }
            else:
                logger.warning(f'Unknown view "{view}"!')
                continue
            data.append(view_data)

        return Response(data, status=status.HTTP_200_OK)
