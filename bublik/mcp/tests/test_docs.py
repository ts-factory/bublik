# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Parsing, ranking, lookup and cache behaviour of the documentation index."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from bublik.mcp.docs.index import (
    compute_signature,
    get_index,
    parse_page,
    reset_index_cache,
    slugify,
    tokenize,
    web_path_for,
)
from bublik.mcp.docs.models import DocsUnavailableError, UnknownHeadingError, UnknownPageError
from bublik.mcp.docs.search import (
    _expand,
    _idf,
    find_page,
    find_section,
    normalize_path,
    search,
)


if TYPE_CHECKING:
    from pathlib import Path


INDEXED_PAGE_COUNT = 6

MCP_PAGE = """# MCP Setup

Connect AI agents to Bublik.

## What You Need

Bublik v2.10.4 or newer.

## Client Configuration

### OpenCode

Add this server definition:

```json
# not a heading
{"url": "https://x/mcp"}
```

### Claude

Add the claude server definition.

## Docker .env Options

Set BUBLIK_DOCKER_MCP_PORT=8001 in the .env file.
"""

INTRO_PAGE = """# Introduction

<!--toc:start-->
- [Core Components](#core-components)
<!--toc:end-->

Bublik stores test results.

## Core Components

The Test Environment runs suites.
"""

CONFIGURATION_PAGE = """<!--toc:start-->
- [Project config](#project-config)
<!--toc:end-->

Overview of configuration topics.

### Project config

See the project page.

## Report

### Project config

See the report page.
"""

CHAT_PAGE = """# AI Chat

The assistant uses the same tools as the MCP server.
"""

BLOG_NO_H1 = """We're excited to announce Bublik v2.10.5!

### What's New

MCP server rollout and history search.
"""

BLOG_WITH_H1 = """# Release v2.9.0

Dashboard improvements.
"""


def write_tree(root: Path) -> None:
    files = {
        'intro.md': INTRO_PAGE,
        'index.html': '<html/>',
        'configuration.md': CONFIGURATION_PAGE,
        'configuration.html': '<html/>',
        'configuration/mcp.md': MCP_PAGE,
        'configuration/mcp.html': '<html/>',
        'configuration/chat.md': CHAT_PAGE,
        'configuration/chat.html': '<html/>',
        'blog/release-v2.10.5.md': BLOG_NO_H1,
        'blog/release-v2.9.0.md': BLOG_WITH_H1,
        'assets/ignored.md': '# Not indexed',
    }
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')


@pytest.fixture
def docs_root(tmp_path):
    reset_index_cache()
    write_tree(tmp_path)
    yield tmp_path
    reset_index_cache()


@pytest.fixture
def index(docs_root):
    return get_index(docs_root)


class TestParsing:
    def test_title_sections_and_fences(self, docs_root):
        page = parse_page(docs_root, docs_root / 'configuration/mcp.md')
        assert page.title == 'MCP Setup'
        assert page.path == 'configuration/mcp'
        assert page.scope == 'docs'
        assert page.headings == [
            'What You Need',
            'Client Configuration',
            'OpenCode',
            'Claude',
            'Docker .env Options',
        ]
        assert page.sections[0].heading is None
        assert page.sections[0].text == 'Connect AI agents to Bublik.'
        assert page.summary == 'Connect AI agents to Bublik.'
        assert [s.level for s in page.sections] == [0, 2, 2, 3, 3, 2]
        assert page.sections[-1].anchor == 'docker-env-options'

    def test_toc_block_is_stripped(self, docs_root):
        page = parse_page(docs_root, docs_root / 'intro.md')
        assert 'toc:start' not in page.markdown
        assert 'Core Components' in page.markdown
        assert page.summary == 'Bublik stores test results.'
        assert all('toc:start' not in s.text for s in page.sections)

    def test_h1_less_pages_get_titles_from_the_file_name(self, docs_root):
        configuration = parse_page(docs_root, docs_root / 'configuration.md')
        assert configuration.title == 'Configuration'
        assert configuration.summary == 'Overview of configuration topics.'
        blog = parse_page(docs_root, docs_root / 'blog/release-v2.10.5.md')
        assert blog.title == 'Release v2.10.5'
        assert blog.scope == 'blog'
        assert blog.headings == ["What's New"]

    def test_web_path(self, docs_root):
        assert web_path_for(docs_root, docs_root / 'intro.md') == ''
        assert (
            web_path_for(docs_root, docs_root / 'configuration/mcp.md') == 'configuration/mcp'
        )
        assert (
            web_path_for(docs_root, docs_root / 'blog/release-v2.9.0.md')
            == 'blog/release-v2.9.0'
        )

    def test_tokenize_keeps_identifiers_and_their_parts(self):
        tokens = tokenize('Set BUBLIK_UI_DOCS_DIR in the settings.py file')
        assert 'bublik_ui_docs_dir' in tokens
        assert {'bublik', 'ui', 'docs', 'dir', 'settings.py', 'settings', 'py'} <= set(tokens)
        assert 'the' not in tokens

    @pytest.mark.parametrize(
        ('heading', 'anchor'),
        [
            ('Docker .env Options', 'docker-env-options'),
            ("What's New", 'whats-new'),
            ('DASHBOARD_HEADER', 'dashboard_header'),
            ('Setup `BUBLIK_FQDN`', 'setup-bublik_fqdn'),
            ('`json/node_*.json`', 'jsonnode_json'),
            ('`user_name` conventions (recommended)', 'user_name-conventions-recommended'),
            ('🚀 New Feature', '-new-feature'),
            ('♻️ Code Refactoring', '\ufe0f-code-refactoring'),
        ],
    )
    def test_slugify_matches_docusaurus(self, heading, anchor):
        assert slugify(heading) == anchor

    def test_repeated_headings_get_numbered_anchors(self, docs_root):
        page = parse_page(docs_root, docs_root / 'configuration.md')
        anchors = [s.anchor for s in page.sections if s.heading]
        assert anchors == ['project-config', 'report', 'project-config-1']
        assert 'report page' in find_section(page, 'project-config-1')

    def test_skipped_directories_are_not_indexed(self, index):
        assert 'assets/ignored' not in index.pages
        assert len(index.pages) == INDEXED_PAGE_COUNT


