# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Data shapes shared by the documentation index, search and service layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


SCOPE_DOCS = 'docs'
SCOPE_BLOG = 'blog'
SCOPE_ALL = 'all'
SCOPES = (SCOPE_DOCS, SCOPE_BLOG, SCOPE_ALL)


class DocsUnavailableError(Exception):
    """The documentation directory is missing or holds no markdown pages."""

    def __init__(self, root: Path | str):
        self.root = root
        super().__init__(f'no documentation pages found under {root}')


class UnknownPageError(LookupError):
    """No page matches the requested path; ``suggestions`` are close paths."""

    def __init__(self, path: str, suggestions: list[str]):
        self.path = path
        self.suggestions = suggestions
        super().__init__(path)


class UnknownHeadingError(LookupError):
    """No section matches the requested heading; ``headings`` lists the page's."""

    def __init__(self, heading: str, headings: list[str]):
        self.heading = heading
        self.headings = headings
        super().__init__(heading)


@dataclass(frozen=True)
class DocSection:
    """One heading-delimited chunk; the text before the first heading has level 0."""

    heading: str | None
    level: int
    anchor: str
    markdown: str
    text: str
    text_lower: str
    token_counts: dict[str, int]
    heading_tokens: tuple[str, ...]


@dataclass(frozen=True)
class DocPage:
    path: str  # 'configuration/mcp': relative, no extension, no leading slash
    scope: str  # SCOPE_DOCS or SCOPE_BLOG
    title: str
    summary: str
    web_path: str  # path of the HTML twin under /docs/, '' for the site root
    markdown: str  # full page with the generated toc block removed
    sections: tuple[DocSection, ...]
    title_tokens: tuple[str, ...]
    tokens: frozenset[str]  # every token of the page, title included

    @property
    def headings(self) -> list[str]:
        return [section.heading for section in self.sections if section.heading]


@dataclass
class DocsIndex:
    root: Path
    pages: dict[str, DocPage]
    signature: tuple
    checked_at: float
    vocabulary: tuple[str, ...]  # sorted, for prefix lookups


@dataclass(frozen=True)
class SearchHit:
    path: str
    title: str
    heading: str | None
    anchor: str
    snippet: str
    score: float
    web_path: str
