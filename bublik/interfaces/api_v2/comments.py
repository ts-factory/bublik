# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import typing

from django.db import transaction
from rest_framework import status
from rest_framework.mixins import DestroyModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.auth import check_action_permission
from bublik.core.filter_backends import ProjectFilterBackend
from bublik.core.shortcuts import serialize
from bublik.data.models import MetaTest, Project, Test
from bublik.data.serializers import (
    MetaTestSerializer,
)


class TestCommentViewSet(DestroyModelMixin, GenericViewSet):
    serializer_class = MetaTestSerializer
    filter_backends: typing.ClassVar[list] = [ProjectFilterBackend]

    def get_queryset(self):
        test_id = self.kwargs.get('test_id')
        project_id = self.request.query_params.get('project')
        if test_id is not None and project_id is not None:
            return self.filter_queryset(
                MetaTest.objects.filter(
                    meta__type='comment',
                    test_id=test_id,
                ),
            )
        return MetaTest.objects.none()

    def get_object(self):
        queryset = self.get_queryset()
        filter_kwargs = {'meta': self.kwargs.get('pk')}
        return queryset.get(**filter_kwargs)

    @check_action_permission('manage_test_comments')
    def create(self, request, *args, **kwargs):
        '''
        Add a comment to the Test by creating a MetaTest linking it
        to the retrieved or newly created comment-type Meta in the provided project.
        Request: POST tests/<test_id>/comments/?project=<project_id>.
        '''
        test = Test.objects.get(pk=self.kwargs['test_id'])
        project = Project.objects.get(pk=request.query_params.get('project'))
        comment_data = {
            'comment': request.data.get('comment'),
        }
        serializer = serialize(
            self.serializer_class,
            data=comment_data,
            context={'test': test, 'project': project},
        )
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def partial_update(self, request, *args, **kwargs):
        '''
        Update a test comment by replacing the existing MetaTest linking the Test
        to the old Meta with a new MetaTest linking it to the received or newly
        created Meta with the new value in the same project.
        Request: PATCH tests/<test_id>/comments/<meta_id>/?project=<project_id>.
        '''
        metatest = self.get_object()
        upd_test_comment_data = {
            'comment': request.data.get('comment'),
        }
        serializer = serialize(
            self.serializer_class,
            data=upd_test_comment_data,
            context={
                'test': metatest.test,
                'serial': metatest.serial,
                'project': metatest.project,
            },
        )
        with transaction.atomic():
            serializer.save()
            metatest.delete()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def destroy(self, request, *args, **kwargs):
        '''
        Request: DELETE tests/<test_id>/comments/<meta_id>/?project=<project_id>.
        '''
        return super().destroy(request, *args, **kwargs)
