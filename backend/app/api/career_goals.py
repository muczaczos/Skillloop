"""Career goal endpoints (docs/API-DESIGN.md, section 6).

POST /career-goals, GET /career-goals/active, PATCH /career-goals/{goal_id} and
POST /career-goals/{goal_id}/archive.

All endpoints are plain `def` functions on purpose: the services use blocking database
access, so FastAPI runs them in its thread pool instead of on the event loop.

The identity always comes from the verified access token (`CurrentUser`); the request model
rejects unknown fields (`user_id`, `id`, `status`), so nothing in a request can name another
user or choose the status. Business errors (ACTIVE_GOAL_EXISTS, NO_ACTIVE_GOAL, GOAL_NOT_FOUND,
GOAL_ARCHIVED) are raised by the service as AppError and turned into the unified error response
by the central handlers. A goal of another user is indistinguishable from a missing one (404).

Route order: the literal path "/active" must stay declared before the "/{goal_id}" routes.
"active" is not a UUID, so PATCH /career-goals/active is a validation error (422), not a 405.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from psycopg_pool import ConnectionPool

from app.api.deps import CurrentUser, get_db_pool
from app.api.openapi import VALIDATION_ERROR, conflict, not_found, protected_responses
from app.core.errors import (
    ActiveGoalExistsError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
)
from app.schemas.career_goal import CareerGoalCreate, CareerGoalResponse, CareerGoalUpdate
from app.services import career_goal_service

router = APIRouter(
    prefix="/career-goals",
    tags=["career-goals"],
    responses=protected_responses(user_may_be_missing=True),
)

DbPool = Annotated[ConnectionPool, Depends(get_db_pool)]


@router.post(
    "",
    response_model=CareerGoalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the active career goal of the authenticated user",
    responses={**conflict(ActiveGoalExistsError), **VALIDATION_ERROR},
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
    responses=not_found(NoActiveGoalError),
)
def get_active_career_goal(user: CurrentUser, pool: DbPool) -> CareerGoalResponse:
    """Returns the active goal of the user, or 404 NO_ACTIVE_GOAL if there is none (for
    example after the goal was archived)."""
    return CareerGoalResponse.model_validate(career_goal_service.get_active_goal(pool, user))


@router.patch(
    "/{goal_id}",
    response_model=CareerGoalResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an active career goal of the authenticated user",
    responses={
        **not_found(GoalNotFoundError),
        **conflict(GoalArchivedError),
        **VALIDATION_ERROR,
    },
)
def update_career_goal(
    goal_id: UUID, data: CareerGoalUpdate, user: CurrentUser, pool: DbPool
) -> CareerGoalResponse:
    """Fields that are left out are not changed. `description` may be set to null to clear it;
    `target_role` and `seniority` may not be null. At least one field is required. An archived
    goal cannot be changed (409)."""
    return CareerGoalResponse.model_validate(
        career_goal_service.update_goal(pool, user, goal_id, data)
    )


@router.post(
    "/{goal_id}/archive",
    response_model=CareerGoalResponse,
    status_code=status.HTTP_200_OK,
    summary="Archive an active career goal of the authenticated user",
    responses={
        **not_found(GoalNotFoundError),
        **conflict(GoalArchivedError),
        **VALIDATION_ERROR,
    },
)
def archive_career_goal(goal_id: UUID, user: CurrentUser, pool: DbPool) -> CareerGoalResponse:
    """Returns the archived goal. Archiving an archived goal is refused with 409."""
    return CareerGoalResponse.model_validate(career_goal_service.archive_goal(pool, user, goal_id))
