"""POST /career-goals and GET /career-goals/active end to end: HTTP -> JWT -> service ->
repository -> PostgreSQL with Row Level Security.

Nothing between the HTTP request and the database is replaced: the access token is verified
by the real TokenVerifier against a local JWKS server, the services run real
user_transaction()s and RLS applies. Needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL
(see db_integration_helpers); the throwaway users come from the `users` fixture. Goals are
archived through the admin connection because the archive endpoint does not exist yet.
"""

import logging
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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
from tests.test_services_concurrency import TIMEOUT, held_transaction, wait_until_blocked_by

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

COLLECTION = "/api/v1/career-goals"
ACTIVE = "/api/v1/career-goals/active"
VALID = {"target_role": "DevOps Engineer", "seniority": "junior", "description": "Goal"}
CANARY = "CANARY-c41e7"
RESOURCE_KEYS = {
    "id",
    "target_role",
    "seniority",
    "description",
    "status",
    "created_at",
    "updated_at",
}


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


def bearer(key: SigningKey, user: AuthenticatedUser, **claims: Any) -> dict[str, str]:
    token = make_token(key, default_claims(sub=str(user.id), **claims))
    return {"Authorization": f"Bearer {token}"}


def goal_rows(admin: psycopg.Connection, user: AuthenticatedUser) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select id, user_id, target_role, seniority, description, status "
        "from public.career_goals where user_id = %s order by created_at, id",
        [user.id],
    ).fetchall()


def archive_all(admin: psycopg.Connection, user: AuthenticatedUser) -> None:
    admin.execute(
        "update public.career_goals set status = 'archived' where user_id = %s", [user.id]
    )


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    UUID(body["error"]["request_id"])
    return body["error"]


# --- POST /career-goals -----------------------------------------------------------------------


def test_post_creates_the_goal_for_the_user_of_the_token(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]

    response = client.post(COLLECTION, json=VALID, headers=bearer(signing_key, user))

    assert response.status_code == 201
    body = response.json()
    assert set(body) == RESOURCE_KEYS
    assert (body["target_role"], body["seniority"], body["description"]) == (
        "DevOps Engineer",
        "junior",
        "Goal",
    )
    assert body["status"] == "active"
    assert "location" not in response.headers
    assert goal_rows(admin, user) == [
        (UUID(body["id"]), user.id, "DevOps Engineer", "junior", "Goal", "active")
    ]


