from __future__ import annotations

import asyncio
from collections.abc import Mapping
from io import BytesIO
from typing import BinaryIO
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from api.core.config import Settings


class R2ServiceError(RuntimeError):
    """Raised when an R2 operation fails."""


class R2Service:
    """Cloudflare R2 storage helper built on top of boto3's S3 client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    @property
    def bucket_name(self) -> str:
        return self._settings.r2_bucket_name

    async def upload_file(
        self,
        *,
        fileobj: BinaryIO,
        object_key: str,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """Upload a file-like object to R2 and return its public URL."""

        def _upload() -> None:
            extra_args: dict[str, object] = {"ContentType": content_type}
            if metadata:
                extra_args["Metadata"] = dict(metadata)
            try:
                self._client.upload_fileobj(
                    fileobj,
                    self.bucket_name,
                    object_key,
                    ExtraArgs=extra_args,
                )
            except ClientError as exc:
                raise R2ServiceError(self._format_client_error("upload", object_key, exc)) from exc
            except BotoCoreError as exc:
                raise R2ServiceError(f"R2 upload failed for '{object_key}': {exc}") from exc

        await asyncio.to_thread(_upload)
        return self.get_public_url(object_key)

    async def upload_bytes(
        self,
        data: bytes,
        object_key: str,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> str:
        """Upload raw bytes to R2 and return the public URL."""

        def _upload() -> None:
            extra_args: dict[str, object] = {"ContentType": content_type}
            if metadata:
                extra_args["Metadata"] = dict(metadata)
            try:
                self._client.upload_fileobj(
                    BytesIO(data),
                    self.bucket_name,
                    object_key,
                    ExtraArgs=extra_args,
                )
            except ClientError as exc:
                raise R2ServiceError(self._format_client_error("upload", object_key, exc)) from exc
            except BotoCoreError as exc:
                raise R2ServiceError(f"R2 upload failed for '{object_key}': {exc}") from exc

        await asyncio.to_thread(_upload)
        return self.get_public_url(object_key)

    async def delete_file(self, object_key: str) -> None:
        """Delete a file from R2."""

        def _delete() -> None:
            try:
                self._client.delete_object(Bucket=self.bucket_name, Key=object_key)
            except ClientError as exc:
                raise R2ServiceError(self._format_client_error("delete", object_key, exc)) from exc
            except BotoCoreError as exc:
                raise R2ServiceError(f"R2 delete failed for '{object_key}': {exc}") from exc

        await asyncio.to_thread(_delete)

    async def download_bytes(self, object_key: str) -> bytes:
        """Download an object from R2 into memory."""

        def _download() -> bytes:
            try:
                response = self._client.get_object(Bucket=self.bucket_name, Key=object_key)
                return response["Body"].read()
            except ClientError as exc:
                raise R2ServiceError(self._format_client_error("download", object_key, exc)) from exc
            except BotoCoreError as exc:
                raise R2ServiceError(f"R2 download failed for '{object_key}': {exc}") from exc

        return await asyncio.to_thread(_download)

    def get_public_url(self, object_key: str) -> str:
        """Build the public URL for an object stored in R2."""

        encoded_key = quote(object_key, safe="/")
        base_url = self._settings.r2_public_url.strip().rstrip("/")
        if base_url:
            return f"{base_url}/{encoded_key}"
        return (
            f"https://{self._settings.r2_account_id}.r2.cloudflarestorage.com/"
            f"{self.bucket_name}/{encoded_key}"
        )

    @staticmethod
    def _format_client_error(action: str, object_key: str, exc: ClientError) -> str:
        error = exc.response.get("Error", {})
        code = error.get("Code", "Unknown")
        message = error.get("Message", str(exc))
        return f"R2 {action} failed for '{object_key}': {code} - {message}"