class TestSearch:
    def test_heading_match_wins_and_sets_the_hit_heading(self, index):
        hits = search(index, 'mcp client configuration', scope='docs', limit=5)
        assert hits[0].path == 'configuration/mcp'
        assert hits[0].heading == 'Client Configuration'
        assert hits[0].anchor == 'client-configuration'
        assert hits[0].web_path == 'configuration/mcp'

    def test_title_match_outranks_body_match(self, index):
        hits = search(index, 'mcp', scope='docs', limit=5)
        assert [h.path for h in hits[:2]] == ['configuration/mcp', 'configuration/chat']

    def test_scope_filters_pages(self, index):
        blog = search(index, 'mcp', scope='blog', limit=5)
        assert blog
        assert all(h.path.startswith('blog/') for h in blog)
        docs = search(index, 'mcp', scope='docs', limit=5)
        assert docs
        assert not any(h.path.startswith('blog/') for h in docs)
        everything = {h.path for h in search(index, 'mcp', scope='all', limit=10)}
        assert {'configuration/mcp', 'blog/release-v2.10.5'} <= everything

    def test_snippet_contains_the_matched_term(self, index):
        hit = search(index, 'BUBLIK_DOCKER_MCP_PORT', scope='docs', limit=5)[0]
        assert 'BUBLIK_DOCKER_MCP_PORT=8001' in hit.snippet
        assert hit.heading == 'Docker .env Options'

    def test_empty_or_stopword_query(self, index):
        assert search(index, '   ', scope='all', limit=5) == []
        assert search(index, 'the and of', scope='all', limit=5) == []

    def test_prefix_terms_use_the_df_of_their_matches(self, index):
        pages = list(index.pages.values())
        config = _expand(index, 'config')
        assert {'config', 'configuration'} <= config
        assert _idf(pages, config) < _idf(pages, frozenset({'config'}))

    def test_limit(self, index):
        limit = 2
        assert len(search(index, 'bublik', scope='all', limit=limit)) == limit


class TestLookup:
    @pytest.mark.parametrize(
        'raw',
        [
            'configuration/mcp',
            '/configuration/mcp.md',
            'docs/configuration/mcp',
            '/docs/configuration/mcp.html',
            'configuration//mcp/',
            ' configuration/mcp ',
        ],
    )
    def test_normalize_path_spellings(self, raw):
        assert normalize_path(raw) == 'configuration/mcp'

    @pytest.mark.parametrize('raw', ['../../etc/passwd', 'configuration/../../x', './intro'])
    def test_parent_segments_are_rejected(self, index, raw):
        with pytest.raises(ValueError, match='Invalid documentation path'):
            find_page(index, raw)

    def test_absolute_paths_outside_the_tree_are_unknown(self, index):
        with pytest.raises(UnknownPageError):
            find_page(index, '/etc/passwd')

    def test_unknown_page_suggests_close_paths(self, index):
        with pytest.raises(UnknownPageError) as exc_info:
            find_page(index, 'configuration/mcp-setup')
        assert 'configuration/mcp' in exc_info.value.suggestions

    def test_find_section_includes_children(self, index):
        page = index.pages['configuration/mcp']
        by_text = find_section(page, 'Client Configuration')
        by_slug = find_section(page, '#client-configuration')
        assert by_text == by_slug
        assert by_text.startswith('## Client Configuration')
        assert '### OpenCode' in by_text
        assert '### Claude' in by_text
        assert 'Docker .env' not in by_text
        assert find_section(page, 'Claude').startswith('### Claude')

    def test_find_section_unknown_heading(self, index):
        with pytest.raises(UnknownHeadingError) as exc_info:
            find_section(index.pages['configuration/mcp'], 'Nope')
        assert exc_info.value.headings == index.pages['configuration/mcp'].headings


def _bump_mtime(path: Path, seconds: int = 120) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + seconds * 10**9))


class TestCache:
    def test_index_is_reused_until_the_tree_changes(self, docs_root):
        first = get_index(docs_root)
        assert get_index(docs_root) is first
        first.checked_at = 0  # force a signature check
        assert get_index(docs_root) is first

    def test_changed_file_rebuilds_the_index(self, docs_root):
        first = get_index(docs_root)
        chat = docs_root / 'configuration/chat.md'
        chat.write_text('# AI Chat\n\nNow with rebuilt content.\n', encoding='utf-8')
        _bump_mtime(chat)
        first.checked_at = 0
        second = get_index(docs_root)
        assert second is not first
        assert 'rebuilt content' in second.pages['configuration/chat'].markdown

    def test_deleted_file_drops_the_page(self, docs_root):
        first = get_index(docs_root)
        (docs_root / 'configuration/chat.md').unlink()
        first.checked_at = 0
        assert 'configuration/chat' not in get_index(docs_root).pages

    def test_signature_skips_files_removed_mid_scan(self, docs_root):
        files = [docs_root / 'intro.md', docs_root / 'gone.md']
        assert compute_signature(files) == compute_signature(files[:1])

    def test_missing_or_empty_directory(self, tmp_path):
        reset_index_cache()
        with pytest.raises(DocsUnavailableError):
            get_index(tmp_path / 'nope')
        with pytest.raises(DocsUnavailableError):
            get_index(tmp_path)
