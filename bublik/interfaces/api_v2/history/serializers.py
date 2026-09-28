# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers


class HistoryListQuerySerializer(serializers.Serializer):
    test_name = serializers.CharField(help_text='Name of the test')
    project = serializers.IntegerField(required=False)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=10000)
    from_date = serializers.DateField(required=False)
    to_date = serializers.DateField(required=False)
    run_ids = serializers.CharField(
        required=False,
        help_text='Semicolon-separated run IDs',
    )
    branches = serializers.CharField(
        required=False,
        help_text='Semicolon-separated branches',
    )
    revisions = serializers.CharField(
        required=False,
        help_text='Semicolon-separated revisions',
    )
    labels = serializers.CharField(
        required=False,
        help_text='Semicolon-separated labels',
    )
    tags = serializers.CharField(required=False, help_text='Semicolon-separated tags')
    branch_expr = serializers.CharField(required=False)
    rev_expr = serializers.CharField(required=False)
    label_expr = serializers.CharField(required=False)
    tag_expr = serializers.CharField(required=False)
    run_properties = serializers.CharField(
        required=False,
        help_text='Semicolon-separated run properties',
    )
    hash = serializers.CharField(required=False, help_text='Test iteration hash')
    test_args = serializers.CharField(
        required=False,
        help_text='Semicolon-separated key=value test arguments',
    )
    test_arg_expr = serializers.CharField(required=False)
    result_statuses = serializers.CharField(
        required=False,
        help_text='Semicolon-separated result statuses',
    )
    verdict = serializers.CharField(required=False)
    verdict_lookup = serializers.CharField(
        required=False,
        help_text='regex, string, or none',
    )
    verdict_expr = serializers.CharField(required=False)
    result_types = serializers.CharField(
        required=False,
        help_text='Semicolon-separated expected or unexpected values',
    )


class HistoryProjectQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(required=False)


class HistoryParamsSearchQuerySerializer(HistoryProjectQuerySerializer):
    test_name = serializers.CharField(help_text='Name of the test')


class HistoryCountsSerializer(serializers.Serializer):
    runs = serializers.IntegerField()
    iterations = serializers.IntegerField()
    total_results = serializers.IntegerField()
    expected_results = serializers.IntegerField()
    unexpected_results = serializers.IntegerField()


class HistoryPaginationSerializer(serializers.Serializer):
    page = serializers.IntegerField()
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)


class HistoryKeySerializer(serializers.Serializer):
    name = serializers.CharField()
    url = serializers.CharField(allow_null=True)


class HistoryExpectedResultSerializer(serializers.Serializer):
    result_type = serializers.CharField(allow_null=True)
    verdicts = serializers.ListField(child=serializers.CharField())
    keys = HistoryKeySerializer(many=True)


class HistoryObtainedResultSerializer(serializers.Serializer):
    result_type = serializers.CharField()
    verdicts = serializers.ListField(child=serializers.CharField())


class HistoryResultSerializer(serializers.Serializer):
    start_date = serializers.DateTimeField()
    finish_date = serializers.DateTimeField()
    duration = serializers.CharField()
    obtained_result = HistoryObtainedResultSerializer()
    expected_results = HistoryExpectedResultSerializer(many=True)
    important_tags = serializers.ListField(child=serializers.CharField())
    relevant_tags = serializers.ListField(child=serializers.CharField())
    metadata = serializers.ListField(child=serializers.CharField())
    parameters = serializers.ListField(child=serializers.CharField())
    has_error = serializers.BooleanField()
    has_measurements = serializers.BooleanField()
    run_id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    result_id = serializers.IntegerField()
    iteration_id = serializers.IntegerField()
    report_config_id = serializers.IntegerField(allow_null=True)


class HistoryVerdictResultSerializer(serializers.Serializer):
    run_id = serializers.IntegerField()
    result_id = serializers.IntegerField()
    start_date = serializers.DateTimeField()
    important_tags = serializers.ListField(child=serializers.CharField())
    relevant_tags = serializers.ListField(child=serializers.CharField())


class HistoryVerdictGroupSerializer(serializers.Serializer):
    key = serializers.CharField()
    result_type = serializers.CharField()
    has_error = serializers.BooleanField()
    verdict = serializers.ListField(child=serializers.CharField())
    results_data = HistoryVerdictResultSerializer(many=True)


class HistoryIterationGroupSerializer(serializers.Serializer):
    hash = serializers.CharField()
    iteration_id = serializers.IntegerField()
    parameters = serializers.ListField(child=serializers.CharField())
    results_by_verdicts = HistoryVerdictGroupSerializer(many=True)


@extend_schema_serializer(many=False)
class HistoryListResponseSerializer(serializers.Serializer):
    from_date = serializers.DateField(allow_null=True)
    to_date = serializers.DateField(allow_null=True)
    counts = HistoryCountsSerializer()
    pagination = HistoryPaginationSerializer()
    results = HistoryResultSerializer(many=True)
    results_ids = serializers.ListField(child=serializers.IntegerField())


class HistoryGroupedResponseSerializer(serializers.Serializer):
    from_date = serializers.DateField(allow_null=True)
    to_date = serializers.DateField(allow_null=True)
    counts = HistoryCountsSerializer()
    pagination = HistoryPaginationSerializer()
    results = HistoryIterationGroupSerializer(many=True)
    results_ids = serializers.ListField(child=serializers.IntegerField())


class HistoryTagsSerializer(serializers.Serializer):
    important = serializers.ListField(child=serializers.CharField())
    relevant = serializers.ListField(child=serializers.CharField())
    all = serializers.ListField(child=serializers.CharField())


class HistoryMetasSearchOptionsSerializer(serializers.Serializer):
    tags = HistoryTagsSerializer()
    branches = serializers.ListField(child=serializers.CharField())
    revisions = serializers.ListField(child=serializers.CharField())
    labels = serializers.ListField(child=serializers.CharField())
