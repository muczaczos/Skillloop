"""SQL for public.profiles.

Rules for every repository function: the connection is passed in and comes from
user_transaction(), which has already bound the request to the authenticated user and
switched to the `authenticated` role, so Row Level Security applies. Nothing here begins,
commits or rolls back a transaction or touches roles or settings. Database errors (unique
violation, privilege errors, ...) are not caught; the layers above map them.

The user id is also part of every query. RLS would restrict the rows anyway; the explicit
condition keeps the query correct on its own (defense in depth).
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from psycopg import Connection, sql

from app.models.profile import ProfileRecord
from app.repositories._sql import fetch_one, set_clause

# Same as the column-level UPDATE grant of the authenticated role.
UPDATABLE_COLUMNS = ("display_name", "preferred_language")

_COLUMNS = sql.SQL("id, display_name, preferred_language, created_at, updated_at")


def get(conn: Connection, user_id: UUID) -> ProfileRecord | None:
    query = sql.SQL("SELECT {columns} FROM public.profiles WHERE id = %s").format(columns=_COLUMNS)
    return fetch_one(conn, ProfileRecord, query, [user_id])


def update(conn: Connection, user_id: UUID, changes: Mapping[str, Any]) -> ProfileRecord | None:
    """Set the given columns (see UPDATABLE_COLUMNS); None if the profile was not found."""
    assignments, values = set_clause(changes, UPDATABLE_COLUMNS)
    query = sql.SQL("UPDATE public.profiles SET {assignments} WHERE id = %s RETURNING {columns}")
    return fetch_one(
        conn,
        ProfileRecord,
        query.format(assignments=assignments, columns=_COLUMNS),
        [*values, user_id],
    )
