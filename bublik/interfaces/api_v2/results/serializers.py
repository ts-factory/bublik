# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class PaginationSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)


class RunListQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    finish_date = serializers.DateField(required=False)
    project = serializers.IntegerField(required=False)
    run_status = serializers.CharField(required=False)
    run_metas = serializers.CharField(required=False)
    tag_expr = serializers.CharField(required=False)
    label_expr = serializers.CharField(required=False)
    revision_expr = serializers.CharField(required=False)
    branch_expr = serializers.CharField(required=False)


class DropCacheRequestSerializer(serializers.Serializer):
    keys = serializers.ListField(child=serializers.CharField())


class DropCacheResponseSerializer(serializers.Serializer):
    results = serializers.ListField(child=serializers.IntegerField())


class CompromisedDetailsSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    comment = serializers.CharField(allow_null=True, required=False)
    bug_id = serializers.CharField(allow_null=True, required=False)
    bug_url = serializers.CharField(allow_null=True, required=False)


class RevisionSerializer(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.CharField()
    url = serializers.CharField(allow_blank=True)


class RunSummaryStatsSerializer(serializers.Serializer):
    tests_total = serializers.IntegerField()
    tests_total_plan_percent = serializers.IntegerField(allow_null=True)
    tests_total_ok = serializers.IntegerField()
    tests_total_ok_percent = serializers.IntegerField()
    tests_total_nok = serializers.IntegerField()
    tests_total_nok_percent = serializers.IntegerField()


class RunListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    start = serializers.DateTimeField(allow_null=True)
    finish = serializers.DateTimeField(allow_null=True)
    duration = serializers.DurationField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    status_by_nok = serializers.CharField()
    compromised = serializers.BooleanField(allow_null=True)
    conclusion = serializers.CharField()
    conclusion_reason = serializers.CharField(allow_null=True)
    metadata = serializers.ListField(child=serializers.CharField())
    important_tags = serializers.ListField(child=serializers.CharField())
    relevant_tags = serializers.ListField(child=serializers.CharField())
    stats = RunSummaryStatsSerializer(allow_null=True)


class RunListResponseSerializer(serializers.Serializer):
    pagination = PaginationSerializer()
    results = RunListItemSerializer(many=True)


class RunDetailsResponseSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    id = serializers.IntegerField()
    start = serializers.DateTimeField(allow_null=True)
    finish = serializers.DateTimeField(allow_null=True)
    duration = serializers.DurationField(allow_null=True)
    main_package = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    status_by_nok = serializers.CharField()
    compromised = CompromisedDetailsSerializer(allow_null=True)
    conclusion = serializers.CharField()
    conclusion_reason = serializers.CharField(allow_null=True)
    important_tags = serializers.ListField(child=serializers.CharField())
    relevant_tags = serializers.ListField(child=serializers.CharField())
    branches = serializers.ListField(child=serializers.CharField())
    revisions = RevisionSerializer(many=True)
    labels = serializers.ListField(child=serializers.CharField())
    special_categories = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
    )
    configuration = serializers.CharField(allow_null=True)


class RunStatsQuerySerializer(serializers.Serializer):
    requirements = serializers.CharField(required=False)


class RunStatsValuesSerializer(serializers.Serializer):
    passed = serializers.IntegerField()
    failed = serializers.IntegerField()
    passed_unexpected = serializers.IntegerField()
    failed_unexpected = serializers.IntegerField()
    skipped = serializers.IntegerField()
    skipped_unexpected = serializers.IntegerField()
    abnormal = serializers.IntegerField()


class RunStatsCommentSerializer(serializers.Serializer):
    comment_id = serializers.CharField()
    updated = serializers.CharField()
    serial = serializers.CharField()
    comment = serializers.CharField()


class RunStatsNodeSerializer(serializers.Serializer):
    result_id = serializers.IntegerField()
    exec_seqno = serializers.IntegerField()
    parent_id = serializers.IntegerField(allow_null=True)
    type = serializers.CharField()
    test_id = serializers.IntegerField()
    test_name = serializers.CharField()
    period = serializers.CharField()
    path = serializers.ListField(child=serializers.CharField())
    objective = serializers.CharField(allow_blank=True)
    children = serializers.ListField(
        child=serializers.DictField(),
        help_text='Child nodes with the same structure.',
    )
    stats = RunStatsValuesSerializer()
    comments = RunStatsCommentSerializer(many=True)


class RunStatsResponseSerializer(serializers.Serializer):
    results = RunStatsNodeSerializer(allow_null=True)


class RunRequirementsResponseSerializer(serializers.Serializer):
    requirements = serializers.ListField(child=serializers.CharField())


class RunSourceResponseSerializer(serializers.Serializer):
    url = serializers.CharField(allow_null=True)


class RunStatusResponseSerializer(serializers.Serializer):
    status = serializers.CharField(allow_null=True)


class RunCompromisedResponseSerializer(serializers.Serializer):
    compromised = serializers.BooleanField()


class MarkRunCompromisedRequestSerializer(serializers.Serializer):
    comment = serializers.CharField()
    bug_id = serializers.CharField(required=False, allow_null=True)
    reference_key = serializers.CharField(required=False, allow_null=True)


class MarkRunCompromisedResponseSerializer(serializers.Serializer):
    comment = serializers.CharField()
    bug = serializers.CharField(allow_null=True)


class UnmarkRunCompromisedResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class RunCommentRequestSerializer(serializers.Serializer):
    comment = serializers.CharField()


class RunCommentValueResponseSerializer(serializers.Serializer):
    comment = serializers.CharField(allow_null=True)


class RunCommentResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    comment = serializers.CharField()


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
