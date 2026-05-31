"""Sync R2 (S3-compatible) object store client — the canonical media uploader.

Sync `boto3` (not aioboto3): SablePlatform and SableSlopper are sync; SableTracking
wraps this in `run_in_executor` from its async pipeline. boto3 is imported lazily so
this module imports without it (callers install the consumer's `[r2]`/r2 extra).

Stored reference form is ``'<bucket>/<key>'`` (no scheme, no leading slash) — resolve
to a URL with ``sable_platform.media.urls.build_media_url``.
"""
from __future__ import annotations

import logging

log = logging.getLogger("sable_platform.media.r2")


class R2Store:
    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str):
        self._account_id = account_id
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._endpoint = (
            f"https://{account_id}.r2.cloudflarestorage.com" if account_id else ""
        )

    def is_configured(self) -> bool:
        return bool(self._account_id and self._access_key and self._secret_key and self._bucket)

    def _client(self):
        import boto3  # lazy — optional dep
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )

    def put(self, file_bytes: bytes, key: str, mime: str, bucket: str | None = None) -> str:
        """Upload bytes under ``key``; return the canonical ``'<bucket>/<key>'`` ref."""
        if not self.is_configured():
            raise RuntimeError("R2Store is not configured (missing account/key/secret/bucket)")
        b = bucket or self._bucket
        self._client().put_object(
            Bucket=b,
            Key=key,
            Body=file_bytes,
            ContentType=mime,
            ContentDisposition="inline",
        )
        return f"{b}/{key}"

    def presign_get(self, ref: str, ttl: int = 3600) -> str:
        """Return a presigned GET URL for a stored ``'<bucket>/<key>'`` ref."""
        if "/" not in ref:
            raise ValueError(f"Invalid media ref (expected '<bucket>/<key>'): {ref!r}")
        bucket, key = ref.split("/", 1)
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl,
        )
