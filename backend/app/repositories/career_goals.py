"""SQL for public.career_goals.

Same rules as the profile repository: the connection comes from user_transaction(), nothing
here manages transactions, roles or settings, database errors are not caught, and the user
id is part of every query next to Row Level Security.

A goal that is archived is immutable (decision D13): update_active() and archive_active()
only touch rows WHERE status = 'active' and return None otherwise, so the caller can tell
"nothing was changed" from a change.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from psycopg import Connection, sql

from app.models.career_goal import CareerGoalRecord, Seniority
from app.repositories._sql import fetch_one, set_clause

# Same as the column-level UPDATE grant of the authenticated role, without `status`:
# the status only changes through archive_active().
UPDATABLE_COLUMNS = ("target_role", "seniority", "description")

_COLUMNS = sql.SQL(
    "id, user_id, target_role, seniority, description, status, created_at, updated_at"
)


def insert(
    conn: Connection,
    user_id: UUID,
    *,
    target_role: str,
    seniority: Seniority,
    description: str | None,
) -> CareerGoalRecord:
    """Create a goal; it is always active. A second active goal of the same user violates
    the unique index one_active_goal_per_user (psycopg.errors.UniqueViolation)."""
    query = sql.SQL(
        "INSERT INTO public.career_goals (user_id, target_role, seniority, description) "
        "VALUES (%s, %s, %s, %s) RETURNING {columns}"
    ).format(columns=_COLUMNS)
    record = fetch_one(
        conn, CareerGoalRecord, query, [user_id, target_role, seniority, description]
    )
    if record is None:  # INSERT ... RETURNING always yields the row
        raise RuntimeError("INSERT returned no row")
    return record


def get_by_id(conn: Connection, user_id: UUID, goal_id: UUID) -> CareerGoalRecord | None:
    """A goal of this user in any status (None if it does not exist or is not theirs)."""
    query = sql.SQL("SELECT {columns} FROM public.career_goals WHERE id = %s AND user_id = %s")
    return fetch_one(conn, CareerGoalRecord, query.format(columns=_COLUMNS), [goal_id, user_id])


def get_active(conn: Connection, user_id: UUID) -> CareerGoalRecord | None:
    query = sql.SQL(
        "SELECT {columns} FROM public.career_goals WHERE user_id = %s AND status = 'active'"
    )
    return fetch_one(conn, CareerGoalRecord, query.format(columns=_COLUMNS), [user_id])


def update_active(
    conn: Connection, user_id: UUID, goal_id: UUID, changes: Mapping[str, Any]
) -> CareerGoalRecord | None:
    """Set the given columns (see UPDATABLE_COLUMNS) of an ACTIVE goal of this user.
    None if there is no such goal (missing, not theirs or archived); nothing was changed."""
    assignments, values = set_clause(changes, UPDATABLE_COLUMNS)
    query = sql.SQL(
        "UPDATE public.career_goals SET {assignments} "
        "WHERE id = %s AND user_id = %s AND status = 'active' RETURNING {columns}"
    ).format(assignments=assignments, columns=_COLUMNS)
    return fetch_one(conn, CareerGoalRecord, query, [*values, goal_id, user_id])


def archive_active(conn: Connection, user_id: UUID, goal_id: UUID) -> CareerGoalRecord | None:
    """Archive an ACTIVE goal of this user; None if there is no such goal."""
    query = sql.SQL(
        "UPDATE public.career_goals SET status = 'archived' "
        "WHERE id = %s AND user_id = %s AND status = 'active' RETURNING {columns}"
    ).format(columns=_COLUMNS)
    return fetch_one(conn, CareerGoalRecord, query, [goal_id, user_id])
