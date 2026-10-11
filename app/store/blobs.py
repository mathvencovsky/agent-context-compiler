"""S3 blob storage for raw bodies and serialized IR — M5.

Keys are derived from the ``version_id`` (``ver_ + sha256(body)[:16]``), which
is already content-addressed, so writes are idempotent: the same bytes always
land on the same key (DECISIONS.md 2026-10-11). The boto3 client mirrors the
timeouts used by the ``/health`` blob probe (``app/main.py::_check_blobs``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
from botocore.config import Config as BotoConfig

from app.config import Settings, get_settings
from app.extract.ir import Document

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class BlobStore:
    """Read/write raw bodies and serialized IR documents in S3."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._bucket = self._settings.s3_bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=self._settings.s3_endpoint_url,
            aws_access_key_id=self._settings.s3_access_key,
            aws_secret_access_key=self._settings.s3_secret_key,
            region_name=self._settings.s3_region,
            config=BotoConfig(
                connect_timeout=self._settings.connect_timeout,
                read_timeout=self._settings.total_timeout,
                retries={"max_attempts": 1},
            ),
        )

    def raw_key(self, version_id: str) -> str:
        """Return the S3 key for a version's raw body."""
        return f"raw/{version_id}"

    def ir_key(self, version_id: str) -> str:
        """Return the S3 key for a version's serialized IR."""
        return f"ir/{version_id}.json"

    def put_raw(self, version_id: str, body: bytes) -> str:
        """Store the raw body bytes and return their key (idempotent)."""
        key = self.raw_key(version_id)
        self._client.put_object(Bucket=self._bucket, Key=key, Body=body)
        return key

    def put_ir(self, version_id: str, doc: Document) -> str:
        """Store the serialized IR document and return its key (idempotent)."""
        key = self.ir_key(version_id)
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=doc.model_dump_json().encode(),
        )
        return key

    def get_raw(self, version_id: str) -> bytes:
        """Return the stored raw body bytes for a version."""
        response = self._client.get_object(
            Bucket=self._bucket, Key=self.raw_key(version_id)
        )
        return response["Body"].read()

    def get_ir(self, version_id: str) -> Document:
        """Return the stored IR document for a version."""
        response = self._client.get_object(
            Bucket=self._bucket, Key=self.ir_key(version_id)
        )
        return Document.model_validate_json(response["Body"].read())
