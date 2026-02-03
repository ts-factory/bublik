# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import os.path

from django.conf import settings
from django.core.exceptions import ValidationError
import requests

from bublik.core.run.external_links import get_result_log, get_sources
from bublik.data import models


class LogService:
    @staticmethod
    def get_result(result_id: int) -> models.TestIterationResult:
        '''
        Get a result by ID.

        Args:
            result_id: The ID of the test result

        Returns:
            TestIterationResult instance

        Raises:
            ValidationError: if result not found
        '''
        try:
            return models.TestIterationResult.objects.get(id=result_id)
        except models.TestIterationResult.DoesNotExist as e:
            msg = f'Result {result_id} not found'
            raise ValidationError(msg) from e

    @staticmethod
    def get_json_log_urls(
        result_id: int,
        page: int | None = None,
        request_origin: str | None = None,
    ) -> dict:
        '''
        Get JSON log and attachments URLs for a result.

        Args:
            result_id: The ID of the test result
            page: Optional page number (0 for all pages combined)
            request_origin: Optional origin URL for proxy mode (e.g., 'https://example.com')

        Returns:
            Dictionary with 'url' and 'attachments_url' keys

        Raises:
            ValidationError: if result not found or invalid page parameter
        '''
        result = LogService.get_result(result_id)
        run_source_link = get_sources(result)

        if not run_source_link:
            return {'url': None, 'attachments_url': None}

        # Validate page parameter
        if page is not None and not isinstance(page, int):
            msg = f'Incorrect value for page parameter: {page}. Expecting number'
            raise ValidationError(msg)

        # Build node name
        if not result.test_run:
            # The file `node_1_0.json` contains TE startup log in JSON
            node = 'node_1_0'
        else:
            node = f'node_id{result.exec_seqno}'
            if page == 0:
                node += '_all'
            elif page:
                node += f'_p{page}'

        # Build URLs
        url = os.path.join(run_source_link, 'json', node + '.json')
        attachments_url = os.path.join(
            run_source_link,
            'attachments',
            node,
            'attachments.json',
        )

        # Apply proxy if enabled
        if settings.ENABLE_JSON_LOGS_PROXY and request_origin:
            protocol = 'https' if settings.SECURE_HTTP else 'http'
            forwarding_url = (
                f'{protocol}://{request_origin}{settings.PREFIX}/api/v2/logs/proxy/?url={url}'
            )
            attachments_url = f'{protocol}://{request_origin}{settings.PREFIX}/api/v2/logs/proxy/?url={attachments_url}'
            return {'url': forwarding_url, 'attachments_url': attachments_url}

        return {'url': url, 'attachments_url': attachments_url}

    @staticmethod
    def get_html_log_url(result_id: int) -> str | None:
        '''
        Get HTML log URL for a result.

        Args:
            result_id: The ID of the test result

        Returns:
            URL string or None if not available

        Raises:
            ValidationError: if result not found
        '''
        result = LogService.get_result(result_id)
        return get_result_log(result)

    @staticmethod
    def fetch_log_content(url: str) -> dict:
        '''
        Fetch JSON content from URL.

        Args:
            url: URL to fetch from

        Returns:
            Parsed JSON dict or error dict with 'error' key

        Raises:
            ValidationError: if URL is missing
        '''
        if not url:
            msg = 'URL parameter is missing'
            raise ValidationError(msg)

        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {'error': f'Error fetching log: {e!s}'}
        except ValueError as e:
            return {'error': f'Error parsing JSON: {e!s}'}

    @staticmethod
    def get_log_json(result_id: int, page: int | None = None) -> dict:
        '''
        Fetch and return actual JSON log content.

        Args:
            result_id: The ID of the test result
            page: Optional page number (0 for all pages combined)

        Returns:
            Dictionary with:
                - log: Parsed JSON log content (or error dict)
                - attachments: Parsed attachments content (or None/error dict)
                - urls: Dict with 'url' and 'attachments_url'

        Raises:
            ValidationError: if result not found or invalid parameters
        '''
        # Get URLs (without proxy for direct fetching)
        urls = LogService.get_json_log_urls(result_id, page, request_origin=None)

        result = {
            'urls': urls,
            'log': None,
            'attachments': None,
        }

        # Fetch log content
        if urls['url']:
            result['log'] = LogService.fetch_log_content(urls['url'])

        # Fetch attachments content
        if urls['attachments_url']:
            result['attachments'] = LogService.fetch_log_content(urls['attachments_url'])

        return result
