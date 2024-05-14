# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import logging

from rest_framework import status
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.meta.match_references import build_revision_references
from bublik.core.report.services import (
    build_report_title,
    check_report_config,
    get_report_config_by_id,
)
from bublik.core.run.external_links import get_sources
from bublik.core.shortcuts import build_absolute_uri
from bublik.data.models import Meta, TestIterationResult
from bublik.data.serializers import TestIterationResultSerializer


logger = logging.getLogger('')


__all__ = [
    'ReportViewSet',
]


class ReportViewSet(RetrieveModelMixin, GenericViewSet):
    queryset = TestIterationResult.objects.all()
    serializer_class = TestIterationResultSerializer

    def retrieve(self, request, pk=None):
        ### Get and check report config ###
        # check if the config ID has been passed
        report_config_id = request.query_params.get('config')
        if not report_config_id:
            msg = 'report config wasn\'t passed'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': msg},
            )

        # check if there is a config of the correct format with the passed config ID
        report_config = get_report_config_by_id(report_config_id)
        if not report_config:
            msg = 'report config is not found or have an incorrect format. JSON is expected'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': msg},
            )

        # check if the config has all the necessary keys
        try:
            check_report_config(report_config)
        except KeyError as ke:
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': ke.args[0]},
            )

        # get the session and the corresponding main package by ID
        result = self.get_object()
        main_pkg = result.root

        ### Get common report data ###

        # form the title according to report config
        title_content = report_config['title_content']
        title = build_report_title(main_pkg, title_content)

        # get run source link
        run_source_link = get_sources(main_pkg)

        # get Bublik run stats link
        run_stats_link = build_absolute_uri(request, f'v2/runs/{main_pkg.id}')

        # get branches
        meta_branches = Meta.objects.filter(
            metaresult__result__id=main_pkg.id,
            type='branch',
        ).values('name', 'value')
        branches = meta_branches

        # get revisions
        meta_revisions = Meta.objects.filter(
            metaresult__result__id=main_pkg.id,
            type='revision',
        ).values('name', 'value')
        revisions = build_revision_references(meta_revisions)

        ### Collect report data ###

        content = [
            {
                'type': 'branch-block',
                'id': 'branches',
                'label': 'Branches',
                'content': branches,
            },
            {
                'type': 'rev-block',
                'id': 'revisions',
                'label': 'Revisions',
                'content': revisions,
            },
        ]

        report = {
            'title': title,
            'run_source_link': run_source_link,
            'run_stats_link': run_stats_link,
            'version': report_config['version'],
            'content': content,
        }

        return Response(data=report, status=status.HTTP_200_OK)
