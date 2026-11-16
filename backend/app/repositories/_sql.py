"""Small helpers shared by the repositories. Not part of their public API."""

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from psycopg import Connection, sql
from psycopg.rows import class_row
from pydantic import BaseModel


def fetch_one[RecordT: BaseModel](
    conn: Connection, record_type: type[RecordT], query: sql.Composable, params: Sequence[Any]
) -> RecordT | None:
    """Run a query that returns at most one row and map it to a record (None if no row)."""
    with conn.cursor(row_factory=class_row(record_type)) as cur:
        cur.execute(query, params)
        return cur.fetchone()


def set_clause(
    changes: Mapping[str, Any], allowed: Collection[str]
) -> tuple[sql.Composable, list[Any]]:
    """Build "col1 = %s, col2 = %s" and the matching values for an UPDATE.

    Column names never come from the caller's data as such: every key must be in the fixed
    `allowed` list of the repository, and it is quoted as an identifier. Anything else
    (including id, user_id, status, created_at) is a programming error, not an SQL fragment.
    """
    if not changes:
        raise ValueError("there is nothing to update")
    unknown = sorted(name for name in changes if name not in allowed)
    if unknown:
        raise ValueError(f"these columns cannot be updated: {unknown}")
    assignments = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in changes)
    return assignments, list(changes.values())
