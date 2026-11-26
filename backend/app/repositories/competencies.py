"""SQL for public.competencies.

Same rules as the other repositories: the connection comes from user_transaction(), nothing here
manages transactions, roles or settings, and database errors are not caught or translated; the
service maps them (by constraint name) to API errors.

Nothing is checked before a write. The database enforces, in this order for an INSERT: that the
goal is the caller's active goal and that it has fewer than 50 active competencies (the row
trigger, SQLSTATE 23514, constraints competencies_goal_not_active and competencies_active_limit),
then the unique active name (23505, competencies_active_name_unique). Row Level Security restricts
every query to the competencies of the caller.

An archived competency is immutable, and so is every competency of an archived goal. update_active()
and archive_active() return None when they change nothing: the first rule is in their WHERE clause,
the second in the UPDATE policy. If the goal is archived while the statement waits for the goal
lock, the row trigger refuses instead (competencies_goal_not_active).
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from psycopg import Connection, sql

from app.models.competency import CompetencyRecord, ExpectedLevel, Importance, KnowledgeType
from app.repositories._sql import fetch_all, fetch_one, set_clause

# Same as the column-level UPDATE grant of the authenticated role, without `status`:
# the status only changes through archive_active().
UPDATABLE_COLUMNS = (
    "name",
    "description",
    "rationale",
    "category",
    "knowledge_type",
    "importance",
    "expected_level",
    "display_order",
)

_COLUMNS = sql.SQL(
    "id, career_goal_id, user_id, name, description, rationale, category, knowledge_type, "
    "importance, expected_level, display_order, status, created_at, updated_at"
)


def list_by_goal(conn: Connection, user_id: UUID, goal_id: UUID) -> list[CompetencyRecord]:
    """Every competency of the goal that belongs to the user, active and archived, in a stable order.

    The user id is part of the query next to Row Level Security (defense in depth), as in the
    other repositories. A goal that does not exist, or is not the user's, gives an empty list; the
    service reads the goal to tell that apart from a goal without competencies. The order is
    total: `id` breaks ties of `display_order` and `created_at`.
    """
    query = sql.SQL(
        "SELECT {columns} FROM public.competencies WHERE career_goal_id = %s AND user_id = %s "
        "ORDER BY display_order, created_at, id"
    ).format(columns=_COLUMNS)
    return fetch_all(conn, CompetencyRecord, query, [goal_id, user_id])


def insert(
    conn: Connection,
    user_id: UUID,
    goal_id: UUID,
    *,
    name: str,
    description: str | None,
    rationale: str | None,
    category: str | None,
    knowledge_type: KnowledgeType,
    importance: Importance,
    expected_level: ExpectedLevel,
    display_order: int,
) -> CompetencyRecord:
    """Create a competency in the goal; it is always active (`status` is not insertable)."""
    query = sql.SQL(
        "INSERT INTO public.competencies (career_goal_id, user_id, name, description, rationale, "
        "category, knowledge_type, importance, expected_level, display_order) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {columns}"
    ).format(columns=_COLUMNS)
    record = fetch_one(
        conn,
        CompetencyRecord,
        query,
        [
            goal_id,
            user_id,
            name,
            description,
            rationale,
            category,
            knowledge_type,
            importance,
            expected_level,
            display_order,
        ],
    )
    if record is None:  # INSERT ... RETURNING always yields the row
        raise RuntimeError("INSERT returned no row")
    return record


def get_by_id(conn: Connection, user_id: UUID, competency_id: UUID) -> CompetencyRecord | None:
    """A competency of this user in any status (None if it does not exist or is not theirs)."""
    query = sql.SQL("SELECT {columns} FROM public.competencies WHERE id = %s AND user_id = %s")
    return fetch_one(
        conn, CompetencyRecord, query.format(columns=_COLUMNS), [competency_id, user_id]
    )


def update_active(
    conn: Connection, user_id: UUID, competency_id: UUID, changes: Mapping[str, Any]
) -> CompetencyRecord | None:
    """Set the given columns (see UPDATABLE_COLUMNS) of an ACTIVE competency of this user in an
    active goal. None if there is no such competency; nothing was changed."""
    assignments, values = set_clause(changes, UPDATABLE_COLUMNS)
    query = sql.SQL(
        "UPDATE public.competencies SET {assignments} "
        "WHERE id = %s AND user_id = %s AND status = 'active' RETURNING {columns}"
    ).format(assignments=assignments, columns=_COLUMNS)
    return fetch_one(conn, CompetencyRecord, query, [*values, competency_id, user_id])


def archive_active(conn: Connection, user_id: UUID, competency_id: UUID) -> CompetencyRecord | None:
    """Archive an ACTIVE competency of this user in an active goal; None if there is none."""
    query = sql.SQL(
        "UPDATE public.competencies SET status = 'archived' "
        "WHERE id = %s AND user_id = %s AND status = 'active' RETURNING {columns}"
    ).format(columns=_COLUMNS)
    return fetch_one(conn, CompetencyRecord, query, [competency_id, user_id])
