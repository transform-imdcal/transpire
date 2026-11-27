"""Private object storage for uploaded files.

Two backends share one contract so the upload flow is identical everywhere:

* ``s3``: the browser posts the file straight to a private bucket with a short-lived signed
  form (size and type are locked into the signature). The server signs with its instance
  role; no access keys exist anywhere.
* ``local``: development only. The browser posts the same form to the API, which writes
  under ``STORAGE_LOCAL_DIR``.

Object keys are always server-generated (``tenants/{tenant}/...``) and never contain a
user's file name.
"""

import asyncio
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from app.core.config import Settings

ScanStatus = Literal["clean", "pending", "blocked"]
SCAN_TAG = "GuardDutyMalwareScanStatus"


def scan_status_from_tag(value: str | None) -> ScanStatus:
    """GuardDuty Malware Protection result → what the app may do with the file.

    No tag yet means the scan hasn't finished. Anything other than a clean result
    (threats, unsupported, access denied, failed) keeps the file closed: fail safe.
    """
    if value is None:
        return "pending"
    return "clean" if value == "NO_THREATS_FOUND" else "blocked"


class StorageNotConfiguredError(RuntimeError):
    pass


class StoredObjectMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadForm:
    # "s3": post multipart form fields + file to url, without credentials.
    # "api": post the same form to this API path with the session (development only).
    target: Literal["s3", "api"]
    url: str
    fields: dict[str, str] = field(default_factory=dict)


def _disposition(filename: str, inline: bool) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "") or "file"
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


class LocalStorage:
    backend = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Storage key escapes the storage root")
        return candidate

    async def upload_form(
        self, *, key: str, content_type: str, max_bytes: int, upload_path: str
    ) -> UploadForm:
        return UploadForm(target="api", url=upload_path)

    async def write_stream(self, key: str, chunks: AsyncIterator[bytes], max_bytes: int) -> int:
        """Copy an uploaded body to disk, refusing anything past max_bytes."""

        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with open(target, "wb") as output:  # noqa: ASYNC230 - development backend only
                async for chunk in chunks:
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("File is larger than allowed")
                    output.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return written

    async def size(self, key: str) -> int | None:
        target = self.path(key)
        return target.stat().st_size if target.is_file() else None

    async def download_to(self, key: str, destination: Path) -> None:
        source = self.path(key)
        if not source.is_file():
            raise StoredObjectMissingError(key)
        await asyncio.to_thread(shutil.copyfile, source, destination)

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    async def delete(self, keys: list[str]) -> None:
        for key in keys:
            self.path(key).unlink(missing_ok=True)

    async def scan_status(self, key: str) -> ScanStatus:
        return "clean"  # Development files are never scanned.

    async def download_url(self, key: str, filename: str, content_type: str, inline: bool) -> None:
        # Served by the API itself (see stream_local_object); no signed URL exists.
        return None


class S3Storage:
    backend = "s3"

    def __init__(self, settings: Settings) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = settings.storage_bucket
        self.kms_key_id = settings.storage_kms_key_id
        self.url_seconds = settings.storage_url_seconds
        self.client = boto3.client(
            "s3",
            region_name=settings.storage_region,
            endpoint_url=settings.storage_endpoint_url or None,
            config=Config(
                signature_version="s3v4",
                # Test servers (moto, MinIO) answer on one host; AWS uses bucket subdomains.
                s3={"addressing_style": "path" if settings.storage_endpoint_url else "virtual"},
            ),
        )

    def _encryption(self) -> dict[str, str]:
        if not self.kms_key_id:
            return {}
        return {
            "x-amz-server-side-encryption": "aws:kms",
            "x-amz-server-side-encryption-aws-kms-key-id": self.kms_key_id,
        }

    async def upload_form(
        self, *, key: str, content_type: str, max_bytes: int, upload_path: str
    ) -> UploadForm:
        fields = {"Content-Type": content_type, **self._encryption()}
        conditions: list = [
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],
            *({name: value} for name, value in self._encryption().items()),
        ]
        signed = await asyncio.to_thread(
            self.client.generate_presigned_post,
            Bucket=self.bucket,
            Key=key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=self.url_seconds,
        )
        return UploadForm(target="s3", url=signed["url"], fields=signed["fields"])

    async def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            head = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return int(head["ContentLength"])

    async def download_to(self, key: str, destination: Path) -> None:
        from botocore.exceptions import ClientError

        try:
            await asyncio.to_thread(self.client.download_file, self.bucket, key, str(destination))
        except ClientError as error:
            raise StoredObjectMissingError(key) from error

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        extra: dict[str, str] = {}
        if self.kms_key_id:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self.kms_key_id}
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            **extra,
        )

    async def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):
            batch = keys[start : start + 1000]
            if batch:
                await asyncio.to_thread(
                    self.client.delete_objects,
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )

    async def scan_status(self, key: str) -> ScanStatus:
        from botocore.exceptions import ClientError

        try:
            tagging = await asyncio.to_thread(
                self.client.get_object_tagging, Bucket=self.bucket, Key=key
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return "blocked"
            raise
        tags = {tag["Key"]: tag["Value"] for tag in tagging.get("TagSet", [])}
        return scan_status_from_tag(tags.get(SCAN_TAG))

    async def download_url(self, key: str, filename: str, content_type: str, inline: bool) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": _disposition(filename, inline),
            },
            ExpiresIn=60,
        )


Storage = LocalStorage | S3Storage


@lru_cache(maxsize=4)
def _cached_storage(
    backend: str, bucket: str, region: str, endpoint: str, root: str, kms: str, seconds: int
):
    if backend == "s3":
        settings = Settings.model_construct(
            storage_bucket=bucket,
            storage_region=region,
            storage_endpoint_url=endpoint,
            storage_kms_key_id=kms,
            storage_url_seconds=seconds,
        )
        return S3Storage(settings)
    return LocalStorage(root)


def get_storage(settings: Settings) -> Storage:
    backend = settings.storage_backend.strip().casefold()
    if backend not in {"s3", "local"}:
        raise StorageNotConfiguredError(f"Unknown STORAGE_BACKEND {settings.storage_backend!r}")
    if backend == "s3" and not settings.storage_bucket.strip():
        raise StorageNotConfiguredError("S3_BUCKET is not set")
    return _cached_storage(
        backend,
        settings.storage_bucket.strip(),
        settings.storage_region.strip(),
        settings.storage_endpoint_url.strip(),
        os.path.abspath(settings.storage_local_dir),
        settings.storage_kms_key_id.strip(),
        settings.storage_url_seconds,
    )


def content_disposition(filename: str, inline: bool) -> str:
    return _disposition(filename, inline)
