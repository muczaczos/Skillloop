"""Cross-cutting end-to-end tests of the career goal endpoints:
HTTP -> JWT -> endpoint -> service -> repository -> PostgreSQL with Row Level Security.

Nothing between the HTTP request and the database is replaced: tokens are verified by the real
TokenVerifier against a local JWKS server and the services run real user_transaction()s. The
only test-side interference is a wrapper around `career_goals.insert` (see T2) that calls the
real function and merely pauses the first caller inside its still open transaction.

Needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL (see db_integration_helpers). Helpers are
imported from the existing integration modules instead of being written a second time.
"""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from app import main
from app.api.deps import get_db_pool, get_token_verifier
from app.models.auth import AuthenticatedUser
from app.repositories import career_goals
from tests.auth_helpers import JWKSServer, SigningKey, make_verifier
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    open_api_pool,
)
from tests.test_career_goals_api_integration import (
    ACTIVE,
    COLLECTION,
    VALID,
    assert_error,
    bearer,
)
from tests.test_career_goals_update_archive_integration import archive_url, item, state
from tests.test_profile_api_integration import admin_profile
from tests.test_services_concurrency import TIMEOUT, wait_until_blocked_by

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

PROFILE = "/api/v1/profile"


@pytest.fixture
def wire(jwks_server: JWKSServer) -> Iterator[Callable[[int], ConnectionPool]]:
    """`wire(max_size)` opens a real pool and points the real application at it, with the real
    verifier (local JWKS). Pools are closed and the overrides removed afterwards."""
    opened: list[ConnectionPool] = []
    verifier = make_verifier(jwks_server.url)

    def connect(max_size: int) -> ConnectionPool:
        pool = open_api_pool(max_size=max_size, timeout=10)
        opened.append(pool)
        main.app.dependency_overrides[get_token_verifier] = lambda: verifier
        main.app.dependency_overrides[get_db_pool] = lambda: pool
        return pool

    try:
        yield connect
    finally:
        main.app.dependency_overrides.pop(get_token_verifier, None)
        main.app.dependency_overrides.pop(get_db_pool, None)
        for pool in opened:
            pool.close()


def http() -> TestClient:
    return TestClient(main.app, raise_server_exceptions=False)


def server_pid(conn: psycopg.Connection) -> int:
    """PID of the PostgreSQL backend that runs this transaction. Asked from the server on
    purpose: behind Supavisor `conn.info.backend_pid` is the pooler's number, which
    pg_blocking_pids() (used by wait_until_blocked_by) knows nothing about."""
    row = conn.execute("select pg_backend_pid()").fetchone()
    assert row is not None
    return row[0]


def goal_count(
    admin: psycopg.Connection, user: AuthenticatedUser, status: str | None = None
) -> int:
    row = admin.execute(
        "select count(*) from public.career_goals where user_id = %s "
        "and (%s::text is null or status = %s)",
        [user.id, status, status],
    ).fetchone()
    assert row is not None
    return row[0]


# --- T1: the complete life cycle ---------------------------------------------------------------


