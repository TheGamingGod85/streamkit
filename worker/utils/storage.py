from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Mapping
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from api.core.config import Settings


class R2StorageError(RuntimeError):
	"""Raised when an R2 storage operation fails."""


class R2StorageClient:
	"""Async-friendly Cloudflare R2 helper for worker tasks."""

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

	async def _upload_fileobj(
		self,
		fileobj: BinaryIO,
		object_key: str,
		content_type: str,
		metadata: Mapping[str, str] | None = None,
	) -> str:
		def _upload() -> None:
			try:
				extra_args: dict[str, Any] = {"ContentType": content_type}
				if metadata:
					extra_args["Metadata"] = dict(metadata)
				self._client.upload_fileobj(
					fileobj,
					self.bucket_name,
					object_key,
					ExtraArgs=extra_args,
				)
			except ClientError as exc:
				raise R2StorageError(self._format_client_error("upload", object_key, exc)) from exc
			except BotoCoreError as exc:
				raise R2StorageError(f"R2 upload failed for '{object_key}': {exc}") from exc

		await asyncio.to_thread(_upload)
		return self.get_public_url(object_key)

	async def upload_path(
		self,
		local_path: Path,
		object_key: str,
		content_type: str,
		metadata: Mapping[str, str] | None = None,
	) -> str:
		"""Upload a local file path to R2 and return the public URL."""

		with local_path.open("rb") as file_handle:
			return await self._upload_fileobj(file_handle, object_key, content_type, metadata)

	async def upload_bytes(
		self,
		data: bytes,
		object_key: str,
		content_type: str,
		metadata: Mapping[str, str] | None = None,
	) -> str:
		"""Upload raw bytes to R2 and return the public URL."""

		return await self._upload_fileobj(BytesIO(data), object_key, content_type, metadata)

	async def upload_text(
		self,
		text: str,
		object_key: str,
		content_type: str = "text/plain; charset=utf-8",
		metadata: Mapping[str, str] | None = None,
	) -> str:
		"""Upload text content to R2 and return the public URL."""

		return await self.upload_bytes(text.encode("utf-8"), object_key, content_type, metadata)


	async def download_to_path(self, object_key: str, destination: Path) -> Path:
		"""Download an object from R2 to a local path."""

		def _download() -> None:
			try:
				response = self._client.get_object(Bucket=self.bucket_name, Key=object_key)
				destination.parent.mkdir(parents=True, exist_ok=True)
				with destination.open("wb") as file_handle:
					file_handle.write(response["Body"].read())
			except ClientError as exc:
				raise R2StorageError(self._format_client_error("download", object_key, exc)) from exc
			except BotoCoreError as exc:
				raise R2StorageError(f"R2 download failed for '{object_key}': {exc}") from exc

		await asyncio.to_thread(_download)
		return destination

	async def download_bytes(self, object_key: str) -> bytes:
		"""Download an object from R2 into memory."""

		def _download() -> bytes:
			try:
				response = self._client.get_object(Bucket=self.bucket_name, Key=object_key)
				return response["Body"].read()
			except ClientError as exc:
				raise R2StorageError(self._format_client_error("download", object_key, exc)) from exc
			except BotoCoreError as exc:
				raise R2StorageError(f"R2 download failed for '{object_key}': {exc}") from exc

		return await asyncio.to_thread(_download)

	async def delete_file(self, object_key: str) -> None:
		"""Delete an object from R2."""

		def _delete() -> None:
			try:
				self._client.delete_object(Bucket=self.bucket_name, Key=object_key)
			except ClientError as exc:
				raise R2StorageError(self._format_client_error("delete", object_key, exc)) from exc
			except BotoCoreError as exc:
				raise R2StorageError(f"R2 delete failed for '{object_key}': {exc}") from exc

		await asyncio.to_thread(_delete)

	def get_public_url(self, object_key: str) -> str:
		"""Return the public CDN URL for an object key."""

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
