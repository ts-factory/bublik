# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import logging
from pprint import pformat

from deepdiff import DeepHash
from django.test import TestCase

from bublik.core.hash_system import HashedModelSerializer
from bublik.core.shortcuts import serialize
from bublik.data import models
from bublik.data.serializers import (
    ExpectationSerializer,
    ExpectMetaReadSerializer,
    MetaSerializer,
    ReferenceSerializer,
)
from bublik.tests.fake_generator import gen_expectation_data, gen_meta_data


logger = logging.getLogger()


class HashSystemTest(TestCase):
    """Test hash system based on HashedModelSerializer."""

    def test_generate_hash(self):
        """
        Check that a hash generated in HashedModelSerializer does not depend
        on the ordering: neither dict keys nor its nested lists order.
        It should be also true in case of dict - for any named iterable
        in case of list - for any iterable.
        """

        logger.info('TEST: Compare hashes of 3 the same dicts ' 'with unordered elements')

        d1 = {1: 3, 0: {0: 0, 1: [0, 1, 2]}}
        d2 = {1: 3, 0: {1: [0, 1, 2], 0: 0}}
        d3 = {1: 3, 0: {0: 0, 1: [2, 0, 1]}}

        h1 = HashedModelSerializer(data=d1).create_hash(d1)
        h2 = HashedModelSerializer(data=d2).create_hash(d2)
        h3 = HashedModelSerializer(data=d3).create_hash(d3)

        logger.info(f'{d1} -> {h1}')
        logger.info(f'{d2} -> {h2}')
        logger.info(f'{d3} -> {h3}')

        self.assertTrue(h1 == h2 == h3)
        logger.info('PASSED')

    def test_create_hashed_object(self):
        """Get or create hashed object through serializer."""

        logger.info('TEST: Create hashed objects')

        def test(serializer_class, data):
            model = serializer_class.Meta.model

            logger.info(f'Create {model._meta.object_name} object')
            logger.info(f'Incoming data:\n{pformat(data)}')

            serializer = serialize(serializer_class, data)
            o, _ = serializer.get_or_create()

            logger.info(f'Serialized data:\n{pformat(serializer.data)}')
            logger.info(f'Hash: {o.hash}')

            self.assertIsInstance(o, model)

        test(MetaSerializer, gen_meta_data())
        test(ExpectationSerializer, gen_expectation_data())

        logger.info('PASSED')

    def test_update_hashed_object(self):
        """
        Update hashed object through serializer.
        Check whether its hash was updated properly.
        """

        logger.info('TEST: Check hash recalculation after instance updating')

        ms = serialize(MetaSerializer, gen_meta_data(set_data={'value': 'init'}))
        logger.info(f'Create Meta object:\n{pformat(ms.validated_data)}')

        m1, _ = ms.get_or_create()
        m1_hash = m1.hash
        logger.info(f'Hash: {m1_hash}')

        logger.info("Update with value='new'")
        ms2 = serialize(MetaSerializer, instance=m1, data={'value': 'new'}, partial=True)
        m2 = ms2.update(m1, ms2.validated_data)
        m2_hash = m2.hash
        logger.info(f'Value: {m2.value}, hash: {m2_hash}')

        logger.info("Update again with value='init'")
        ms3 = serialize(MetaSerializer, instance=m2, data={'value': 'init'}, partial=True)
        ms3.save()
        m3 = ms3.update_hash()
        m3_hash = m3.hash
        logger.info(f'Value: {m3.value}, hash: {m3_hash}')

        self.assertTrue(m1_hash == m3_hash and m1_hash != m2_hash and m2_hash != m3_hash)
        logger.info('PASSED')

    def test_update_many_hashed(self):
        """Recalculate hashes for a queryset through its model serializer."""

        logger.info('TEST: Recalculate hashes for a set of hashed objects')

        for i in range(3):
            serializer = serialize(MetaSerializer, gen_meta_data(i))
            serializer.get_or_create()

        metas = models.Meta.objects
        hashes_before_update = list(metas.all().values_list('hash', flat=True))
        MetaSerializer.update_hash_for_many()
        hashes_after_update = list(metas.all().values_list('hash', flat=True))

        self.assertListEqual(hashes_before_update, hashes_after_update, msg='PASS')
        logger.info('PASSED')
