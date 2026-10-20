# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Index the ``.md`` twins the Docusaurus build writes next to every page."""

from __future__ import annotations

from collections import Counter
import logging
import os
from pathlib import Path
import re
import threading
import time
import unicodedata

from bublik.mcp.docs.models import (
    SCOPE_BLOG,
    SCOPE_DOCS,
    DocPage,
    DocSection,
    DocsIndex,
    DocsUnavailableError,
)


logger = logging.getLogger(__name__)

BLOG_PREFIX = 'blog/'
SKIP_DIRS = frozenset({'assets', 'img', 'node_modules'})
SUMMARY_MAX_CHARS = 200
CHECK_INTERVAL_SECONDS = 30.0

TOC_RE = re.compile(r'<!--toc:start-->.*?<!--toc:end-->[ \t]*\n?', re.S)
HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')
FENCE_RE = re.compile(r'^\s*(```|~~~)')
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.S)
IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
LINK_RE = re.compile(r'\[([^\]]*)\]\([^)]*\)')
HTML_TAG_RE = re.compile(r'<[^>\n]+>')
LINE_MARKUP_RE = re.compile(r'^\s*(?:```.*|~~~.*|:::.*|#{1,6}\s+|(?:[-+*]|\d+\.)\s+)', re.M)
INLINE_MARKUP_RE = re.compile(r'[`*~|>]')
TOKEN_RE = re.compile(r'[a-z0-9][a-z0-9_.\-]*[a-z0-9]|[a-z0-9]')
TOKEN_SPLIT_RE = re.compile(r'[._\-]+')
SLUG_KEEP_CHARS = frozenset('_- ')
STOPWORDS = frozenset(
    {
        'a',
        'an',
        'and',
        'are',
        'as',
        'at',
        'be',
        'by',
        'can',
        'for',
        'how',
        'i',
        'in',
        'is',
        'it',
        'of',
        'on',
        'or',
        'that',
        'the',
        'this',
        'to',
        'with',
        'you',
        'your',
    },
)


def tokenize(text: str) -> list[str]:
    """Lowercase tokens; identifiers are kept whole and also split into parts."""
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text.lower()):
        if raw not in STOPWORDS:
            tokens.append(raw)
        tokens.extend(
            part
            for part in TOKEN_SPLIT_RE.split(raw)
            if part and part != raw and part not in STOPWORDS
        )
    return tokens


def slugify(heading: str) -> str:
    """The heading id Docusaurus (github-slugger) generates, before deduplication."""
    kept = (
        ch
        for ch in heading.lower()
        if ch in SLUG_KEEP_CHARS or unicodedata.category(ch)[0] in 'LMN'
    )
    return ''.join(kept).replace(' ', '-')


def unique_anchors(headings: list[str | None]) -> list[str]:
    """Slug each heading, suffixing repeats with ``-1``, ``-2`` like Docusaurus."""
    occurrences: dict[str, int] = {}
    anchors: list[str] = []
    for heading in headings:
        if not heading:
            anchors.append('')
            continue
        base = anchor = slugify(heading)
        while anchor in occurrences:
            occurrences[base] += 1
            anchor = f'{base}-{occurrences[base]}'
        occurrences[anchor] = 0
        anchors.append(anchor)
    return anchors


def to_plain_text(markdown: str) -> str:
    """Strip markdown and html decoration, keeping link text and code content."""
    text = HTML_COMMENT_RE.sub(' ', markdown)
    text = IMAGE_RE.sub(' ', text)
    text = LINK_RE.sub(r'\1', text)
    text = HTML_TAG_RE.sub(' ', text)
    text = LINE_MARKUP_RE.sub('', text)
    text = INLINE_MARKUP_RE.sub(' ', text)
    return ' '.join(text.split())


def humanize_stem(stem: str) -> str:
    """``release-v2.10.5`` -> ``Release v2.10.5``, the title of an H1-less page."""
    words = stem.replace('-', ' ').replace('_', ' ').strip()
    return words[:1].upper() + words[1:]


def _outside_fences(lines: list[str]):
    """Yield ``(index, line, in_fence)`` tracking fenced code blocks."""
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            yield i, line, True
            continue
        yield i, line, in_fence


def _extract_title(lines: list[str]) -> tuple[str | None, list[str]]:
    """The leading H1 as the title, and the body without it."""
    for i, line, in_fence in _outside_fences(lines):
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match is None:
            continue
        if len(match.group(1)) == 1:
            return match.group(2).strip(), lines[:i] + lines[i + 1 :]
        break
    return None, lines


def _split_sections(lines: list[str]) -> list[tuple[str | None, int, list[str]]]:
    sections: list[tuple[str | None, int, list[str]]] = [(None, 0, [])]
    for _, line, in_fence in _outside_fences(lines):
        match = None if in_fence else HEADING_RE.match(line)
        if match is not None:
            sections.append((match.group(2).strip(), len(match.group(1)), [line]))
        else:
            sections[-1][2].append(line)
    return sections


