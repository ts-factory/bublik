# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
S3-compatible object storage.

Every call acts on an :class:`S3Target`: a connection (endpoint, credentials,
region) plus a bucket. Without one, the default target is built from the
``S3_*`` settings -- the bundled SeaweedFS, or any S3-compatible endpoint
(real AWS S3 included). That is what AI-chat generated files use, selected by
:mod:`bublik.core.ai_chat_storage` when ``AI_CHAT_FILE_STORAGE_BACKEND`` is
``s3``; logs storage builds its own targets from the references config.

All functions are synchronous boto3 calls; async callers must run them in a
thread (``anyio.to_thread.run_sync`` / ``sync_to_async``).

A bucket is created lazily on first upload (``ensure_bucket``) instead of via
an init container: against real S3 the bucket already exists and the
``head_bucket`` probe simply succeeds.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import threading
from typing import IO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from bublik.core.ai_chat_storage import setting


@dataclass(frozen=True)
class S3Target:
    """One bucket on one S3-compatible endpoint."""

    bucket: str
    endpoint_url: str
    access_key: str
    secret_key: str
    region: str = 'us-east-1'
    # Browser-reachable endpoint for presigned URLs; empty when there is none.
    public_endpoint_url: str = ''

    def __repr__(self) -> str:
        # Keep the secret out of logs and tracebacks.
        return (
            f'S3Target(bucket={self.bucket!r}, endpoint_url={self.endpoint_url!r}, '
            f'region={self.region!r})'
        )


def default_target(bucket: str | None = None) -> S3Target:
    """The target described by the ``S3_*`` settings, optionally on another bucket."""
    return S3Target(
        bucket=bucket or setting('S3_BUCKET'),
        endpoint_url=setting('S3_ENDPOINT_URL'),
        access_key=setting('S3_ACCESS_KEY'),
        secret_key=setting('S3_SECRET_KEY'),
        region=setting('S3_REGION'),
        public_endpoint_url=setting('S3_PUBLIC_ENDPOINT_URL'),
    )


def bucket() -> str:
    return setting('S3_BUCKET')


@lru_cache(maxsize=32)
def _client(endpoint_url: str, access_key: str, secret_key: str, region: str):
    """One boto3 client per connection (thread-safe once constructed)."""
    return boto3.client(
        's3',
        endpoint_url=endpoint_url or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        # Path-style addressing: virtual-host style does not work against a
        # plain host:port S3-compatible endpoint.
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
    )


def client_for(target: S3Target):
    """The cached client reaching ``target``'s endpoint."""
    return _client(target.endpoint_url, target.access_key, target.secret_key, target.region)


def s3_client():
    """The client of the default target."""
    return client_for(default_target())


def _resolve(target: S3Target | None) -> S3Target:
    return target if target is not None else default_target()


_bucket_lock = threading.Lock()
_buckets_ready: set[tuple[str, str]] = set()


def ensure_bucket(target: S3Target | None = None) -> None:
    """Create the target's bucket on first use (idempotent, thread-safe)."""
    target = _resolve(target)
    ready_key = (target.endpoint_url, target.bucket)
    if ready_key in _buckets_ready:
        return
    with _bucket_lock:
        if ready_key in _buckets_ready:
            return
        client = client_for(target)
        try:
            client.head_bucket(Bucket=target.bucket)
        except ClientError as exc:
            error_code = exc.response.get('Error', {}).get('Code', '')
            if error_code not in ('404', 'NoSuchBucket'):
                raise
            kwargs: dict = {'Bucket': target.bucket}
            if target.region and target.region != 'us-east-1':
                kwargs['CreateBucketConfiguration'] = {'LocationConstraint': target.region}
            client.create_bucket(**kwargs)
        _buckets_ready.add(ready_key)


