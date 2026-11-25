"""GET /career-goals/{goal_id}/competencies end to end: HTTP -> JWT -> service -> repository ->
PostgreSQL with Row Level Security (Stage 9.2f, step A).

Nothing between the HTTP request and the database is replaced: the access token is verified by
the real TokenVerifier against a local JWKS server, the service runs a real user_transaction()
as role `authenticated` on the `skillloop_api` login, and RLS applies. Needs TEST_DATABASE_URL and
TEST_DATABASE_ADMIN_URL (see db_integration_helpers); the throwaway users come from the `users`
fixture, which deletes them (and, by cascade, their goals and competencies) afterwards.

The administration connection only PREPARES data (goals, competencies, archiving) and is never
used to make a request. Its writes follow contract L2 (docs/DATABASE-DESIGN.md, section 4.8):
the goal is locked first. Competencies are archived BEFORE their goal, because the row trigger
refuses every write to a competency of an archived goal.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from app import main
from app.api.deps import get_db_pool, get_token_verifier
from app.models.auth import AuthenticatedUser
from tests.auth_helpers import JWKSServer, SigningKey, make_verifier
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    open_api_pool,
)
from tests.test_career_goals_api_integration import assert_error, bearer

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

BASE = "/api/v1/career-goals"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
RESPONSE_KEYS = {
    "id",
    "career_goal_id",
    "name",
    "description",
    "rationale",
    "category",
    "knowledge_type",
    "importance",
    "expected_level",
    "display_order",
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


# --- data preparation (administration connection only) ---------------------------------------


def url(goal_id: UUID) -> str:
    return f"{BASE}/{goal_id}/competencies"


def make_goal(admin: psycopg.Connection, user: AuthenticatedUser) -> UUID:
    row = admin.execute(
        "insert into public.career_goals (user_id, target_role, seniority) "
        "values (%s, 'DevOps Engineer', 'junior') returning id",
        [user.id],
    ).fetchone()
    assert row
    return row[0]


def archive_goal(admin: psycopg.Connection, goal_id: UUID) -> None:
    admin.execute("update public.career_goals set status = 'archived' where id = %s", [goal_id])


def add(
    admin: psycopg.Connection,
    user: AuthenticatedUser,
    goal_id: UUID,
    name: str,
    *,
    display_order: int = 0,
    created_at: datetime = T0,
    archived: bool = False,
) -> UUID:
    """Insert one competency (goal locked first, L2) and optionally archive it."""
    with admin.transaction():
        admin.execute(
            "select 1 from public.career_goals where id = %s for no key update", [goal_id]
        ).fetchall()
        row = admin.execute(
            "insert into public.competencies (career_goal_id, user_id, name, knowledge_type, "
            "importance, expected_level, display_order, created_at) "
            "values (%s, %s, %s, 'application', 'high', 'intermediate', %s, %s) returning id",
            [goal_id, user.id, name, display_order, created_at],
        ).fetchone()
        assert row
        if archived:
            admin.execute(
                "update public.competencies set status = 'archived' where id = %s", [row[0]]
            )
    return row[0]


def names(response: Any) -> list[str]:
    return [item["name"] for item in response.json()["competencies"]]


# --- 1. an empty list ------------------------------------------------------------------------


def test_a_goal_without_competencies_answers_200_and_an_empty_list(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    assert response.json() == {"competencies": []}


# --- 2. active and archived competencies -----------------------------------------------------


def test_the_list_contains_active_and_archived_competencies_with_their_data(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    active_id = add(admin, user, goal, "Docker", display_order=0)
    archived_id = add(admin, user, goal, "Old tool", display_order=1, archived=True)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    items = response.json()["competencies"]
    assert [(i["id"], i["name"], i["status"]) for i in items] == [
        (str(active_id), "Docker", "active"),
        (str(archived_id), "Old tool", "archived"),
    ]
    assert {i["career_goal_id"] for i in items} == {str(goal)}
    first = items[0]
    assert (first["knowledge_type"], first["importance"], first["expected_level"]) == (
        "application",
        "high",
        "intermediate",
    )
    assert (first["description"], first["rationale"], first["category"]) == (None, None, None)
    assert first["created_at"] == "2026-01-01T12:00:00Z"


# --- 3. the order: display_order, created_at, id ---------------------------------------------


def test_the_order_is_display_order_then_created_at_then_id(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    # Inserted in a deliberately "wrong" order, with archived rows in between: the status plays
    # no part in the order.
    add(admin, user, goal, "order-5", display_order=5, created_at=T0)
    add(admin, user, goal, "order-0-late", display_order=0, created_at=T0 + timedelta(hours=2))
    add(
        admin,
        user,
        goal,
        "order-0-early-archived",
        display_order=0,
        created_at=T0 + timedelta(hours=1),
        archived=True,
    )
    add(admin, user, goal, "order-0-first", display_order=0, created_at=T0)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    assert names(response) == [
        "order-0-first",
        "order-0-early-archived",
        "order-0-late",
        "order-5",
    ]


def test_ties_of_display_order_and_created_at_are_broken_by_id(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    ids = [add(admin, user, goal, f"tie-{n}", display_order=3, created_at=T0) for n in range(5)]
    add(admin, user, goal, "after-the-ties", display_order=4, created_at=T0)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    listed = [item["id"] for item in response.json()["competencies"]]
    # PostgreSQL compares uuid bytewise, which is the order of the lowercase hex text.
    assert listed[:5] == sorted(str(i) for i in ids)
    assert response.json()["competencies"][5]["name"] == "after-the-ties"
    # A second request returns exactly the same order (the order is total, not by luck).
    again = client.get(url(goal), headers=bearer(signing_key, user))
    assert again.json() == response.json()


# --- 4. and 5. a goal of another user, a goal that does not exist ----------------------------


def test_the_goal_of_another_user_is_a_404_and_reveals_nothing(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user_a, user_b = users[0], users[1]
    goal_a = make_goal(admin, user_a)
    goal_b = make_goal(admin, user_b)
    add(admin, user_a, goal_a, "Owned by A")
    add(admin, user_b, goal_b, "Owned by B (secret)")

    response = client.get(url(goal_b), headers=bearer(signing_key, user_a))

    error = assert_error(response, 404, "GOAL_NOT_FOUND")
    assert "secret" not in response.text and str(user_b.id) not in response.text
    # The same answer as for a goal that does not exist.
    missing = client.get(url(uuid4()), headers=bearer(signing_key, user_a))
    assert assert_error(missing, 404, "GOAL_NOT_FOUND")["message"] == error["message"]
    # The control: each user still reads own data, and only own data.
    assert names(client.get(url(goal_a), headers=bearer(signing_key, user_a))) == ["Owned by A"]
    assert names(client.get(url(goal_b), headers=bearer(signing_key, user_b))) == [
        "Owned by B (secret)"
    ]
    # A goal of A is just as invisible to B.
    foreign = client.get(url(goal_a), headers=bearer(signing_key, user_b))
    assert_error(foreign, 404, "GOAL_NOT_FOUND")


def test_a_goal_that_does_not_exist_is_a_404(
    client: TestClient, signing_key: SigningKey, users: list[AuthenticatedUser]
) -> None:
    response = client.get(url(uuid4()), headers=bearer(signing_key, users[0]))

    assert_error(response, 404, "GOAL_NOT_FOUND")


# --- 6. an archived goal ---------------------------------------------------------------------


def test_an_archived_goal_still_lists_its_competencies(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    add(admin, user, goal, "Kept active", display_order=0)
    add(admin, user, goal, "Kept archived", display_order=1, archived=True)
    archive_goal(admin, goal)  # last: afterwards no competency of the goal can be written

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    items = response.json()["competencies"]
    assert [(i["name"], i["status"]) for i in items] == [
        ("Kept active", "active"),
        ("Kept archived", "archived"),
    ]


def test_an_archived_goal_without_competencies_gives_an_empty_list(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    archive_goal(admin, goal)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    assert response.json() == {"competencies": []}


# --- 7. no user_id ---------------------------------------------------------------------------


def test_the_response_never_contains_the_user_id(
    client: TestClient,
    signing_key: SigningKey,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    add(admin, user, goal, "One")
    add(admin, user, goal, "Two", display_order=1, archived=True)

    response = client.get(url(goal), headers=bearer(signing_key, user))

    assert response.status_code == 200
    items = response.json()["competencies"]
    assert len(items) == 2
    for item in items:
        assert set(item) == RESPONSE_KEYS
    assert "user_id" not in response.text
    assert str(user.id) not in response.text


# --- 8. no token -----------------------------------------------------------------------------


def test_a_request_without_a_token_is_a_401_and_does_not_reach_the_data(
    client: TestClient,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user)
    add(admin, user, goal, "Hidden without a token")

    response = client.get(url(goal))

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Hidden" not in response.text