def _build_section(
    heading: str | None,
    level: int,
    lines: list[str],
    anchor: str,
) -> DocSection:
    markdown = '\n'.join(lines).strip()
    text = to_plain_text(markdown)
    return DocSection(
        heading=heading,
        level=level,
        anchor=anchor,
        markdown=markdown,
        text=text,
        text_lower=text.lower(),
        token_counts=dict(Counter(tokenize(text))),
        heading_tokens=tuple(tokenize(heading)) if heading else (),
    )


def _is_prose_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if HEADING_RE.match(stripped) or stripped.startswith((':::', '<!--', '![', '|', '<')):
        return False
    return not FENCE_RE.match(stripped)


def _first_paragraph(lines: list[str]) -> str:
    """The first run of prose lines, flattened and truncated at a word boundary."""
    collected: list[str] = []
    for _, line, in_fence in _outside_fences(lines):
        if not in_fence and _is_prose_line(line):
            collected.append(line)
        elif collected:
            break
    text = to_plain_text('\n'.join(collected))
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    cut = text.rfind(' ', 0, SUMMARY_MAX_CHARS)
    return text[: cut if cut > 0 else SUMMARY_MAX_CHARS].rstrip() + '…'


def web_path_for(root: Path, md_file: Path) -> str:
    """The path the HTML twin is served at under ``/docs/``, ``''`` for the root."""
    rel = md_file.relative_to(root).with_suffix('')
    if md_file.with_suffix('.html').exists():
        return rel.as_posix()
    if (md_file.parent / 'index.html').exists():
        parent = rel.parent.as_posix()
        return '' if parent == '.' else parent
    return rel.as_posix()


def parse_page(root: Path, md_file: Path) -> DocPage:
    cleaned = TOC_RE.sub('', md_file.read_text(encoding='utf-8'))
    lines = cleaned.splitlines()
    title, body = _extract_title(lines)
    if title is None:
        title = humanize_stem(md_file.stem)
    path = md_file.relative_to(root).with_suffix('').as_posix()
    split = [section for section in _split_sections(body) if section[0] or section[2]]
    # Docusaurus gives H1s no id.
    anchors = unique_anchors([heading if level > 1 else None for heading, level, _ in split])
    sections = tuple(
        _build_section(heading, level, section_lines, anchor)
        for (heading, level, section_lines), anchor in zip(split, anchors, strict=True)
    )
    if not sections:
        sections = (_build_section(None, 0, [], ''),)
    title_tokens = tuple(tokenize(title))
    tokens = set(title_tokens)
    for section in sections:
        tokens.update(section.token_counts)
    return DocPage(
        path=path,
        scope=SCOPE_BLOG if path.startswith(BLOG_PREFIX) else SCOPE_DOCS,
        title=title,
        summary=_first_paragraph(body),
        web_path=web_path_for(root, md_file),
        markdown=cleaned.strip(),
        sections=sections,
        title_tokens=title_tokens,
        tokens=frozenset(tokens),
    )


def scan_md_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        files.extend(Path(dirpath) / name for name in sorted(filenames) if name.endswith('.md'))
    return files


def compute_signature(files: list[Path]) -> tuple:
    """File set, newest mtime and total size; files removed mid-scan are skipped."""
    stats = []
    for path in files:
        try:
            stats.append((path, path.stat()))
        except OSError:
            continue
    return (
        hash(tuple(path.as_posix() for path, _ in stats)),
        max((st.st_mtime_ns for _, st in stats), default=0),
        sum(st.st_size for _, st in stats),
    )


def build_index(root: Path, files: list[Path], signature: tuple) -> DocsIndex:
    pages: dict[str, DocPage] = {}
    vocabulary: set[str] = set()
    for md_file in files:
        try:
            page = parse_page(root, md_file)
        except (OSError, UnicodeDecodeError):
            logger.warning('skipping unreadable documentation page %s', md_file, exc_info=True)
            continue
        pages[page.path] = page
        vocabulary.update(page.tokens)
    return DocsIndex(
        root=root,
        pages=pages,
        signature=signature,
        checked_at=time.monotonic(),
        vocabulary=tuple(sorted(vocabulary)),
    )


_CACHE: dict[Path, DocsIndex] = {}
_LOCK = threading.Lock()


def get_index(root: Path) -> DocsIndex:
    """The index for ``root``; the tree is rechecked every ``CHECK_INTERVAL_SECONDS``."""
    root = Path(root)
    if not root.is_dir():
        raise DocsUnavailableError(root)
    with _LOCK:
        cached = _CACHE.get(root)
        now = time.monotonic()
        if cached is not None and now - cached.checked_at < CHECK_INTERVAL_SECONDS:
            return cached
        files = scan_md_files(root)
        signature = compute_signature(files)
        if cached is not None and cached.signature == signature:
            cached.checked_at = now
            return cached
        if not files:
            _CACHE.pop(root, None)
            raise DocsUnavailableError(root)
        index = build_index(root, files, signature)
        _CACHE[root] = index
        logger.info('indexed %d documentation pages under %s', len(index.pages), root)
        return index


def reset_index_cache() -> None:
    with _LOCK:
        _CACHE.clear()
