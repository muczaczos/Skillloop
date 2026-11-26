"""PATCH /competencies/{id} and POST /competencies/{id}/archive end to end:
HTTP -> JWT -> service -> repository -> PostgreSQL with Row Level Security (Stage 9.3).

Nothing between the HTTP request and the database is replaced. Needs TEST_DATABASE_URL and
TEST_DATABASE_ADMIN_URL (see db_integration_helpers); the throwaway users come from the `users`
fixture, which deletes them (and, by cascade, their goals and competencies) afterwards. The
administration connection only prepares and inspects data (writes follow contract L2).

Races are made deterministic like in test_services_concurrency.py: a transaction is held open
(`held_transaction`), the HTTP request is started and seen WAITING for it
(`wait_until_blocked_by`), then the holder commits. No sleeps order anything.
"""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from app import main
from app.api.deps import get_db_pool, get_token_verifier
from app.models.auth import AuthenticatedUser
from app.repositories import career_goals, competencies
from tests.auth_helpers import JWKSServer, SigningKey, make_verifier
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    open_api_pool,
)
from tests.test_career_goals_api_integration import assert_error, bearer
from tests.test_competencies_api_integration import (
    CANARY,
    FULL,
    MAX_DISPLAY_ORDER,
    REQUIRED,
    RESPONSE_KEYS,
    add,
    archive_goal,
    make_goal,
    url,
)
from tests.test_services_concurrency import TIMEOUT, held_transaction, wait_until_blocked_by

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)


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


# --- helpers ---------------------------------------------------------------------------------


def item(competency_id: Any) -> str:
    return f"/api/v1/competencies/{competency_id}"


def archive_url(competency_id: Any) -> str:
    return f"{item(competency_id)}/archive"


def create(
    client: TestClient, headers: dict[str, str], goal: UUID, **changes: Any
) -> dict[str, Any]:
    """A competency with every field set, created through the API."""
    response = client.post(url(goal), json={**FULL, **changes}, headers=headers)
    assert response.status_code == 201
    return response.json()


def row(admin: psycopg.Connection, competency_id: Any) -> dict[str, Any]:
    """The whole row as the database holds it, updated_at included."""
    found = admin.execute(
        "select to_jsonb(c) from public.competencies c where id = %s", [competency_id]
    ).fetchone()
    assert found
    return found[0]


def without_updated_at(competency: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in competency.items() if k != "updated_at"}


def assert_changed(
    admin: psycopg.Connection, response: Any, before: dict[str, Any], changes: dict[str, Any]
) -> None:
    """200, exactly `changes` differ from `before` (the API object), in the answer and in the
    database, and updated_at moved forward."""
    assert response.status_code == 200
    body = response.json()
    assert set(body) == RESPONSE_KEYS
    assert without_updated_at(body) == without_updated_at({**before, **changes})
    assert datetime.fromisoformat(body["updated_at"]) > datetime.fromisoformat(before["updated_at"])
    stored = row(admin, before["id"])
    for key in RESPONSE_KEYS - {"created_at", "updated_at"}:
        assert stored[key] == body[key], key
    assert datetime.fromisoformat(stored["updated_at"]) == datetime.fromisoformat(
        body["updated_at"]
    )


def patch_call(client: TestClient, competency_id: Any, headers: dict[str, str]) -> Any:
    return client.patch(item(competency_id), json={"name": "Changed"}, headers=headers)


def archive_call(client: TestClient, competency_id: Any, headers: dict[str, str]) -> Any:
    return client.post(archive_url(competency_id), headers=headers)


BOTH = pytest.mark.parametrize("call", [patch_call, archive_call], ids=["patch", "archive"])


