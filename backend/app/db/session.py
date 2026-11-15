"""Per-request database transaction bound to the authenticated user.

`user_transaction()` is the ONLY way application code obtains a database connection for a
user request. It makes PostgreSQL enforce Row Level Security for exactly that user:

    pool.connection()                      # connection logged in as skillloop_api (no privileges)
      BEGIN
        SET LOCAL ROLE authenticated       # RLS applies from here on
        set_config('request.jwt.claims', {"sub": <user id>, "role": "authenticated"}, true)
        verify auth.uid() == user.id and current_user == authenticated
        ... service code ...
      COMMIT, or ROLLBACK on any exception

Everything is transaction-local (SET LOCAL / set_config(..., true)), so nothing about one
user can remain on a pooled connection that serves the next one; the pool additionally
resets and verifies every returned connection (see app/db/pool.py).
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.db.errors import translate_db_errors
from app.models.auth import AuthenticatedUser

AUTHENTICATED_DB_ROLE = "authenticated"

# True while a user_transaction() is open in the current execution context. A ContextVar is
# isolated per execution context (each thread has its own; a sync request handler run in the
# thread pool gets a copy of the context), not literally "per thread": code that moves work to
# another thread or task may or may not carry the value along, depending on how it is started.
_active: ContextVar[bool] = ContextVar("user_transaction_active", default=False)


class NestedUserTransactionError(RuntimeError):
    """user_transaction() was called while another one is already open (a programming error)."""


class UserContextMismatchError(RuntimeError):
    """The database does not see the identity that was bound to the transaction.

    Never raised in normal operation; it means session state leaked or the setup is broken.
    The message is fixed: it contains no user id.
    """


@contextmanager
def user_transaction(pool: ConnectionPool, user: AuthenticatedUser) -> Iterator[Connection]:
    """Run a block in one transaction that PostgreSQL attributes to `user`.

    Yields the connection; the block must pass it to repositories and must not keep it
    after the block ends (it goes back to the pool and serves other users).

    Trust: `user` must be the AuthenticatedUser produced by the existing JWT verification
    (app.api.deps.get_current_user). The isinstance check below only rejects obvious
    misuse such as passing a raw UUID; it is NOT a verification of identity. Never build
    an AuthenticatedUser from request data.

    Rules:
    - Sync only: call it from sync code (route handlers declared with `def`, services).
    - No nesting: a second call inside the block raises NestedUserTransactionError. The
      guard follows the execution context (ContextVar) in which the block runs. It cannot
      see a connection handed to other code that runs in a different execution context, for
      example another thread.
    - Keep it short and never do slow external I/O (AI calls, HTTP) inside the block: the
      transaction holds one of the few pooled connections.
    - Isolation level is the PostgreSQL default, READ COMMITTED.

    Errors: connection and pool problems (also while the block runs, on COMMIT and on
    ROLLBACK) become DatabaseUnavailableError. SQL errors such as unique violations are
    raised unchanged. Any exception rolls the transaction back.
    """
    if not isinstance(user, AuthenticatedUser):
        raise TypeError("user must be the AuthenticatedUser produced by token verification")
    if _active.get():
        raise NestedUserTransactionError("user_transaction() must not be nested")

    token = _active.set(True)
    try:
        # translate_db_errors covers the whole life cycle: getting a connection, BEGIN,
        # the block, COMMIT / ROLLBACK and returning the connection to the pool.
        with translate_db_errors(), pool.connection() as conn, conn.transaction():
            _bind_user(conn, user)
            yield conn
    finally:
        _active.reset(token)


def _bind_user(conn: Connection, user: AuthenticatedUser) -> None:
    claims = json.dumps({"sub": str(user.id), "role": AUTHENTICATED_DB_ROLE})
    conn.execute("SET LOCAL ROLE authenticated")
    # Bound as a parameter, never formatted into SQL. `true` = local to this transaction.
    conn.execute("SELECT set_config('request.jwt.claims', %s, true)", [claims])
    # auth.uid() prefers the legacy request.jwt.claim.sub setting over the claims JSON, so a
    # value left on the session by a bug would silently override the identity set above.
    row = conn.execute("select auth.uid(), current_user").fetchone()
    if row is None or row[0] != user.id or row[1] != AUTHENTICATED_DB_ROLE:
        raise UserContextMismatchError(
            "database user context does not match the authenticated user"
        )
