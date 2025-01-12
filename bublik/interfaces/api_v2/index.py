# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from django.conf import settings
from django.shortcuts import render
from django.views.static import serve
from django.http import Http404


def render_react(request):
    react_index = os.path.join(settings.BUBLIK_UI_STATIC, 'index.html')
    return render(request, react_index)


def render_docs(request):
    docs_subpath = '/docs/'
    docs_index = request.path.find(docs_subpath)
    if docs_index == -1:
        raise Http404

    path = request.path[docs_index + len(docs_subpath) :].rstrip('/')

    if not path:
        return serve(request, 'index.html', document_root=settings.BUBLIK_UI_DOCS_DIR)

    base_file_path = os.path.join(settings.BUBLIK_UI_DOCS_DIR, path)
    html_file_path = base_file_path + '.html'

    if os.path.isdir(base_file_path):
        index_path = os.path.join(base_file_path, 'index.html')
        if os.path.exists(index_path):
            relative_path = os.path.join(path, 'index.html')
            return serve(request, relative_path, document_root=settings.BUBLIK_UI_DOCS_DIR)

    if os.path.exists(html_file_path):
        return serve(request, path + '.html', document_root=settings.BUBLIK_UI_DOCS_DIR)

    if os.path.exists(base_file_path):
        return serve(request, path, document_root=settings.BUBLIK_UI_DOCS_DIR)

    raise Http404