def test_the_complete_goal_lifecycle_with_the_database_checked_after_every_step(
    wire: Callable[[int], ConnectionPool],
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    wire(4)
    client, user = http(), users[0]
    headers = bearer(signing_key, user)

    # 1. create
    created = client.post(
        COLLECTION,
        json={"target_role": "DevOps Engineer", "seniority": "junior", "description": "Start"},
        headers=headers,
    )
    assert created.status_code == 201
    goal = created.json()
    goal_id = UUID(goal["id"])
    after_create = state(admin, goal_id)
    assert after_create is not None
    assert after_create[:5] == (user.id, "DevOps Engineer", "junior", "Start", "active")
    assert goal_count(admin, user) == 1

    # 2. read: the active goal is the one that was created
    read = client.get(ACTIVE, headers=headers)
    assert read.status_code == 200 and read.json() == goal
    assert state(admin, goal_id) == after_create  # a read changes nothing

    # 3. update
    patched = client.patch(
        item(goal["id"]),
        json={"target_role": "Platform Engineer", "seniority": "mid", "description": None},
        headers=headers,
    )
    assert patched.status_code == 200
    updated = patched.json()
    assert (updated["target_role"], updated["seniority"], updated["description"]) == (
        "Platform Engineer",
        "mid",
        None,
    )
    assert updated["id"] == goal["id"] and updated["status"] == "active"
    after_patch = state(admin, goal_id)
    assert after_patch is not None
    assert after_patch[:5] == (user.id, "Platform Engineer", "mid", None, "active")
    assert after_patch[5] == after_create[5]  # created_at never changes
    assert after_patch[6] > after_create[6]  # updated_at moved
    assert updated["created_at"] == goal["created_at"]
    assert datetime.fromisoformat(updated["updated_at"]) == after_patch[6]
    read = client.get(ACTIVE, headers=headers)
    assert read.status_code == 200 and read.json() == updated

    # 4. archive: the row stays, with everything that was in it
    archived = client.post(archive_url(goal["id"]), headers=headers)
    assert archived.status_code == 200
    final = archived.json()
    assert final["status"] == "archived"
    assert {
        k: final[k] for k in ("id", "target_role", "seniority", "description", "created_at")
    } == {k: updated[k] for k in ("id", "target_role", "seniority", "description", "created_at")}
    after_archive = state(admin, goal_id)
    assert after_archive is not None, "an archived goal is never deleted"
    assert after_archive[:4] == after_patch[:4] and after_archive[4] == "archived"
    assert after_archive[5] == after_patch[5] and after_archive[6] > after_patch[6]
    assert datetime.fromisoformat(final["updated_at"]) == after_archive[6]

    # 5. the archived goal is not active any more
    gone = client.get(ACTIVE, headers=headers)
    assert_error(gone, 404, "NO_ACTIVE_GOAL")
    assert goal_count(admin, user, "active") == 0
    assert goal_count(admin, user) == 1 and goal_count(admin, user, "archived") == 1


# --- T2: two real HTTP requests, the first held inside its transaction -------------------------


def test_two_http_posts_with_the_first_one_held_inside_its_transaction(
    wire: Callable[[int], ConnectionPool],
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deterministic, no sleeps. The wrapper below runs the REAL insert; only the first call is
    then paused (transaction still open, row inserted but not committed). The second request is
    started afterwards and must be seen WAITING on the lock of the first one in pg_stat_activity.
    It can therefore only be refused by the unique index while the first is still uncommitted;
    a 409 that merely came from an already committed row is impossible here."""
    wire(4)  # holder + waiter + spare; the admin connection is separate
    user = users[0]
    headers = bearer(signing_key, user)
    real_insert = career_goals.insert
    guard = threading.Lock()
    calls: list[str] = []
    inserted, release = threading.Event(), threading.Event()
    holder: dict[str, Any] = {}

    def holding_insert(conn: psycopg.Connection, *args: Any, **kwargs: Any) -> Any:
        with guard:
            is_first = not calls
            calls.append("first" if is_first else "later")
        record = real_insert(conn, *args, **kwargs)  # the real INSERT, in the real transaction
        if is_first:
            holder["pid"] = server_pid(conn)
            inserted.set()
            if not release.wait(TIMEOUT):  # bounded: never hangs the suite
                raise RuntimeError("the held request was never released")
        return record

    monkeypatch.setattr(career_goals, "insert", holding_insert)

    def post(role: str) -> Any:
        return http().post(COLLECTION, json={**VALID, "target_role": role}, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            first = executor.submit(post, "First")
            assert inserted.wait(TIMEOUT), "the first request never reached its INSERT"
            second = executor.submit(post, "Second")
            wait_until_blocked_by(admin, holder["pid"])  # fails the test if it never waits
            assert not second.done(), "the second request must wait for the first transaction"
            assert goal_count(admin, user) == 0, "nothing is committed while the first is held"
        finally:
            release.set()  # also when an assertion above failed
        first_response = first.result(timeout=TIMEOUT)
        second_response = second.result(timeout=TIMEOUT)

    assert first_response.status_code == 201
    error = assert_error(second_response, 409, "ACTIVE_GOAL_EXISTS")
    assert "details" not in error
    assert "one_active_goal_per_user" not in second_response.text
    assert calls == ["first", "later"], "both requests must have reached the real INSERT"
    rows = admin.execute(
        "select id, target_role, status from public.career_goals where user_id = %s", [user.id]
    ).fetchall()
    assert rows == [(UUID(first_response.json()["id"]), "First", "active")]
    assert http().get(ACTIVE, headers=headers).json() == first_response.json()


# --- T3: a deleted account with a still valid token ------------------------------------------


def test_a_deleted_account_with_a_still_valid_token(
    wire: Callable[[int], ConnectionPool],
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire(1)  # ONE connection: every request below runs on the same server session
    client = http()
    a, b = users[0], users[1]
    headers_a, headers_b = bearer(signing_key, a), bearer(signing_key, b)  # issued once, reused

    # Which server session ran each INSERT (the real function is still called).
    real_insert = career_goals.insert
    insert_sessions: list[tuple[UUID, int]] = []

    def recording_insert(conn: psycopg.Connection, user_id: UUID, **kwargs: Any) -> Any:
        insert_sessions.append((user_id, server_pid(conn)))
        return real_insert(conn, user_id, **kwargs)

    monkeypatch.setattr(career_goals, "insert", recording_insert)

    # --- before the deletion: user A works normally, user B is the control
    assert client.get(PROFILE, headers=headers_a).status_code == 200
    old = client.post(COLLECTION, json=VALID, headers=headers_a)
    assert old.status_code == 201
    assert client.post(archive_url(old.json()["id"]), headers=headers_a).status_code == 200
    current = client.post(COLLECTION, json={**VALID, "target_role": "Current"}, headers=headers_a)
    assert current.status_code == 201
    assert client.get(ACTIVE, headers=headers_a).json() == current.json()
    old_id, current_id = old.json()["id"], current.json()["id"]
    goal_b = client.post(COLLECTION, json={**VALID, "target_role": "Of B"}, headers=headers_b)
    assert goal_b.status_code == 201
    b_goal_before = state(admin, goal_b.json()["id"])
    b_profile_before = admin_profile(admin, b)
    assert goal_count(admin, a) == 2 and goal_count(admin, a, "archived") == 1

    # --- the account of A is deleted; the token of A is still valid (it is checked offline)
    admin.execute("delete from auth.users where id = %s", [a.id])

    created = client.post(COLLECTION, json=VALID, headers=headers_a)
    read = client.get(ACTIVE, headers=headers_a)
    patched_old = client.patch(item(old_id), json={"description": "x"}, headers=headers_a)
    patched_current = client.patch(item(current_id), json={"description": "x"}, headers=headers_a)
    archived_old = client.post(archive_url(old_id), headers=headers_a)
    archived_current = client.post(archive_url(current_id), headers=headers_a)
    profile_read = client.get(PROFILE, headers=headers_a)
    profile_patch = client.patch(PROFILE, json={"display_name": "Ghost"}, headers=headers_a)

    # the agreed matrix: 401 only where the database itself rejects the missing user (FK)
    assert_error(created, 401, "UNAUTHORIZED")
    assert created.headers["www-authenticate"] == "Bearer"
    assert_error(read, 404, "NO_ACTIVE_GOAL")
    for response in (patched_old, patched_current, archived_old, archived_current):
        assert_error(response, 404, "GOAL_NOT_FOUND")
    for response in (profile_read, profile_patch):
        assert_error(response, 404, "PROFILE_NOT_FOUND")
        assert "www-authenticate" not in response.headers
    for response in (created, read, patched_old, archived_old, profile_read):
        for secret in (str(a.id), old_id, current_id, "career_goals", "auth.users", "fkey"):
            assert secret not in response.text, secret

    # --- nothing of A is left, nothing was created, nothing points at a missing account
    assert admin_profile(admin, a) is None
    assert goal_count(admin, a) == 0
    assert state(admin, old_id) is None and state(admin, current_id) is None
    orphans = admin.execute(
        "select (select count(*) from public.career_goals g "
        "         where not exists (select 1 from auth.users u where u.id = g.user_id)), "
        "       (select count(*) from public.profiles p "
        "         where not exists (select 1 from auth.users u where u.id = p.id))"
    ).fetchone()
    assert orphans == (0, 0)

    # --- the data of B is untouched, and the pool still works for B after A's FK error
    assert state(admin, goal_b.json()["id"]) == b_goal_before
    assert admin_profile(admin, b) == b_profile_before
    assert client.get(ACTIVE, headers=headers_b).json() == goal_b.json()
    assert (
        client.patch(
            item(goal_b.json()["id"]), json={"description": "B"}, headers=headers_b
        ).status_code
        == 200
    )
    assert client.get(PROFILE, headers=headers_b).status_code == 200
    # B can still create after archiving: the connection is not left in a failed transaction
    assert client.post(archive_url(goal_b.json()["id"]), headers=headers_b).status_code == 200
    again = client.post(COLLECTION, json={**VALID, "target_role": "B again"}, headers=headers_b)
    assert again.status_code == 201

    # the SAME server session handled A's failing INSERT and B's later INSERTs
    sessions = dict.fromkeys(pid for _, pid in insert_sessions)
    assert len(sessions) == 1, f"the single pooled connection was replaced: {insert_sessions}"
    failing = [entry for entry in insert_sessions if entry[0] == a.id][-1]
    later_b = [entry for entry in insert_sessions if entry[0] == b.id][-1]
    assert failing[1] == later_b[1]
