from __future__ import annotations

import base64
import hashlib
import hmac
import json
from functools import lru_cache
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from jwt import decode as jwt_decode
from jwt import get_unverified_header
from jwt import PyJWKClient

from api.core.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")


class SupabaseAuthError(ValueError):
	"""Raised when a Supabase JWT cannot be validated."""


@dataclass(frozen=True)
class AuthContext:
	"""Validated Supabase auth context extracted from a bearer token."""

	user_id: UUID
	token: str
	claims: dict[str, Any]
	role: str | None = None


def _urlsafe_b64decode(segment: str) -> bytes:
	padding = "=" * (-len(segment) % 4)
	try:
		return base64.urlsafe_b64decode(segment + padding)
	except (ValueError, TypeError) as exc:
		raise SupabaseAuthError("Supabase JWT contains invalid base64 data.") from exc


def _decode_json_segment(segment: str) -> dict[str, Any]:
	try:
		payload = _urlsafe_b64decode(segment)
		decoded = json.loads(payload.decode("utf-8"))
	except (UnicodeDecodeError, json.JSONDecodeError) as exc:
		raise SupabaseAuthError("Supabase JWT contains invalid JSON.") from exc
	if not isinstance(decoded, dict):
		raise SupabaseAuthError("Supabase JWT payload must be an object.")
	return decoded


def _jwks_url(supabase_url: str) -> str:
	return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
	return PyJWKClient(jwks_url)


def _validate_standard_claims(claims: dict[str, Any]) -> None:
	now = datetime.now(timezone.utc).timestamp()
	expires_at = claims.get("exp")
	if expires_at is None:
		raise SupabaseAuthError("Supabase JWT is missing an exp claim.")
	try:
		if float(expires_at) <= now:
			raise SupabaseAuthError("Supabase JWT has expired.")
	except (TypeError, ValueError) as exc:
		raise SupabaseAuthError("Supabase JWT exp claim is invalid.") from exc

	not_before = claims.get("nbf")
	if not_before is not None:
		try:
			if float(not_before) > now:
				raise SupabaseAuthError("Supabase JWT is not active yet.")
		except (TypeError, ValueError) as exc:
			raise SupabaseAuthError("Supabase JWT nbf claim is invalid.") from exc


def _decode_supabase_jwt_hs256(token: str, secret: str) -> dict[str, Any]:
	parts = token.split(".")
	if len(parts) != 3:
		raise SupabaseAuthError("Supabase JWT is malformed.")

	header_segment, payload_segment, signature_segment = parts
	header = _decode_json_segment(header_segment)
	claims = _decode_json_segment(payload_segment)

	algorithm = header.get("alg")
	if algorithm != "HS256":
		raise SupabaseAuthError("Supabase JWT must use the HS256 algorithm.")

	signing_input = f"{header_segment}.{payload_segment}".encode("utf-8")
	expected_signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
	actual_signature = _urlsafe_b64decode(signature_segment)
	if not hmac.compare_digest(expected_signature, actual_signature):
		raise SupabaseAuthError("Supabase JWT signature is invalid.")

	_validate_standard_claims(claims)
	return claims


def _decode_supabase_jwt_asymmetric(token: str, supabase_url: str) -> dict[str, Any]:
	try:
		header = get_unverified_header(token)
		algorithm = str(header.get("alg") or "")
		if not algorithm:
			raise SupabaseAuthError("Supabase JWT header is missing algorithm.")

		jwks_client = _jwks_client(_jwks_url(supabase_url))
		signing_key = jwks_client.get_signing_key_from_jwt(token)
		issuer = f"{supabase_url.rstrip('/')}/auth/v1"
		claims = jwt_decode(
			token,
			signing_key.key,
			algorithms=[algorithm],
			issuer=issuer,
			options={"verify_aud": False},
		)
		if not isinstance(claims, dict):
			raise SupabaseAuthError("Supabase JWT payload must be an object.")
		_validate_standard_claims(claims)
		return claims
	except SupabaseAuthError:
		raise
	except InvalidTokenError as exc:
		raise SupabaseAuthError(f"Supabase JWT is invalid: {exc}") from exc
	except Exception as exc:
		raise SupabaseAuthError(f"Failed to validate Supabase JWT via JWKS: {exc}") from exc


def decode_supabase_jwt(token: str, secret: str, supabase_url: str) -> dict[str, Any]:
	"""Validate a Supabase JWT and return its claims."""

	parts = token.split(".")
	if len(parts) != 3:
		raise SupabaseAuthError("Supabase JWT is malformed.")

	header_segment = parts[0]
	header = _decode_json_segment(header_segment)
	algorithm = str(header.get("alg") or "")

	# Supabase projects can use either legacy HS256 signing or modern asymmetric keys.
	if algorithm == "HS256":
		if not secret.strip():
			raise SupabaseAuthError("Supabase JWT secret is not configured for HS256 validation.")
		return _decode_supabase_jwt_hs256(token, secret)

	if not supabase_url.strip():
		raise SupabaseAuthError("Supabase URL is not configured for JWKS validation.")
	return _decode_supabase_jwt_asymmetric(token, supabase_url)


def build_auth_context(token: str, secret: str, supabase_url: str) -> AuthContext:
	"""Validate a token and resolve the authenticated user context."""

	claims = decode_supabase_jwt(token, secret, supabase_url)
	user_id_value = claims.get("sub") or claims.get("user_id")
	if user_id_value is None:
		raise SupabaseAuthError("Supabase JWT does not include a user subject.")
	try:
		user_id = UUID(str(user_id_value))
	except ValueError as exc:
		raise SupabaseAuthError("Supabase JWT subject is not a valid UUID.") from exc

	role_value = claims.get("role")
	role = str(role_value) if role_value is not None else None
	return AuthContext(user_id=user_id, token=token, claims=claims, role=role)


async def get_optional_auth_context(
	request: Request,
	credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthContext | None:
	"""Return a validated auth context when a bearer token is supplied."""

	token: str | None = None
	if credentials is not None:
		token = credentials.credentials
	else:
		# Browsers cannot attach Authorization headers to <img>/<video>/<a> URLs.
		# Accept query token fallback for media delivery endpoints.
		token = request.query_params.get("access_token") or request.query_params.get("token")

	if not token:
		return None
	settings = getattr(request.app.state, "settings", None)
	if not isinstance(settings, Settings):
		settings = get_settings()
	try:
		return build_auth_context(
			token,
			settings.supabase_jwt_secret,
			settings.supabase_url,
		)
	except SupabaseAuthError as exc:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail=str(exc),
			headers={"WWW-Authenticate": "Bearer"},
		) from exc


async def get_required_auth_context(
	auth_context: AuthContext | None = Depends(get_optional_auth_context),
) -> AuthContext:
	"""Require a valid bearer token for the current request."""

	if auth_context is None:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authentication credentials were not provided.",
			headers={"WWW-Authenticate": "Bearer"},
		)
	return auth_context


def require_asset_access(asset_row: Mapping[str, Any], auth_context: AuthContext | None) -> None:
	"""Enforce owner-only access for user-scoped assets while allowing public ones."""

	metadata = asset_row.get("metadata") or {}
	if isinstance(metadata, Mapping):
		visibility = str(metadata.get("visibility") or "public").lower()
	else:
		visibility = "public"

	if visibility != "private":
		return

	owner_value = asset_row.get("user_id")
	if auth_context is None:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="This asset requires authentication.",
			headers={"WWW-Authenticate": "Bearer"},
		)
	if str(owner_value) != str(auth_context.user_id):
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="You do not have access to this asset.",
		)
