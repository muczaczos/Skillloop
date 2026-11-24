"""Business logic for the competencies of a career goal.

Reading uses one plain user_transaction(). Writing uses run_user_transaction(), which repeats
the whole transaction on a deadlock or a serialization failure (see app/db/session.py); the
repeatable work is the repository INSERT and nothing else.

Nothing is checked before a write (no check-then-insert). The rules live in the database: the row
trigger refuses a goal that is not active (23514, competencies_goal_not_active) and the 51st
active competency (23514, competencies_active_limit), and the unique index refuses an active name
that is taken (23505, competencies_active_name_unique). This module only gives those outcomes
their business meaning, by CONSTRAINT NAME and never by message text. The `except` clauses are
outside run_user_transaction(): by the time they run, the failed transaction has been rolled back
and its connection released, so a follow-up query can use a fresh transaction.
"""

from typing import NoReturn
from uuid import UUID

from psycopg import Connection
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg_pool import ConnectionPool

from app.core.errors import (
    CompetencyLimitReachedError,
    CompetencyNameExistsError,
    GoalArchivedError,
    GoalNotFoundError,
)
from app.db.session import run_user_transaction, user_transaction
from app.models.auth import AuthenticatedUser
from app.models.competency import CompetencyRecord
from app.repositories import career_goals, competencies
from app.schemas.competency import CompetencyCreate

ACTIVE_LIMIT_CONSTRAINT = "competencies_active_limit"
GOAL_NOT_ACTIVE_CONSTRAINT = "competencies_goal_not_active"
ACTIVE_NAME_CONSTRAINT = "competencies_active_name_unique"


def list_competencies(
    pool: ConnectionPool, user: AuthenticatedUser, goal_id: UUID
) -> list[CompetencyRecord]:
    """Every competency of the user's goal, active and archived (also for an archived goal).

    An empty list does not tell a goal without competencies from a goal that is missing or not
    the user's, so the goal is read first, in the same transaction.
    """
    with user_transaction(pool, user) as conn:
        goal = career_goals.get_by_id(conn, user.id, goal_id)
        records = [] if goal is None else competencies.list_by_goal(conn, user.id, goal_id)
    if goal is None:
        raise GoalNotFoundError
    return records


def create_competency(
    pool: ConnectionPool, user: AuthenticatedUser, goal_id: UUID, data: CompetencyCreate
) -> CompetencyRecord:
    """Add one competency to the user's active goal; it is always created active."""

    def insert(conn: Connection) -> CompetencyRecord:
        # Only the INSERT: this function may run more than once (see run_user_transaction).
        return competencies.insert(
            conn,
            user.id,
            goal_id,
            name=data.name,
            description=data.description,
            rationale=data.rationale,
            category=data.category,
            knowledge_type=data.knowledge_type,
            importance=data.importance,
            expected_level=data.expected_level,
            display_order=data.display_order,
        )

    try:
        return run_user_transaction(pool, user, insert)
    except CheckViolation as exc:
        constraint = exc.diag.constraint_name
        if constraint == ACTIVE_LIMIT_CONSTRAINT:
            raise CompetencyLimitReachedError from exc
        if constraint == GOAL_NOT_ACTIVE_CONSTRAINT:
            _raise_goal_problem(pool, user, goal_id, exc)
        raise
    except UniqueViolation as exc:
        if exc.diag.constraint_name == ACTIVE_NAME_CONSTRAINT:
            raise CompetencyNameExistsError from exc
        raise


def _raise_goal_problem(
    pool: ConnectionPool, user: AuthenticatedUser, goal_id: UUID, cause: CheckViolation
) -> NoReturn:
    """Explain why the trigger refused the goal. The trigger cannot tell an archived goal from a
    missing or foreign one (its lock query cannot see them), so the goal is read here, in a NEW
    transaction: the one that failed is gone, and a failed transaction cannot run another query."""
    with user_transaction(pool, user) as conn:
        goal = career_goals.get_by_id(conn, user.id, goal_id)
    if goal is None:  # missing, another user's, or its owner was deleted meanwhile
        raise GoalNotFoundError from cause
    if goal.status == "archived":
        raise GoalArchivedError from cause
    # The goal is active although the trigger refused it. A goal only goes from active to
    # archived and its id is never reused, so this cannot happen in a consistent database:
    # it is a bug, not a client error (the API answers 500).
    raise RuntimeError("the goal is active but the database refused it as not active") from cause
