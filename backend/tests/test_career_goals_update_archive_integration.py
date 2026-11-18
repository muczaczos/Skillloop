"""PATCH /career-goals/{id} and POST /career-goals/{id}/archive end to end:
HTTP -> JWT -> service -> repository -> PostgreSQL with Row Level Security.

Nothing between the HTTP request and the database is replaced: tokens are verified by the real
TokenVerifier against a local JWKS server, the services run real user_transaction()s and RLS
applies. Needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL (see db_integration_helpers); the
throwaway users come from the `users` fixture.

Races are made deterministic like in test_services_concurrency.py: a transaction is held open
(`held_transaction`), the HTTP request is started and seen WAITING on the row lock
(`wait_until_blocked_by`, pg_stat_activity), then the holder commits. No sleeps order anything.
Goals are created through POST /career-goals (the endpoint under the same JWT) unless a test
needs a state the API cannot produce.
"""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from app import main
from app.api.deps import get_db_pool, get_token_verifier
from app.models.auth import AuthenticatedUser
from app.repositories import career_goals
from tests.auth_helpers import (
    JWKSServer,
    SigningKey,
    default_claims,
    make_hs256_token_with_public_key,
    make_token,
    make_unsigned_token,
    make_verifier,
    running_jwks_server,
)
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    open_api_pool,
)
from tests.test_career_goals_api_integration import (
    ACTIVE,
    COLLECTION,
    RESOURCE_KEYS,
    VALID,
    assert_error,
    bearer,
)
from tests.test_services_concurrency import TIMEOUT, held_transaction, wait_until_blocked_by

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

CANARY = "CANARY-6d2f8"


def item(goal_id: Any) -> str:
    return f"{COLLECTION}/{goal_id}"


def archive_url(goal_id: Any) -> str:
    return f"{COLLECTION}/{goal_id}/archive"


@pytest.fixture
def api_pool() -> Iterator[ConnectionPool]:
    pool = open_api_pool(max_size=4, timeout=10)
    try:
        yield pool
    finally:
        pool.close()


@pytest.fixture
def client(jwks_server: JWKSServer, api_pool: ConnectionPool) -> Iterator[TestClient]:
    """The real application with the real verifier (local JWKS) and a real pool."""
    verifier = make_verifier(jwks_server.url)
    main.app.dependency_overrides[get_token_verifier] = lambda: verifier
    main.app.dependency_overrides[get_db_pool] = lambda: api_pool
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_token_verifier, None)
        main.app.dependency_overrides.pop(get_db_pool, None)


def state(admin: psycopg.Connection, goal_id: Any) -> tuple[Any, ...] | None:
    """Everything stored about a goal (None if there is no such row)."""
    return admin.execute(
        "select user_id, target_role, seniority, description, status, created_at, updated_at "
        "from public.career_goals where id = %s",
        [goal_id],
    ).fetchone()


def new_goal(client: TestClient, key: SigningKey, user: AuthenticatedUser, **body: Any) -> dict:
    response = client.post(COLLECTION, json={**VALID, **body}, headers=bearer(key, user))
    assert response.status_code == 201
    return response.json()


def run_while_held(
    pool: ConnectionPool,
    user: AuthenticatedUser,
    admin: psycopg.Connection,
    action: Callable[[psycopg.Connection], Any],
    request: Callable[[TestClient], Any],
) -> Any:
    """Hold a transaction that ran `action`, start `request` on its own thread, wait until the
    database shows it blocked by the holder, then let the holder COMMIT. Returns the response."""
    with held_transaction(pool, user, action) as held, ThreadPoolExecutor(max_workers=1) as pool_:
        future = pool_.submit(lambda: request(TestClient(main.app, raise_server_exceptions=False)))
        wait_until_blocked_by(admin, held.pid)
        assert not future.done(), "the request must wait for the lock of the holder"
        held.release()
        return future.result(timeout=TIMEOUT)


def archive_action(goal_id: Any, user: AuthenticatedUser) -> Callable[[psycopg.Connection], Any]:
    return lambda conn: career_goals.archive_active(conn, user.id, UUID(str(goal_id)))


