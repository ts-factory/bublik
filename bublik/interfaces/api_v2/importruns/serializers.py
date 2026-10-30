# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from argparse import ArgumentTypeError

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

from bublik.core.argparse import (
    parser_type_date,
    parser_type_force,
)
from bublik.core.config.services import ConfigServices
from bublik.core.logs_storage import (
    LogsStorageError,
    is_local_url,
    is_s3_url,
    parse_file_url,
    parse_s3_url,
)
from bublik.data.models import GlobalConfigs, Project


class ImportrunsSerializer(serializers.Serializer):
    # http(s) URLs of a TE log server, s3:// URLs of an S3 logs base, or
    # file:// URLs of a local one.
    url = serializers.CharField()
    project = serializers.IntegerField(required=False, allow_null=True)
    date_from = serializers.CharField(required=False, allow_null=True)
    date_to = serializers.CharField(required=False, allow_null=True)
    force = serializers.CharField(required=False, allow_null=True)

    def to_internal_value(self, data):
        data = data.copy()
        if 'from' in data:
            data['date_from'] = data.pop('from')[0]
        if 'to' in data:
            data['date_to'] = data.pop('to')[0]
        return super().to_internal_value(data)

    def validate_url(self, url):
        if is_s3_url(url) or is_local_url(url):
            try:
                (parse_s3_url if is_s3_url(url) else parse_file_url)(url)
            except LogsStorageError as error:
                raise serializers.ValidationError(error.message) from None
        else:
            try:
                URLValidator(schemes=['http', 'https'])(url)
            except DjangoValidationError:
                msg = 'Enter a valid http(s)://, s3:// or file:// URL.'
                raise serializers.ValidationError(msg) from None

        project_id = self.initial_data.get('project')
        project_ids = (
            Project.objects.filter(id=project_id)
            if project_id is not None
            else Project.objects.all()
        ).values_list('id', flat=True)

        allowed_uris = set()
        for pid in project_ids:
            logs_bases = ConfigServices.getattr_from_global(
                GlobalConfigs.REFERENCES.name,
                'LOGS_BASES',
                pid,
            )
            for logs_base in logs_bases:
                allowed_uris.update(logs_base.get('uri', []))

        if not any(url.startswith(uri) for uri in allowed_uris):
            msg = 'URL does not match any allowed logs base URI'
            raise serializers.ValidationError(msg)

        return url

    def validate_date_from(self, date_from):
        try:
            return parser_type_date(date_from.replace('-', '.')) if date_from else None
        except ArgumentTypeError as ate:
            raise serializers.ValidationError(ate) from None

    def validate_date_to(self, date_to):
        try:
            return parser_type_date(date_to.replace('-', '.')) if date_to is not None else None
        except ArgumentTypeError as ate:
            raise serializers.ValidationError(ate) from None

    def validate_force(self, force):
        try:
            return parser_type_force(force) if force is not None else False
        except ArgumentTypeError as ate:
            raise serializers.ValidationError(ate) from None

    def to_importruns_params(self):
        data = self.validated_data
        return {
            'url': data['url'],
            'project_name': (
                Project.objects.get(id=data['project']).name
                if data.get('project') is not None
                else None
            ),
            'date_from': (
                data['date_from'].replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                if data.get('date_from')
                else None
            ),
            'date_to': (
                data['date_to']
                .replace(hour=23, minute=59, second=59, microsecond=0)
                .isoformat()
                if data.get('date_to')
                else None
            ),
            'force': data.get('force', False),
        }


class ImportUploadSerializer(serializers.Serializer):
    """Form fields of a log tarball upload."""

    file = serializers.FileField()
    project = serializers.IntegerField(required=False, allow_null=True)
    force = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_project(self, project_id):
        if project_id is None:
            return None
        project = Project.objects.filter(id=project_id).first()
        if project is None:
            msg = f'Project {project_id} does not exist'
            raise serializers.ValidationError(msg)
        return project

    def validate_force(self, force):
        try:
            return parser_type_force(force) if force else False
        except ArgumentTypeError as ate:
            raise serializers.ValidationError(ate) from None
