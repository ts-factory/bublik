# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Documentation tools over the built site at ``settings.BUBLIK_DOCS_STATIC``."""

from __future__ import annotations

from pathlib import Path
import re

from django.conf import settings
from rest_framework.exceptions import ValidationError

from bublik.mcp.docs.index import get_index
from bublik.mcp.docs.models import (
    SCOPE_ALL,
    SCOPE_BLOG,
    SCOPES,
    DocPage,
    DocsIndex,
    DocsUnavailableError,
    UnknownHeadingError,
    UnknownPageError,
)
from bublik.mcp.docs.search import find_page, find_section, search


DEFAULT_LIMIT = 10
MAX_LIMIT = 50
VERSION_RE = re.compile(r'v(\d+(?:\.\d+)*)')


def _load_index() -> DocsIndex:
    root = str(getattr(settings, 'BUBLIK_DOCS_STATIC', '') or '').strip()
    if not root:
        raise DocsUnavailableError(root)
    return get_index(Path(root))


def build_url(web_path: str) -> str:
    """Absolute page URL when ``BUBLIK_FQDN`` is set, origin-relative otherwise."""
    prefix = settings.URL_PREFIX.strip('/')
    base = f'/{prefix}/docs' if prefix else '/docs'
    path = f'{base}/{web_path}' if web_path else f'{base}/'
    fqdn = getattr(settings, 'BUBLIK_FQDN', '').strip().rstrip('/')
    return f'{fqdn}{path}'


def _unavailable_message(exc: DocsUnavailableError) -> str:
    return (
        'Documentation is not deployed on this server '
        f'(BUBLIK_DOCS_STATIC={exc.root}); answer from general knowledge and say so.'
    )


def _validate_scope(scope: str) -> None:
    if scope not in SCOPES:
        msg = f'scope must be one of {", ".join(SCOPES)}, got {scope!r}'
        raise ValidationError(msg)


def _pages_in_scope(index: DocsIndex, scope: str) -> list[DocPage]:
    return [p for p in index.pages.values() if scope in (SCOPE_ALL, p.scope)]


def _version_key(page: DocPage) -> tuple[int, ...]:
    match = VERSION_RE.search(page.path)
    return tuple(int(part) for part in match.group(1).split('.')) if match else ()


def _ordered(pages: list[DocPage]) -> list[DocPage]:
    docs = sorted((p for p in pages if p.scope != SCOPE_BLOG), key=lambda p: p.path.split('/'))
    blog = sorted((p for p in pages if p.scope == SCOPE_BLOG), key=_version_key, reverse=True)
    return docs + blog


def search_docs(query: str, limit: int = DEFAULT_LIMIT, scope: str = 'docs') -> dict:
    query = (query or '').strip()
    if not query:
        msg = 'query must not be blank'
        raise ValidationError(msg)
    _validate_scope(scope)
    if not 1 <= limit <= MAX_LIMIT:
        msg = f'limit must be between 1 and {MAX_LIMIT}'
        raise ValidationError(msg)
    result = {'query': query, 'scope': scope, 'total_pages': 0, 'hits': [], 'message': None}
    try:
        index = _load_index()
    except DocsUnavailableError as exc:
        result['message'] = _unavailable_message(exc)
        return result
    hits = search(index, query, scope=scope, limit=limit)
    result['total_pages'] = len(_pages_in_scope(index, scope))
    result['hits'] = [
        {
            'path': hit.path,
            'title': hit.title,
            'heading': hit.heading,
            'anchor': hit.anchor,
            'snippet': hit.snippet,
            'score': hit.score,
            'url': build_url(hit.web_path),
        }
        for hit in hits
    ]
    if not hits:
        result['message'] = (
            'No pages matched; try different words, another scope, '
            'or list_docs for the table of contents.'
        )
    return result


def get_doc(path: str, heading: str | None = None) -> dict:
    if not (path or '').strip():
        msg = 'path must not be blank'
        raise ValidationError(msg)
    try:
        index = _load_index()
    except DocsUnavailableError as exc:
        return {'path': path.strip(), 'markdown': None, 'message': _unavailable_message(exc)}
    try:
        page = find_page(index, path)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except UnknownPageError as exc:
        hint = f' Did you mean: {", ".join(exc.suggestions)}?' if exc.suggestions else ''
        msg = (
            f'Unknown documentation page {exc.path!r}.{hint} '
            'Use search_docs or list_docs to find the right path.'
        )
        raise ValidationError(msg) from exc
    markdown = page.markdown
    if heading:
        try:
            markdown = find_section(page, heading)
        except UnknownHeadingError as exc:
            msg = (
                f'Page {page.path!r} has no heading {exc.heading!r}. '
                f'Available headings: {"; ".join(exc.headings) or "none"}'
            )
            raise ValidationError(msg) from exc
    return {
        'path': page.path,
        'title': page.title,
        'scope': page.scope,
        'url': build_url(page.web_path),
        'heading': heading or None,
        'headings': page.headings,
        'markdown': markdown,
        'message': None,
    }


def list_docs(scope: str = 'docs') -> dict:
    _validate_scope(scope)
    result = {'scope': scope, 'count': 0, 'pages': [], 'message': None}
    try:
        index = _load_index()
    except DocsUnavailableError as exc:
        result['message'] = _unavailable_message(exc)
        return result
    pages = _ordered(_pages_in_scope(index, scope))
    result['count'] = len(pages)
    result['pages'] = [
        {
            'path': page.path,
            'title': page.title,
            'summary': page.summary,
            'category': page.path.split('/')[0] if '/' in page.path else '',
            'url': build_url(page.web_path),
        }
        for page in pages
    ]
    return result
