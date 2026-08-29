# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from bublik.data.models import IssueCategory, IssueState, RuleResultOrigin


class ResultListQuerySerializer(serializers.Serializer):
    parent_id = serializers.IntegerField(required=False)
    test_name = serializers.CharField(required=False)
    start_exec_seqno = serializers.IntegerField(required=False)
    results = serializers.CharField(required=False)
    result_properties = serializers.CharField(required=False)
    requirements = serializers.CharField(required=False)
    issue = serializers.CharField(required=False)


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


class ResultIssueSerializer(serializers.Serializer):
    """One RuleResult stamp on a result - see build_rule_result_info()."""

    issue_id = serializers.IntegerField()
    issue_title = serializers.CharField()
    issue_state = serializers.ChoiceField(choices=IssueState.choices)
    bug_key = serializers.CharField(allow_null=True)
    bug_url = serializers.CharField(allow_null=True)
    category = serializers.ChoiceField(choices=IssueCategory.choices)
    expected = serializers.BooleanField(allow_null=True)
    rule_result_id = serializers.IntegerField()
    rule_id = serializers.IntegerField()
    origin = serializers.ChoiceField(choices=RuleResultOrigin.choices)


class ResultDetailsSerializer(serializers.Serializer):
    name = serializers.CharField()
    path = serializers.CharField(allow_null=True)
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
    issues = ResultIssueSerializer(many=True)
    effective_expected = serializers.BooleanField()


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


@extend_schema_field(
    {
        'oneOf': [
            {'type': 'integer', 'description': 'ID of an existing Issue to reuse.'},
            {
                'type': 'object',
                'description': 'Data to create a new Issue.',
                'properties': {
                    'title': {'type': 'string'},
                    'description': {'type': 'string', 'nullable': True},
                    'bug_key': {
                        'type': 'string',
                        'nullable': True,
                        'description': 'External bug reference, e.g. ref://JIRA/FOO-123.',
                    },
                },
                'required': ['title'],
            },
        ],
    },
)
class IssueRefField(serializers.JSONField):
    """Accepts either an existing Issue ID or a payload to create a new one."""


class ClassifyMatcherSerializer(serializers.Serializer):
    """
    The matcher of the rules for the results of one test. Its values are
    compared with those of the results exactly, so they are taken as given,
    without trimming.
    """

    test_id = serializers.IntegerField(min_value=1)
    parameters = serializers.DictField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
    )
    verdicts = serializers.ListField(child=serializers.CharField(trim_whitespace=False))
    tags = serializers.ListField(child=serializers.CharField(trim_whitespace=False))


class ClassifyOptionsRequestSerializer(serializers.Serializer):
    result_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        help_text='IDs of the results to classify; all of them from one run.',
    )

    def validate_result_ids(self, value):
        return list(dict.fromkeys(value))


class ClassifyRequestSerializer(ClassifyOptionsRequestSerializer):
    issue = IssueRefField(
        help_text='Existing issue ID, or {title, description?, bug_key?} to create a new one.',
    )
    categories = serializers.ListField(
        child=serializers.ChoiceField(choices=IssueCategory.choices),
        allow_empty=False,
        help_text='Categories to classify the results under; a rule is created for each.',
    )
    expected = serializers.BooleanField(required=False, allow_null=True)
    scope = serializers.ChoiceField(choices=['future', 'oneoff'], default='future')
    matchers = ClassifyMatcherSerializer(
        many=True,
        allow_empty=False,
        help_text=(
            'The matcher of the rules for each test among the results, one per test. '
            'Empty parameters, verdicts or tags do not restrict the rule.'
        ),
    )

    def validate_categories(self, value):
        # A repeated category would only repeat the same rule.
        return list(dict.fromkeys(value))

    def validate_matchers(self, value):
        test_ids = [matcher['test_id'] for matcher in value]
        repeated = sorted({test_id for test_id in test_ids if test_ids.count(test_id) > 1})
        if repeated:
            msg = f'More than one matcher for the tests: {repeated}.'
            raise serializers.ValidationError(msg)
        return value

    def validate_issue(self, value):
        # A new issue's fields are validated in full by the view, once the
        # project (that of the classified result) is known.
        # bool is a subclass of int, but `true` is not an issue ID.
        is_id = isinstance(value, int) and not isinstance(value, bool)
        is_id_str = isinstance(value, str) and value.isascii() and value.isdecimal()
        if is_id or is_id_str or isinstance(value, dict):
            return value
        msg = 'Expected an issue ID or an object with the new issue fields.'
        raise serializers.ValidationError(msg)


class ClassifyResponseSerializer(serializers.Serializer):
    issue_id = serializers.IntegerField()
    rule_ids = serializers.ListField(
        child=serializers.IntegerField(),
        help_text=(
            'The rules the results were stamped with: test by test, in the order the '
            'tests first appear in result_ids, and within a test in the order of '
            'categories.'
        ),
    )


class ClassifyOptionsTagsSerializer(serializers.Serializer):
    default = serializers.ListField(
        child=serializers.CharField(),
        help_text="The run's important tags, which a rule usually contains.",
    )
    allowed = serializers.ListField(
        child=serializers.CharField(),
        help_text='The important and relevant tags of the run; a rule may contain any of them.',
    )


class ClassifyOptionsTestSerializer(serializers.Serializer):
    test_id = serializers.IntegerField()
    test_name = serializers.CharField()
    test_path = serializers.CharField()
    result_ids = serializers.ListField(child=serializers.IntegerField())
    parameters = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        help_text=(
            "The parameters all the test's results have: a rule may contain any part of them."
        ),
    )
    verdicts = serializers.ListField(
        child=serializers.CharField(),
        help_text=(
            "The verdicts all the test's results have: a rule may contain any part of them."
        ),
    )


class ClassifyOptionsResponseSerializer(serializers.Serializer):
    """
    What a rule classifying the results may contain, so that a matcher can be
    checked against it before it is sent. Each test has its own rules.
    """

    run_id = serializers.IntegerField()
    tags = ClassifyOptionsTagsSerializer()
    tests = ClassifyOptionsTestSerializer(many=True)
