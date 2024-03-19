# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from base64 import b64encode
from hashlib import blake2s

from bublik.core.hash_system import HashedModelSerializer
from bublik.data.models import EndpointURL


__all__ = [
    'EndpointURLSerializer',
]


def blake2sb64hex(obj):
    if isinstance(obj, str):
        obj = obj.encode('utf-8')
    enc_key = blake2s(obj, digest_size=8).digest()
    return b64encode(enc_key).decode('utf-8')


class EndpointURLSerializer(HashedModelSerializer):
    class Meta:
        model = EndpointURL
        fields = ('created', 'view', 'endpoint')

    def create_hash(self, data, hasher=blake2sb64hex):
        return super().create_hash(data, hasher)

    def update_hash(self, hasher=blake2sb64hex):
        return super().update_hash(hasher)
