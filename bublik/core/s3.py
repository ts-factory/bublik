# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
S3-compatible object storage backend for AI-chat generated files.

Selected by :mod:`bublik.core.ai_chat_storage` when
``AI_CHAT_FILE_STORAGE_BACKEND`` is ``s3``, and reaching ``S3_ENDPOINT_URL``:
the bundled SeaweedFS service, or any S3-compatible endpoint (real AWS S3
included). All functions are synchronous boto3 calls; async callers must run
them in a thread (``anyio.to_thread.run_sync`` / ``sync_to_async``).

The bucket is created lazily on first upload (``ensure_bucket``) instead of
via an init container: against real S3 the bucket already exists and the
``head_bucket`` probe simply succeeds.
"""

from __future__ import annotations

from functools import lru_cache
import threading

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from bublik.core.ai_chat_storage import setting


_bucket_lock = threading.Lock()
_bucket_ready = False


def bucket() -> str:
    return setting('S3_BUCKET')


@lru_cache(maxsize=1)
def s3_client():
    """The per-process boto3 S3 client (thread-safe once constructed)."""
    return boto3.client(
        's3',
        endpoint_url=setting('S3_ENDPOINT_URL'),
        aws_access_key_id=setting('S3_ACCESS_KEY'),
        aws_secret_access_key=setting('S3_SECRET_KEY'),
        region_name=setting('S3_REGION'),
        # Path-style addressing: virtual-host style does not work against a
        # plain host:port S3-compatible endpoint.
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
    )


def ensure_bucket() -> None:
    """Create the configured bucket on first use (idempotent, thread-safe)."""
    global _bucket_ready  # noqa: PLW0603
    if _bucket_ready:
        return
    with _bucket_lock:
        if _bucket_ready:
            return
        client = s3_client()
        try:
            client.head_bucket(Bucket=bucket())
        except ClientError as exc:
            error_code = exc.response.get('Error', {}).get('Code', '')
            if error_code != '404':
                raise
            kwargs: dict = {'Bucket': bucket()}
            region = setting('S3_REGION')
            if region and region != 'us-east-1':
                kwargs['CreateBucketConfiguration'] = {'LocationConstraint': region}
            client.create_bucket(**kwargs)
        _bucket_ready = True


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    """Upload one object, creating the bucket if this is the first ever upload."""
    ensure_bucket()
    s3_client().put_object(
        Bucket=bucket(),
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def read_object(key: str) -> bytes:
    """Read a whole object into memory (objects are capped by AI_CHAT_FILE_MAX_SIZE)."""
    return s3_client().get_object(Bucket=bucket(), Key=key)['Body'].read()


def delete_prefix(prefix: str) -> None:
    """Best-effort bulk delete of every object under ``prefix``."""
    client = s3_client()
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket(), Prefix=prefix):
        objects = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
        if objects:
            client.delete_objects(
                Bucket=bucket(),
                Delete={'Objects': objects},
            )


def public_download_url(key: str, filename: str) -> str | None:
    """Presigned download URL against the browser-reachable public endpoint.

    ``None`` unless ``S3_PUBLIC_ENDPOINT_URL`` is configured, telling the
    download endpoint to proxy the bytes instead: SigV4 signs the Host header,
    so a URL presigned against an internal endpoint (the bundled SeaweedFS
    listens on loopback) would be unusable from a browser.
    """
    public_endpoint = setting('S3_PUBLIC_ENDPOINT_URL')
    if not public_endpoint:
        return None
    public_client = boto3.client(
        's3',
        endpoint_url=public_endpoint,
        aws_access_key_id=setting('S3_ACCESS_KEY'),
        aws_secret_access_key=setting('S3_SECRET_KEY'),
        region_name=setting('S3_REGION'),
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
    )
    return public_client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': bucket(),
            'Key': key,
            'ResponseContentDisposition': f'attachment; filename="{filename}"',
        },
        ExpiresIn=setting('S3_PRESIGN_EXPIRY'),
    )
