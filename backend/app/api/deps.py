"""Shared FastAPI dependencies."""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg_pool import ConnectionPool

from app.core.config import settings
from app.core.security import (
    AuthServiceUnavailableError,
    InvalidTokenError,
    TokenVerifier,
    create_token_verifier,
)
from app.db.errors import DatabaseUnavailableError
from app.models.auth import AuthenticatedUser

logger = logging.getLogger(__name__)

# auto_error=False: missing or non-Bearer credentials are handled below, so every
# authentication failure returns the same 401 response.
bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description=(
        "Supabase Auth access token (a JWT signed with ES256), sent as "
        "`Authorization: Bearer <token>`. Obtain it from Supabase Auth; this API issues no tokens."
    ),
)

UNAUTHORIZED_DETAIL = "Invalid or missing authentication token."
AUTH_UNAVAILABLE_DETAIL = "Authentication is temporarily unavailable. Please try again later."


@lru_cache
def get_token_verifier() -> TokenVerifier:
    """Return the process-wide token verifier (its JWKS cache is shared between requests)."""
    return create_token_verifier(settings)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=UNAUTHORIZED_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> AuthenticatedUser:
    """Authenticate the request using the Supabase access token from the Bearer header.

    Declared as a regular (sync) function on purpose: fetching the JWKS is blocking I/O,
    and FastAPI runs sync dependencies in a thread pool instead of the event loop.
    """
    if credentials is None:
        raise _unauthorized()

    try:
        return verifier.verify(credentials.credentials)
    except InvalidTokenError as exc:
        # The message is a fixed string from app.core.security; it never contains the token.
        logger.info("Rejected access token: %s", exc)
        raise _unauthorized() from None
    except AuthServiceUnavailableError as exc:
        logger.error("Cannot verify access tokens: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=AUTH_UNAVAILABLE_DETAIL,
        ) from None


def get_db_pool(request: Request) -> ConnectionPool:
    """Return the process-wide connection pool created in the application lifespan.

    It hands out the pool only; connections and transactions are managed by the
    service layer, never by a request-scoped dependency.
    """
    pool: ConnectionPool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise DatabaseUnavailableError("database unavailable (pool is not initialised)")
    return pool


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
