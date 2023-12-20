# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import ProhibitNullCharactersValidator
from django.forms import fields
from django.utils.translation import gettext_lazy as _

from bublik.core.run.tests_organization import get_test_by_full_path


class TestNameField(fields.CharField):
    default_error_messages = {
        'invalid': _('Enter a valid test name.'),
    }

    def __init__(self, *, strip=True, empty_value='', **kwargs):
        self.strip = strip
        self.empty_value = empty_value
        super().__init__(**kwargs)
        self.validators.append(ProhibitNullCharactersValidator())

    def to_python(self, value):
        '''Return a string.'''
        if value not in self.empty_values:
            try:
                test = get_test_by_full_path(value)
                return test
            except ObjectDoesNotExist:
                raise ValidationError(
                    self.error_messages['invalid'],
                    code='invalid',
                ) from ValidationError
        return self.empty_value
