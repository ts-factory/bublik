# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.


class AnalyticsRouter:
    app_label = 'analytics'
    db_alias = 'analytics'

    def db_for_read(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.db_alias
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == self.app_label:
            return self.db_alias
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if self.app_label in {obj1._meta.app_label, obj2._meta.app_label}:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            return db == self.db_alias
        if db == self.db_alias:
            return False
        return None
