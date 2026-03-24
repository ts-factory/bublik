# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import typing
from uuid import uuid4

from django.db import models


class AnalyticsEvent(models.Model):
    event_uuid = models.UUIDField(default=uuid4, unique=True, db_index=True)
    event_type = models.CharField(max_length=64, db_index=True)
    event_name = models.CharField(max_length=128, blank=True, default='')
    path = models.CharField(max_length=512, blank=True, default='', db_index=True)
    anon_id = models.CharField(max_length=128, blank=True, default='', db_index=True)
    session_id = models.CharField(max_length=128, blank=True, default='')
    browser_name = models.CharField(max_length=128, blank=True, default='')
    browser_version = models.CharField(max_length=64, blank=True, default='')
    os_name = models.CharField(max_length=128, blank=True, default='')
    user_agent = models.CharField(max_length=512, blank=True, default='')
    app_version = models.CharField(max_length=64, blank=True, default='', db_index=True)
    payload = models.JSONField(blank=True, default=dict)
    occurred_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering: typing.ClassVar = ['-occurred_at', '-id']
        indexes: typing.ClassVar = [
            models.Index(fields=['event_type', 'occurred_at']),
            models.Index(fields=['event_name', 'occurred_at']),
            models.Index(fields=['path', 'occurred_at']),
        ]

    def __str__(self):
        if self.event_name:
            return f'{self.event_type}:{self.event_name} [{self.event_uuid}]'

        return f'{self.event_type} [{self.event_uuid}]'
