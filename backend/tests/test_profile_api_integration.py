"""GET / PATCH /api/v1/profile end to end: HTTP -> JWT -> service -> repository -> PostgreSQL.

Nothing between the HTTP request and the database is replaced: the access token is verified
by the real TokenVerifier against a local JWKS server, the service runs a real
user_transaction() and Row Level Security applies. Needs TEST_DATABASE_URL and
TEST_DATABASE_ADMIN_URL (see db_integration_helpers); the throwaway users come from the
`users` fixture (conftest.py).
"""

import logging
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from app import main
from app.api.deps import get_db_pool, get_token_verifier
from app.models.auth import AuthenticatedUser
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

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

URL = "/api/v1/profile"
CANARY = "CANARY-90b4e"


@pytest.fixture
def api_pool() -> Iterator[ConnectionPool]:
    pool = open_api_pool(max_size=3, timeout=10)
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


def admin_profile(admin: psycopg.Connection, user: AuthenticatedUser) -> tuple[Any, ...] | None:
    cur = admin.execute(
        "select display_name, preferred_language, created_at, updated_at "
        "from public.profiles where id = %s",
        [user.id],
    )
    return cur.fetchone()


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    UUID(body["error"]["request_id"])
    return body["error"]


# --- reading ------------------------------------------------------------------------------


def test_a_new_user_reads_the_profile_created_at_registration(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    user = users[0]

    response = client.get(URL, headers=bearer(signing_key, user))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "display_name", "preferred_language", "created_at", "updated_at"}
    assert body["id"] == str(user.id)
    assert (body["display_name"], body["preferred_language"]) == (None, "en")
    assert body["created_at"].endswith("Z") and body["updated_at"].endswith("Z")


def test_the_identity_comes_from_the_token_and_nothing_else(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    a, b = users[0], users[1]

    response = client.get(
        URL,
        params={"user_id": str(b.id), "id": str(b.id)},
        headers={**bearer(signing_key, a), "X-User-Id": str(b.id)},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(a.id)


# --- updating -----------------------------------------------------------------------------


def test_patch_updates_the_profile_and_the_change_persists(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)

    patched = client.patch(
        URL, json={"display_name": "Ann", "preferred_language": "pl"}, headers=headers
    )
    read_back = client.get(URL, headers=headers)

    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Ann"
    assert patched.json()["preferred_language"] == "pl"
    assert read_back.json() == patched.json()
    row = admin_profile(admin, user)
    assert row is not None and row[:2] == ("Ann", "pl")


def test_patch_changes_only_what_was_sent_and_null_clears_the_display_name(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    headers = bearer(signing_key, users[0])

    named = client.patch(URL, json={"display_name": "  Ann  "}, headers=headers).json()
    polish = client.patch(URL, json={"preferred_language": "pl"}, headers=headers).json()
    cleared = client.patch(URL, json={"display_name": None}, headers=headers).json()

    assert (named["display_name"], named["preferred_language"]) == ("Ann", "en")  # stripped
    assert (polish["display_name"], polish["preferred_language"]) == ("Ann", "pl")  # name kept
    assert (cleared["display_name"], cleared["preferred_language"]) == (None, "pl")  # lang kept


def test_the_timestamps_behave(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    headers = bearer(signing_key, users[0])
    before = client.get(URL, headers=headers).json()

    after = client.patch(URL, json={"display_name": "Ann"}, headers=headers).json()

    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]  # ISO 8601 UTC strings sort as time


@pytest.mark.parametrize("name", ["x", "x" * 100, "😀" * 100])
def test_boundary_display_names_are_accepted(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser], name: str
) -> None:
    response = client.patch(URL, json={"display_name": name}, headers=bearer(signing_key, users[0]))

    assert response.status_code == 200
    assert response.json()["display_name"] == name


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"display_name": ""},
        {"display_name": "x" * 101},
        {"display_name": "😀" * 101},
        {"display_name": "a\u0000b"},
        {"preferred_language": "de"},
        {"preferred_language": None},
        {"display_name": "Changed", "preferred_language": "xx"},
        {"user_id": CANARY},
        {"display_name": "Changed", "created_at": CANARY},
    ],
)
def test_invalid_updates_answer_422_and_leave_the_database_untouched(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    body: dict[str, Any],
) -> None:
    user = users[0]
    before = admin_profile(admin, user)

    response = client.patch(URL, json=body, headers=bearer(signing_key, user))

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"]
    assert CANARY not in response.text
    assert admin_profile(admin, user) == before


# --- isolation of users -------------------------------------------------------------------


