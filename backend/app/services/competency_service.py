"""Business logic for the competencies of a career goal.

Reading uses one plain user_transaction(). Writing uses run_user_transaction(), which repeats
the whole transaction on a deadlock or a serialization failure (see app/db/session.py); the
repeatable work is database statements only: one write and, if an UPDATE changed
nothing, the reads that explain why.

Nothing is checked before a write (no check-then-insert). The rules live in the database: the row
trigger refuses a goal that is not active (23514, competencies_goal_not_active) and the 51st
active competency (23514, competencies_active_limit), and the unique index refuses an active name
that is taken (23505, competencies_active_name_unique). An UPDATE of a competency that cannot be
changed matches no row and raises nothing. This module only gives those outcomes
their business meaning, by CONSTRAINT NAME and never by message text. The `except` clauses are
outside run_user_transaction(): by the time they run, the failed transaction has been rolled back
and its connection released, so a follow-up query can use a fresh transaction.
"""

from collections.abc import Callable
from typing import NoReturn
from uuid import UUID

from psycopg import Connection
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg_pool import ConnectionPool

from app.core.errors import (
    CompetencyArchivedError,
    CompetencyLimitReachedError,
    CompetencyNameExistsError,
    CompetencyNotFoundError,
    GoalArchivedError,
    GoalNotFoundError,
)
from app.db.session import run_user_transaction, user_transaction
from app.models.auth import AuthenticatedUser
from app.models.competency import CompetencyRecord
from app.repositories import career_goals, competencies
from app.schemas.competency import CompetencyCreate, CompetencyUpdate

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


def update_competency(
    pool: ConnectionPool, user: AuthenticatedUser, competency_id: UUID, data: CompetencyUpdate
) -> CompetencyRecord:
    """Change the sent fields of an active competency in the user's active goal."""
    changes = data.changes()
    try:
        return _change_active(
            pool,
            user,
            competency_id,
            lambda conn: competencies.update_active(conn, user.id, competency_id, changes),
        )
    except UniqueViolation as exc:
        if exc.diag.constraint_name == ACTIVE_NAME_CONSTRAINT:
            raise CompetencyNameExistsError from exc
        raise


def archive_competency(
    pool: ConnectionPool, user: AuthenticatedUser, competency_id: UUID
) -> CompetencyRecord:
    """Archive an active competency in the user's active goal. It cannot be undone."""
    return _change_active(
        pool,
        user,
        competency_id,
        lambda conn: competencies.archive_active(conn, user.id, competency_id),
    )


def _change_active(
    pool: ConnectionPool,
    user: AuthenticatedUser,
    competency_id: UUID,
    change: Callable[[Connection], CompetencyRecord | None],
) -> CompetencyRecord:
    """Run one UPDATE of an active competency and explain it if it was not applied."""

    def apply(conn: Connection) -> CompetencyRecord:
        record = change(conn)
        if record is None:
            _raise_unchanged_reason(conn, user, competency_id)
        return record

    try:
        return run_user_transaction(pool, user, apply)
    except CheckViolation as exc:
        if exc.diag.constraint_name != GOAL_NOT_ACTIVE_CONSTRAINT:
            raise
        # The goal was archived while the UPDATE waited for its lock. The failed transaction is
        # gone, so the reason is read in a new one, with the same precedence as above.
        with user_transaction(pool, user) as conn:
            _raise_unchanged_reason(conn, user, competency_id)


def _raise_unchanged_reason(
    conn: Connection, user: AuthenticatedUser, competency_id: UUID
) -> NoReturn:
    """Explain why a competency could not be changed. The order is part of the API contract:
    not visible, then the competency itself is archived, then its goal is archived."""
    competency = competencies.get_by_id(conn, user.id, competency_id)
    if competency is None:  # missing, or another user's competency hidden by RLS
        raise CompetencyNotFoundError
    if competency.status == "archived":
        raise CompetencyArchivedError
    goal = career_goals.get_by_id(conn, user.id, competency.career_goal_id)
    if goal is not None and goal.status == "archived":
        raise GoalArchivedError
    # Competency and goal are active although the database did not apply the change. Both only
    # ever go from active to archived, so this is a bug, not a client error (500).
    raise RuntimeError("the active competency was not changed")


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
