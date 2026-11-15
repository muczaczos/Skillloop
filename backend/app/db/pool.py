"""PostgreSQL connection pool.

The backend connects to the Supavisor SESSION pooler as the powerless `skillloop_api`
role. Connections are opened in autocommit mode: a request opens an explicit transaction
(see the transaction helper, added later) in which it does `SET LOCAL ROLE authenticated`
and sets the transaction-local `request.jwt.claims`.

A pooled connection is shared by many users one after another, so nothing about one user
may survive on the connection. This module enforces that fail closed:
- `configure_connection` refuses connections that are not logged in as skillloop_api;
- `reset_connection` runs every time a connection returns to the pool and clears the
  session state; if the state cannot be proven clean, the connection is closed and the
  pool discards it instead of reusing it.
"""

import logging
import time

import psycopg
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool

from app.core.config import API_DATABASE_ROLE, Settings

logger = logging.getLogger(__name__)

POOL_NAME = "skillloop-api"
APPLICATION_NAME = "skillloop-api"
# Connections are replaced periodically (DNS changes, rotated credentials).
POOL_MAX_LIFETIME_SECONDS = 1800
# How long application start-up waits for the first connection before going on in
# degraded mode.
STARTUP_WAIT_SECONDS = 5

_SESSION_STATE_QUERY = (
    "select current_user,"
    " coalesce(current_setting('request.jwt.claims', true), ''),"
    " coalesce(current_setting('request.jwt.claim.sub', true), '')"
)


class ConnectionStateError(psycopg.OperationalError):
    """A connection is not in the state the application requires (it is never reused).

    Messages are fixed strings: they never contain connection details or user data.
    """


def _reject(conn: Connection, message: str) -> ConnectionStateError:
    # Close first: whatever happens to the exception, this connection must not be reused.
    conn.close()
    return ConnectionStateError(message)


def configure_connection(conn: Connection) -> None:
    """Called once for every new connection: it must be logged in as skillloop_api."""
    row = conn.execute("select current_user").fetchone()
    if row is None or row[0] != API_DATABASE_ROLE:
        raise _reject(conn, "database connection is not using the expected role")


def reset_connection(conn: Connection) -> None:
    """Called when a connection returns to the pool, after the pool rolled back any
    open transaction. Clears session state and verifies the result."""
    # RESET ALL does not undo SET ROLE, hence the explicit RESET ROLE.
    conn.execute("RESET ROLE; RESET ALL")
    row = conn.execute(_SESSION_STATE_QUERY).fetchone()
    if row is None:
        raise _reject(conn, "database connection state could not be verified")
    current_user, claims, claim_sub = row
    if current_user != API_DATABASE_ROLE:
        raise _reject(conn, "database connection kept a switched role")
    if claims or claim_sub:
        raise _reject(conn, "database connection kept user identity settings")
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise _reject(conn, "database connection is still inside a transaction")


def create_pool(settings: Settings) -> ConnectionPool:
    """Build the pool. It is created closed: no connection is attempted until open()."""
    return ConnectionPool(
        settings.database_url.get_secret_value(),
        name=POOL_NAME,
        open=False,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout_seconds,
        max_lifetime=POOL_MAX_LIFETIME_SECONDS,
        kwargs={
            "autocommit": True,
            # No server-side prepared statements: they are pointless at this scale and
            # would break if the pooler mode ever changed.
            "prepare_threshold": None,
            "connect_timeout": settings.db_connect_timeout_seconds,
            "application_name": APPLICATION_NAME,
        },
        configure=configure_connection,
        check=ConnectionPool.check_connection,
        reset=reset_connection,
    )


def wait_until_ready(pool: ConnectionPool, timeout: float) -> bool:
    """Block until the pool holds an idle connection; return False on timeout.

    ConnectionPool.wait() is not used on purpose: it CLOSES the pool when the timeout
    expires, but the application must stay up and let the pool keep reconnecting.
    """
    deadline = time.monotonic() + timeout
    while True:
        if pool.get_stats().get("pool_available", 0) >= 1:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
