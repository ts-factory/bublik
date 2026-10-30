# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import json
import traceback

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from bublik.core.auth import auth_required
from bublik.core.cache import RunCache
from bublik.core.exceptions import ImportrunsError
from bublik.core.importruns.live.context import LiveLogContext, LiveLogError
from bublik.core.importruns.source.run_traversal import schedule_runs
from bublik.core.importruns.upload import (
    UploadError,
    UploadTooLargeError,
    open_log_tar,
    publish_log_tar,
)
from bublik.core.logging import get_task_or_server_logger
from bublik.core.logs_storage import upload_storage_for
from bublik.core.shortcuts import get_current_scheme_host_prefix
from bublik.core.utils import (
    create_event,
    create_import_job,
    get_import_job_task,
    get_local_log,
)
from bublik.data.models import EventLog, Project
from bublik.interfaces.api_v2.importruns.serializers import (
    ImportrunsSerializer,
    ImportUploadSerializer,
)


logger = get_task_or_server_logger()

# Default for IMPORT_UPLOAD_MAX_SIZE, matching the settings templates.
DEFAULT_IMPORT_UPLOAD_MAX_SIZE = 2 * 1024**3


def import_upload_max_size() -> int:
    return int(getattr(settings, 'IMPORT_UPLOAD_MAX_SIZE', DEFAULT_IMPORT_UPLOAD_MAX_SIZE))


class ImportrunsViewSet(ViewSet):
    """
    ViewSet to import run logs.
    """

    @method_decorator(never_cache)
    @action(detail=False, methods=['get', 'post'])
    def source(self, request):
        """
        Creates a Celery task to import a session from the provided URI.
        """
        serializer = ImportrunsSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        url = serializer.validated_data['url']
        import_job = create_import_job(url)

        try:
            # Schedule Celery tasks
            tasks_data = schedule_runs(
                request=request,
                requesting_host=get_current_scheme_host_prefix(request),
                job_id=import_job.id,
                **serializer.to_importruns_params(),
            )

            return Response(
                data={
                    'job_id': import_job.id,
                    'job_tasks_data': tasks_data,
                },
            )

        finally:
            import_job.finished_at = datetime.now()
            import_job.save()

    @method_decorator(never_cache)
    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser])
    @auth_required(as_admin=True)
    def upload(self, request):
        """
        Store an uploaded log tarball in the project's upload base (a log
        server's directory or an S3 prefix) and import it.

        Multipart form: ``file`` (the tar made by TE's publish_logs, or a
        bundle holding ``bublik.json`` and its ``json/`` pages), optional
        ``project`` (id; defaults to the PROJECT meta of the tarball) and
        ``force``. Answers like ``source``, plus the run's ``url``.
        """
        serializer = ImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data['file']
        project = serializer.validated_data.get('project')
        force = serializer.validated_data.get('force', False)

        try:
            if upload.size > import_upload_max_size():
                msg = (
                    f'the tarball is {upload.size} bytes, over the '
                    f'{import_upload_max_size()} byte upload limit'
                )
                raise UploadTooLargeError(message=msg)

            log_tar = open_log_tar(upload)
            project = project or self._project_from_metas(log_tar.project_name)
            storage = upload_storage_for(project.id)
            if storage is None:
                msg = (
                    f'project {project.name!r} does not accept uploads: give a '
                    "LOGS_BASES entry of the project's references config a "
                    '"storage" block with "upload": true -- a log server directory, '
                    '{"type": "fs", "root": "/home/te-logs/logs", "upload": true}, '
                    'or an S3 prefix, {"type": "s3", "upload": true} with an '
                    's3://bucket/prefix/ uri'
                )
                raise UploadError(message=msg)
            url = publish_log_tar(log_tar, storage)
        except ImportrunsError as error:
            self._record_rejected_upload(upload.name, error)
            status_code = getattr(error, 'status_code', status.HTTP_400_BAD_REQUEST)
            return Response(data={'message': error.message}, status=status_code)

        import_job = create_import_job(url)
        try:
            tasks_data = schedule_runs(
                request=request,
                requesting_host=get_current_scheme_host_prefix(request),
                job_id=import_job.id,
                url=url,
                project_name=project.name,
                date_from=None,
                date_to=None,
                force=force,
            )
            return Response(
                data={
                    'job_id': import_job.id,
                    'job_tasks_data': tasks_data,
                    'url': url,
                },
            )
        finally:
            import_job.finished_at = datetime.now()
            import_job.save()

    @staticmethod
    def _project_from_metas(project_name):
        if not project_name:
            msg = (
                'no project given: pass the project form field, or set the '
                'PROJECT meta in meta_data.json'
            )
            raise UploadError(message=msg)
        project = Project.objects.filter(name=project_name).first()
        if project is None:
            msg = f'project {project_name!r} from the PROJECT meta does not exist'
            raise UploadError(message=msg)
        return project

    @staticmethod
    def _record_rejected_upload(filename, error):
        """Log a rejected upload as an import job, so it shows among import events."""
        import_job = create_import_job(f'upload:{filename}')
        create_event(
            facility=EventLog.FacilityChoices.IMPORTRUNS,
            severity=EventLog.SeverityChoices.ERR,
            msg=f'rejected upload {filename} -- Error: {error.message}',
            job_task_execution=get_import_job_task(import_job.id),
        )
        import_job.finished_at = datetime.now()
        import_job.save()

    @action(detail=False, methods=['get'])
    def log(self, request):
        r"""
        Return import logs in JSON.
        Route: /api/v2/importruns/log/?task_id=<task_id\>.
        """

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

        return Response(data=json_log)

    @method_decorator(never_cache)
    @action(detail=False, methods=['post'], renderer_classes=[JSONRenderer])
    def init(self, request, format=None):
        """Starts live import initializing run. Returns run identifier."""
        try:
            cache = None
            data = json.loads(request.body)
            ctx = LiveLogContext(data)

            ctx.serialize()
            cache = RunCache.by_id(ctx.run, 'livelog')
            cache.data = ctx

            return Response(
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
        """Accepts chunks of the run tree."""
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
        """Finish a live import session."""
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
