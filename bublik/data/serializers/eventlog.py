# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework.serializers import ModelSerializer

from bublik.data.models import EventLog


__all__ = ['EventLogSerializer']


class EventLogSerializer(ModelSerializer):
    class Meta:
        model = EventLog
        fields = ('pk', 'timestamp', 'facility', 'severity', 'msg')
