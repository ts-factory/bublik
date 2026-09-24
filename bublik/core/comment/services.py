# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from bublik.core.exceptions import NotFoundError
from bublik.core.project import ProjectService
from bublik.core.shortcuts import serialize
from bublik.data.models import MetaTest, Test
from bublik.data.serializers import MetaTestSerializer


class TestCommentService:
    """
    Reading and writing test comments, shared by the REST API and the MCP server.
    """

    @staticmethod
    def get_test(test_id: int | str) -> Test:
        """
        Get a test by ID.

        Args:
            test_id: The ID of the test

        Returns:
            Test model instance

        Raises:
            NotFoundError: if the test does not exist
        """
        try:
            return Test.objects.get(pk=test_id)
        except (ObjectDoesNotExist, TypeError, ValueError) as e:
            msg = f'Test {test_id} not found'
            raise NotFoundError(msg) from e

    @staticmethod
    def list_for_test(test_id: int | str, project_id: int | str):
        """
        Comments on a test within one project.

        Args:
            test_id: The ID of the test
            project_id: The ID of the project

        Returns:
            QuerySet of MetaTest rows
        """
        if test_id is None or project_id is None:
            return MetaTest.objects.none()
        return MetaTest.objects.filter(
            meta__type='comment',
            test_id=test_id,
            project_id=project_id,
        )

    @staticmethod
    def get(test_id: int | str, project_id: int | str, comment_id: int | str) -> MetaTest:
        """
        One comment on a test, by its meta id.

        Args:
            test_id: The ID of the test
            project_id: The ID of the project
            comment_id: The ID of the comment's Meta

        Returns:
            MetaTest model instance

        Raises:
            NotFoundError: if there is no such comment on that test in that project
        """
        try:
            return TestCommentService.list_for_test(test_id, project_id).get(meta_id=comment_id)
        except (ObjectDoesNotExist, TypeError, ValueError) as e:
            msg = f'Comment {comment_id} not found on test {test_id} in project {project_id}'
            raise NotFoundError(msg) from e

    @staticmethod
    def delete(metatest: MetaTest) -> None:
        metatest.delete()

    @staticmethod
    def add(test_id: int | str, project_id: int | str, comment: str) -> dict:
        """
        Add a comment to a test.

        Args:
            test_id: The ID of the test to comment on
            project_id: The ID of the project the comment belongs to
            comment: The comment text

        Returns:
            The created comment as serialized data

        Raises:
            NotFoundError: if the test or the project does not exist
            ValidationError: if the same comment already exists on the test
        """
        test = TestCommentService.get_test(test_id)
        project = ProjectService.get_project_instance(project_id)
        serializer = serialize(
            MetaTestSerializer,
            data={'comment': comment},
            context={'test': test, 'project': project},
        )
        serializer.save()
        return serializer.data

    @staticmethod
    def update(metatest: MetaTest, comment: str) -> dict:
        """
        Replace a test comment with new text, keeping its serial.

        Args:
            metatest: The MetaTest row to replace
            comment: The new comment text

        Returns:
            The new comment as serialized data

        Raises:
            ValidationError: if the new text duplicates an existing comment
        """
        serializer = serialize(
            MetaTestSerializer,
            data={'comment': comment},
            context={
                'test': metatest.test,
                'serial': metatest.serial,
                'project': metatest.project,
            },
        )
        with transaction.atomic():
            serializer.save()
            metatest.delete()
        return serializer.data
