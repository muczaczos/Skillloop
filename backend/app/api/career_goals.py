"""Career goal endpoints, create and read (docs/API-DESIGN.md, section 6).

Both endpoints are plain `def` functions on purpose: the services use blocking database
access, so FastAPI runs them in its thread pool instead of on the event loop.

The identity always comes from the verified access token (`CurrentUser`); the request model
rejects unknown fields (`user_id`, `id`, `status`), so nothing in a request can name another
user or choose the status. Business errors (ACTIVE_GOAL_EXISTS, NO_ACTIVE_GOAL) are raised by
the service as AppError and turned into the unified error response by the central handlers.

Route order: the literal path "/active" must stay declared before any "/{goal_id}" route
(added with the update and archive endpoints), otherwise "active" would be read as an id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from psycopg_pool import ConnectionPool

from app.api.deps import CurrentUser, get_db_pool
from app.schemas.career_goal import CareerGoalCreate, CareerGoalResponse
from app.services import career_goal_service

router = APIRouter(prefix="/career-goals", tags=["career-goals"])

DbPool = Annotated[ConnectionPool, Depends(get_db_pool)]


@router.post(
    "",
    response_model=CareerGoalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the active career goal of the authenticated user",
)
def create_career_goal(
    data: CareerGoalCreate, user: CurrentUser, pool: DbPool
) -> CareerGoalResponse:
    """A user can have only one active goal; a second one is refused with 409."""
    return CareerGoalResponse.model_validate(career_goal_service.create_goal(pool, user, data))


@router.get(
    "/active",
    response_model=CareerGoalResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the active career goal of the authenticated user",
)
def get_active_career_goal(user: CurrentUser, pool: DbPool) -> CareerGoalResponse:
    return CareerGoalResponse.model_validate(career_goal_service.get_active_goal(pool, user))
