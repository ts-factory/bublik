# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.contrib import admin

from bublik.data import models


admin.site.register(models.Test)
admin.site.register(models.TestArgument)
admin.site.register(models.TestIteration)
admin.site.register(models.TestIterationRelation)
admin.site.register(models.TestIterationResult)
admin.site.register(models.Meta)
admin.site.register(models.Measurement)
admin.site.register(models.MeasurementResult)
admin.site.register(models.MetaCategory)
admin.site.register(models.MetaPattern)
admin.site.register(models.MetaResult)
admin.site.register(models.Reference)
admin.site.register(models.Expectation)
admin.site.register(models.ExpectMeta)
admin.site.register(models.View)
admin.site.register(models.ChartView)