def test_the_created_goal_is_returned_by_get_active(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    headers = bearer(signing_key, users[0])

    created = client.post(COLLECTION, json=VALID, headers=headers)
    read = client.get(ACTIVE, headers=headers)

    assert read.status_code == 200
    assert read.json() == created.json()


def test_a_goal_without_a_description_is_stored_with_null(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    body = {"target_role": "SRE", "seniority": "senior", "description": "   "}

    response = client.post(COLLECTION, json=body, headers=bearer(signing_key, users[0]))

    assert response.status_code == 201 and response.json()["description"] is None
    assert goal_rows(admin, users[0])[0][4] is None


def test_the_identity_comes_from_the_token_and_nothing_else(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    a, b = users[0], users[1]

    smuggled = client.post(
        COLLECTION,
        json={**VALID, "user_id": str(b.id)},
        params={"user_id": str(b.id)},
        headers={**bearer(signing_key, a), "X-User-Id": str(b.id)},
    )
    created = client.post(
        COLLECTION,
        json=VALID,
        params={"user_id": str(b.id)},
        headers={**bearer(signing_key, a), "X-User-Id": str(b.id)},
    )

    assert_error(smuggled, 422, "VALIDATION_ERROR")
    assert created.status_code == 201
    assert [row[1] for row in goal_rows(admin, a)] == [a.id]
    assert goal_rows(admin, b) == []


def test_a_second_active_goal_answers_409_and_keeps_the_first(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    headers = bearer(signing_key, users[0])
    first = client.post(COLLECTION, json=VALID, headers=headers)

    second = client.post(
        COLLECTION, json={**VALID, "target_role": f"Other {CANARY}"}, headers=headers
    )

    error = assert_error(second, 409, "ACTIVE_GOAL_EXISTS")
    assert "details" not in error
    assert CANARY not in second.text and "one_active_goal_per_user" not in second.text
    assert [str(row[0]) for row in goal_rows(admin, users[0])] == [first.json()["id"]]
    assert client.get(ACTIVE, headers=headers).json() == first.json()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"target_role": "SRE"},
        {"seniority": "junior"},
        {"target_role": "", "seniority": "junior"},
        {"target_role": "   ", "seniority": "junior"},
        {"target_role": "r" * 101, "seniority": "junior"},
        {"target_role": "SRE", "seniority": "lead"},
        {"target_role": "SRE", "seniority": None},
        {"target_role": "SRE", "seniority": "junior", "description": "d" * 2001},
        {"target_role": "SRE", "seniority": "junior", "description": "a\u0000b"},
        {"target_role": "a\u0000b", "seniority": "junior"},
        {"target_role": "SRE", "seniority": "junior", "status": "archived"},
        {"target_role": "SRE", "seniority": "junior", "id": CANARY},
        {"target_role": "SRE", "seniority": "junior", "user_id": CANARY},
    ],
)
def test_invalid_input_answers_422_and_creates_no_row(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    body: dict[str, Any],
) -> None:
    response = client.post(COLLECTION, json=body, headers=bearer(signing_key, users[0]))

    assert_error(response, 422, "VALIDATION_ERROR")
    assert CANARY not in response.text
    assert goal_rows(admin, users[0]) == []


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
    headers = bearer(signing_key, users[0])
    body = {"target_role": role, "seniority": "mid", "description": description}

    created = client.post(COLLECTION, json=body, headers=headers)
    read = client.get(ACTIVE, headers=headers)

    assert created.status_code == 201 and read.status_code == 200
    assert (read.json()["target_role"], read.json()["description"]) == (role, description)
    assert goal_rows(admin, users[0])[0][2:5] == (role, "mid", description)


@pytest.mark.parametrize("seniority", ["junior", "mid", "senior"])
def test_every_seniority_is_stored(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser], seniority: str
) -> None:
    body = {"target_role": "SRE", "seniority": seniority}

    response = client.post(COLLECTION, json=body, headers=bearer(signing_key, users[0]))

    assert response.status_code == 201 and response.json()["seniority"] == seniority