def update_action(
    goal_id: Any, user: AuthenticatedUser, **changes: Any
) -> Callable[[psycopg.Connection], Any]:
    return lambda conn: career_goals.update_active(conn, user.id, UUID(str(goal_id)), changes)


# --- PATCH: field semantics -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"target_role": "Platform Engineer"}, {"target_role": "Platform Engineer"}),
        ({"seniority": "senior"}, {"seniority": "senior"}),
        ({"description": "A new description"}, {"description": "A new description"}),
        (
            {"target_role": "SRE", "seniority": "mid", "description": "All three"},
            {"target_role": "SRE", "seniority": "mid", "description": "All three"},
        ),
        ({"target_role": "  Trimmed  "}, {"target_role": "Trimmed"}),
    ],
    ids=["role", "seniority", "description", "all-fields", "stripped"],
)
def test_patch_changes_exactly_the_fields_that_were_sent(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    body: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    user = users[0]
    created = new_goal(client, signing_key, user, description="Original")
    before = state(admin, created["id"])
    assert before is not None

    response = client.patch(item(created["id"]), json=body, headers=bearer(signing_key, user))

    assert response.status_code == 200
    result = response.json()
    assert set(result) == RESOURCE_KEYS
    want = {**{k: created[k] for k in ("target_role", "seniority", "description")}, **expected}
    assert {k: result[k] for k in want} == want
    after = state(admin, created["id"])
    assert after is not None
    assert after[:5] == (
        user.id,
        want["target_role"],
        want["seniority"],
        want["description"],
        "active",
    )
    assert after[5] == before[5]  # created_at is never changed
    assert after[6] > before[6]  # updated_at is moved by the trigger
    assert result["created_at"] == created["created_at"] and result["id"] == created["id"]
    assert client.get(ACTIVE, headers=bearer(signing_key, user)).json() == result  # persisted


def test_a_null_or_blank_description_clears_it(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user, description="Original")

    cleared = client.patch(item(created["id"]), json={"description": None}, headers=headers)
    assert cleared.status_code == 200 and cleared.json()["description"] is None
    assert state(admin, created["id"])[3] is None  # type: ignore[index]

    client.patch(item(created["id"]), json={"description": "Again"}, headers=headers)
    blank = client.patch(item(created["id"]), json={"description": "   "}, headers=headers)
    assert blank.status_code == 200 and blank.json()["description"] is None
    assert state(admin, created["id"])[3] is None  # type: ignore[index]
    # the other fields survived both requests
    assert (blank.json()["target_role"], blank.json()["seniority"]) == ("DevOps Engineer", "junior")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"target_role": None},
        {"seniority": None},
        {"target_role": None, "description": "x"},
        {"target_role": ""},
        {"target_role": "   "},
        {"target_role": "r" * 101},
        {"target_role": "a\u0000b"},
        {"seniority": "lead"},
        {"description": "d" * 2001},
        {"description": "a\u0000b"},
        {"description": "x", "id": CANARY},
        {"description": "x", "user_id": CANARY},
        {"description": "x", "status": "archived"},
        {"description": "x", "created_at": CANARY},
        {"description": "x", "updated_at": CANARY},
        {"description": "x", "unknown": CANARY},
    ],
)
def test_invalid_updates_answer_422_and_leave_the_row_untouched(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    body: dict[str, Any],
) -> None:
    user = users[0]
    created = new_goal(client, signing_key, user, description="Original")
    before = state(admin, created["id"])

    response = client.patch(item(created["id"]), json=body, headers=bearer(signing_key, user))

    assert_error(response, 422, "VALIDATION_ERROR")
    assert CANARY not in response.text
    assert state(admin, created["id"]) == before


