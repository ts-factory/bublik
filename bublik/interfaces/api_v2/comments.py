# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import typing

from rest_framework import status
from rest_framework.mixins import DestroyModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.auth import check_action_permission
from bublik.core.comment import TestCommentService
from bublik.core.filter_backends import ProjectFilterBackend
from bublik.data.serializers import (
    MetaTestSerializer,
)


class TestCommentViewSet(DestroyModelMixin, GenericViewSet):
    serializer_class = MetaTestSerializer
    filter_backends: typing.ClassVar[list] = [ProjectFilterBackend]

    def get_queryset(self):
        return self.filter_queryset(
            TestCommentService.list_for_test(
                test_id=self.kwargs.get('test_id'),
                project_id=self.request.query_params.get('project'),
            ),
        )

    def get_object(self):
        queryset = self.get_queryset()
        filter_kwargs = {'meta': self.kwargs.get('pk')}
        return queryset.get(**filter_kwargs)

    @check_action_permission('manage_test_comments')
    def create(self, request, *args, **kwargs):
        """
        Add a comment to the Test by creating a MetaTest linking it
        to the retrieved or newly created comment-type Meta in the provided project.
        Request: POST tests/<test_id>/comments/?project=<project_id>.
        """
        comment = TestCommentService.add(
            test_id=self.kwargs['test_id'],
            project_id=request.query_params.get('project'),
            comment=request.data.get('comment'),
        )
        return Response(comment, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def partial_update(self, request, *args, **kwargs):
        """
        Update a test comment by replacing the existing MetaTest linking the Test
        to the old Meta with a new MetaTest linking it to the received or newly
        created Meta with the new value in the same project.
        Request: PATCH tests/<test_id>/comments/<meta_id>/?project=<project_id>.
        """
        comment = TestCommentService.update(
            metatest=self.get_object(),
            comment=request.data.get('comment'),
        )
        return Response(comment, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def destroy(self, request, *args, **kwargs):
        """
        Request: DELETE tests/<test_id>/comments/<meta_id>/?project=<project_id>.
        """
        return super().destroy(request, *args, **kwargs)
