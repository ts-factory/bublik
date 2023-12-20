# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import json
import traceback

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer, TemplateHTMLRenderer
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from bublik.core.cache import RunCache
from bublik.core.importruns import get_import_log_for_run
from bublik.core.importruns.live.context import LiveLogContext, LiveLogError
from bublik.core.importruns.utils import indicate_collision
from bublik.core.response import bad_request, internal_error
from bublik.core.shortcuts import build_absolute_uri, get_current_scheme_host_prefix
from bublik.interfaces.celery import tasks


class ImportrunsViewSet(ViewSet):
    '''
    ViewSet to import run logs from source or in life mode by chunks.
    Requires authentication. Available for admin users.
    '''

    renderer_classes = [TemplateHTMLRenderer]

    @method_decorator(never_cache)
    @action(detail=False, methods=['get', 'post'])
    def source(self, request, format=None):
        """
        Imports run from source.

        This method should be POST only as it creates some resource and
        it's not idempotent, but until client UI is implemented it's needed
        to call it directly from a browser where by default GET method is used.
        """

        param_url = request.query_params.get('url')
        param_from = request.query_params.get('from', '')
        param_to = request.query_params.get('to', '')
        param_force = request.query_params.get('force', 'false')

        try:
            requesting_host = get_current_scheme_host_prefix(request)

            task_id = tasks.importruns.delay(
                param_url,
                param_force,
                param_from,
                param_to,
                requesting_host,
            )
            if indicate_collision(str(task_id), param_url):
                task_id = tasks.importruns.delay(
                    param_url,
                    param_force,
                    param_from,
                    param_to,
                    requesting_host,
                )

            context = [
                {
                    'label': 'Processing details in runtime:',
                    'href': build_absolute_uri(request, f'flower/task/{task_id}'),
                },
                {
                    'label': 'Bublik log:',
                    'href': build_absolute_uri(request, f'importlog/{task_id}'),
                },
            ]

            return Response(
                data={'links_context': context},
                status=status.HTTP_201_CREATED,
                template_name='bublik/inline/links.html',
            )

        except Exception as e:
            return internal_error(str(e), self.get_view_name())

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

    @action(detail=False, methods=['get'])
    def logs(self, request, format=None):
        '''Shows importruns log for a certain run.'''

        run = request.query_params.get('run', None)
        if not run:
            return bad_request("Expected 'run' in query parameters", self.get_view_name())

        logpath = None
        try:
            logpath = get_import_log_for_run(run)
        except Exception as e:
            return internal_error(str(e), self.get_view_name())

        if logpath is None:
            text = 'Run was imported directly on the server via manage.py, so it has no logfile'
        else:
            with open(logpath) as f:
                text = f.read()

        return Response(
            data={'file': text},
            status=status.HTTP_200_OK,
            template_name='bublik/import_log.html',
        )
