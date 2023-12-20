# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.cache import RunCache
from bublik.core.run.tree import path_to_node, tree_representation
from bublik.data.models import TestIterationResult


__all__ = [
    'TreeViewSet',
]


class TreeViewSet(RetrieveModelMixin, GenericViewSet):
    queryset = TestIterationResult.objects.all()

    def tree(self, result):
        cache = RunCache.by_obj(result, 'tree')
        tree = cache.data
        if not tree:
            tree = tree_representation(result)
            cache.data = tree
        return tree

    def retrieve(self, request, pk=None):
        result = self.get_object()
        tree = self.tree(result)

        main_package = None
        if result.main_package:
            main_package = result.main_package.id

        if result.root is not result:
            tree = tree.subtree(result.id)
            main_package = result.id

        tree = tree.to_linear_dict(with_data=True)

        return Response(
            data={'tree': tree, 'main_package': main_package},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['get'])
    def path(self, request, pk=None):
        result = self.get_object()
        node_path = path_to_node(result)
        return Response(data={'path': node_path}, status=status.HTTP_200_OK)
