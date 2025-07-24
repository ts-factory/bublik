# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.mixins import DestroyModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.auth import check_action_permission
from bublik.core.shortcuts import serialize
from bublik.data.models import MetaTest, Test
from bublik.data.serializers import (
    MetaTestSerializer,
)


class TestCommentViewSet(DestroyModelMixin, GenericViewSet):
    serializer_class = MetaTestSerializer

    def get_queryset(self):
        test_id = self.kwargs.get('test_id')
        if test_id is not None:
            return MetaTest.objects.filter(meta__type='comment', test_id=test_id)
        return MetaTest.objects.none()

    def get_object(self):
        queryset = self.get_queryset()
        filter_kwargs = {'meta': self.kwargs.get('pk')}
        return queryset.get(**filter_kwargs)

    @check_action_permission('manage_test_comments')
    def create(self, request, *args, **kwargs):
        '''
        Add a comment to the Test by creating a MetaTest linking it
        to the retrieved or newly created comment-type Meta.
        Request: POST tests/<test_id>/comments.
        '''
        test = Test.objects.get(pk=self.kwargs['test_id'])
        comment_data = {
            'comment': request.data.get('comment'),
        }
        serializer = serialize(self.serializer_class, data=comment_data, context={'test': test})
        test_comment, created = serializer.get_or_create(serializer.validated_data)
        test_comment_data = self.get_serializer(test_comment).data
        if not created:
            return Response(test_comment_data, status=status.HTTP_302_FOUND)
        return Response(test_comment_data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def partial_update(self, request, *args, **kwargs):
        '''
        Update a test comment by replacing the existing MetaTest that links the Test
        to the old Meta with a new MetaTest linking the Test to the received or newly
        created Meta with the new value.
        Request: PATCH tests/<test_id>/comments/<meta_id>.
        '''
        metatest = self.get_object()
        upd_test_comment_data = {
            'comment': request.data.get('comment'),
        }
        serializer = serialize(
            self.serializer_class,
            data=upd_test_comment_data,
            context={'test': metatest.test, 'serial': metatest.serial},
        )
        serializer.is_valid(raise_exception=True)
        test_comment, created = serializer.get_or_create(serializer.validated_data)
        metatest.delete()
        test_comment_data = self.get_serializer(test_comment).data
        if not created:
            return Response(test_comment_data, status=status.HTTP_302_FOUND)
        return Response(test_comment_data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
