# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Pure document rendering for the ``generate_file`` chat tool.

Renders agent-authored content to the bytes of a downloadable file
(pdf/docx/xlsx/csv/md/html/json/txt). This module is deliberately free of
Django, S3 and async concerns -- it is CPU-bound, synchronous and directly
unit-testable; the tool wiring (deps, size limits, persistence) lives in
:mod:`bublik.ai.files`.

WeasyPrint is imported lazily inside the PDF renderer: importing it pulls in
native pango/cairo libraries that development machines may lack, and that
must not break the rest of the chat app.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Literal

from bs4 import BeautifulSoup
from html4docx import HtmlToDocx
import markdown
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic_ai import ModelRetry


FileFormat = Literal['pdf', 'docx', 'xlsx', 'md', 'html', 'csv', 'json', 'txt']

CONTENT_TYPES: dict[str, str] = {
    'pdf': 'application/pdf',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'md': 'text/markdown',
    'html': 'text/html',
    'csv': 'text/csv',
    'json': 'application/json',
    'txt': 'text/plain',
}

# Formats whose input is the `content` string (markdown or raw text);
# the rest (xlsx, csv) take tabular `rows`, csv also accepting raw `content`.
_CONTENT_FORMATS = frozenset({'pdf', 'docx', 'md', 'html', 'json', 'txt'})

_MARKDOWN_EXTENSIONS = ['tables', 'fenced_code']

# Minimal print stylesheet for PDF output; DejaVu is installed in the image.
_PDF_CSS = """
@page { size: A4; margin: 2cm; }
body { font-family: "DejaVu Sans", sans-serif; font-size: 10pt; line-height: 1.45; }
h1, h2, h3, h4 { line-height: 1.2; }
table { border-collapse: collapse; width: 100%; margin: 0.5em 0; }
th, td { border: 1px solid #999; padding: 4px 8px; text-align: left; }
th { background: #eee; }
code, pre { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; }
pre { background: #f5f5f5; padding: 8px; white-space: pre-wrap; }
"""


def _reject_remote(url: str, *args, **kwargs):
    """WeasyPrint URL fetcher that refuses every non-``data:`` URL.

    Model-authored markdown can reference arbitrary URLs (images,
    stylesheets); fetching them from inside a host-network container would be
    an SSRF vector, so only inline ``data:`` resources are allowed.
    """
    if url.startswith('data:'):
        from weasyprint import default_url_fetcher  # noqa: PLC0415

        return default_url_fetcher(url)
    msg = f'remote resources are not allowed in generated files: {url}'
    raise ValueError(msg)


def _markdown_to_html(content: str) -> str:
    return markdown.markdown(content, extensions=_MARKDOWN_EXTENSIONS)


def _render_pdf(content: str, title: str) -> bytes:
    # Lazy: importing weasyprint dlopens pango/cairo (see module docstring).
    import weasyprint  # noqa: PLC0415

    body = _markdown_to_html(content)
    html = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<title>{title}</title><style>{_PDF_CSS}</style></head>'
        f'<body>{body}</body></html>'
    )
    return weasyprint.HTML(string=html, url_fetcher=_reject_remote).write_pdf()


def _render_docx(content: str) -> bytes:
    # html4docx fetches image URLs with urllib.request.urlopen() and opens local
    # paths; unlike WeasyPrint it has no url_fetcher hook, so we strip every
    # <img> tag from the model-authored markdown before HTML parsing.
    html = _markdown_to_html(content)
    soup = BeautifulSoup(html, 'html.parser')
    for img in soup.find_all('img'):
        img.decompose()
    parser = HtmlToDocx()
    parser.table_style = 'Table Grid'
    document = parser.parse_html_string(str(soup))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _render_xlsx(rows: list[list], title: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    # Sheet titles are limited to 31 chars and a few forbidden characters.
    sheet.title = re.sub(r'[\\/*?:\[\]]', '_', title)[:31] or 'Sheet1'
    for row in rows:
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _render_csv(rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return buffer.getvalue().encode('utf-8')


def _render_text(file_format: str, content: str) -> bytes:
    if file_format == 'json':
        # Normalize when the model sends valid JSON; keep raw otherwise.
        try:
            return json.dumps(json.loads(content), indent=2).encode('utf-8')
        except ValueError:
            return content.encode('utf-8')
    return content.encode('utf-8')


def sanitize_filename(filename: str, file_format: str) -> str:
    """Keep only the basename, drop control/special characters, fix extension."""
    name = filename.replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[^\w.\- ]', '_', name).strip('. ')
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    if not stem:
        stem = 'generated'
    return f'{stem}.{file_format}'[:255]


def render(
    file_format: str,
    content: str | None,
    rows: list[list] | None,
    title: str,
) -> bytes:
    """Render ``content``/``rows`` to the bytes of a ``file_format`` document.

    Raises :class:`pydantic_ai.ModelRetry` when the required input for the
    format is missing, so the model gets an actionable retry prompt.
    """
    if file_format in _CONTENT_FORMATS:
        if not content:
            msg = f'`content` is required for format {file_format!r}.'
            raise ModelRetry(msg)
        if file_format == 'pdf':
            return _render_pdf(content, title)
        if file_format == 'docx':
            return _render_docx(content)
        return _render_text(file_format, content)
    # xlsx / csv
    if rows:
        if file_format == 'xlsx':
            return _render_xlsx(rows, title)
        return _render_csv(rows)
    if file_format == 'csv' and content:
        return content.encode('utf-8')
    msg = f'`rows` (first row = column headers) is required for format {file_format!r}.'
    raise ModelRetry(msg)
