"""user_transaction() against a REAL local PostgreSQL / Supavisor with real RLS.

Needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL (see db_integration_helpers). Throwaway
users are created in auth.users (the trigger gives each a profile) and deleted afterwards.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app.db.errors import DatabaseUnavailableError
from app.db.pool import configure_connection
from app.db.session import (
    NestedUserTransactionError,
    UserContextMismatchError,
    user_transaction,
)
from app.models.auth import AuthenticatedUser
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    USER_COUNT,
    assert_local,
    open_api_pool,
)

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)


class BlockError(Exception):
    pass


def _display_name(admin: psycopg.Connection, user: AuthenticatedUser) -> str | None:
    row = admin.execute("select display_name from public.profiles where id = %s", [user.id])
    return row.fetchone()[0]  # type: ignore[index]


def _clean_session(conn: psycopg.Connection) -> None:
    assert conn.execute("select current_user").fetchone() == ("skillloop_api",)
    for setting in ("request.jwt.claims", "request.jwt.claim.sub"):
        assert conn.execute("select current_setting(%s, true)", [setting]).fetchone() in {
            (None,),
            ("",),
        }
    assert conn.execute("show statement_timeout").fetchone() == ("15s",)


# --- a normal transaction ----------------------------------------------------------------------


def test_the_user_sees_only_own_rows_and_a_minimal_claims_context(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    user = users[0]

    with user_transaction(pool, user) as conn:
        assert conn.execute("select id from public.profiles").fetchall() == [(user.id,)]
        assert conn.execute("select auth.uid(), current_user").fetchone() == (
            user.id,
            "authenticated",
        )
        claims = conn.execute("select current_setting('request.jwt.claims')").fetchone()[0]  # type: ignore[index]
        assert json.loads(claims) == {"sub": str(user.id), "role": "authenticated"}


def test_changes_are_committed(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with user_transaction(pool, users[0]) as conn:
        conn.execute("update public.profiles set display_name = 'Committed'")

    assert _display_name(admin, users[0]) == "Committed"
    assert _display_name(admin, users[1]) is None


def test_a_user_cannot_touch_another_users_rows(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with user_transaction(pool, users[0]) as conn:
        cur = conn.execute(
            "update public.profiles set display_name = 'Hacked' where id = %s", [users[1].id]
        )
        assert cur.rowcount == 0

    assert _display_name(admin, users[1]) is None


def test_a_block_cannot_leave_the_authenticated_role_unnoticed(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    # Outside user_transaction the connection has no privileges at all (fail closed).
    with pool.connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select count(*) from public.profiles")

    with user_transaction(pool, users[0]) as conn:
        assert conn.execute("select count(*) from public.profiles").fetchone() == (1,)


# --- exceptions and rollback --------------------------------------------------------------------


def test_an_exception_rolls_back_and_the_connection_is_reusable_and_clean(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with pytest.raises(BlockError), user_transaction(pool, users[0]) as conn:
        pid = conn.execute("select pg_backend_pid()").fetchone()
        conn.execute("update public.profiles set display_name = 'Temporary'")
        raise BlockError

    assert _display_name(admin, users[0]) is None
    with user_transaction(pool, users[1]) as conn:
        assert conn.execute("select pg_backend_pid()").fetchone() == pid, "same session expected"
        assert conn.execute("select id from public.profiles").fetchall() == [(users[1].id,)]
    with pool.connection() as conn:
        _clean_session(conn)


def test_sql_errors_surface_unchanged_and_roll_back(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), user_transaction(pool, users[0]) as conn:
        conn.execute("update public.profiles set display_name = 'ok'")
        conn.execute("update public.profiles set preferred_language = 'xx'")

    assert _display_name(admin, users[0]) is None


# --- nesting -------------------------------------------------------------------------------------


def test_nesting_is_rejected_for_the_same_and_for_another_user(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with user_transaction(pool, users[0]) as conn:
        for inner in (users[0], users[1]):
            with pytest.raises(NestedUserTransactionError), user_transaction(pool, inner):
                pytest.fail("block must not run")
        # The outer transaction still has its identity and can finish normally.
        assert conn.execute("select auth.uid()").fetchone() == (users[0].id,)
        conn.execute("update public.profiles set display_name = 'Outer'")

    assert _display_name(admin, users[0]) == "Outer"
    with user_transaction(pool, users[1]) as conn:  # the guard was released
        assert conn.execute("select auth.uid()").fetchone() == (users[1].id,)


# --- identity check ------------------------------------------------------------------------------


def _pool_without_reset() -> ConnectionPool:
    """A pool whose reset callback does nothing: simulates a broken or missing pool reset."""
    assert TEST_DATABASE_URL
    assert_local(TEST_DATABASE_URL)
    pool = ConnectionPool(
        TEST_DATABASE_URL,
        min_size=1,
        max_size=1,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": None},
        configure=configure_connection,
        reset=None,
    )
    pool.open(wait=True, timeout=10)
    return pool


def test_a_leaked_legacy_claim_is_caught_even_if_the_pool_reset_fails(
    users: list[AuthenticatedUser],
) -> None:
    """Defense in depth: a session-level request.jwt.claim.sub left behind would be
    preferred by auth.uid() over our claims; the identity check must refuse to proceed."""
    pool = _pool_without_reset()
    try:
        with pool.connection() as conn:
            conn.execute(
                "select set_config('request.jwt.claim.sub', %s, false)", [str(users[1].id)]
            )

        executed = False
        with pytest.raises(UserContextMismatchError), user_transaction(pool, users[0]):
            executed = True
        assert not executed, "service code must never run with a spoofed identity"
    finally:
        pool.close()


def test_role_and_claims_are_local_to_the_transaction_even_without_pool_reset(
    users: list[AuthenticatedUser],
) -> None:
    """The helper itself must not leave anything on the session; the pool reset is only a
    second barrier. Checked on a pool WITHOUT reset, so SET ROLE or set_config(..., false)
    would show up here."""
    pool = _pool_without_reset()
    try:
        with user_transaction(pool, users[0]):
            pass
        with pool.connection() as conn:
            assert conn.execute("select current_user").fetchone() == ("skillloop_api",)
            claims = conn.execute("select current_setting('request.jwt.claims', true)").fetchone()
            assert claims in {(None,), ("",)}
    finally:
        pool.close()


# --- parallel requests ---------------------------------------------------------------------------


def test_parallel_requests_of_different_users_never_see_each_others_context(
    users: list[AuthenticatedUser],
) -> None:
    threads, iterations = 8, 25
    pool = open_api_pool(max_size=3)  # far fewer connections than threads: heavy reuse
    barrier = threading.Barrier(threads)
    served: dict[int, set[str]] = {}  # backend pid -> users served
    lock = threading.Lock()

    def worker(index: int) -> None:
        user = users[index % USER_COUNT]
        barrier.wait(timeout=10)
        for _ in range(iterations):
            with user_transaction(pool, user) as conn:
                pid = conn.execute("select pg_backend_pid()").fetchone()[0]  # type: ignore[index]
                conn.execute("select pg_sleep(0.005)")  # let transactions overlap
                assert conn.execute("select id from public.profiles").fetchall() == [(user.id,)]
                assert conn.execute("select auth.uid()").fetchone() == (user.id,)
                claims = conn.execute("select current_setting('request.jwt.claims')").fetchone()
                assert json.loads(claims[0])["sub"] == str(user.id)  # type: ignore[index]
                with lock:
                    served.setdefault(pid, set()).add(str(user.id))

    try:
        with ThreadPoolExecutor(max_workers=threads) as executor:
            for future in [executor.submit(worker, i) for i in range(threads)]:
                future.result(timeout=60)  # re-raises any assertion from a worker
        with pool.connection() as conn:
            _clean_session(conn)
    finally:
        pool.close()

    assert len(served) <= 3
    assert any(len(who) > 1 for who in served.values()), "no connection was shared between users"


# --- no state leaks between requests -------------------------------------------------------------


def test_session_level_state_set_by_a_block_does_not_reach_the_next_user(
    pool: ConnectionPool, users: list[AuthenticatedUser]
) -> None:
    with user_transaction(pool, users[0]) as conn:
        pid = conn.execute("select pg_backend_pid()").fetchone()
        # Mistakes a careless service could make: session-level (not LOCAL) settings.
        conn.execute("select set_config('request.jwt.claim.sub', %s, false)", [str(users[0].id)])
        conn.execute("select set_config('request.jwt.claims', 'leaked', false)")
        conn.execute("set statement_timeout = '1ms'")

    with user_transaction(pool, users[1]) as conn:
        assert conn.execute("select pg_backend_pid()").fetchone() == pid
        assert conn.execute("select auth.uid()").fetchone() == (users[1].id,)
        assert conn.execute("select id from public.profiles").fetchall() == [(users[1].id,)]
        assert conn.execute("show statement_timeout").fetchone() == ("15s",)


# --- infrastructure failures ----------------------------------------------------------------------


def test_pool_exhaustion_becomes_database_unavailable(users: list[AuthenticatedUser]) -> None:
    pool = open_api_pool(max_size=1, timeout=0.3)
    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with user_transaction(pool, users[0]):
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holding.wait(10)
        with pytest.raises(DatabaseUnavailableError), user_transaction(pool, users[1]):
            pytest.fail("block must not run")
    finally:
        release.set()
        thread.join()
        pool.close()


def test_a_killed_connection_becomes_database_unavailable_and_the_pool_recovers(
    pool: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    with pytest.raises(DatabaseUnavailableError), user_transaction(pool, users[0]) as conn:
        pid = conn.execute("select pg_backend_pid()").fetchone()[0]  # type: ignore[index]
        admin.execute("select pg_terminate_backend(%s)", [pid])
        conn.execute("select 1")

    with user_transaction(pool, users[1]) as conn:
        assert conn.execute("select pg_backend_pid()").fetchone() != (pid,)
        assert conn.execute("select id from public.profiles").fetchall() == [(users[1].id,)]
