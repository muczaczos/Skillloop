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

from uuid import UUID

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKSetError,
    PyJWTError,
)

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


class InvalidTokenError(Exception):
    """The access token is malformed, expired or otherwise not acceptable."""


class AuthServiceUnavailableError(Exception):
    """The signing keys could not be obtained, so no token can be verified."""


class TokenVerifier:
    """Verifies Supabase access tokens and extracts the authenticated user."""

    def __init__(self, *, jwks_client: PyJWKClient, issuer: str, audience: str) -> None:
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

    def _get_signing_key(self, kid: str) -> jwt.PyJWK:
        try:
            return self._jwks_client.get_signing_key(kid)
        except (PyJWKClientConnectionError, PyJWKSetError) as exc:
            # Checked before PyJWKClientError, which is the base class of the connection error.
            raise AuthServiceUnavailableError("signing keys could not be loaded") from exc
        except PyJWKClientError as exc:
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
    jwks_client = PyJWKClient(
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
