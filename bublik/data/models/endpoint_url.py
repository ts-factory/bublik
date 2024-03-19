# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.


from django.db import models


__all__ = [
    'EndpointURL',
]


class EndpointURL(models.Model):
    '''
    The endpoints of Bublik URLs and the corresponding hashes used for short URLs.
    '''

    hashable = (
        'view',
        'endpoint',
    )
    created = models.DateTimeField(help_text='Timestamp of the short URL creation.')
    view = models.TextField(help_text='The Bublik URL view.')
    endpoint = models.TextField(help_text='The Bublik URL endpoint.')
    hash = models.CharField(
        max_length=16,
        unique=True,
        help_text='The Bublik URL endpoint hash.',
    )

    class Meta:
        db_table = 'bublik_endpoint_url'

    def __repr__(self):
        return (
            f'EndpointURL(created={self.created!r}, view={self.view!r}, '
            f'endpoint={self.endpoint!r}, hash={self.hash!r})'
        )
