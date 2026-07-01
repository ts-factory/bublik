# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers


class ResultListQuerySerializer(serializers.Serializer):
    parent_id = serializers.IntegerField(required=False)
    test_name = serializers.CharField(required=False)
    start_exec_seqno = serializers.IntegerField(required=False)
    results = serializers.CharField(required=False)
    result_properties = serializers.CharField(required=False)
    requirements = serializers.CharField(required=False)


class ResultKeySerializer(serializers.Serializer):
    name = serializers.CharField()
    url = serializers.CharField(allow_null=True)


class ExpectedResultSerializer(serializers.Serializer):
    result_type = serializers.CharField(allow_null=True)
    verdicts = serializers.ListField(child=serializers.CharField())
    keys = ResultKeySerializer(many=True)


class ObtainedResultSerializer(serializers.Serializer):
    result_type = serializers.CharField(allow_null=True)
    verdicts = serializers.ListField(child=serializers.CharField())


class ResultDetailsSerializer(serializers.Serializer):
    name = serializers.CharField()
    result_id = serializers.IntegerField()
    run_id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    iteration_id = serializers.IntegerField()
    start = serializers.DateTimeField(allow_null=True)
    obtained_result = ObtainedResultSerializer()
    expected_results = ExpectedResultSerializer(many=True)
    artifacts = serializers.ListField(child=serializers.CharField())
    parameters = serializers.ListField(child=serializers.CharField())
    comments = serializers.ListField(child=serializers.CharField())
    requirements = serializers.ListField(child=serializers.CharField())
    has_error = serializers.BooleanField()
    has_measurements = serializers.BooleanField()


@extend_schema_serializer(many=False)
class ResultListResponseSerializer(serializers.Serializer):
    results = ResultDetailsSerializer(many=True)


class ResultRetrieveResponseSerializer(serializers.Serializer):
    result = ResultDetailsSerializer()


class MetaValueSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_null=True)
    type = serializers.CharField()
    value = serializers.CharField(allow_null=True)
    hash = serializers.CharField()
    comment = serializers.CharField(allow_null=True)


class ResultArtifactsAndVerdictsResponseSerializer(serializers.Serializer):
    artifacts = MetaValueSerializer(many=True)
    verdicts = MetaValueSerializer(many=True)


class ResultMeasurementsResponseSerializer(serializers.Serializer):
    run_id = serializers.IntegerField(allow_null=True)
    iteration_id = serializers.IntegerField()
    charts = serializers.ListField(child=serializers.DictField())
    tables = serializers.ListField(child=serializers.DictField())