@pytest.mark.parametrize("content", ["not json", '{"description": ', "[]", '"text"', "42"])
def test_a_body_that_is_not_a_json_object_answers_422_and_changes_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    content: str,
) -> None:
    created = new_goal(client, signing_key, users[0])
    before = state(admin, created["id"])
    headers = {**bearer(signing_key, users[0]), "Content-Type": "application/json"}

    response = client.patch(item(created["id"]), content=content, headers=headers)

    assert_error(response, 422, "VALIDATION_ERROR")
    assert state(admin, created["id"]) == before


@pytest.mark.parametrize(
    ("role", "description"),
    [
        ("R", "D"),
        ("r" * 100, "d" * 2000),
        ("ł" * 100, "ż" * 2000),  # 2 bytes per character: limits count characters, not bytes
        ("工程师" * 33 + "x", "日本語" * 666 + "ab"),
        ("Inżynier DevOps 🚀", "Zażółć gęślą jaźń 🚀"),  # 4-byte characters
    ],
    ids=["minimum", "maximum-ascii", "maximum-2-byte", "maximum-cjk", "emoji"],
)
def test_boundary_and_multibyte_values_round_trip_unchanged(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    role: str,
    description: str,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user)

    patched = client.patch(
        item(created["id"]),
        json={"target_role": role, "seniority": "senior", "description": description},
        headers=headers,
    )
    read = client.get(ACTIVE, headers=headers)

    assert patched.status_code == 200 and read.status_code == 200
    assert (read.json()["target_role"], read.json()["description"]) == (role, description)
    assert state(admin, created["id"])[1:4] == (role, "senior", description)  # type: ignore[index]


@pytest.mark.parametrize("seniority", ["junior", "mid", "senior"])
def test_every_seniority_can_be_set(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser], seniority: str
) -> None:
    created = new_goal(client, signing_key, users[0], seniority="mid")

    response = client.patch(
        item(created["id"]), json={"seniority": seniority}, headers=bearer(signing_key, users[0])
    )

    assert response.status_code == 200 and response.json()["seniority"] == seniority


