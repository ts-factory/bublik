# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from http import HTTPStatus
import os.path
from typing import TYPE_CHECKING

from django.conf import settings
import requests
from rest_framework.exceptions import ValidationError

from bublik.core.exceptions import BadGatewayError, UnprocessableEntityError
from bublik.core.log.dto import LogContentDTO, LogUrlsDTO
from bublik.core.result.services import ResultService
from bublik.core.run.external_links import get_result_log, get_sources
from bublik.core.url import get_url


if TYPE_CHECKING:
    from pydantic import JsonValue


class LogService:
    @staticmethod
    def get_json_log_urls(
        result_id: int,
        page: int | None = None,
        request_origin: str | None = None,
    ) -> LogUrlsDTO:
        """
        Get JSON log and attachments URLs for a result.

        Args:
            result_id: The ID of the test result
            page: Optional page number (0 for all pages combined)
            request_origin: Optional origin URL for proxy mode (e.g., 'https://example.com')

        Returns:
            DTO with JSON log and attachments URLs, or None for unavailable URLs

        Raises:
            NotFoundError: if result not found
            UnprocessableEntityError: if page is not a non-negative integer
        """
        if page is not None:
            if not isinstance(page, int):
                msg = f'Incorrect value for page parameter: {page}. Expecting number'
                raise UnprocessableEntityError(msg)
            if page < 0:
                msg = (
                    f'Incorrect value for page parameter: {page}. Expecting non-negative number'
                )
                raise UnprocessableEntityError(msg)

        result = ResultService.get_result(result_id)
        run_source_link = get_sources(result)

        if not run_source_link:
            return LogUrlsDTO(url=None, attachments_url=None)

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
            return LogUrlsDTO(url=forwarding_url, attachments_url=attachments_url)

        return LogUrlsDTO(url=url, attachments_url=attachments_url)

    @staticmethod
    def get_html_log_url(result_id: int) -> str | None:
        """
        Get HTML log URL for a result.

        Args:
            result_id: The ID of the test result

        Returns:
            URL string or None if not available

        Raises:
            NotFoundError: if result not found
            UnprocessableEntityError: if page is not a non-negative integer
        """
        result = ResultService.get_result(result_id)
        return get_result_log(result)

    @staticmethod
    def fetch_log_content(url: str, allow_not_found: bool = True) -> JsonValue:
        """
        Fetch JSON content from URL.

        Args:
            url: URL to fetch from

        Returns:
            Parsed JSON value, or None when the remote content is not found

        Raises:
            UnprocessableEntityError: if URL is missing
            BadGatewayError: if remote content could not be fetched or is not valid JSON
        """
        if not url:
            msg = 'URL parameter is missing'
            raise UnprocessableEntityError(msg)

        try:
            response = get_url(url, quiet_404=allow_not_found, timeout=30)
            if response is None:
                return None
            return response.json()
        except requests.exceptions.HTTPError as re:
            if (
                allow_not_found
                and re.response is not None
                and re.response.status_code == HTTPStatus.NOT_FOUND
            ):
                return None
            msg = 'Error forwarding request'
            raise BadGatewayError(msg) from re
        except requests.exceptions.RequestException as re:
            msg = 'Error forwarding request'
            raise BadGatewayError(msg) from re
        except ValueError as e:
            msg = f'Error parsing JSON: {e!s}'
            raise ValidationError(msg) from e

    @staticmethod
    def get_log_json(result_id: int, page: int | None = None) -> LogContentDTO:
        """
        Fetch and return actual JSON log content.

        Args:
            result_id: The ID of the test result
            page: Optional page number (0 for all pages combined)

        Returns:
            DTO with log URLs and the original JSON log and attachments values

        Raises:
            NotFoundError: if result not found
            UnprocessableEntityError: if page is not a non-negative integer
        """
        # Get URLs (without proxy for direct fetching)
        urls = LogService.get_json_log_urls(result_id, page, request_origin=None)

        result = LogContentDTO(urls=urls)

        # Fetch log content
        if urls.url:
            result.log = LogService.fetch_log_content(urls.url)

        # Fetch attachments content
        if urls.attachments_url:
            result.attachments = LogService.fetch_log_content(
                urls.attachments_url,
                allow_not_found=True,
            )

        return result
