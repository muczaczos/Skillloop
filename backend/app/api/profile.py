"""Profile endpoints (docs/API-DESIGN.md, section 5).

Both endpoints are plain `def` functions on purpose: the services use blocking database
access, so FastAPI runs them in its thread pool instead of on the event loop.

The identity always comes from the verified access token (`CurrentUser`); nothing in the
request can name another user (the request model rejects unknown fields such as `user_id`).
Business errors (e.g. a missing profile) are raised by the service as AppError and turned
into the unified error response by the central handlers.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from psycopg_pool import ConnectionPool

from app.api.deps import CurrentUser, get_db_pool
from app.schemas.profile import ProfileResponse, ProfileUpdate
from app.services import profile_service

router = APIRouter(prefix="/profile", tags=["profile"])

DbPool = Annotated[ConnectionPool, Depends(get_db_pool)]


@router.get(
    "",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the profile of the authenticated user",
)
def get_profile(user: CurrentUser, pool: DbPool) -> ProfileResponse:
    return ProfileResponse.model_validate(profile_service.get_profile(pool, user))


@router.patch(
    "",
    response_model=ProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Update the profile of the authenticated user",
)
def update_profile(data: ProfileUpdate, user: CurrentUser, pool: DbPool) -> ProfileResponse:
    """Fields that are left out are not changed. `display_name` may be set to null to clear
    it; `preferred_language` may not be null. At least one field is required."""
    return ProfileResponse.model_validate(profile_service.update_profile(pool, user, data))
