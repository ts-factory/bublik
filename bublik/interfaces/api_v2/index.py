# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path
from pathlib import Path

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.static import serve


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
        return serve(request, 'index.html', document_root=settings.BUBLIK_DOCS_STATIC)

    base_dir = Path(settings.BUBLIK_DOCS_STATIC).resolve()
    base_file_path = (base_dir / path).resolve()

    try:
        base_file_path.relative_to(base_dir)
    except ValueError:
        msg = f'Invalid path: {base_file_path}'
        raise Http404(msg) from None

    if base_file_path.is_dir():
        index_path = base_file_path / 'index.html'
        if index_path.exists():
            relative_path = str(index_path.relative_to(base_dir))
            return serve(request, relative_path, document_root=settings.BUBLIK_DOCS_STATIC)

    html_file_path = base_file_path.with_suffix('.html')
    if html_file_path.exists():
        relative_path = str(html_file_path.relative_to(base_dir))
        return serve(request, relative_path, document_root=settings.BUBLIK_DOCS_STATIC)

    if base_file_path.exists():
        relative_path = str(base_file_path.relative_to(base_dir))
        return serve(request, relative_path, document_root=settings.BUBLIK_DOCS_STATIC)

    raise Http404
