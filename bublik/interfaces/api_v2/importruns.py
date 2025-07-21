# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import json
import logging
import traceback

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from bublik.core.cache import RunCache
from bublik.core.importruns.live.context import LiveLogContext, LiveLogError
from bublik.core.importruns.utils import indicate_collision
from bublik.core.shortcuts import build_absolute_uri, get_current_scheme_host_prefix
from bublik.core.utils import get_local_log
from bublik.interfaces.celery import tasks


logger = logging.getLogger()


class ImportrunsViewSet(ViewSet):
    '''
    ViewSet to import run logs.
    '''

    @method_decorator(never_cache)
    @action(detail=False, methods=['get', 'post'])
    def source(self, request):
        '''
        Creates a Celery task to import a session from the provided URI.
        '''

        param_url = request.query_params.get('url')
        param_from = request.query_params.get('from', '').replace('-', '.')
        param_to = request.query_params.get('to', '').replace('-', '.')
        param_force = request.query_params.get('force', 'false')
        param_project = request.query_params.get('prj')

        try:
            requesting_host = get_current_scheme_host_prefix(request)

            task_id = tasks.importruns.delay(
                param_url,
                param_force,
                param_from,
                param_to,
                requesting_host,
                param_project,
            )
            if indicate_collision(str(task_id), param_url):
                task_id = tasks.importruns.delay(
                    param_url,
                    param_force,
                    param_from,
                    param_to,
                    requesting_host,
                    param_project,
                )

            data = {
                'celery_task_id': str(task_id),
                'flower': build_absolute_uri(request, f'flower/task/{task_id}'),
                'import_log': build_absolute_uri(request, f'importlog/{task_id}'),
            }
            return Response(data=data)

        except Exception as e:
            return Response({'error': str(e), 'backtrace': traceback.format_exc()})

    @action(detail=False, methods=['get'])
    def log(self, request):
        r'''
        Return import logs in JSON.
        Route: /api/v2/importruns/log/?task_id=<task_id\>.
        '''

        task_id = request.query_params.get('task_id')

        logpath = get_local_log(task_id)

        with open(logpath) as f:
            lines = f.readlines()

        json_log = []
        for line in lines:
            try:
                json_line = json.loads(line)
            except json.decoder.JSONDecodeError:
                logger.warning(
                    f'Incorrect log format: {line[:-1]}. Expecting '
                    f'{{"asctime": "...", "levelname": "...", '
                    f'"module": "...", "message": "..."}}',
                )
                line = (
                    f'{{"asctime": "0000-00-00 00:00:00,000", "levelname": "INFO",'
                    f'"module": "console", "message": {json.dumps(line[:-1])}}}'
                )
                json_line = json.loads(line)
            json_log.append(json_line)

        return Response(data=json_log, status=status.HTTP_200_OK)

    @method_decorator(never_cache)
    @action(detail=False, methods=['post'], renderer_classes=[JSONRenderer])
    def init(self, request, format=None):
        '''Starts live import initializing run. Returns run identifier.'''
        try:
            cache = None
            data = json.loads(request.body)
            ctx = LiveLogContext(data)

            ctx.serialize()
            cache = RunCache.by_id(ctx.run, 'livelog')
            cache.data = ctx

            return Response(
                status=status.HTTP_200_OK,
                data={'runid': ctx.run},
            )
        except json.decoder.JSONDecodeError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={'message': 'malformed JSON'},
            )
        except LiveLogError as e:
            traceback.print_exc()
            return e.to_response()
        except Exception:
            if cache is not None:
                del cache.data
            traceback.print_exc()
            return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @method_decorator(never_cache)
    @action(detail=False, methods=['post'], renderer_classes=[JSONRenderer])
    def feed(self, request, format=None):
        '''Accepts chunks of the run tree.'''
        try:
            ctx = None
            cache = None
            run_id = request.query_params.get('run')
            cache = RunCache.by_id(run_id, 'livelog')
            events = json.loads(request.body)

            ctx = cache.data
            if ctx is None:
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={'message': 'unknown session'},
                )
            assert ctx.run == int(run_id)

            if not events:
                return Response(status=status.HTTP_204_NO_CONTENT)

            ctx.deserialize()
            ctx.feed(events)

            ctx.serialize()
            cache.data = ctx

            return Response(status=status.HTTP_204_NO_CONTENT)
        except json.decoder.JSONDecodeError:
            if ctx is not None:
                ctx.deserialize()
                ctx.fatal_error()
                del cache.data
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={'message': 'malformed JSON'},
            )
        except LiveLogError as e:
            traceback.print_exc()
            if ctx is not None:
                ctx.deserialize()
                ctx.fatal_error()
                del cache.data
            return e.to_response()
        except Exception:
            traceback.print_exc()
            if ctx is not None:
                ctx.deserialize()
                ctx.fatal_error()
                del cache.data
            return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @method_decorator(never_cache)
    @action(detail=False, methods=['post'], renderer_classes=[JSONRenderer])
    def finish(self, request, format=None):
        '''Finish a live import session.'''
        try:
            ctx = None
            cache = None
            run_id = request.query_params.get('run')
            cache = RunCache.by_id(run_id, 'livelog')
            data = json.loads(request.body)

            ctx = cache.data
            if ctx is None:
                return Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={'message': 'unknown session'},
                )
            del cache.data
            assert ctx.run == int(run_id)

            ctx.deserialize()
            ctx.finish(data)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except json.decoder.JSONDecodeError:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={'message': 'malformed JSON'},
            )
        except LiveLogError as e:
            traceback.print_exc()
            if ctx is not None:
                ctx.fatal_error()
            return e.to_response()
        except Exception:
            traceback.print_exc()
            if ctx is not None:
                ctx.fatal_error()
            return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)
