"""Competency endpoints (docs/API-DESIGN.md, section 7).

GET and POST /career-goals/{goal_id}/competencies.

Both endpoints are plain `def` functions on purpose: the service uses blocking database access,
so FastAPI runs them in its thread pool instead of on the event loop.

The router only connects HTTP to the service: it validates the request, passes the user from the
verified token, and shapes the response. Every rule (ownership, the 50-active limit, unique active
names, archived goals) lives in the service and the database. Their errors (GOAL_NOT_FOUND,
GOAL_ARCHIVED, COMPETENCY_LIMIT_REACHED, COMPETENCY_NAME_EXISTS) are raised as AppError and turned
into the unified error response by the central handlers, so nothing is mapped here.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from psycopg_pool import ConnectionPool

from app.api.deps import CurrentUser, get_db_pool
from app.api.openapi import (
    VALIDATION_ERROR,
    conflict,
    not_found,
    protected_responses,
)
from app.core.errors import (
    CompetencyLimitReachedError,
    CompetencyNameExistsError,
    GoalArchivedError,
    GoalNotFoundError,
)
from app.schemas.competency import CompetencyCreate, CompetencyListResponse, CompetencyResponse
from app.services import competency_service

router = APIRouter(
    prefix="/career-goals/{goal_id}/competencies",
    tags=["competencies"],
    responses=protected_responses(),
)

DbPool = Annotated[ConnectionPool, Depends(get_db_pool)]


@router.get(
    "",
    response_model=CompetencyListResponse,
    status_code=status.HTTP_200_OK,
    summary="List the competencies of a career goal of the authenticated user",
    responses={**not_found(GoalNotFoundError), **VALIDATION_ERROR},
)
def list_competencies(goal_id: UUID, user: CurrentUser, pool: DbPool) -> CompetencyListResponse:
    """Returns every competency of the goal, active and archived, in the order the repository
    gives (`display_order`, `created_at`, `id`). The list may be empty. It also works for an
    archived goal. A goal that is missing or belongs to another user is a 404."""
    records = competency_service.list_competencies(pool, user, goal_id)
    return CompetencyListResponse(
        competencies=[CompetencyResponse.model_validate(record) for record in records]
    )


@router.post(
    "",
    response_model=CompetencyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a competency to an active career goal of the authenticated user",
    responses={
        **not_found(GoalNotFoundError),
        **conflict(GoalArchivedError, CompetencyNameExistsError, CompetencyLimitReachedError),
        **VALIDATION_ERROR,
    },
)
def create_competency(
    goal_id: UUID, data: CompetencyCreate, user: CurrentUser, pool: DbPool
) -> CompetencyResponse:
    """The competency is always created active. A goal has at most 50 active competencies and
    the name of an active competency is unique within the goal, ignoring case. An archived goal
    cannot get new competencies (409)."""
    return CompetencyResponse.model_validate(
        competency_service.create_competency(pool, user, goal_id, data)
    )