def upload_bytes(
    key: str,
    data: bytes,
    content_type: str,
    *,
    target: S3Target | None = None,
) -> None:
    """Upload one object, creating the bucket if this is the first ever upload."""
    target = _resolve(target)
    ensure_bucket(target)
    client_for(target).put_object(
        Bucket=target.bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def upload_fileobj(
    key: str,
    fileobj: IO[bytes],
    content_type: str,
    *,
    target: S3Target | None = None,
) -> None:
    """Stream a file object into one object (multipart when it is large)."""
    target = _resolve(target)
    ensure_bucket(target)
    client_for(target).upload_fileobj(
        fileobj,
        target.bucket,
        key,
        ExtraArgs={'ContentType': content_type},
    )


def read_object(key: str, *, target: S3Target | None = None) -> bytes:
    """Read a whole object into memory; only for objects known to be small."""
    target = _resolve(target)
    return client_for(target).get_object(Bucket=target.bucket, Key=key)['Body'].read()


def open_object(key: str, *, target: S3Target | None = None) -> dict | None:
    """Open an object for streaming, or ``None`` if it does not exist.

    Returns the boto3 ``get_object`` response: ``Body`` is a streaming body to
    read in chunks and close, ``ContentType`` / ``ContentLength`` describe it.
    """
    target = _resolve(target)
    try:
        return client_for(target).get_object(Bucket=target.bucket, Key=key)
    except ClientError as exc:
        if is_missing(exc):
            return None
        raise


def download_file(key: str, path: str, *, target: S3Target | None = None) -> None:
    """Stream an object to a local file."""
    target = _resolve(target)
    client_for(target).download_file(target.bucket, key, path)


def object_exists(key: str, *, target: S3Target | None = None) -> bool:
    """Whether ``key`` exists."""
    target = _resolve(target)
    try:
        client_for(target).head_object(Bucket=target.bucket, Key=key)
    except ClientError as exc:
        if is_missing(exc):
            return False
        raise
    return True


def list_prefixes(prefix: str, *, target: S3Target | None = None) -> list[str]:
    """The immediate sub-prefixes of ``prefix`` (``prefix`` should end in ``/``)."""
    target = _resolve(target)
    paginator = client_for(target).get_paginator('list_objects_v2')
    prefixes: list[str] = []
    for page in paginator.paginate(Bucket=target.bucket, Prefix=prefix, Delimiter='/'):
        prefixes.extend(entry['Prefix'] for entry in page.get('CommonPrefixes', []))
    return prefixes


def list_entries(prefix: str, *, target: S3Target | None = None) -> tuple[list[str], list[str]]:
    """The sub-prefixes and object keys directly under ``prefix``, like a directory."""
    target = _resolve(target)
    paginator = client_for(target).get_paginator('list_objects_v2')
    prefixes: list[str] = []
    keys: list[str] = []
    for page in paginator.paginate(Bucket=target.bucket, Prefix=prefix, Delimiter='/'):
        prefixes.extend(entry['Prefix'] for entry in page.get('CommonPrefixes', []))
        keys.extend(entry['Key'] for entry in page.get('Contents', []))
    return prefixes, keys


def delete_prefix(prefix: str, *, target: S3Target | None = None) -> None:
    """Best-effort bulk delete of every object under ``prefix``."""
    target = _resolve(target)
    client = client_for(target)
    paginator = client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=target.bucket, Prefix=prefix):
        objects = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
        if objects:
            client.delete_objects(
                Bucket=target.bucket,
                Delete={'Objects': objects},
            )


def _presigned_get(target: S3Target, params: dict) -> str | None:
    """Presign a GET against the target's public endpoint, if it has one.

    SigV4 signs the Host header, so a URL presigned against an internal
    endpoint (the bundled SeaweedFS listens on loopback) would be unusable
    from a browser.
    """
    if not target.public_endpoint_url:
        return None
    public_client = _client(
        target.public_endpoint_url,
        target.access_key,
        target.secret_key,
        target.region,
    )
    return public_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': target.bucket, **params},
        ExpiresIn=setting('S3_PRESIGN_EXPIRY'),
    )


def public_download_url(
    key: str,
    filename: str,
    *,
    target: S3Target | None = None,
) -> str | None:
    """Presigned download URL (served as an attachment) on the public endpoint.

    ``None`` unless the target has a public endpoint, telling the download
    endpoint to proxy the bytes instead.
    """
    return _presigned_get(
        _resolve(target),
        {'Key': key, 'ResponseContentDisposition': f'attachment; filename="{filename}"'},
    )


def public_read_url(key: str, *, target: S3Target | None = None) -> str | None:
    """Presigned URL for reading an object inline, or ``None`` without a public endpoint."""
    return _presigned_get(_resolve(target), {'Key': key})


def is_missing(exc: ClientError) -> bool:
    """Whether a client error means the object or bucket does not exist."""
    return exc.response.get('Error', {}).get('Code', '') in ('404', 'NoSuchKey', 'NotFound')
