# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class LatestCommitSerializer(serializers.Serializer):
    commit_date = serializers.DateField()
    commit_rev = serializers.CharField()
    commit_summary = serializers.CharField()


class RepoRevisionsSerializer(serializers.Serializer):
    repo_url = serializers.CharField()
    repo_branch = serializers.CharField()
    repo_tag = serializers.CharField()
    latest_commit = LatestCommitSerializer()
