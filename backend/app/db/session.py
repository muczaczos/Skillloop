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
import logging
import random
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from psycopg import Connection
from psycopg.errors import DeadlockDetected, SerializationFailure
from psycopg_pool import ConnectionPool

from app.db.errors import DatabaseUnavailableError, translate_db_errors
from app.models.auth import AuthenticatedUser

logger = logging.getLogger(__name__)

AUTHENTICATED_DB_ROLE = "authenticated"

# run_user_transaction(): at most this many attempts in total (the first one and the retries).
RETRY_MAX_ATTEMPTS = 3
# A short random pause before a retry, so that two transactions that just collided do not
# collide again at the same instant. Seconds; deliberately small.
RETRY_PAUSE_MIN_SECONDS = 0.01
RETRY_PAUSE_MAX_SECONDS = 0.05
# The only failures that are retried: 40P01 deadlock_detected and 40001 serialization_failure.
# PostgreSQL has already rolled the transaction back when it reports them, and running the same
# work again in a new transaction is what it asks the client to do.
RETRYABLE_ERRORS = (DeadlockDetected, SerializationFailure)

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


def run_user_transaction[ResultT](
    pool: ConnectionPool,
    user: AuthenticatedUser,
    work: Callable[[Connection], ResultT],
    *,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
) -> ResultT:
    """Run `work(conn)` in a user_transaction(), repeating the WHOLE transaction when PostgreSQL
    reports a deadlock (40P01) or a serialization failure (40001).

    Every attempt is a complete, new user_transaction(): a new connection checkout, BEGIN,
    SET LOCAL ROLE authenticated and the claims of `user`. The exception is caught OUTSIDE the
    `with` block, so by then user_transaction() has rolled the failed transaction back and
    returned the connection to the pool (which resets it). The next attempt never starts before
    that. A failure at COMMIT is handled the same way: nothing was committed, so nothing is
    applied twice. Anything else, for example any other database error or an error of the
    application, is raised at once and unchanged, without a retry. After `max_attempts` failed
    attempts the answer is DatabaseUnavailableError, which the API reports as 503.

    `work` must be safe to run more than once: everything it does has to happen inside the
    transaction (database statements only). It must not send messages, call external services
    or keep state between attempts, because an attempt that failed is rolled back but its side
    effects outside the database are not.

    Only code that really needs this uses it (writes to competencies); user_transaction()
    itself and the existing services are unchanged.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(1, max_attempts + 1):
        try:
            with user_transaction(pool, user) as conn:
                return work(conn)
        except RETRYABLE_ERRORS as exc:
            # Only the SQLSTATE is logged or reported: the message of such an error can hold
            # SQL text and row values.
            sqlstate = exc.sqlstate
            if attempt == max_attempts:
                logger.error(
                    "transaction failed after %d attempts (SQLSTATE %s)", max_attempts, sqlstate
                )
                raise DatabaseUnavailableError(
                    f"database unavailable (SQLSTATE {sqlstate}, {max_attempts} attempts)"
                ) from exc
            logger.warning(
                "transaction attempt %d of %d failed (SQLSTATE %s); retrying",
                attempt,
                max_attempts,
                sqlstate,
            )
            time.sleep(random.uniform(RETRY_PAUSE_MIN_SECONDS, RETRY_PAUSE_MAX_SECONDS))
    raise AssertionError("unreachable: the last attempt returns or raises")  # pragma: no cover
