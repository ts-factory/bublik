# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Rank documentation pages for a query and look pages and sections up by name."""

from __future__ import annotations

import bisect
import difflib
import math
import re

from bublik.mcp.docs.index import slugify, tokenize
from bublik.mcp.docs.models import (
    SCOPE_ALL,
    DocPage,
    DocSection,
    DocsIndex,
    SearchHit,
    UnknownHeadingError,
    UnknownPageError,
)


TITLE_WEIGHT = 5.0
HEADING_WEIGHT = 3.0
BODY_WEIGHT = 1.0
PHRASE_TITLE_BONUS = 6.0
PHRASE_TEXT_BONUS = 4.0
OTHER_SECTIONS_WEIGHT = 0.2
# Shorter query terms match whole tokens only, longer ones also match as prefixes.
PREFIX_MIN_LEN = 4
SNIPPET_WIDTH = 220
SUGGESTION_COUNT = 5
SUGGESTION_CUTOFF = 0.4
PATH_SUFFIXES = ('.md', '.html')
ELLIPSIS = '…'


def _expand(index: DocsIndex, term: str) -> frozenset[str]:
    """The indexed tokens a query term matches."""
    if len(term) < PREFIX_MIN_LEN:
        return frozenset({term})
    begin = bisect.bisect_left(index.vocabulary, term)
    end = bisect.bisect_left(index.vocabulary, term + '\uffff')
    return frozenset(index.vocabulary[begin:end])


def _idf(pages: list[DocPage], tokens: frozenset[str]) -> float:
    df = sum(1 for page in pages if not tokens.isdisjoint(page.tokens))
    return math.log(1 + len(pages) / (1 + df))


def _score_section(
    section: DocSection,
    expansions: list[frozenset[str]],
    idfs: list[float],
    in_title: list[bool],
    phrase: str | None,
    title_bonus: float,
) -> float:
    score = 0.0
    matched = 0
    for tokens, idf, title_match in zip(expansions, idfs, in_title, strict=True):
        in_heading = not tokens.isdisjoint(section.heading_tokens)
        tf = sum(section.token_counts.get(token, 0) for token in tokens)
        if not (title_match or in_heading or tf):
            continue
        matched += 1
        score += idf * (
            TITLE_WEIGHT * title_match
            + HEADING_WEIGHT * in_heading
            + BODY_WEIGHT * math.log1p(tf)
        )
    if not matched:
        return 0.0
    if phrase:
        score += title_bonus
        if phrase in section.text_lower:
            score += PHRASE_TEXT_BONUS
    return score * matched / len(expansions)


def _trim_to_words(snippet: str, *, leading: bool, trailing: bool) -> str:
    if leading:
        cut = snippet.find(' ')
        snippet = ELLIPSIS + (snippet[cut + 1 :] if cut >= 0 else snippet)
    if trailing:
        cut = snippet.rfind(' ')
        snippet = (snippet[:cut] if cut > 0 else snippet) + ELLIPSIS
    return snippet.strip()


def make_snippet(text: str, terms: list[str], width: int = SNIPPET_WIDTH) -> str:
    """A window of ``text`` around the first occurrence of any term."""
    lower = text.lower()
    first = None
    for term in terms:
        match = re.search(r'\b' + re.escape(term), lower)
        if match and (first is None or match.start() < first):
            first = match.start()
    if first is None:
        first = 0
    begin = max(0, first - width // 3)
    end = min(len(text), begin + width)
    return _trim_to_words(text[begin:end], leading=begin > 0, trailing=end < len(text))


def search(index: DocsIndex, query: str, *, scope: str, limit: int) -> list[SearchHit]:
    terms = tokenize(query)
    if not terms:
        return []
    pages = [page for page in index.pages.values() if scope in (SCOPE_ALL, page.scope)]
    phrase = ' '.join(query.lower().split()) if len(terms) > 1 else None
    expansions = [_expand(index, term) for term in terms]
    idfs = [_idf(pages, tokens) for tokens in expansions]
    hits: list[SearchHit] = []
    for page in pages:
        in_title = [not tokens.isdisjoint(page.title_tokens) for tokens in expansions]
        title_bonus = PHRASE_TITLE_BONUS if phrase and phrase in page.title.lower() else 0.0
        scored = [
            (_score_section(section, expansions, idfs, in_title, phrase, title_bonus), section)
            for section in page.sections
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best = scored[0]
        if best_score <= 0:
            continue
        total = best_score + OTHER_SECTIONS_WEIGHT * sum(score for score, _ in scored[1:])
        hits.append(
            SearchHit(
                path=page.path,
                title=page.title,
                heading=best.heading,
                anchor=best.anchor,
                snippet=make_snippet(best.text, terms),
                score=round(total, 2),
                web_path=page.web_path,
            ),
        )
    hits.sort(key=lambda hit: (-hit.score, hit.path))
    return hits[:limit]


def normalize_path(raw: str) -> str:
    """Reduce ``/docs/x.md``, ``x.html`` or ``x/`` to the index key; reject ``..``."""
    value = re.sub(r'/+', '/', raw.strip().replace('\\', '/')).strip('/')
    if value.startswith('docs/'):
        value = value[len('docs/') :]
    for suffix in PATH_SUFFIXES:
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    value = value.strip('/')
    if any(segment in ('.', '..') for segment in value.split('/')):
        msg = f'Invalid documentation path: {raw!r}'
        raise ValueError(msg)
    return value


def _suggest_paths(index: DocsIndex, wanted: str) -> list[str]:
    paths = list(index.pages)
    suggestions = difflib.get_close_matches(
        wanted,
        paths,
        n=SUGGESTION_COUNT,
        cutoff=SUGGESTION_CUTOFF,
    )
    leaf = wanted.rsplit('/', 1)[-1]
    if leaf:
        suggestions.extend(p for p in paths if leaf in p.rsplit('/', 1)[-1])
    return list(dict.fromkeys(suggestions))[:SUGGESTION_COUNT]


def find_page(index: DocsIndex, raw_path: str) -> DocPage:
    """Raises ``ValueError`` for a malformed path, ``UnknownPageError`` otherwise."""
    path = normalize_path(raw_path)
    page = index.pages.get(path)
    if page is None:
        raise UnknownPageError(path, _suggest_paths(index, path))
    return page


def find_section(page: DocPage, heading: str) -> str:
    """The markdown of one section, including its nested subsections."""
    wanted = heading.strip().lstrip('#').strip()
    wanted_text = wanted.lower()
    wanted_slug = slugify(wanted)
    sections = page.sections
    for i, section in enumerate(sections):
        if section.heading is None:
            continue
        if section.heading.lower() != wanted_text and section.anchor != wanted_slug:
            continue
        parts = [section.markdown]
        for child in sections[i + 1 :]:
            if child.level <= section.level:
                break
            parts.append(child.markdown)
        return '\n\n'.join(parts)
    raise UnknownHeadingError(heading, page.headings)