def test_the_timestamps_are_utc_and_consistent(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    headers = bearer(signing_key, users[0])
    before = datetime.now().astimezone() - timedelta(minutes=5)

    body = client.post(COLLECTION, json=VALID, headers=headers).json()

    for field in ("created_at", "updated_at"):
        assert body[field].endswith("Z") and "+" not in body[field]
    created = datetime.fromisoformat(body["created_at"])
    updated = datetime.fromisoformat(body["updated_at"])
    assert updated >= created > before
    assert created.utcoffset() == timedelta(0)
    # Whatever time zone the session of the database uses, the API answers the same instant.
    stored = admin.execute(
        "select created_at from public.career_goals where id = %s", [body["id"]]
    ).fetchone()
    assert stored is not None and stored[0] == created


# --- GET /career-goals/active -----------------------------------------------------------------


def test_get_without_a_goal_answers_404(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    response = client.get(ACTIVE, headers=bearer(signing_key, users[0]))

    error = assert_error(response, 404, "NO_ACTIVE_GOAL")
    assert "details" not in error


def test_an_archived_goal_alone_means_404(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    headers = bearer(signing_key, users[0])
    client.post(COLLECTION, json=VALID, headers=headers)
    archive_all(admin, users[0])

    assert_error(client.get(ACTIVE, headers=headers), 404, "NO_ACTIVE_GOAL")


def test_user_b_does_not_see_the_goal_of_user_a(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    a, b = users[0], users[1]
    created = client.post(COLLECTION, json=VALID, headers=bearer(signing_key, a))

    seen_by_b = client.get(ACTIVE, params={"user_id": str(a.id)}, headers=bearer(signing_key, b))

    assert created.status_code == 201
    assert_error(seen_by_b, 404, "NO_ACTIVE_GOAL")
    assert CANARY not in seen_by_b.text and created.json()["id"] not in seen_by_b.text


# --- lifecycle and isolation ------------------------------------------------------------------


def test_after_archiving_the_user_can_create_a_new_active_goal(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    old = client.post(COLLECTION, json=VALID, headers=headers).json()
    archive_all(admin, user)

    new = client.post(
        COLLECTION,
        json={"target_role": "Platform Engineer", "seniority": "senior"},
        headers=headers,
    )
    read = client.get(ACTIVE, headers=headers)

    assert new.status_code == 201 and new.json()["id"] != old["id"]
    assert read.status_code == 200 and read.json() == new.json()
    assert read.json()["target_role"] == "Platform Engineer"
    assert sorted(row[5] for row in goal_rows(admin, user)) == ["active", "archived"]
    assert client.post(COLLECTION, json=VALID, headers=headers).status_code == 409


def test_every_user_can_have_an_own_active_goal(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    created = {}
    for number, user in enumerate(users):
        body = {"target_role": f"Role {number}", "seniority": "mid"}
        response = client.post(COLLECTION, json=body, headers=bearer(signing_key, user))
        assert response.status_code == 201
        created[user.id] = response.json()

    for number, user in enumerate(users):
        read = client.get(ACTIVE, headers=bearer(signing_key, user)).json()
        assert read == created[user.id] and read["target_role"] == f"Role {number}"
        assert [row[1] for row in goal_rows(admin, user)] == [user.id]


def test_parallel_requests_of_different_users_never_see_each_other(
    signing_key: SigningKey, users: list[AuthenticatedUser], jwks_server: JWKSServer
) -> None:
    pool = open_api_pool(max_size=3, timeout=10)  # fewer connections than users
    verifier = make_verifier(jwks_server.url)
    main.app.dependency_overrides[get_token_verifier] = lambda: verifier
    main.app.dependency_overrides[get_db_pool] = lambda: pool
    barrier = threading.Barrier(len(users))

    def worker(user: AuthenticatedUser) -> None:
        http = TestClient(main.app, raise_server_exceptions=False)
        headers = bearer(signing_key, user)
        barrier.wait(timeout=10)
        created = http.post(
            COLLECTION, json={"target_role": f"Role {user.id}", "seniority": "mid"}, headers=headers
        )
        assert created.status_code == 201
        for _ in range(10):
            read = http.get(ACTIVE, headers=headers)
            assert read.status_code == 200
            assert read.json() == created.json()

    try:
        with ThreadPoolExecutor(max_workers=len(users)) as executor:
            for future in [executor.submit(worker, user) for user in users]:
                future.result(timeout=60)
    finally:
        main.app.dependency_overrides.pop(get_token_verifier, None)
        main.app.dependency_overrides.pop(get_db_pool, None)
        pool.close()


# --- concurrency ------------------------------------------------------------------------------


def test_a_create_that_waits_for_an_open_create_answers_409(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """Deterministic: a transaction that already inserted an active goal stays open, the HTTP
    request starts and is seen WAITING on its lock (pg_stat_activity), then the holder commits.
    The request must lose to the constraint: 409, never 201 and never 500."""
    user = users[0]
    headers = bearer(signing_key, user)

    with (
        held_transaction(
            api_pool,
            user,
            lambda conn: career_goals.insert(
                conn, user.id, target_role="Held", seniority="mid", description=None
            ),
        ) as held,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(
            lambda: TestClient(main.app, raise_server_exceptions=False).post(
                COLLECTION, json=VALID, headers=headers
            )
        )
        wait_until_blocked_by(admin, held.pid)
        assert not future.done()
        held.release()
        response = future.result(timeout=TIMEOUT)

    assert_error(response, 409, "ACTIVE_GOAL_EXISTS")
    rows = goal_rows(admin, user)
    assert [(row[2], row[5]) for row in rows] == [("Held", "active")]


def test_a_get_does_not_wait_for_an_uncommitted_create(
    client: TestClient,
    api_pool: ConnectionPool,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)

    with held_transaction(
        api_pool,
        user,
        lambda conn: career_goals.insert(
            conn, user.id, target_role="Held", seniority="mid", description=None
        ),
    ):
        response = client.get(ACTIVE, headers=headers)  # reads committed data only

    assert_error(response, 404, "NO_ACTIVE_GOAL")


def test_two_simultaneous_posts_give_exactly_one_201_and_one_409(
    jwks_server: JWKSServer,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    pool = open_api_pool(max_size=4, timeout=10)
    verifier = make_verifier(jwks_server.url)
    main.app.dependency_overrides[get_token_verifier] = lambda: verifier
    main.app.dependency_overrides[get_db_pool] = lambda: pool
    user = users[0]
    headers = bearer(signing_key, user)

    try:
        for round_number in range(6):
            archive_all(admin, user)
            barrier = threading.Barrier(2)

            def send(number: int, barrier: threading.Barrier = barrier) -> Any:
                http = TestClient(main.app, raise_server_exceptions=False)
                barrier.wait(timeout=10)
                body = {"target_role": f"Racer {number}", "seniority": "mid"}
                return http.post(COLLECTION, json=body, headers=headers)

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(send, number) for number in (1, 2)]
                responses = [future.result(timeout=30) for future in futures]

            assert sorted(r.status_code for r in responses) == [201, 409], round_number
            winner = next(r for r in responses if r.status_code == 201).json()
            loser = next(r for r in responses if r.status_code == 409)
            assert_error(loser, 409, "ACTIVE_GOAL_EXISTS")
            active = [row for row in goal_rows(admin, user) if row[5] == "active"]
            assert [str(row[0]) for row in active] == [winner["id"]]
    finally:
        main.app.dependency_overrides.pop(get_token_verifier, None)
        main.app.dependency_overrides.pop(get_db_pool, None)
        pool.close()


# --- authentication with the real verifier ------------------------------------------------------


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
@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [("post", COLLECTION, {"json": VALID}), ("get", ACTIVE, {})],
    ids=["POST", "GET"],
)
def test_invalid_tokens_answer_401_and_touch_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    build: Any,
    method: str,
    url: str,
    kwargs: dict[str, Any],
) -> None:
    user = users[0]
    token = build(signing_key, user)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    response = getattr(client, method)(url, headers=headers, **kwargs)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert goal_rows(admin, user) == []


def test_a_rejected_token_is_not_written_to_the_logs_or_the_response(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = make_token(
        signing_key, default_claims(sub=str(users[0].id), exp=int(time.time()) - 3600)
    )

    with caplog.at_level(logging.DEBUG):
        response = client.post(COLLECTION, json=VALID, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    for fragment in token.split("."):
        assert fragment not in response.text and fragment not in caplog.text


def test_unreachable_signing_keys_answer_503_not_401(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    with running_jwks_server() as server:
        dead_url = server.url  # stopped when the block ends: nothing listens there
    dead = make_verifier(dead_url)
    main.app.dependency_overrides[get_token_verifier] = lambda: dead
    headers = bearer(signing_key, users[0])

    created = client.post(COLLECTION, json=VALID, headers=headers)
    read = client.get(ACTIVE, headers=headers)

    for response in (created, read):
        error = assert_error(response, 503, "SERVICE_UNAVAILABLE")
        assert "www-authenticate" not in response.headers
        assert "jwks" not in error["message"].lower()
    assert goal_rows(admin, users[0]) == []


# --- infrastructure and edge cases ------------------------------------------------------------


def test_a_valid_token_of_a_deleted_user_answers_401_on_post(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    admin.execute("delete from auth.users where id = %s", [user.id])

    response = client.post(COLLECTION, json=VALID, headers=headers)

    assert_error(response, 401, "UNAUTHORIZED")  # career_goals_user_id_fkey
    assert response.headers["www-authenticate"] == "Bearer"
    assert "career_goals" not in response.text and str(user.id) not in response.text
    assert goal_rows(admin, user) == []


def test_a_valid_token_of_a_deleted_user_finds_no_goal_on_get(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    client.post(COLLECTION, json=VALID, headers=headers)
    admin.execute("delete from auth.users where id = %s", [user.id])  # cascades to the goal

    assert_error(client.get(ACTIVE, headers=headers), 404, "NO_ACTIVE_GOAL")


def test_a_closed_pool_answers_503(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    closed = open_api_pool(max_size=1, timeout=2)
    closed.close()
    main.app.dependency_overrides[get_db_pool] = lambda: closed
    headers = bearer(signing_key, users[0])

    created = client.post(COLLECTION, json=VALID, headers=headers)
    read = client.get(ACTIVE, headers=headers)

    assert_error(created, 503, "SERVICE_UNAVAILABLE")
    assert_error(read, 503, "SERVICE_UNAVAILABLE")
    assert goal_rows(admin, users[0]) == []