def test_one_user_cannot_read_or_change_the_profile_of_another(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    a, b = users[0], users[1]
    client.patch(URL, json={"display_name": "Bea"}, headers=bearer(signing_key, b))
    b_before = admin_profile(admin, b)

    changed = client.patch(URL, json={"display_name": "Ann"}, headers=bearer(signing_key, a))
    smuggled = client.patch(
        URL,
        json={"user_id": str(b.id), "display_name": "Hacked"},
        headers=bearer(signing_key, a),
    )
    read_a = client.get(URL, headers=bearer(signing_key, a)).json()
    read_b = client.get(URL, headers=bearer(signing_key, b)).json()

    assert changed.status_code == 200 and changed.json()["id"] == str(a.id)
    assert_error(smuggled, 422, "VALIDATION_ERROR")  # `user_id` is not an accepted field
    assert (read_a["id"], read_a["display_name"]) == (str(a.id), "Ann")
    assert (read_b["id"], read_b["display_name"]) == (str(b.id), "Bea")
    assert admin_profile(admin, b) == b_before


def test_parallel_requests_of_different_users_never_see_each_other(
    signing_key: SigningKey, users: list[AuthenticatedUser], jwks_server: JWKSServer
) -> None:
    pool = open_api_pool(max_size=3, timeout=10)  # fewer connections than users x threads
    verifier = make_verifier(jwks_server.url)
    main.app.dependency_overrides[get_token_verifier] = lambda: verifier
    main.app.dependency_overrides[get_db_pool] = lambda: pool
    barrier = threading.Barrier(len(users))

    def worker(user: AuthenticatedUser) -> None:
        http = TestClient(main.app, raise_server_exceptions=False)
        headers = bearer(signing_key, user)
        barrier.wait(timeout=10)
        for round_number in range(10):
            name = f"{user.id}-{round_number}"
            patched = http.patch(URL, json={"display_name": name}, headers=headers)
            read = http.get(URL, headers=headers)
            assert patched.status_code == 200 and read.status_code == 200
            assert patched.json()["id"] == read.json()["id"] == str(user.id)
            assert read.json()["display_name"] == name

    try:
        with ThreadPoolExecutor(max_workers=len(users)) as executor:
            for future in [executor.submit(worker, user) for user in users]:
                future.result(timeout=60)
    finally:
        main.app.dependency_overrides.pop(get_token_verifier, None)
        main.app.dependency_overrides.pop(get_db_pool, None)
        pool.close()


def test_parallel_updates_of_different_fields_lose_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)

    for round_number in range(8):
        language = "pl" if round_number % 2 == 0 else "en"
        name = f"Round {round_number}"
        barrier = threading.Barrier(2)

        def send(body: dict[str, Any], barrier: threading.Barrier = barrier) -> int:
            http = TestClient(main.app, raise_server_exceptions=False)
            barrier.wait(timeout=10)
            return http.patch(URL, json=body, headers=headers).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(send, {"display_name": name}),
                executor.submit(send, {"preferred_language": language}),
            ]
            assert [f.result(timeout=30) for f in futures] == [200, 200]

        row = admin_profile(admin, user)
        assert row is not None and row[:2] == (name, language)


# --- authentication with the real verifier --------------------------------------------------


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
            lambda key, user: make_token(key, default_claims(sub=str(user.id), is_anonymous=True)),
            id="anonymous-user",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id), role="anon")),
            id="role-anon",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id), aud="other")),
            id="wrong-audience",
        ),
        pytest.param(
            lambda key, user: make_token(
                key, default_claims(sub=str(user.id), iss="https://evil.example/auth/v1")
            ),
            id="wrong-issuer",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub="not-a-uuid")),
            id="subject-not-a-uuid",
        ),
        pytest.param(
            lambda key, user: make_token(key, default_claims(sub=str(user.id)), omit=("sub",)),
            id="no-subject",
        ),
    ],
)
@pytest.mark.parametrize(
    ("method", "kwargs"),
    [("get", {}), ("patch", {"json": {"display_name": "Hacked"}})],
    ids=["GET", "PATCH"],
)
def test_invalid_tokens_answer_401_and_touch_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    build: Any,
    method: str,
    kwargs: dict[str, Any],
) -> None:
    user = users[0]
    token = build(signing_key, user)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    before = admin_profile(admin, user)

    response = getattr(client, method)(URL, headers=headers, **kwargs)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert admin_profile(admin, user) == before


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
        response = client.get(URL, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    for fragment in token.split("."):
        assert fragment not in response.text and fragment not in caplog.text


def test_unreachable_signing_keys_answer_503_not_401(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    with running_jwks_server() as server:
        dead_url = server.url  # stopped when the block ends: nothing listens there
    dead = make_verifier(dead_url)
    main.app.dependency_overrides[get_token_verifier] = lambda: dead

    response = client.get(URL, headers=bearer(signing_key, users[0]))

    error = assert_error(response, 503, "SERVICE_UNAVAILABLE")
    assert "www-authenticate" not in response.headers
    assert "jwks" not in error["message"].lower()


# --- infrastructure and edge cases ----------------------------------------------------------


def test_a_valid_token_of_a_deleted_user_finds_no_profile(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    admin.execute("delete from auth.users where id = %s", [user.id])  # cascades to the profile

    read = client.get(URL, headers=headers)
    patched = client.patch(URL, json={"display_name": "Ghost"}, headers=headers)

    assert_error(read, 404, "PROFILE_NOT_FOUND")
    assert_error(patched, 404, "PROFILE_NOT_FOUND")


def test_a_closed_pool_answers_503(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    closed = open_api_pool(max_size=1, timeout=2)
    closed.close()
    main.app.dependency_overrides[get_db_pool] = lambda: closed
    headers = bearer(signing_key, users[0])

    read = client.get(URL, headers=headers)
    patched = client.patch(URL, json={"display_name": "Ann"}, headers=headers)

    assert_error(read, 503, "SERVICE_UNAVAILABLE")
    assert_error(patched, 503, "SERVICE_UNAVAILABLE")
