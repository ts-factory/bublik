# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.tree.services import TreeService
from bublik.data.models import TestIterationResult


__all__ = [
    'TreeViewSet',
]


class TreeViewSet(RetrieveModelMixin, GenericViewSet):
    queryset = TestIterationResult.objects.all()

    def retrieve(self, request, pk=None):
        try:
            tree_data = TreeService.get_tree(int(pk))
            return Response(data=tree_data)
        except DjangoValidationError as e:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={'message': str(e)},
            )

    @action(detail=True, methods=['get'])
    def path(self, request, pk=None):
        try:
            node_path = TreeService.get_tree_path(int(pk))
            return Response(data={'path': node_path})
        except DjangoValidationError as e:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={'message': str(e)},
            )
