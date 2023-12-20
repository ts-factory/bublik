# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand

from bublik.data.serializers import (
    ExpectationSerializer,
    MeasurementSerializer,
    MetaSerializer,
    TestArgumentSerializer,
    ViewSerializer,
)


class Command(BaseCommand):
    def handle(self, *args, **options):
        '''The order of hashed models updating is valuable.'''

        hashed_models = [
            MetaSerializer,
            MeasurementSerializer,
            ViewSerializer,
            ExpectationSerializer,
            TestArgumentSerializer,
        ]

        for hashed_model in hashed_models:
            model_name = hashed_model.Meta.model._meta.object_name
            objects_updated = hashed_model.update_hash_for_many()

            if not objects_updated:
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully update hash for {objects_updated} {model_name} objects',
                ),
            )
