"""SQL for public.competencies.

Same rules as the other repositories: the connection comes from user_transaction(), nothing here
manages transactions, roles or settings, and database errors are not caught or translated; the
service maps them (by constraint name) to API errors.

Nothing is checked before a write. The database enforces, in this order for an INSERT: that the
goal is the caller's active goal and that it has fewer than 50 active competencies (the row
trigger, SQLSTATE 23514, constraints competencies_goal_not_active and competencies_active_limit),
then the unique active name (23505, competencies_active_name_unique). Row Level Security restricts
every query to the competencies of the caller.
"""

from uuid import UUID

from psycopg import Connection, sql

from app.models.competency import CompetencyRecord, ExpectedLevel, Importance, KnowledgeType
from app.repositories._sql import fetch_all, fetch_one

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