# --- PATCH: successful updates ----------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"name": "Terraform Cloud"}, id="name"),
        pytest.param({"description": "New description."}, id="description"),
        pytest.param({"rationale": "New rationale."}, id="rationale"),
        pytest.param({"category": "Cloud"}, id="category"),
        pytest.param({"knowledge_type": "memory"}, id="knowledge_type"),
        pytest.param({"importance": "low"}, id="importance"),
        pytest.param({"expected_level": "advanced"}, id="expected_level"),
        pytest.param({"display_order": 0}, id="display_order-zero"),
        pytest.param({"display_order": MAX_DISPLAY_ORDER}, id="display_order-max"),
        pytest.param(
            {
                "name": "Ansible",
                "description": "Configuration management.",
                "rationale": "Servers are configured as code.",
                "category": "Automation",
                "knowledge_type": "documentation",
                "importance": "critical",
                "expected_level": "beginner",
                "display_order": 2,
            },
            id="every-field",
        ),
        pytest.param({"description": None, "rationale": None, "category": None}, id="nulls"),
        pytest.param({"name": "n" * 100, "category": "c" * 60}, id="longest-name-category"),
        pytest.param({"description": "d" * 1000, "rationale": "r" * 1000}, id="longest-texts"),
    ],
)
def test_patch_changes_exactly_the_sent_fields(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    changes: dict[str, Any],
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    before = create(client, headers, make_goal(admin, user))

    response = client.patch(item(before["id"]), json=changes, headers=headers)

    assert_changed(admin, response, before, changes)


@pytest.mark.parametrize("field", ["description", "rationale", "category"])
@pytest.mark.parametrize("value", [None, "", "  \n\t "], ids=["null", "empty", "blank"])
def test_patch_clears_one_optional_text_and_keeps_the_others(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    field: str,
    value: str | None,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    before = create(client, headers, make_goal(admin, user))

    response = client.patch(item(before["id"]), json={field: value}, headers=headers)

    assert_changed(admin, response, before, {field: None})


def test_patch_strips_whitespace_and_the_change_is_listed(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    before = create(client, headers, goal)

    response = client.patch(
        item(before["id"]), json={"name": "  Git \n", "category": " VCS "}, headers=headers
    )

    assert_changed(admin, response, before, {"name": "Git", "category": "VCS"})
    assert client.get(url(goal), headers=headers).json() == {"competencies": [response.json()]}


# --- PATCH: validation, 422 and nothing is written ---------------------------------------------

INVALID = [
    pytest.param({}, id="empty"),
    *[
        pytest.param({"importance": "low", f: "x"}, id=f"forbidden-{f}")
        for f in ("id", "created_at", "updated_at", "unknown")
    ],
    pytest.param({"status": "archived"}, id="status-archived"),
    pytest.param({"importance": "low", "status": "active"}, id="status-active"),
    pytest.param({"importance": "low", "user_id": str(uuid4())}, id="user_id"),
    pytest.param({"importance": "low", "career_goal_id": str(uuid4())}, id="career_goal_id"),
    *[
        pytest.param({f: None}, id=f"null-{f}")
        for f in ("name", "knowledge_type", "importance", "expected_level", "display_order")
    ],
    pytest.param({"name": "   "}, id="name-blank"),
    pytest.param({"knowledge_type": "magic"}, id="knowledge_type"),
    pytest.param({"importance": "urgent"}, id="importance"),
    pytest.param({"expected_level": "not_assessed"}, id="expected_level"),
    *[
        pytest.param({"display_order": v}, id=f"display_order-{v!r}")
        for v in (5.0, 5.5, "5", True, False, -1, MAX_DISPLAY_ORDER + 1, 10**30)
    ],
    pytest.param({"name": "n" * 101}, id="name-101"),
    pytest.param({"description": "d" * 1001}, id="description-1001"),
    pytest.param({"rationale": "r" * 1001}, id="rationale-1001"),
    pytest.param({"category": "c" * 61}, id="category-61"),
    *[
        pytest.param({f: "a\x00b"}, id=f"nul-{f}")
        for f in ("name", "description", "rationale", "category")
    ],
    pytest.param({"name": "Valid new name", "importance": "urgent"}, id="valid-plus-invalid"),
]


@pytest.mark.parametrize("body", INVALID)
def test_an_invalid_patch_is_422_and_writes_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    body: dict[str, Any],
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    competency = create(client, headers, make_goal(admin, user))
    before = row(admin, competency["id"])

    response = client.patch(item(competency["id"]), json=body, headers=headers)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert error["details"]
    assert row(admin, competency["id"]) == before


def test_a_rejected_patch_value_is_not_echoed(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    competency = create(client, headers, make_goal(admin, user))

    response = client.patch(
        item(competency["id"]), json={"importance": CANARY, "status": CANARY}, headers=headers
    )

    assert_error(response, 422, "VALIDATION_ERROR")
    assert CANARY not in response.text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("patch", "/api/v1/competencies/not-a-uuid", {"name": "Git"}),
        ("post", "/api/v1/competencies/not-a-uuid/archive", None),
    ],
    ids=["patch", "archive"],
)
def test_a_malformed_id_is_422(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    response = client.request(
        method.upper(), path, json=body, headers=bearer(signing_key, users[0])
    )

    assert_error(response, 422, "VALIDATION_ERROR")


# --- authentication and ownership --------------------------------------------------------------


@BOTH
def test_without_a_token_nothing_is_changed(
    client: TestClient,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    call: Callable[..., Any],
) -> None:
    user = users[0]
    competency_id = add(admin, user, make_goal(admin, user), "Docker")
    before = row(admin, competency_id)

    response = call(client, competency_id, {})

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert row(admin, competency_id) == before


@BOTH
@pytest.mark.parametrize("state", ["active", "archived", "goal-archived"])
def test_another_users_competency_is_404_whatever_its_state(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    call: Callable[..., Any],
    state: str,
) -> None:
    owner, intruder = users[0], users[1]
    goal = make_goal(admin, owner)
    make_goal(admin, intruder)
    competency_id = add(admin, owner, goal, "Secret skill", archived=state == "archived")
    if state == "goal-archived":
        archive_goal(admin, goal)
    before = row(admin, competency_id)
    headers = bearer(signing_key, intruder)

    foreign = call(client, competency_id, headers)
    missing = call(client, uuid4(), headers)

    error = assert_error(foreign, 404, "COMPETENCY_NOT_FOUND")
    assert assert_error(missing, 404, "COMPETENCY_NOT_FOUND")["message"] == error["message"]
    assert "Secret" not in foreign.text and str(owner.id) not in foreign.text
    assert row(admin, competency_id) == before


@BOTH
def test_a_valid_token_of_a_deleted_user_gets_404(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    call: Callable[..., Any],
) -> None:
    user = users[0]
    competency_id = add(admin, user, make_goal(admin, user), "Docker")
    headers = bearer(signing_key, user)
    admin.execute("delete from auth.users where id = %s", [user.id])

    response = call(client, competency_id, headers)

    assert_error(response, 404, "COMPETENCY_NOT_FOUND")


# --- the state of the resource -----------------------------------------------------------------


@BOTH
@pytest.mark.parametrize(
    ("competency_archived", "goal_archived", "code"),
    [
        (True, False, "COMPETENCY_ARCHIVED"),
        (False, True, "GOAL_ARCHIVED"),
        (True, True, "COMPETENCY_ARCHIVED"),  # the resource is reported before its parent
    ],
    ids=["competency", "goal", "both"],
)
def test_an_archived_competency_or_goal_is_409_and_frozen(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    call: Callable[..., Any],
    competency_archived: bool,
    goal_archived: bool,
    code: str,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    competency_id = add(admin, user, goal, "Docker", archived=competency_archived)
    if goal_archived:
        archive_goal(admin, goal)
        make_goal(admin, user)  # a new active goal does not thaw the old one
    before = row(admin, competency_id)

    response = call(client, competency_id, bearer(signing_key, user))

    assert_error(response, 409, code)
    assert row(admin, competency_id) == before


# --- names --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Docker", "docker", "  DOCKER \n"])
def test_a_rename_to_a_taken_name_is_409_and_no_field_is_saved(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    name: str,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    taken = create(client, headers, goal, name="Docker")
    other = create(client, headers, goal, name="Podman")
    before = (row(admin, taken["id"]), row(admin, other["id"]))

    response = client.patch(
        item(other["id"]),
        json={"name": name, "importance": "low", "description": None},
        headers=headers,
    )

    assert_error(response, 409, "COMPETENCY_NAME_EXISTS")
    assert (row(admin, taken["id"]), row(admin, other["id"])) == before


def test_a_competency_may_change_the_case_of_its_own_name(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    before = create(client, headers, make_goal(admin, user), name="docker")

    response = client.patch(item(before["id"]), json={"name": "Docker"}, headers=headers)

    assert_changed(admin, response, before, {"name": "Docker"})


def test_a_rename_may_take_the_name_of_an_archived_competency_or_of_another_users(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user, other = users[0], users[1]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    add(admin, user, goal, "Retired", archived=True)
    add(admin, other, make_goal(admin, other), "Elsewhere")
    before = create(client, headers, goal, name="Podman")

    first = client.patch(item(before["id"]), json={"name": "retired"}, headers=headers)
    assert_changed(admin, first, before, {"name": "retired"})
    second = client.patch(item(before["id"]), json={"name": "Elsewhere"}, headers=headers)
    assert_changed(admin, second, first.json(), {"name": "Elsewhere"})


# --- archive -------------------------------------------------------------------------------------


def test_archive_returns_the_archived_competency_and_changes_nothing_else(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    before = create(client, headers, goal)
    neighbour = create(client, headers, goal, name="Neighbour")
    neighbour_row = row(admin, neighbour["id"])

    response = client.post(archive_url(before["id"]), headers=headers)

    assert_changed(admin, response, before, {"status": "archived"})
    assert str(user.id) not in response.text
    assert row(admin, neighbour["id"]) == neighbour_row
    assert client.get(url(goal), headers=headers).json() == {
        "competencies": [response.json(), neighbour]
    }


def test_an_archived_competency_cannot_be_changed_through_any_path(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    competency = create(client, headers, goal)
    assert client.post(archive_url(competency["id"]), headers=headers).status_code == 200
    before = row(admin, competency["id"])
    target = item(competency["id"])

    again = client.post(archive_url(competency["id"]), headers=headers)
    edited = client.patch(target, json={"description": None}, headers=headers)
    revived = client.patch(target, json={"status": "active"}, headers=headers)
    replaced = client.put(target, json=FULL, headers=headers)
    deleted = client.delete(target, headers=headers)
    added = client.post(url(goal), json=FULL, headers=headers)  # same name, a NEW row

    assert_error(again, 409, "COMPETENCY_ARCHIVED")
    assert_error(edited, 409, "COMPETENCY_ARCHIVED")
    assert_error(revived, 422, "VALIDATION_ERROR")
    assert_error(replaced, 405, "METHOD_NOT_ALLOWED")
    assert_error(deleted, 405, "METHOD_NOT_ALLOWED")
    assert added.status_code == 201 and added.json()["id"] != competency["id"]
    assert row(admin, competency["id"]) == before


def test_archive_frees_a_slot_of_the_limit_and_releases_the_name(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    ids = [add(admin, user, goal, f"filler-{n}") for n in range(50)]
    refused = client.post(url(goal), json={**REQUIRED, "name": "filler-7"}, headers=headers)
    assert_error(refused, 409, "COMPETENCY_LIMIT_REACHED")

    assert client.post(archive_url(ids[7]), headers=headers).status_code == 200
    accepted = client.post(url(goal), json={**REQUIRED, "name": "filler-7"}, headers=headers)
    full_again = client.post(url(goal), json=REQUIRED, headers=headers)

    assert accepted.status_code == 201
    assert_error(full_again, 409, "COMPETENCY_LIMIT_REACHED")
    count = admin.execute(
        "select count(*) filter (where status = 'active'), count(*) "
        "from public.competencies where career_goal_id = %s",
        [goal],
    ).fetchone()
    assert count == (50, 51)


# --- concurrency ---------------------------------------------------------------------------------


def send_while_held(
    admin: psycopg.Connection,
    pool: ConnectionPool,
    user: AuthenticatedUser,
    action: Callable[[psycopg.Connection], Any],
    request: Callable[[], Any],
) -> Any:
    """Keep `action` uncommitted, send `request`, prove that it waits for that transaction, let
    the transaction commit and return the response."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        with held_transaction(pool, user, action) as held:
            future = executor.submit(request)
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
        return future.result(TIMEOUT)


def test_two_updates_of_one_competency_are_both_kept(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    api_pool: ConnectionPool,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    competency = create(client, headers, make_goal(admin, user))
    competency_id = UUID(competency["id"])

    response = send_while_held(
        admin,
        api_pool,
        user,
        lambda conn: competencies.update_active(conn, user.id, competency_id, {"name": "First"}),
        lambda: client.patch(item(competency_id), json={"importance": "low"}, headers=headers),
    )

    assert response.status_code == 200
    assert (response.json()["name"], response.json()["importance"]) == ("First", "low")
    stored = row(admin, competency_id)
    assert (stored["name"], stored["importance"]) == ("First", "low")


@BOTH
def test_a_request_that_waits_for_an_archiving_of_the_competency_is_409(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    api_pool: ConnectionPool,
    call: Callable[..., Any],
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    competency_id = add(admin, user, make_goal(admin, user), "Docker")

    response = send_while_held(
        admin,
        api_pool,
        user,
        lambda conn: competencies.archive_active(conn, user.id, competency_id),
        lambda: call(client, competency_id, headers),
    )

    assert_error(response, 409, "COMPETENCY_ARCHIVED")
    stored = row(admin, competency_id)
    assert (stored["name"], stored["status"]) == ("Docker", "archived")


@BOTH
def test_a_request_that_waits_for_an_archiving_of_the_goal_is_409(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    api_pool: ConnectionPool,
    call: Callable[..., Any],
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    competency_id = add(admin, user, goal, "Docker")
    before = row(admin, competency_id)

    response = send_while_held(
        admin,
        api_pool,
        user,
        lambda conn: career_goals.archive_active(conn, user.id, goal),
        lambda: call(client, competency_id, headers),
    )

    assert_error(response, 409, "GOAL_ARCHIVED")
    assert row(admin, competency_id) == before


def test_a_rename_that_waits_for_the_same_new_name_is_409(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
    api_pool: ConnectionPool,
) -> None:
    user = users[0]
    headers = bearer(signing_key, user)
    goal = make_goal(admin, user)
    first = add(admin, user, goal, "One")
    second = add(admin, user, goal, "Two")
    before = row(admin, second)

    response = send_while_held(
        admin,
        api_pool,
        user,
        lambda conn: competencies.update_active(conn, user.id, first, {"name": "Shared"}),
        lambda: client.patch(item(second), json={"name": "shared"}, headers=headers),
    )

    assert_error(response, 409, "COMPETENCY_NAME_EXISTS")
    assert row(admin, first)["name"] == "Shared"
    assert row(admin, second) == before