def test_the_timestamps_in_the_response_are_utc_and_follow_the_row(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    created = new_goal(client, signing_key, users[0])
    headers = bearer(signing_key, users[0])

    patched = client.patch(item(created["id"]), json={"description": "x"}, headers=headers).json()
    archived = client.post(archive_url(created["id"]), headers=headers).json()

    row = state(admin, created["id"])
    assert row is not None
    for body in (created, patched, archived):
        assert body["created_at"].endswith("Z") and body["updated_at"].endswith("Z")
        assert datetime.fromisoformat(body["created_at"]).utcoffset() == timedelta(0)
    assert created["created_at"] == patched["created_at"] == archived["created_at"]
    # the instants only move forward (the strings are all UTC with the same precision rules)
    stamps = [datetime.fromisoformat(b["updated_at"]) for b in (created, patched, archived)]
    assert stamps[0] < stamps[1] < stamps[2]
    assert datetime.fromisoformat(archived["updated_at"]) == row[6]
    assert datetime.fromisoformat(archived["created_at"]) == row[5]


# --- archive ------------------------------------------------------------------------------------


def test_archive_returns_the_archived_goal_and_keeps_the_row(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    created = new_goal(client, signing_key, user, description="Keep me")
    before = state(admin, created["id"])
    assert before is not None

    response = client.post(archive_url(created["id"]), headers=bearer(signing_key, user))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == RESOURCE_KEYS
    assert body["status"] == "archived"
    assert {
        k: body[k] for k in ("id", "target_role", "seniority", "description", "created_at")
    } == {k: created[k] for k in ("id", "target_role", "seniority", "description", "created_at")}
    after = state(admin, created["id"])
    assert after is not None, "an archived goal is never deleted"
    assert after[:4] == before[:4] and after[4] == "archived"
    assert after[5] == before[5] and after[6] > before[6]  # trigger moved updated_at
    assert admin.execute("select count(*) from public.career_goals").fetchone()[0] == 1  # type: ignore[index]


def test_archiving_twice_answers_409_and_changes_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    headers = bearer(signing_key, users[0])
    created = new_goal(client, signing_key, users[0])
    client.post(archive_url(created["id"]), headers=headers)
    before = state(admin, created["id"])

    again = client.post(archive_url(created["id"]), headers=headers)

    error = assert_error(again, 409, "GOAL_ARCHIVED")
    assert "details" not in error
    assert state(admin, created["id"]) == before


def test_patching_an_archived_goal_answers_409_and_changes_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    headers = bearer(signing_key, users[0])
    created = new_goal(client, signing_key, users[0])
    client.post(archive_url(created["id"]), headers=headers)
    before = state(admin, created["id"])

    response = client.patch(item(created["id"]), json={"description": "late"}, headers=headers)

    assert_error(response, 409, "GOAL_ARCHIVED")
    assert state(admin, created["id"]) == before


# --- ownership and isolation --------------------------------------------------------------------


def _attempts(client: TestClient, headers: dict[str, str], goal_id: Any) -> list[Any]:
    """PATCH and archive of `goal_id` as the caller with `headers`."""
    return [
        client.patch(item(goal_id), json={"description": CANARY}, headers=headers),
        client.post(archive_url(goal_id), headers=headers),
    ]


def _shape(response: Any) -> tuple[Any, ...]:
    """Everything of an error response except the per-request id."""
    error = dict(response.json()["error"])
    error.pop("request_id")
    return (response.status_code, error, sorted(response.headers), response.headers["content-type"])


def test_a_missing_goal_and_a_foreign_goal_get_identical_404s(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    a, b = users[0], users[1]
    foreign_active = new_goal(client, signing_key, b)
    foreign_archived = new_goal(client, signing_key, users[2])
    client.post(archive_url(foreign_archived["id"]), headers=bearer(signing_key, users[2]))
    before = [state(admin, g["id"]) for g in (foreign_active, foreign_archived)]
    caller = bearer(signing_key, a)

    missing = _attempts(client, caller, uuid4())
    foreign = _attempts(client, caller, foreign_active["id"])
    foreign_gone = _attempts(client, caller, foreign_archived["id"])  # archived: still a plain 404

    for responses in (missing, foreign, foreign_gone):
        for response in responses:
            assert_error(response, 404, "GOAL_NOT_FOUND")
            assert CANARY not in response.text
    # the API cannot tell "does not exist" from "belongs to someone else" (nor from archived)
    for index in (0, 1):
        assert _shape(missing[index]) == _shape(foreign[index]) == _shape(foreign_gone[index])
    assert [state(admin, g["id"]) for g in (foreign_active, foreign_archived)] == before


def test_user_b_cannot_change_or_archive_the_goal_of_user_a(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    a, b = users[0], users[1]
    goal_a = new_goal(client, signing_key, a, description="Mine")
    goal_b = new_goal(client, signing_key, b, description="Theirs")
    before_a, before_b = state(admin, goal_a["id"]), state(admin, goal_b["id"])

    patched = client.patch(
        item(goal_a["id"]),
        json={"target_role": "Hacked", "description": "Hacked"},
        headers=bearer(signing_key, b),
    )
    archived = client.post(archive_url(goal_a["id"]), headers=bearer(signing_key, b))
    smuggled = client.patch(
        item(goal_a["id"]),
        json={"user_id": str(b.id), "description": "x"},
        headers=bearer(signing_key, b),
    )

    assert_error(patched, 404, "GOAL_NOT_FOUND")
    assert_error(archived, 404, "GOAL_NOT_FOUND")
    assert_error(smuggled, 422, "VALIDATION_ERROR")
    assert state(admin, goal_a["id"]) == before_a
    assert state(admin, goal_b["id"]) == before_b
    # the owner still has a working, unchanged active goal
    assert client.get(ACTIVE, headers=bearer(signing_key, a)).json() == goal_a


def test_the_identity_comes_from_the_token_and_nothing_else(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    a, b = users[0], users[1]
    goal_a = new_goal(client, signing_key, a)
    before = state(admin, goal_a["id"])
    spoof = {"user_id": str(a.id)}

    patched = client.patch(
        item(goal_a["id"]),
        json={"description": "x"},
        params=spoof,
        headers={**bearer(signing_key, b), "X-User-Id": str(a.id)},
    )
    archived = client.post(
        archive_url(goal_a["id"]),
        params=spoof,
        headers={**bearer(signing_key, b), "X-User-Id": str(a.id)},
    )

    assert_error(patched, 404, "GOAL_NOT_FOUND")
    assert_error(archived, 404, "GOAL_NOT_FOUND")
    assert state(admin, goal_a["id"]) == before


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "active", "0" * 35, "0" * 37])
def test_an_invalid_id_answers_422_for_both_endpoints(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    bad_id: str,
) -> None:
    created = new_goal(client, signing_key, users[0])
    before = state(admin, created["id"])
    headers = bearer(signing_key, users[0])

    patched = client.patch(item(bad_id), json={"description": "x"}, headers=headers)
    archived = client.post(archive_url(bad_id), headers=headers)

    for response in (patched, archived):
        error = assert_error(response, 422, "VALIDATION_ERROR")
        assert "goal_id" in {entry["field"] for entry in error["details"]}
    assert state(admin, created["id"]) == before


def test_a_valid_token_of_a_deleted_user_finds_no_goal(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user)
    admin.execute("delete from auth.users where id = %s", [user.id])  # cascades to the goal

    patched = client.patch(item(created["id"]), json={"description": "x"}, headers=headers)
    archived = client.post(archive_url(created["id"]), headers=headers)

    assert_error(patched, 404, "GOAL_NOT_FOUND")
    assert_error(archived, 404, "GOAL_NOT_FOUND")
    assert state(admin, created["id"]) is None


# --- life cycle ---------------------------------------------------------------------------------


def test_the_full_life_cycle_of_a_goal(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)

    created = new_goal(client, signing_key, user, target_role="Junior Dev", description="v1")
    updated = client.patch(
        item(created["id"]), json={"target_role": "Mid Dev", "seniority": "mid"}, headers=headers
    )
    archived = client.post(archive_url(created["id"]), headers=headers)
    old_before = state(admin, created["id"])
    late_patch = client.patch(item(created["id"]), json={"description": "late"}, headers=headers)
    second_archive = client.post(archive_url(created["id"]), headers=headers)
    no_active = client.get(ACTIVE, headers=headers)
    new = client.post(
        COLLECTION, json={"target_role": "Senior Dev", "seniority": "senior"}, headers=headers
    )
    active = client.get(ACTIVE, headers=headers)

    assert updated.status_code == 200 and updated.json()["target_role"] == "Mid Dev"
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert archived.json()["target_role"] == "Mid Dev"
    assert_error(late_patch, 409, "GOAL_ARCHIVED")
    assert_error(second_archive, 409, "GOAL_ARCHIVED")
    assert_error(no_active, 404, "NO_ACTIVE_GOAL")
    assert new.status_code == 201 and new.json()["id"] != created["id"]
    assert active.status_code == 200 and active.json() == new.json()
    # the old goal is still archived and exactly as it was when it was archived
    assert state(admin, created["id"]) == old_before
    assert old_before is not None and old_before[4] == "archived"
    # the new goal can be changed and archived as well, the old one stays untouched
    assert (
        client.patch(item(new.json()["id"]), json={"description": "n"}, headers=headers).status_code
        == 200
    )
    assert client.post(archive_url(new.json()["id"]), headers=headers).status_code == 200
    assert state(admin, created["id"]) == old_before


def test_users_archive_independently_of_each_other(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    a, b = users[0], users[1]
    goal_a = new_goal(client, signing_key, a)
    goal_b = new_goal(client, signing_key, b)

    archived = client.post(archive_url(goal_a["id"]), headers=bearer(signing_key, a))

    assert archived.status_code == 200
    assert state(admin, goal_b["id"])[4] == "active"  # type: ignore[index]
    assert client.get(ACTIVE, headers=bearer(signing_key, b)).json() == goal_b


# --- concurrency (deterministic) ----------------------------------------------------------------


def test_a_patch_waiting_for_an_archive_answers_409(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user, description="Original")

    response = run_while_held(
        api_pool,
        user,
        admin,
        archive_action(created["id"], user),
        lambda http: http.patch(item(created["id"]), json={"description": "late"}, headers=headers),
    )

    assert_error(response, 409, "GOAL_ARCHIVED")
    row = state(admin, created["id"])
    assert row is not None and row[3] == "Original" and row[4] == "archived"


def test_an_archive_waiting_for_an_archive_answers_409(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user)

    response = run_while_held(
        api_pool,
        user,
        admin,
        archive_action(created["id"], user),
        lambda http: http.post(archive_url(created["id"]), headers=headers),
    )

    assert_error(response, 409, "GOAL_ARCHIVED")
    assert state(admin, created["id"])[4] == "archived"  # type: ignore[index]


def test_an_archive_waiting_for_a_patch_archives_the_patched_goal(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user, description="Original")

    response = run_while_held(
        api_pool,
        user,
        admin,
        update_action(created["id"], user, description="Held change"),
        lambda http: http.post(archive_url(created["id"]), headers=headers),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "archived"
    assert response.json()["description"] == "Held change"  # the archive saw the committed patch
    row = state(admin, created["id"])
    assert row is not None and row[3] == "Held change" and row[4] == "archived"


def test_a_patch_waiting_for_a_patch_of_another_field_keeps_both_changes(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user, description="Original")

    response = run_while_held(
        api_pool,
        user,
        admin,
        update_action(created["id"], user, description="Held change"),
        lambda http: http.patch(
            item(created["id"]), json={"target_role": "Waiter"}, headers=headers
        ),
    )

    assert response.status_code == 200
    assert (response.json()["target_role"], response.json()["description"]) == (
        "Waiter",
        "Held change",
    )
    row = state(admin, created["id"])
    assert row is not None and row[1:5] == ("Waiter", "junior", "Held change", "active")


def test_a_create_waiting_for_an_archive_answers_201(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user)

    response = run_while_held(
        api_pool,
        user,
        admin,
        archive_action(created["id"], user),
        lambda http: http.post(
            COLLECTION, json={"target_role": "Next", "seniority": "mid"}, headers=headers
        ),
    )

    assert response.status_code == 201 and response.json()["target_role"] == "Next"
    assert state(admin, created["id"])[4] == "archived"  # type: ignore[index]
    assert client.get(ACTIVE, headers=headers).json() == response.json()
    rows = admin.execute(
        "select status from public.career_goals where user_id = %s order by status", [user.id]
    ).fetchall()
    assert rows == [("active",), ("archived",)]


def test_a_get_does_not_wait_for_an_uncommitted_archive(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user)

    with held_transaction(api_pool, user, archive_action(created["id"], user)):
        response = client.get(ACTIVE, headers=headers)  # reads the committed (active) version

    assert response.status_code == 200 and response.json() == created


@pytest.fixture
def race_client(
    jwks_server: JWKSServer,
) -> Iterator[tuple[ConnectionPool, Callable[[], TestClient]]]:
    pool = open_api_pool(max_size=4, timeout=10)
    verifier = make_verifier(jwks_server.url)
    main.app.dependency_overrides[get_token_verifier] = lambda: verifier
    main.app.dependency_overrides[get_db_pool] = lambda: pool
    try:
        yield pool, lambda: TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_token_verifier, None)
        main.app.dependency_overrides.pop(get_db_pool, None)
        pool.close()


def _fire(
    calls: list[Callable[[TestClient], Any]], make_client: Callable[[], TestClient]
) -> list[Any]:
    """Start every call at the same moment (barrier) and return the responses in order."""
    barrier = threading.Barrier(len(calls))

    def send(call: Callable[[TestClient], Any]) -> Any:
        http = make_client()
        barrier.wait(timeout=10)
        return call(http)

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(send, call) for call in calls]
        return [future.result(timeout=30) for future in futures]


def test_two_simultaneous_archives_give_exactly_one_200_and_one_409(
    race_client: tuple[ConnectionPool, Callable[[], TestClient]],
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    _, make_client = race_client
    user = users[0]
    headers = bearer(signing_key, user)

    for round_number in range(6):
        created = new_goal(make_client(), signing_key, user, target_role=f"Round {round_number}")
        call = lambda http, gid=created["id"]: http.post(archive_url(gid), headers=headers)

        responses = _fire([call, call], make_client)

        assert sorted(r.status_code for r in responses) == [200, 409], round_number
        winner = next(r for r in responses if r.status_code == 200)
        assert winner.json()["status"] == "archived"
        assert_error(next(r for r in responses if r.status_code == 409), 409, "GOAL_ARCHIVED")
        assert state(admin, created["id"])[4] == "archived"  # type: ignore[index]
        assert admin.execute(
            "select count(*) from public.career_goals where user_id = %s and status = 'active'",
            [user.id],
        ).fetchone() == (0,)


def test_simultaneous_patches_of_different_fields_lose_nothing(
    race_client: tuple[ConnectionPool, Callable[[], TestClient]],
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    _, make_client = race_client
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(make_client(), signing_key, user)

    for round_number in range(6):
        role, seniority = f"Role {round_number}", ("junior", "mid", "senior")[round_number % 3]
        description = f"Description {round_number}"
        calls = [
            lambda http, v=role: http.patch(
                item(created["id"]), json={"target_role": v}, headers=headers
            ),
            lambda http, v=seniority: http.patch(
                item(created["id"]), json={"seniority": v}, headers=headers
            ),
            lambda http, v=description: http.patch(
                item(created["id"]), json={"description": v}, headers=headers
            ),
        ]

        responses = _fire(calls, make_client)

        assert [r.status_code for r in responses] == [200, 200, 200], round_number
        row = state(admin, created["id"])
        assert row is not None and row[1:5] == (role, seniority, description, "active")


def test_a_simultaneous_patch_and_archive_never_leave_an_inconsistent_state(
    race_client: tuple[ConnectionPool, Callable[[], TestClient]],
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    _, make_client = race_client
    user = users[0]
    headers = bearer(signing_key, user)
    outcomes = set()

    for round_number in range(8):
        created = new_goal(make_client(), signing_key, user, description="Original")
        patch = lambda http, gid=created["id"], n=round_number: http.patch(
            item(gid), json={"description": f"Patched {n}"}, headers=headers
        )
        archive = lambda http, gid=created["id"]: http.post(archive_url(gid), headers=headers)

        patched, archived = _fire([patch, archive], make_client)

        assert archived.status_code == 200, (round_number, archived.text)  # archive always wins
        assert archived.json()["status"] == "archived"
        row = state(admin, created["id"])
        assert row is not None and row[4] == "archived"
        if patched.status_code == 200:  # the patch was committed first and is in the row
            assert row[3] == f"Patched {round_number}"
            assert archived.json()["description"] == f"Patched {round_number}"
        else:  # the archive was committed first: the patch lost and changed nothing
            assert_error(patched, 409, "GOAL_ARCHIVED")
            assert row[3] == "Original"
        outcomes.add(patched.status_code)
        assert admin.execute(
            "select count(*) from public.career_goals where user_id = %s and status = 'active'",
            [user.id],
        ).fetchone() == (0,)
    assert outcomes <= {200, 409}


# --- authentication with the real verifier --------------------------------------------------------


def _wrong_key_token(key: SigningKey, user: AuthenticatedUser) -> str:
    return make_token(SigningKey(kid=key.kid), default_claims(sub=str(user.id)))  # same kid


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda key, user: None, id="no-token"),
        pytest.param(
            lambda key, user: make_token(
                key, default_claims(sub=str(user.id), exp=int(time.time()) - 3600)
            ),
            id="expired",
        ),
        pytest.param(lambda key, user: _wrong_key_token(key, user), id="signed-by-another-key"),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id)), kid="unknown"),
            id="unknown-key-id",
        ),
        pytest.param(
            lambda key, user: make_unsigned_token(default_claims(sub=str(user.id)), kid=key.kid),
            id="alg-none",
        ),
        pytest.param(
            lambda key, user: make_hs256_token_with_public_key(
                key, default_claims(sub=str(user.id))
            ),
            id="hs256-confusion",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id), aud="other")),
            id="wrong-audience",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id), role="anon")),
            id="role-anon",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub="not-a-uuid")),
            id="subject-not-a-uuid",
        ),
    ],
)
@pytest.mark.parametrize("method", ["patch", "archive"])
def test_invalid_tokens_answer_401_and_touch_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    build: Any,
    method: str,
) -> None:
    user = users[0]
    created = new_goal(client, signing_key, user)
    before = state(admin, created["id"])
    token = build(signing_key, user)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if method == "patch":
        response = client.patch(item(created["id"]), json={"description": CANARY}, headers=headers)
    else:
        response = client.post(archive_url(created["id"]), headers=headers)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert CANARY not in response.text and created["id"] not in response.text
    assert state(admin, created["id"]) == before


