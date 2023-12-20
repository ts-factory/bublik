# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from django.conf import settings
from django.shortcuts import render


def render_react(request):
    react_index = os.path.join(settings.BUBLIK_UI_STATIC, 'index.html')
    return render(request, react_index)
