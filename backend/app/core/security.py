"""Verification of Supabase Auth access tokens.

Supabase signs access tokens with an asymmetric ECC P-256 key (ES256) and publishes
the public keys as a JWKS document. Tokens are verified locally against those keys,
so no request to Supabase is needed per API call (keys are cached).

Security notes:
- The accepted algorithm is fixed to ES256 and never taken from the token header,
  which blocks "alg: none" and HS256/ES256 algorithm-confusion attacks.
- Error messages raised here are fixed strings. They never contain the token or any
  part of it, so they are safe to log.
"""

import http.client
import time
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError

from app.core.config import Settings
from app.models.auth import AuthenticatedUser

ALLOWED_ALGORITHMS = ["ES256"]
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "role"]
AUTHENTICATED_ROLE = "authenticated"

# Tolerated clock difference between Supabase and this server.
CLOCK_SKEW_LEEWAY_SECONDS = 30
# How long a fetched JWKS is reused before it is downloaded again.
JWKS_CACHE_LIFESPAN_SECONDS = 300
# Minimum interval between forced JWKS refreshes triggered by an unknown "kid".
# Limits load caused by tokens with random key ids, while a newly rotated key
# is still picked up within this interval (or immediately, if it was published
# as a standby key before rotation, which is Supabase's recommended procedure).
JWKS_REFRESH_COOLDOWN_SECONDS = 30
JWKS_FETCH_TIMEOUT_SECONDS = 5
# After a failed JWKS download no new download is attempted for this long. PyJWT's
# own cooldown only starts after a *successful* fetch, so without this, every request
# would retry (and wait up to the fetch timeout) while the endpoint is down.
JWKS_FAILURE_BACKOFF_SECONDS = 10

# Everything that can go wrong while downloading or parsing a JWKS document:
# PyJWT errors (connection, empty or malformed key set), transport errors such as
# connection resets (OSError), invalid JSON / encoding (ValueError, which covers
# JSONDecodeError and UnicodeDecodeError) and HTTP protocol errors.
_JWKS_FAILURES = (PyJWTError, OSError, ValueError, http.client.HTTPException)


class InvalidTokenError(Exception):
    """The access token is malformed, expired or otherwise not acceptable."""


class AuthServiceUnavailableError(Exception):
    """The signing keys could not be obtained, so no token can be verified."""


class JWKSUnavailableError(Exception):
    """The JWKS could not be downloaded or is unusable (a server-side problem).

    Deliberately not a PyJWTError: PyJWKClientError is also raised for an unknown
    "kid", so the two situations are told apart by exception type, not by message.
    """


class SupabaseJWKClient(PyJWKClient):
    """PyJWKClient with typed JWKS failures and a backoff after failed downloads.

    - Any failure to obtain or parse the key set raises JWKSUnavailableError.
      Hence the only PyJWKClientError left to escape get_signing_key() means
      "the JWKS is fine, but contains no key with this kid".
    - Cache policy is unchanged: keys from a valid cache are used, an expired cache
      that cannot be refreshed is an error (expired keys are never served).
    """

    def __init__(
        self,
        uri: str,
        *,
        failure_backoff_seconds: float = JWKS_FAILURE_BACKOFF_SECONDS,
        **kwargs: Any,
    ) -> None:
        super().__init__(uri, **kwargs)
        self._failure_backoff_seconds = failure_backoff_seconds
        self._retry_after: float | None = None

    def fetch_data(self) -> Any:
        # Only called when a network request is needed (cache miss, expiry or forced refresh).
        if self._retry_after is not None and time.monotonic() < self._retry_after:
            raise JWKSUnavailableError("JWKS download skipped after a recent failure")
        try:
            data = super().fetch_data()
        except _JWKS_FAILURES as exc:
            self._retry_after = time.monotonic() + self._failure_backoff_seconds
            raise JWKSUnavailableError("JWKS download failed") from exc
        self._retry_after = None
        return data

    def get_signing_keys(self, refresh: bool = False) -> list[PyJWK]:
        try:
            return super().get_signing_keys(refresh)
        except _JWKS_FAILURES as exc:  # e.g. no usable signing keys, invalid cached set
            raise JWKSUnavailableError("JWKS is unusable") from exc


class TokenVerifier:
    """Verifies Supabase access tokens and extracts the authenticated user."""

    def __init__(self, *, jwks_client: SupabaseJWKClient, issuer: str, audience: str) -> None:
        self._jwks_client = jwks_client
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> AuthenticatedUser:
        """Return the authenticated user or raise InvalidTokenError /
        AuthServiceUnavailableError."""
        kid = self._read_key_id(token)
        signing_key = self._get_signing_key(kid)

        try:
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                options={"require": REQUIRED_CLAIMS},
            )
        except PyJWTError as exc:
            raise InvalidTokenError("token signature or claims are invalid") from exc

        if claims["role"] != AUTHENTICATED_ROLE:
            raise InvalidTokenError("token role is not allowed")
        if claims.get("is_anonymous") is True:
            raise InvalidTokenError("anonymous users are not allowed")

        return AuthenticatedUser(id=self._parse_subject(claims["sub"]))

    @staticmethod
    def _read_key_id(token: str) -> str:
        # The header is read before any key lookup so that malformed tokens and
        # tokens with a wrong algorithm are rejected without contacting the JWKS endpoint.
        try:
            header = jwt.get_unverified_header(token)
        except PyJWTError as exc:
            raise InvalidTokenError("token is malformed") from exc

        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise InvalidTokenError("token algorithm is not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise InvalidTokenError("token has no key id")
        return kid

    def _get_signing_key(self, kid: str) -> PyJWK:
        try:
            return self._jwks_client.get_signing_key(kid)
        except JWKSUnavailableError as exc:
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            # Only the exception class name is included: it is safe to log and useful to debug.
            raise AuthServiceUnavailableError(
                f"signing keys could not be loaded ({type(cause).__name__})"
            ) from exc
        except PyJWKClientError as exc:
            # JWKSUnavailableError covers every JWKS problem, so this is an unknown "kid".
            raise InvalidTokenError("token key id is unknown") from exc

    @staticmethod
    def _parse_subject(subject: object) -> UUID:
        if not isinstance(subject, str):
            raise InvalidTokenError("token subject is invalid")
        try:
            return UUID(subject)
        except ValueError as exc:
            raise InvalidTokenError("token subject is invalid") from exc


def create_token_verifier(settings: Settings) -> TokenVerifier:
    """Build a verifier backed by the Supabase JWKS endpoint.

    No network request is made here; keys are fetched on first use.
    """
    jwks_client = SupabaseJWKClient(
        settings.supabase_jwks_url,
        cache_jwk_set=True,
        lifespan=JWKS_CACHE_LIFESPAN_SECONDS,
        timeout=JWKS_FETCH_TIMEOUT_SECONDS,
        cooldown_duration=JWKS_REFRESH_COOLDOWN_SECONDS,
    )
    return TokenVerifier(
        jwks_client=jwks_client,
        issuer=settings.supabase_jwt_issuer,
        audience=settings.supabase_jwt_audience,
    )
