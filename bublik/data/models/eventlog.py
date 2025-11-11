# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
'''
Contains models responsible for storing data on events happening in Bublik.
'''

from django.db import models


__all__ = ['EventLog']


class EventLog(models.Model):
    '''
    Stores data on events such as start and finish timestamps, facility and
    message.
    '''

    class FacilityChoices(models.TextChoices):
        IMPORTRUNS = 'importruns'
        META_CATEGORIZATION = 'meta_categorization'
        ADD_TAGS = 'add_tags'
        CELERY = 'celery'

    class SeverityChoices(models.TextChoices):
        INFO = 'info'
        ERR = 'err'
        WARNING = 'warning'
        DEBUG = 'debug'

    timestamp = models.DateTimeField(
        auto_now_add=True,
        help_text='The start timestamp of the event.',
    )
    facility = models.CharField(
        choices=FacilityChoices.choices,
        max_length=64,
        null=False,
        help_text='The facility that the event is created by.',
    )
    severity = models.CharField(
        choices=SeverityChoices.choices,
        max_length=64,
        null=False,
        help_text='The severity of the event.',
    )
    msg = models.TextField(null=True, help_text='The message forwarded from facility.')

    class Meta:
        db_table = 'bublik_eventlog'

    def __repr__(self):
        return (
            f'Event(pk={self.pk!r}, timestamp={self.timestamp!r}, facility={self.facility!r}, '
            f'severity={self.severity!r}, msg={self.msg!r})'
        )
