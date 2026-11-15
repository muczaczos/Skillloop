"""Database error translation.

Only "the database cannot be used right now" problems are translated here. Constraint
violations and other SQL errors are left untouched; they are mapped where they have a
business meaning.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import PoolClosed, PoolTimeout

# SQLSTATE classes meaning "the server or connection is unavailable":
# 08 connection exception, 53 insufficient resources, 57P operator intervention
# (e.g. 57P01 admin shutdown). A statement timeout is 57014 and is NOT included.
_UNAVAILABLE_SQLSTATE_PREFIXES = ("08", "53", "57P")


class DatabaseUnavailableError(Exception):
    """The database could not be reached or no connection was available in time.

    The message only holds the class name of the cause (and the SQLSTATE, if any), so it
    is safe to log and never contains the connection string or user data.
    """


def _is_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, PoolTimeout | PoolClosed):
        return True
    if isinstance(exc, psycopg.OperationalError):
        sqlstate = exc.sqlstate
        # No SQLSTATE: a client-side failure (cannot connect, connection lost, ...).
        return sqlstate is None or sqlstate.startswith(_UNAVAILABLE_SQLSTATE_PREFIXES)
    return False


@contextmanager
def translate_db_errors() -> Iterator[None]:
    """Raise DatabaseUnavailableError for connection and pool problems."""
    try:
        yield
    except (psycopg.OperationalError, PoolTimeout, PoolClosed) as exc:
        if not _is_unavailable(exc):
            raise
        detail = type(exc).__name__ + (f", SQLSTATE {exc.sqlstate}" if exc.sqlstate else "")
        raise DatabaseUnavailableError(f"database unavailable ({detail})") from exc
