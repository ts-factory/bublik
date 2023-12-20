# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict
import copy

from deepdiff import DeepDiff, DeepHash
from django.db import transaction
from django_bulk_update.helper import bulk_update
from rest_framework.serializers import ModelSerializer


# Force using SHA256 hex hash by default
DEFAULT_HASHER = DeepHash.sha256hex


class HashedModelSerializer(ModelSerializer):
    '''
    Allows validating and creating hashed instances.

    The order of data items as well as its nested list items order
    does not influence the hash produced by DeepHash class.
    '''

    def is_valid(self, *args, **kwargs):
        status_valid = super().is_valid(*args, **kwargs)
        if status_valid:
            self._validated_data_and_hash = copy.deepcopy(self._validated_data)
            self._validated_data_and_hash.update(
                {'hash': self.create_hash(self._validated_data)},
            )
        return status_valid

    def create_hash(self, data, hasher=DEFAULT_HASHER):
        """
        Getting an object by its hash this function checks if its fields
        are equal to the fields accepted for a hash creation.
        If they aren't, that's means a hash collision. To solve that
        HASH_SALT will be applied to the created hash until it's OK.
        """

        salted_data = {'data': copy.deepcopy(data)}

        while True:
            data_hash = DeepHash(salted_data, hasher=hasher)[salted_data]
            obj = self.get_or_none(hash=data_hash)

            if not obj:
                return data_hash

            data = OrderedDict(data)
            obj_data = OrderedDict(self.__class__(instance=obj).data)
            diff = DeepDiff(data, obj_data, ignore_order=True)
            if not diff:
                '''Just Get the same object'''
                return data_hash

            '''
            This code may be executed in the following cases:
            - creating new object a hash collision occurred;
            - there is a difference between the data passed for validation
              (which is used for a hash generation) and - for creation an instance.

            TODO: msg should be added to the internal logger
            model_name = self.Meta.model._meta.object_name
            msg = (
                f"Hash collision occurred for {model_name} instance "
                f"with data: {data}. The following hash were produced: "
                f"{data_hash}, but it represents different data: {obj_data}."
                f"The diff is: {diff}. This is {salted_data['salt']} collision."
            )
            '''
            salted_data['salt'] = salted_data.get('salt', 0) + 1

    def get_or_none(self, **kwargs):
        try:
            return self.Meta.model.objects.get(**kwargs)
        except Exception:
            return None

    def create(self):
        return super().create(self.validated_data_and_hash)

    def get_or_create(self):
        return self.Meta.model.objects.get_or_create(**self.validated_data_and_hash)

    @property
    def validated_data_and_hash(self):
        if not hasattr(self, '_validated_data_and_hash'):
            msg = 'You must call `.is_valid()` before accessing `.validated_data_and_hash`.'
            raise AssertionError(msg)
        return self._validated_data_and_hash

    def update_hash(self, hasher=DEFAULT_HASHER):
        if self.instance is None:
            msg = (
                'You can call `.update_hash()` only if '
                'an `instance` keyword argument is passed to a serializer.'
            )
            raise AssertionError(msg)

        return self.update(self.instance, {'hash': self.create_hash(self.data, hasher=hasher)})

    @transaction.atomic
    def update(self, instance, validated_data, hasher=DEFAULT_HASHER):
        instance = super().update(instance, validated_data)
        if 'hash' not in validated_data:
            return self.update_hash(hasher=hasher)
        return instance

    @classmethod
    def update_hash_for_many(cls, instances=None):
        if instances is None:
            instances = []
        if not instances:
            instances = cls.Meta.model.objects.all()

        for instance in instances:
            cls(instance=instance).update_hash()

        # TODO: with Django 2.2+
        return bulk_update(instances, update_fields=['hash'])