def test_responses_and_logs_never_contain_tokens_ids_or_internals(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = users[0]
    created = new_goal(client, signing_key, user)
    client.post(archive_url(created["id"]), headers=bearer(signing_key, user))
    expired = make_token(signing_key, default_claims(sub=str(user.id), exp=int(time.time()) - 3600))
    good = bearer(signing_key, user)
    bad_requests = [
        client.patch(item(created["id"]), json={"description": CANARY}, headers=good),  # 409
        client.post(archive_url(created["id"]), headers=good),  # 409
        client.patch(item(uuid4()), json={"description": CANARY}, headers=good),  # 404
        client.post(archive_url(uuid4()), headers=good),  # 404
        client.patch(item("nope"), json={"description": CANARY}, headers=good),  # 422
        client.patch(
            item(created["id"]),
            json={"description": CANARY},
            headers={"Authorization": f"Bearer {expired}"},  # 401
        ),
    ]
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        client.patch(
            item(created["id"]),
            json={"description": CANARY},
            headers={"Authorization": f"Bearer {expired}"},
        )
        client.post(archive_url(created["id"]), headers=good)

    assert [r.status_code for r in bad_requests] == [409, 409, 404, 404, 422, 401]
    for response in bad_requests:
        text = response.text
        for secret in (
            expired,
            good["Authorization"].split()[1],
            str(user.id),
            "career_goals",
            "one_active_goal_per_user",
            "psycopg",
            "Traceback",
            "SELECT",
            "UPDATE",
        ):
            assert secret not in text, secret
    for fragment in expired.split("."):
        assert fragment not in caplog.text
    assert good["Authorization"].split()[1] not in caplog.text
    assert CANARY not in caplog.text


def test_unreachable_signing_keys_answer_503_not_401(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user)
    before = state(admin, created["id"])
    with running_jwks_server() as server:
        dead_url = server.url  # stopped when the block ends: nothing listens there
    dead = make_verifier(dead_url)
    main.app.dependency_overrides[get_token_verifier] = lambda: dead

    patched = client.patch(item(created["id"]), json={"description": "x"}, headers=headers)
    archived = client.post(archive_url(created["id"]), headers=headers)

    for response in (patched, archived):
        error = assert_error(response, 503, "SERVICE_UNAVAILABLE")
        assert "www-authenticate" not in response.headers
        assert "jwks" not in error["message"].lower()
    assert state(admin, created["id"]) == before


def test_a_closed_pool_answers_503(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    created = new_goal(client, signing_key, user)
    before = state(admin, created["id"])
    closed = open_api_pool(max_size=1, timeout=2)
    closed.close()
    main.app.dependency_overrides[get_db_pool] = lambda: closed

    patched = client.patch(item(created["id"]), json={"description": "x"}, headers=headers)
    archived = client.post(archive_url(created["id"]), headers=headers)

    assert_error(patched, 503, "SERVICE_UNAVAILABLE")
    assert_error(archived, 503, "SERVICE_UNAVAILABLE")
    assert state(admin, created["id"]) == before
