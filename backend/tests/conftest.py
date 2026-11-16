import os
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest

# Deterministic settings for tests. Must be set before `app` is imported,
# because Settings() is instantiated at import time and SUPABASE_URL is required.
os.environ["SUPABASE_URL"] = "https://test-project.supabase.co"
os.environ["SUPABASE_JWT_AUDIENCE"] = "authenticated"
# Dummy value, never connected to: unit tests replace the pool. It is assigned (not
# setdefault) so that a real DATABASE_URL from the shell or a .env file can never be used.
os.environ["DATABASE_URL"] = "postgresql://skillloop_api.test:test-password@localhost:5432/postgres"

import psycopg
from psycopg_pool import ConnectionPool

from app.models.auth import AuthenticatedUser
from tests.auth_helpers import (
    JWKSServer,
    SigningKey,
    running_jwks_server,
)
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    USER_COUNT,
    assert_local,
    open_api_pool,
)


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey(kid="test-key-1")


@pytest.fixture
def jwks_server(signing_key: SigningKey) -> Iterator[JWKSServer]:
    with running_jwks_server() as server:
        server.publish(signing_key)
        yield server


class FakeClock:
    """Controllable replacement for time.monotonic (used for cache and cooldown timing)."""

    def __init__(self) -> None:
        self.now = time.monotonic()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Freeze time.monotonic so tests can move past cache lifespan / cooldown instantly."""
    fake = FakeClock()
    monkeypatch.setattr(time, "monotonic", fake)
    return fake


# --- fixtures of the integration tests (need a local database, see db_integration_helpers) ---


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    """Local `postgres` login, used only to prepare and inspect data."""
    assert TEST_DATABASE_ADMIN_URL
    assert_local(TEST_DATABASE_ADMIN_URL)
    with psycopg.connect(TEST_DATABASE_ADMIN_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture
def users(admin: psycopg.Connection) -> Iterator[list[AuthenticatedUser]]:
    """Throwaway users; the trigger gives each a profile. Deleted (cascading) afterwards."""
    created = [AuthenticatedUser(id=uuid4()) for _ in range(USER_COUNT)]
    for user in created:
        admin.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            [user.id, f"{user.id}@session-test.example"],
        )
    try:
        yield created
    finally:
        admin.execute("delete from auth.users where id = any(%s)", [[u.id for u in created]])


@pytest.fixture
def pool() -> Iterator[ConnectionPool]:
    """Application pool with ONE connection, so every checkout reuses the same session."""
    pool = open_api_pool(max_size=1)
    try:
        yield pool
    finally:
        pool.close()
