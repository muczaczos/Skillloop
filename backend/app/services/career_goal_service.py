"""Business logic for career goals.

Each function owns one user_transaction(), so the repository calls run as the authenticated
user and Row Level Security applies. Services never call other services.

Concurrency: nothing is checked before it is written (no check-then-insert). The partial
unique index one_active_goal_per_user and the `status = 'active'` condition of the UPDATE
statements are the guards; this module only gives their outcome a business meaning.
"""

from typing import NoReturn
from uuid import UUID

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from app.core.errors import (
    ActiveGoalExistsError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
)
from app.db.session import user_transaction
from app.models.auth import AuthenticatedUser
from app.models.career_goal import CareerGoalRecord
from app.repositories import career_goals
from app.schemas.career_goal import CareerGoalCreate, CareerGoalUpdate

ACTIVE_GOAL_CONSTRAINT = "one_active_goal_per_user"


def create_goal(
    pool: ConnectionPool, user: AuthenticatedUser, data: CareerGoalCreate
) -> CareerGoalRecord:
    # The except clause is deliberately outside user_transaction(): by the time it runs the
    # failed transaction has been rolled back, and the original UniqueViolation (not a
    # translation made inside the block) is what reaches this function.
    try:
        with user_transaction(pool, user) as conn:
            return career_goals.insert(
                conn,
                user.id,
                target_role=data.target_role,
                seniority=data.seniority,
                description=data.description,
            )
    except UniqueViolation as exc:
        if exc.diag.constraint_name == ACTIVE_GOAL_CONSTRAINT:
            raise ActiveGoalExistsError from exc
        raise


def get_active_goal(pool: ConnectionPool, user: AuthenticatedUser) -> CareerGoalRecord:
    with user_transaction(pool, user) as conn:
        goal = career_goals.get_active(conn, user.id)
    if goal is None:
        raise NoActiveGoalError
    return goal


def update_goal(
    pool: ConnectionPool, user: AuthenticatedUser, goal_id: UUID, data: CareerGoalUpdate
) -> CareerGoalRecord:
    with user_transaction(pool, user) as conn:
        goal = career_goals.update_active(conn, user.id, goal_id, data.changes())
        if goal is None:
            _raise_unchanged_reason(conn, user, goal_id)
    return goal


def archive_goal(pool: ConnectionPool, user: AuthenticatedUser, goal_id: UUID) -> CareerGoalRecord:
    with user_transaction(pool, user) as conn:
        goal = career_goals.archive_active(conn, user.id, goal_id)
        if goal is None:
            _raise_unchanged_reason(conn, user, goal_id)
    return goal


def _raise_unchanged_reason(conn: Connection, user: AuthenticatedUser, goal_id: UUID) -> NoReturn:
    """Explain why an UPDATE of an active goal changed nothing. Runs only on that path."""
    existing = career_goals.get_by_id(conn, user.id, goal_id)
    if existing is None:  # missing, or another user's goal hidden by RLS
        raise GoalNotFoundError
    if existing.status == "archived":
        raise GoalArchivedError
    # Still active although the UPDATE matched nothing: this cannot happen in a consistent
    # database, so it is a bug, not a client error (the API answers 500).
    raise RuntimeError("active goal was not updated")
