"""Integration tests of the pool callbacks against a REAL local PostgreSQL / Supavisor.

Skipped unless TEST_DATABASE_URL is set. It must log in as skillloop_api and point at the
local Supabase stack (see supabase/config.toml); any non-local host makes the tests FAIL
instead of connecting, so these tests can never touch a remote project.
Optional TEST_DATABASE_ADMIN_URL (local postgres login) enables the wrong-role test.
"""

import os
from collections.abc import Iterator
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app.db.pool import ConnectionStateError, configure_connection, create_pool
from tests.test_config import make_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_DATABASE_ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL")

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")


def assert_local(url: str) -> None:
    host = urlsplit(url).hostname
    if host not in LOCAL_HOSTS:
        pytest.fail(f"refusing to run database tests against a non-local host: {host!r}")


@pytest.fixture
def pool() -> Iterator[ConnectionPool]:
    assert TEST_DATABASE_URL
    assert_local(TEST_DATABASE_URL)
    # One connection only, so every checkout reuses the same server session.
    settings = make_settings(database_url=TEST_DATABASE_URL, db_pool_min_size=1, db_pool_max_size=1)
    pool = create_pool(settings)
    pool.open(wait=True, timeout=10)
    try:
        yield pool
    finally:
        pool.close()


def test_connection_is_the_api_role_in_autocommit_with_role_timeouts(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        assert conn.autocommit
        assert conn.execute("select current_user").fetchone() == ("skillloop_api",)
        assert conn.execute("show statement_timeout").fetchone() == ("15s",)
        # Supavisor prefixes application_name ("Supavisor - skillloop-api"); direct keeps ours.
        app_name = conn.execute("show application_name").fetchone()
        assert app_name and app_name[0].endswith("skillloop-api")


def test_a_query_without_set_role_is_denied(pool: ConnectionPool) -> None:
    with pool.connection() as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select count(*) from public.profiles")


def test_session_state_does_not_survive_a_checkout(pool: ConnectionPool) -> None:
    user_a = '{"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}'
    with pool.connection() as conn:
        first_pid = conn.execute("select pg_backend_pid()").fetchone()
        # Everything a buggy request could leave behind on a session:
        conn.execute("set role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, false)", [user_a])
        conn.execute(
            "select set_config('request.jwt.claim.sub', '11111111-1111-1111-1111-111111111111', false)"
        )
        conn.execute("set statement_timeout = '1ms'")
        conn.execute("set application_name = 'tampered'")

    with pool.connection() as conn:
        assert conn.execute("select pg_backend_pid()").fetchone() == first_pid, (
            "not the same session"
        )
        assert conn.execute("select current_user").fetchone() == ("skillloop_api",)
        assert conn.execute("select current_setting('request.jwt.claims', true)").fetchone() in {
            (None,),
            ("",),
        }
        assert conn.execute("select current_setting('request.jwt.claim.sub', true)").fetchone() in {
            (None,),
            ("",),
        }
        # RESET ALL restores the role-level and startup defaults, not the server defaults.
        assert conn.execute("show statement_timeout").fetchone() == ("15s",)
        # Only "not tampered": behind Supavisor RESET ALL returns to the pooler's own value
        # ("Supavisor"), which differs from the one set at connect time. Not security state.
        assert conn.execute("show application_name").fetchone() != ("tampered",)


def test_a_connection_returned_inside_a_transaction_is_rolled_back_and_clean(
    pool: ConnectionPool,
) -> None:
    with pool.connection() as conn:
        first_pid = conn.execute("select pg_backend_pid()").fetchone()
        conn.execute("begin")
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', '{\"sub\": \"x\"}', true)")

    with pool.connection() as conn:
        assert conn.execute("select pg_backend_pid()").fetchone() == first_pid
        assert conn.info.transaction_status.name == "IDLE"
        assert conn.execute("select current_user").fetchone() == ("skillloop_api",)
        assert conn.execute("select current_setting('request.jwt.claims', true)").fetchone() in {
            (None,),
            ("",),
        }


def test_two_users_one_after_another_on_one_connection_see_only_their_own_context(
    pool: ConnectionPool,
) -> None:
    seen = []
    for sub in ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"):
        with pool.connection() as conn, conn.transaction():
            conn.execute("set local role authenticated")
            conn.execute(
                "select set_config('request.jwt.claims', %s, true)", [f'{{"sub": "{sub}"}}']
            )
            row = conn.execute("select current_user, current_setting('request.jwt.claims', true)")
            seen.append((row.fetchone(), conn.execute("select pg_backend_pid()").fetchone()))

    assert seen[0][1] == seen[1][1], "the same server session must have been reused"
    assert "1111" in seen[0][0][1] and "2222" not in seen[0][0][1]
    assert "2222" in seen[1][0][1] and "1111" not in seen[1][0][1]


@pytest.mark.skipif(not TEST_DATABASE_ADMIN_URL, reason="TEST_DATABASE_ADMIN_URL is not set")
def test_configure_rejects_a_connection_logged_in_as_another_role() -> None:
    assert TEST_DATABASE_ADMIN_URL
    assert_local(TEST_DATABASE_ADMIN_URL)
    with psycopg.connect(TEST_DATABASE_ADMIN_URL, autocommit=True) as conn:
        assert conn.execute("select current_user").fetchone() == ("postgres",)
        with pytest.raises(ConnectionStateError):
            configure_connection(conn)
        assert conn.closed
