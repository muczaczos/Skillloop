"""Shared pieces of the integration tests that need a REAL local PostgreSQL / Supavisor.

Two environment variables, both of which must point at the local Supabase stack:
- TEST_DATABASE_URL: login as skillloop_api (what the application uses);
- TEST_DATABASE_ADMIN_URL: local `postgres` login, used only to create and delete test users.

Any host that is not local makes the tests FAIL instead of connecting, for BOTH variables,
so these tests can never touch a remote project.
"""

import os
from urllib.parse import urlsplit

import pytest
from psycopg_pool import ConnectionPool

from app.db.pool import create_pool
from tests.test_config import make_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_DATABASE_ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL")

# Throwaway users created by the `users` fixture (see conftest.py).
USER_COUNT = 4


def assert_local(url: str) -> None:
    host = urlsplit(url).hostname
    if host not in LOCAL_HOSTS:
        pytest.fail(f"refusing to run database tests against a non-local host: {host!r}")


def open_api_pool(*, max_size: int = 1, timeout: float = 5) -> ConnectionPool:
    """Pool for the application login. max_size=1 makes every checkout reuse one session."""
    assert TEST_DATABASE_URL
    assert_local(TEST_DATABASE_URL)
    settings = make_settings(
        database_url=TEST_DATABASE_URL,
        db_pool_min_size=1,
        db_pool_max_size=max_size,
        db_pool_timeout_seconds=timeout,
    )
    pool = create_pool(settings)
    pool.open(wait=True, timeout=10)
    return pool
