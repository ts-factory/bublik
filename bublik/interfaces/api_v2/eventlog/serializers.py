# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class EventLogSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    facility = serializers.CharField()
    severity = serializers.CharField()
    msg = serializers.CharField()
