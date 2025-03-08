# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework.serializers import ModelSerializer

from bublik.data.models import Project


class ProjectSerializer(ModelSerializer):

    class Meta:
        model = Project
        fields = (
            'id',
            'name',
        )
