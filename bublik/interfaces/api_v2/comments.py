# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import json

from rest_framework import status
from rest_framework.mixins import DestroyModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.auth import check_action_permission
from bublik.data.models import MetaTest
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
        Add a comment to the Test object by creating MetaTest object that relates it
        with the received or created Meta object of the comment type.
        Request: POST tests/<test_id>/comments.
        '''
        test_id = self.kwargs.get('test_id')
        comment_value = request.data.get('comment')
        serializer = self.get_serializer(
            data={
                'test': test_id,
                'meta': {'type': 'comment', 'value': json.dumps(comment_value)},
            },
        )
        serializer.update_data()
        serializer.is_valid(raise_exception=True)
        test_comment, created = serializer.get_or_create(serializer.validated_data)
        test_comment_data = self.get_serializer(test_comment).data
        if not created:
            return Response(test_comment_data, status=status.HTTP_302_FOUND)
        return Response(test_comment_data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def partial_update(self, request, *args, **kwargs):
        '''
        Update a test comment by deleting the MetaTest object relates the Test object
        with the Meta object with the old value and creating a new MetaTest object relates
        the Test object with the received or created Meta object with the new value.
        Request: PATCH tests/<test_id>/comments/<meta_id>.
        '''
        # get new MetaTest object data
        metatest = self.get_object()
        upd_comment_value = request.data.get('comment')
        upd_test_comment_data = self.get_serializer(metatest).data
        upd_test_comment_data['meta']['value'] = json.dumps(upd_comment_value)

        # validate new MetaTest object data
        serializer = self.get_serializer(data=upd_test_comment_data)
        serializer.update_data()
        serializer.is_valid(raise_exception=True)

        # create a new MetaTest object and delete the old one
        test_comment, created = serializer.get_or_create(serializer.validated_data)
        metatest.delete()

        test_comment_data = self.get_serializer(test_comment).data
        if not created:
            return Response(test_comment_data, status=status.HTTP_302_FOUND)
        return Response(test_comment_data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_test_comments')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
